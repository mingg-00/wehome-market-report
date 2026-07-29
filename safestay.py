#!/usr/bin/env python3
"""
세이프스테이(한국관광공사) 민박업 통계 수집.
https://safestay.visitkorea.or.kr — 로그인·인증키 없이 공개된 유일한 공식 민박업 통계다.

이 페이지 HTML에는 순진하게 파싱하면 지역을 틀리게 붙이는 함정이 셋 있다:

1. <th>광주</th> 와 <th>전남</th> 이 HTML 주석으로 막혀 있고, 대신 맨 끝에
   '전남광주' 병합 컬럼이 있다. 주석을 먼저 지우지 않으면 헤더가 18개로 읽히는데
   데이터는 16개라, 대전 값이 광주로 밀려 붙는다. -> _strip_comments() 가 선행 조건.
2. 지역별 표(areaPeriod/areaType)의 숫자는 '운영 중'이 아니라 폐업·취소까지 포함한
   누적 등록 수다. 운영 중 수치는 typePeriod 표에만 있다. 둘을 섞으면 시장 규모가
   1.3배 부풀려진다. -> AreaStat.is_cumulative 로 표시해 둔다.
3. 제주 외국인관광도시민박업이 0으로 나온다. 제주특별자치도가 별도 관리해서
   미집계인 것으로 보이며 실제 0은 아니다. -> JEJU_NOT_REPORTED 로 경고.

세 뷰 모두 쿼리 파라미터 없이 최신 12개월을 통째로 내려주므로 GET 3번이면 끝난다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import requests

BASE = "https://safestay.visitkorea.or.kr/usr/stat"
VIEWS = {
    "area_period": f"{BASE}/areaPeriodSelectList.kto?currentMenuSn=102",  # 지역 x 월
    "type_period": f"{BASE}/typePeriodSelectList.kto?currentMenuSn=103",  # 업종 x 영업상태 x 월
    "area_type": f"{BASE}/areaTypeSelectList.kto?currentMenuSn=104",      # 지역 x 업종
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# 주석 제거 후 실제로 남는 16개 지역. '전남광주'는 전남+광주 병합 컬럼이라 분리 불가.
REGIONS = ["서울", "부산", "대구", "인천", "대전", "울산", "세종", "경기",
           "강원", "충북", "충남", "전북", "경북", "경남", "제주", "전남광주"]
MERGED_REGION = "전남광주"
JEJU_NOT_REPORTED = "제주"

LODGE_TYPES = ["외국인관광도시민박업", "한옥체험업", "관광펜션업", "호스텔업"]
STATUSES = ["운영", "휴업", "폐업", "취소 등"]


@dataclass
class Snapshot:
    """세이프스테이 1회 수집분."""
    area_period: dict[str, dict[str, int]] = field(default_factory=dict)   # {"2026.07": {"서울": 9689, ...}}
    area_type: dict[str, dict[str, int]] = field(default_factory=dict)     # {"외국인관광도시민박업": {"서울": 9689, ...}}
    type_period: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    # type_period: {"2026.07": {"외국인관광도시민박업": {"운영": 11957, "폐업": 3206, ...}}}

    @property
    def months(self) -> list[str]:
        """최신순으로 정렬된 년월 목록."""
        return sorted(self.type_period, reverse=True)

    def operating(self, month: str, lodge_type: str = "외국인관광도시민박업") -> int:
        """해당 월 '운영 중' 업소 수. 지역별 표의 누적치와 혼동하지 말 것."""
        return self.type_period[month][lodge_type]["운영"]

    def cumulative(self, month: str, lodge_type: str = "외국인관광도시민박업") -> int:
        """해당 월 누적 등록 수 (운영+휴업+폐업+취소). 지역별 표와 대조되는 값."""
        return sum(self.type_period[month][lodge_type].values())


def _strip_comments(s: str) -> str:
    """<!-- --> 제거. 헤더 정렬의 전제 조건이라 파싱 전 반드시 먼저 호출한다."""
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def _cells(row: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", row, re.S)]


def _rows(page: str, table_index: int = 0) -> list[list[str]]:
    tables = re.findall(r"<table.*?</table>", page, re.S)
    if not tables:
        raise ValueError("표를 찾지 못했다 — 페이지 구조가 바뀌었을 수 있다")
    return [_cells(r) for r in re.findall(r"<tr.*?</tr>", tables[table_index], re.S)]


def _num(s: str) -> int:
    return int(s.replace(",", "").strip() or 0)


def fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return _strip_comments(resp.text)


def parse_area_period(page: str) -> dict[str, dict[str, int]]:
    """지역 x 월 누적 등록 수 (외국인관광도시민박업)."""
    rows = _rows(page)
    header, body = rows[0], rows[1:]
    regions = header[1:]
    _assert_regions(regions, "area_period")
    return {r[0]: dict(zip(regions, map(_num, r[1:]))) for r in body if len(r) == len(header)}


def parse_area_type(page: str) -> dict[str, dict[str, int]]:
    """업종 x 지역 누적 등록 수."""
    rows = _rows(page)
    header, body = rows[0], rows[1:]
    regions = header[1:]
    _assert_regions(regions, "area_type")
    return {r[0]: dict(zip(regions, map(_num, r[1:]))) for r in body if len(r) == len(header)}


def parse_type_period(page: str) -> dict[str, dict[str, dict[str, int]]]:
    """월 x 업종 x 영업상태. 헤더가 2행(업종행 + 상태행)으로 쪼개져 있다."""
    rows = _rows(page)
    types, statuses, body = rows[0][1:], rows[1], rows[2:]
    if types != LODGE_TYPES:
        raise ValueError(f"업종 헤더가 예상과 다르다: {types}")
    if len(statuses) != len(types) * len(STATUSES):
        raise ValueError(f"상태 헤더 개수가 맞지 않는다: {len(statuses)}")

    out: dict[str, dict[str, dict[str, int]]] = {}
    for row in body:
        if len(row) != 1 + len(statuses):
            continue
        values = list(map(_num, row[1:]))
        out[row[0]] = {
            t: dict(zip(STATUSES, values[i * len(STATUSES):(i + 1) * len(STATUSES)]))
            for i, t in enumerate(types)
        }
    return out


def _assert_regions(regions: list[str], view: str) -> None:
    """주석 처리된 <th> 때문에 열이 밀리면 여기서 잡힌다. 조용히 틀리는 것보다 낫다."""
    if regions != REGIONS:
        raise ValueError(
            f"[{view}] 지역 헤더가 예상과 다르다.\n  기대: {REGIONS}\n  실제: {regions}\n"
            "  세이프스테이가 주석 처리된 <th>(광주/전남)를 되살렸을 수 있다. REGIONS를 갱신할 것."
        )


def collect() -> Snapshot:
    """세 뷰를 모두 받아 Snapshot으로 반환."""
    snap = Snapshot(
        area_period=parse_area_period(fetch(VIEWS["area_period"])),
        area_type=parse_area_type(fetch(VIEWS["area_type"])),
        type_period=parse_type_period(fetch(VIEWS["type_period"])),
    )
    _cross_check(snap)
    return snap


def _cross_check(snap: Snapshot) -> list[str]:
    """
    지역별 표 합계와 업종별 표 누적치를 대조한다. 두 표는 집계 시점이 달라 소폭
    어긋나지만, 크게 벌어지면 파싱이 밀렸다는 신호다. 경고만 하고 막지는 않는다.
    """
    warnings = []
    month = snap.months[0]
    if month in snap.area_period:
        area_sum = sum(snap.area_period[month].values())
        cum = snap.cumulative(month)
        if cum and abs(area_sum - cum) / cum > 0.02:
            warnings.append(f"⚠️ {month} 지역합({area_sum:,}) vs 업종누적({cum:,}) 2% 초과 괴리 — 파싱 확인 필요")
    if snap.area_type.get("외국인관광도시민박업", {}).get(JEJU_NOT_REPORTED) == 0:
        warnings.append("⚠️ 제주 외도민 0건 — 제주특별자치도 미집계로 추정. '0'으로 보도하지 말 것")
    for w in warnings:
        print(w)
    return warnings


if __name__ == "__main__":
    s = collect()
    m = s.months[0]
    print(f"\n기준월 {m}")
    print(f"  외도민 운영중   {s.operating(m):,}")
    print(f"  외도민 누적등록 {s.cumulative(m):,}")
    print(f"  서울 누적       {s.area_period[m]['서울']:,}")
    print(f"  수집 월수       {len(s.months)}")
