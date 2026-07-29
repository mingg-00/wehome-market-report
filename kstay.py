#!/usr/bin/env python3
"""
k-stay.ai 오픈 API 클라이언트.

k-stay.ai 는 위홈이 운영하는 서비스로, 행안부 지방행정 인허가 데이터를 매일
04:00 KST 에 받아 5종 숙박 카테고리로 정리해 인증 없이 JSON 으로 뿌린다.
덕분에 data.go.kr 인증키 없이도 **서울 25개 구 단위**와 24개월 시계열을 쓸 수 있다.

⚠️ 이 사이트에는 진짜 데이터와 가짜 데이터가 섞여 있다 — 반드시 구분해서 쓸 것.
  - REAL: 이 모듈이 쓰는 /api/report, /api/stats, /api/registrations/monthly,
    /api/tourism/inbound 는 행안부 원본 CSV(file.localdata.go.kr)를 실제로 받아
    집계한 값이다. 2026-07-28 CSV 직접 다운로드로 대조 검증함(14,060행, 영업중
    10,797 vs API 11,206 — 집계 시점 차이 수준의 오차, 실데이터임을 확인).
  - FAKE: k-stay.ai/analysis 페이지의 "Airbnb 리스팅 대비 미등록률"·"가격/박 샘플"
    수치는 페이지 자체에 "시뮬레이션한 데모용 샘플" 면책 문구가 붙어 있고, 소스코드
    (generate_airbnb_sample.py)가 70%/30% 하드코딩 난수로 찍어낸 것임을 확인했다.
    "서울 에어비앤비 리스팅 중 50%만 등록" 같은 문구는 **리포트에 절대 인용하지 말 것**
    — 이 모듈은 애초에 그 엔드포인트를 호출하지 않는다(문서화된 /api/* 목록에도 없음).

세이프스테이(safestay.py)와 숫자가 다르다 — 같은 대상을 다른 계통으로 집계해서다.
어느 쪽이 맞다고 단정하지 말고 reconcile() 로 격차를 리포트에 명시하는 쪽을 택했다.
공유숙박 데이터는 전부 이해관계자가 만든다. 단일 소스를 그대로 싣는 게 제일 위험하다.

라이선스: 원자료가 공공누리 제4유형(출처표시·상업적이용금지·변경금지)이다.
리포트에 쓸 때 출처 표기는 선택이 아니라 의무다. ATTRIBUTION 참고.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

BASE = "https://k-stay.ai/api"
UA = {"User-Agent": "wehome-market-report/1.0 (+https://wehome.me)"}
ODM_KEY = "foreigner_city_homestays"

ATTRIBUTION = ("출처: 행정안전부 지방행정 인허가 데이터 / K-STAY(k-stay.ai, wehome) "
               "· 공공누리 제4유형")


def get(path: str, **params) -> dict:
    r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params or None,
                     headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


@dataclass
class District:
    sigungu: str
    active: int


@dataclass
class MonthlyReport:
    """/api/report/{ym} 한 달치. 리포트 본문 수치는 전부 여기서 나온다."""
    ym: str
    issue: int
    basis_date: str
    active: int
    closed: int
    total: int
    seoul_active: int
    seoul_districts: list[District]
    busan_districts: list[District]
    monthly_trend: list[tuple[str, int]]     # [("2026-07", 288), ...] 월별 신규 등록
    categories: dict[str, dict]

    @property
    def closure_rate(self) -> float:
        return self.closed / self.total if self.total else 0.0

    @property
    def seoul_share(self) -> float:
        return self.seoul_active / self.active if self.active else 0.0

    def top_districts(self, n: int = 10) -> list[District]:
        return sorted(self.seoul_districts, key=lambda d: -d.active)[:n]

    def concentration(self, n: int = 3) -> float:
        """상위 n개 구가 서울 전체에서 차지하는 비중. 시장 집중도 지표."""
        top = sum(d.active for d in self.top_districts(n))
        return top / self.seoul_active if self.seoul_active else 0.0


def list_reports() -> list[dict]:
    """발행된 호(issue) 목록. year_month 최신순."""
    reports = get("reports")
    items = reports if isinstance(reports, list) else reports.get("reports", [])
    return sorted(items, key=lambda i: i["year_month"], reverse=True)


def latest_report_ym() -> str:
    return list_reports()[0]["year_month"]


def fetch_report(ym: str | None = None) -> MonthlyReport:
    ym = ym or latest_report_ym()
    d = get(f"report/{ym}")
    snap = d["snapshot"]
    odm = snap["categories"][ODM_KEY]
    return MonthlyReport(
        ym=ym,
        issue=d.get("issue_number", 0),
        basis_date=d.get("data_basis_date", ""),
        active=odm["active"],
        closed=odm["closed"],
        total=odm["total"],
        seoul_active=odm["seoul_active"],
        seoul_districts=[District(x["sigungu"], x["active"]) for x in snap.get("seoul_districts", [])],
        busan_districts=[District(x["sigungu"], x["active"]) for x in snap.get("busan_districts", [])],
        monthly_trend=[(x["ym"], x["count"]) for x in snap.get("monthly_trend", [])],
        categories=snap.get("categories", {}),
    )


def fetch_inbound() -> dict:
    """방한 외래객 통계. 수요 서사의 근거."""
    return get("tourism/inbound")


def reconcile(kstay_active: int, safestay_operating: int) -> dict:
    """
    두 공식 계통의 격차를 수치화한다. 리포트에 그대로 싣기 위한 것 —
    숨기면 나중에 독자가 다른 숫자를 들고 와서 신뢰를 잃는다.
    """
    gap = safestay_operating - kstay_active
    base = max(kstay_active, 1)
    return {
        "kstay": kstay_active,
        "safestay": safestay_operating,
        "gap": gap,
        "gap_pct": gap / base,
        "note": (f"행안부 인허가 계통 {kstay_active:,}곳 vs 한국관광공사 세이프스테이 "
                 f"{safestay_operating:,}곳, 격차 {gap:+,}곳({gap / base:+.1%}). "
                 "집계 시점과 영업상태 판정 기준이 달라 생기는 차이로, "
                 "어느 한쪽을 단일 진실로 쓰지 않는다."),
    }


if __name__ == "__main__":
    r = fetch_report()
    print(f"ISSUE {r.issue} · {r.ym} · 기준 {r.basis_date}")
    print(f"  외도민 영업중 {r.active:,} / 폐업 {r.closed:,} (폐업률 {r.closure_rate:.1%})")
    print(f"  서울 {r.seoul_active:,} ({r.seoul_share:.1%})  "
          f"상위3구 집중도 {r.concentration(3):.1%}")
    print(f"  서울 구 {len(r.seoul_districts)}개 · 추이 {len(r.monthly_trend)}개월")
    print("\n  서울 상위 10개 구")
    for i, d in enumerate(r.top_districts(10), 1):
        print(f"   {i:2}. {d.sigungu:<8} {d.active:>6,}")

    import safestay
    s = safestay.collect()
    print("\n" + reconcile(r.active, s.operating(s.months[0]))["note"])
