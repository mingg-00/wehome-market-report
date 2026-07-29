#!/usr/bin/env python3
"""
행정안전부 지방행정 인허가 데이터 직접 수집.

k-stay.ai API 에 얹혀 있던 걸 걷어내고, k-stay가 크롤링해오는 원천
(file.localdata.go.kr)에서 우리가 직접 5종 카테고리 CSV를 받아 우리가 집계한다.
이제 이 사이트의 핵심 통계는 k-stay 가동 여부와 완전히 무관하다.

인증·API키 불필요. 다만 info 페이지를 먼저 GET해 세션 쿠키를 받아야
download 엔드포인트가 200을 준다(쿠키 없이 다이렉트로 치면 403) — k-stay의
공개 GitHub(josanku/wehome-insight, fetch_data.py)에서 확인한 방식 그대로다.

k-stay API 대비 우리가 직접 얻는 이득 셋:
  1. 전국 모든 시군구 단위 접근(k-stay는 서울·부산만 구 단위로 노출).
  2. 인허가일자 원본이 있어 24개월 제한 없이 전체 이력을 월별로 집계할 수 있다.
  3. active/closed/pause 판정 기준을 우리가 정의해 투명하게 공개할 수 있다
     (k-stay는 판정 로직을 공개하지 않아 소폭 다른 숫자가 나온다 — kstay.py 참고).

인바운드 관광객 통계는 여기 포함하지 않는다 — k-stay 카탈로그 자체에
"manual": True 로 표시돼 있어(KTO 데이터랩+법무부 통계연보를 손으로 큐레이션한 것)
"크롤링해오는 곳"이 애초에 없다. 그건 kstay.fetch_inbound() 를 계속 쓴다.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36", "Accept": "*/*"}
INFO_URL = "https://file.localdata.go.kr/file/{slug}/info"
DOWNLOAD_URL = "https://file.localdata.go.kr/file/download/{slug}/info"

CATEGORIES = {
    "foreigner_city_homestays": "외국인관광도시민박업",
    "hanok_experience": "한옥체험업",
    "tourist_accommodations": "관광숙박업(호텔/호스텔)",
    "rural_homestays": "농어촌민박",
    "tourist_pensions": "관광펜션업",
}
FLAGSHIP = "foreigner_city_homestays"  # 이 시장리포트의 주력 카테고리

# LOCALDATA 표준 스키마의 상태명은 업종 불문 공통이다(2026-07-28, 5종 전수 확인).
CLOSED_STATUSES = {"폐업", "등록취소", "지정취소", "허가취소", "직권말소"}
PAUSE_STATUSES = {"휴업"}
# 위 두 집합에 없으면 active 로 본다("영업중"이 절대다수, 업종별 변종 표기 방지).


@dataclass
class CategoryStats:
    slug: str
    name_ko: str
    active: int
    closed: int
    pause: int
    total: int
    by_sigungu: dict[str, int] = field(default_factory=dict)   # "서울특별시 마포구" -> 영업중 수
    monthly_registrations: dict[str, int] = field(default_factory=dict)  # "2026-07" -> 신규등록 건수(전체 이력)
    by_sigungu_monthly: dict[str, dict[str, int]] = field(default_factory=dict)
    # "서울특별시 마포구" -> {"2026-07": 12, ...} — 구별 월간 신규등록(현재 상태 무관, 전체 이력)
    cohort_survival: dict[str, dict[str, float]] = field(default_factory=dict)
    # "2021" -> {"0": 1.0, "1": 0.83, ...} — 등록연도 코호트별 생존율(나이 t년차, life-table)

    def district_rank(self, sido_prefix: str, top_n: int | None = None) -> list[tuple[str, int]]:
        """sido_prefix(예: '서울특별시')로 시작하는 구만 뽑아 내림차순."""
        rows = [(k.split(" ", 1)[1], v) for k, v in self.by_sigungu.items()
                if k.startswith(sido_prefix) and " " in k]
        rows.sort(key=lambda kv: -kv[1])
        return rows[:top_n] if top_n else rows

    def regional_stats(self) -> list[dict]:
        """
        전국 시군구 단위 요약 — "지역 선택하면 시장 지표 보여달라" 기능(estimate.html)의
        원자료. 등록 밀도·증감률뿐이라 실제 임대수익 추정치가 아니다(요금·점유율
        데이터가 없다) — 호출부가 반드시 그렇게 표기해야 한다.
        """
        out = []
        for region, active in self.by_sigungu.items():
            sido, _, sigungu = region.partition(" ")
            months = sorted(self.by_sigungu_monthly.get(region, {}).items())
            recent = sum(c for _, c in months[-6:])
            prior = sum(c for _, c in months[-12:-6])
            growth = (recent - prior) / prior if prior else (float("inf") if recent else 0.0)
            out.append({"sido": sido, "sigungu": sigungu, "active": active,
                        "recent6": recent, "growth": growth})
        return out

    def sido_rank(self, top_n: int | None = None) -> list[tuple[str, int]]:
        """by_sigungu('시도 시군구' -> 영업중)를 시도 단위로 합산해 내림차순. 전국 17개 시도 커버."""
        totals: Counter[str] = Counter()
        for key, cnt in self.by_sigungu.items():
            sido = key.split(" ", 1)[0]
            totals[sido] += cnt
        rows = sorted(totals.items(), key=lambda kv: -kv[1])
        return rows[:top_n] if top_n else rows

    def recent_months(self, n: int = 24) -> list[tuple[str, int]]:
        return sorted(self.monthly_registrations.items())[-n:]

    @property
    def closure_rate(self) -> float:
        return self.closed / self.total if self.total else 0.0

    def saturation_signal(self, sido_prefix: str, recent_n: int = 6,
                           min_active: int = 20) -> list[tuple[str, int, int, float]]:
        """
        구별 '포화 신호' — 이미 밀도가 높은 구에서 최근 신규등록이 꺾이고 있는지를 본다.
        단순 밀도 순위(district_rank)만으로는 "지금 크다"만 보이고 "커지는 중인지 식는
        중인지"는 안 보인다. k-stay 공개 페이지에도 없는 지표라 이게 실질적인 차별 포인트.

        반환: [(구, 활성수, 최근N개월 신규등록, 직전N개월 대비 증감률), ...] 활성수 내림차순.
        증감률이 큰 음수면 "이미 크고 + 최근 유입이 주는" 포화 신호. 표본이 작은 구는
        비율이 요동치므로 min_active 미만은 제외.
        """
        active = dict(self.district_rank(sido_prefix))
        out = []
        for gu, monthly in self.by_sigungu_monthly.items():
            if not gu.startswith(sido_prefix) or " " not in gu:
                continue
            name = gu.split(" ", 1)[1]
            if active.get(name, 0) < min_active:
                continue
            months = sorted(monthly.items())
            recent = sum(c for _, c in months[-recent_n:])
            prior = sum(c for _, c in months[-2 * recent_n:-recent_n])
            growth = (recent - prior) / prior if prior else (float("inf") if recent else 0.0)
            out.append((name, active[name], recent, growth))
        return sorted(out, key=lambda t: -t[1])


def classify(status_name: str) -> str:
    if status_name in CLOSED_STATUSES:
        return "closed"
    if status_name in PAUSE_STATUSES:
        return "pause"
    return "active"


def parse_region(row: dict) -> tuple[str, str]:
    addr = (row.get("도로명주소") or row.get("지번주소") or "").strip()
    parts = addr.split()
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0] if parts else "", "")


def license_ym(row: dict) -> str | None:
    d = (row.get("인허가일자") or "").strip()
    return d[:7] if len(d) >= 7 and d[4] == "-" else None


def _parse_full_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def cohort_survival(deduped_rows: list[dict], min_cohort_size: int = 30,
                     max_years: int = 8, today: date | None = None) -> dict[str, dict[str, float]]:
    """
    등록연도 코호트별 생존곡선(life-table 방식) — "2년차까지 몇 %가 살아남았나".

    폐업 건만 보고 낸 존속기간(예: 중앙값 2.1년)은 아직 영업 중인 곳이 통째로
    빠진 생존편향 수치라 투자 판단에 못 쓴다. 여기서는 아직 안 닫힌 곳을
    우변절단(right-censoring)으로 반영한다 — 그 나이까지 살아있었다는 사실은
    쓰되, "언제 닫힐지"는 모른다고 취급해 분모에서만 카운트하고 분자(폐업)에는
    안 넣는다.

    duration은 연 단위로 내림(완주한 해)한다 — 정확한 날짜 단위 카플란-마이어보다
    거칠지만, 14,000건대 표본에 월간 리포트용 선그래프로는 이 정도 해상도면
    충분하고 계산이 훨씬 단순해진다. 표본이 min_cohort_size 미만인 코호트는
    계단이 튀므로 뺀다.
    """
    today = today or date.today()
    cohorts: dict[int, list[tuple[int, bool]]] = {}  # 등록연도 -> [(생존 연수, 폐업여부), ...]
    for r in deduped_rows:
        reg = _parse_full_date(r.get("인허가일자"))
        if not reg:
            continue
        is_closed = classify(r.get("영업상태명", "")) == "closed"
        end = _parse_full_date(r.get("폐업일자")) if is_closed else None
        end = end or today
        duration = max(0, int((end - reg).days / 365.25))
        cohorts.setdefault(reg.year, []).append((duration, is_closed))

    out: dict[str, dict[str, float]] = {}
    for year, obs in cohorts.items():
        if len(obs) < min_cohort_size:
            continue
        survival = 1.0
        curve = {"0": 1.0}
        for t in range(max_years):
            at_risk = sum(1 for d, _ in obs if d >= t)
            if at_risk == 0:
                break
            deaths = sum(1 for d, closed in obs if closed and d == t)
            survival *= 1 - deaths / at_risk
            curve[str(t + 1)] = round(survival, 4)
        out[str(year)] = curve
    return out


def download_csv(slug: str) -> str:
    session = requests.Session()
    info = INFO_URL.format(slug=slug)
    session.get(info, headers=UA, timeout=15)  # 쿠키 발급 — 이거 없이 바로 아래를 치면 403.
    r = session.get(DOWNLOAD_URL.format(slug=slug), headers={**UA, "Referer": info}, timeout=180)
    r.raise_for_status()
    return r.content.decode("cp949", errors="replace")


def parse_rows(csv_text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def dedup(rows: list[dict]) -> list[dict]:
    """
    같은 업체가 상태변경(등록→휴업→폐업 등)마다 새 row를 갖는다.
    (관리번호+주소) 복합키로 최종수정시점이 가장 늦은 것만 남긴다.
    """
    latest: dict[str, dict] = {}
    for r in rows:
        mgt = (r.get("관리번호") or "").strip()
        addr = (r.get("도로명주소") or "").strip() or (r.get("지번주소") or "").strip()
        if not addr:
            continue
        key = f"{mgt}|{addr}"
        ts = r.get("최종수정시점") or r.get("데이터갱신시점") or ""
        prev = latest.get(key)
        if prev is None or ts > (prev.get("최종수정시점") or prev.get("데이터갱신시점") or ""):
            latest[key] = r
    return list(latest.values())


def aggregate(slug: str, rows: list[dict]) -> CategoryStats:
    deduped = dedup(rows)
    buckets = Counter(classify(r.get("영업상태명", "")) for r in deduped)

    by_sigungu: Counter[str] = Counter()
    monthly: Counter[str] = Counter()
    by_sigungu_monthly: dict[str, Counter[str]] = {}
    for r in deduped:
        sido, sigungu = parse_region(r)
        region_key = f"{sido} {sigungu}" if sido and sigungu else None
        if classify(r.get("영업상태명", "")) == "active" and region_key:
            by_sigungu[region_key] += 1
        ym = license_ym(r)
        if ym:
            monthly[ym] += 1
            if region_key:
                by_sigungu_monthly.setdefault(region_key, Counter())[ym] += 1

    return CategoryStats(
        slug=slug, name_ko=CATEGORIES[slug],
        active=buckets["active"], closed=buckets["closed"], pause=buckets["pause"],
        total=len(deduped),
        by_sigungu=dict(by_sigungu),
        monthly_registrations=dict(monthly),
        by_sigungu_monthly={k: dict(v) for k, v in by_sigungu_monthly.items()},
        cohort_survival=cohort_survival(deduped),
    )


def collect(slugs: list[str] | None = None, verbose: bool = True) -> dict[str, CategoryStats]:
    out = {}
    for slug in (slugs or list(CATEGORIES)):
        if verbose:
            print(f"  [{CATEGORIES[slug]}] 다운로드·집계 중...")
        stats = aggregate(slug, parse_rows(download_csv(slug)))
        if verbose:
            print(f"    영업중 {stats.active:,} · 폐업 {stats.closed:,} · "
                  f"휴업 {stats.pause:,} · 누적 {stats.total:,}")
        out[slug] = stats
    return out


if __name__ == "__main__":
    stats = collect(slugs=[FLAGSHIP])
    s = stats[FLAGSHIP]
    print(f"\n{s.name_ko}")
    print(f"  영업중 {s.active:,} 폐업률 {s.closure_rate:.1%}")
    print("\n  서울 TOP 10")
    for gu, cnt in s.district_rank("서울특별시", 10):
        print(f"    {gu:8} {cnt:,}")
    print("\n  최근 6개월 신규등록")
    for ym, cnt in s.recent_months(6):
        print(f"    {ym}  {cnt}")
