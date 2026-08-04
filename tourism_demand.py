#!/usr/bin/env python3
"""
한국관광 데이터랩(KTO) 메인 대시보드 KPI 번들 클라이언트.

datalab.visitkorea.or.kr 메인 화면 카드들이 프론트에서 그대로 호출하는 공개
POST 엔드포인트다 — qid=MN_01_01_026 하나로 26개 지표(방한여행·의료·한류·
크루즈·국내여행·지역여행 6개 테마)를 한 번에 반환한다. 로그인·쿠키·세션 전혀
불필요(curl 직접 확인, 브라우저 개발자도구 없이도 재현됨).

원래 이 사이트는 뉴스 아카이브용으로 조사했을 때 JS SPA라 드랍했었다(sources.py
DROPPED 참고) — 메뉴 wrapper만 정적으로 내려오고 게시글 목록은 클라이언트 렌더라
'기사'로는 못 썼다. 이번엔 '기사'가 아니라 '통계'를 찾다가, 메인 화면 JS
(main_20260427.js의 chart_init_28)를 읽어서 이 엔드포인트를 다시 발견했다.

26개 지표 중 공유숙박 리포트와 직접 관련된 5개만 골라 쓴다. 나머지 21개
(의료관광·한류관광·크루즈·지역여행 소비 등)는 이 리포트 성격과 안 맞아 버린다.

응답의 MAX_BASE_YM이 실제 발행된 최신월을 알려준다 — 요청 시점의 달을 넘겨도
서버가 알아서 최신 확정치로 돌려준다(2026-07-29 확인 시점 기준 2026.06, 약
1개월 지연 — 월간 통계로는 가장 빠른 수준).
"""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv()  # build_site.py 를 거치지 않고 `python3 tourism_demand.py` 단독 실행해도 TOUR_API_KEY 를 읽게

URL = "https://datalab.visitkorea.or.kr/visualize/getTempleteData.do"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# ───────────────────────────── 한국관광공사 TourAPI(공공데이터포털) 지역방문자수
#
# 위 datalab.visitkorea.or.kr 엔드포인트는 전국 합계 5개 지표만 준다 — 시도·시군구
# 단위로 쪼개서 볼 수가 없다. TourAPI DataLabService는 이동통신 기반 방문자수를
# 시도(metcoRegnVisitrDDList)·시군구(locgoRegnVisitrDDList) 단위 일자별로 제공한다
# (관광객구분: 현지인=거주자, 외지인=국내 타지역 방문객, 외국인).
#
# 실측 결과 발행 지연이 약 30일이다(2026-08-04 확인 시점 기준 최근 29일은 전부
# totalCount=0, 30일 전부터 데이터가 잡힘) — 최신 데이터를 원하면 startYmd/endYmd를
# 오늘 기준 최소 한 달 전으로 잡아야 한다.
TOUR_API_URL = "https://apis.data.go.kr/B551011/DataLabService"
TOU_DIV = {"1": "현지인", "2": "외지인", "3": "외국인"}

# locgoRegnVisitrDDList의 signguCode(법정동코드 5자리) 앞 2자리 -> 시도명. 이 API는 시군구
# 항목에 시도명을 안 준다(signguNm만 옴, "중구"/"동구" 등 동명 지역이 시도 여럿에 존재) —
# 실측(2026-06-05~2026-07-05, 7개 표본일)으로 코드→이름을 역산해 만들었다. localdata.py가
# 주소 파싱으로 만드는 시도명 표기와 그대로 맞춘다(build_site.py의 SIDO_TO_KR_CODE와 동일 표기).
#
# 2026-07-01부터 광주+전남이 "12"(전남광주통합특별시) 코드로 합쳐졌다(그 이전엔 광주=29,
# 전남=46 각각). build_site.py가 이미 이 통합 표기를 다루고 있어(SIDO_TO_KR_CODE 주석 참고)
# 여기도 세 코드를 전부 매핑해둔다 — 과거 날짜를 조회해도 안 깨지게.
SIGUNGU_SIDO_PREFIX = {
    "11": "서울특별시", "12": "전남광주통합특별시", "26": "부산광역시", "27": "대구광역시",
    "28": "인천광역시", "29": "광주광역시", "30": "대전광역시", "31": "울산광역시",
    "36": "세종특별자치시", "41": "경기도", "43": "충청북도", "44": "충청남도",
    "46": "전라남도", "47": "경상북도", "48": "경상남도", "50": "제주특별자치도",
    "51": "강원특별자치도", "52": "전북특별자치도",
}


def _sido_of(signgu_code: str) -> str:
    prefix = signgu_code[:2]
    sido = SIGUNGU_SIDO_PREFIX.get(prefix)
    if sido is None:
        print(f"  ⚠️ signguCode 시도 매핑 없음: {signgu_code} — SIGUNGU_SIDO_PREFIX 갱신 필요")
        return f"미상({prefix})"
    return sido


def tourapi_configured() -> bool:
    return bool(os.getenv("TOUR_API_KEY"))


def _tourapi_items(operation: str, start_ymd: str, end_ymd: str) -> list[dict]:
    """operation(metcoRegnVisitrDDList|locgoRegnVisitrDDList)의 전체 페이지를 모아 item 리스트로 반환."""
    items: list[dict] = []
    page = 1
    # ponytail: 30페이지(numOfRows=1000 기준 3만행, 시군구×전체구분 기준 약 40일치) 넘게
    # 걸리면 멈춘다 — 실측 중 totalCount가 비정상적으로 커져 페이지가 안 끝나는 경우를
    # 봤다(개발계정 일 1,000건 트래픽 소진 직후로 추정). 더 넓은 범위가 필요하면 상향.
    while page <= 30:
        r = requests.get(f"{TOUR_API_URL}/{operation}", params={
            "serviceKey": os.environ["TOUR_API_KEY"], "MobileOS": "ETC",
            "MobileApp": "WehomeMarketReport", "_type": "json",
            "numOfRows": 1000, "pageNo": page,
            "startYmd": start_ymd, "endYmd": end_ymd,
        }, timeout=15)
        r.raise_for_status()
        body = r.json()["response"]["body"]
        node = body.get("items") or {}
        batch = node.get("item", []) if isinstance(node, dict) else []
        items += batch if isinstance(batch, list) else [batch]  # 결과 1건이면 item이 dict로 옴
        if page * body["numOfRows"] >= body["totalCount"]:
            return items
        page += 1
    raise RuntimeError(f"{operation} 페이지네이션이 30페이지를 넘었다 — 트래픽 소진/API 이상 의심")


def _aggregate_visitors(items: list[dict], key_fn) -> dict[str, dict]:
    """item들을 key_fn(item) 기준으로 관광객구분별 합산 — {지역키: {현지인, 외지인, 외국인, total}}."""
    out: dict[str, dict] = {}
    for it in items:
        row = out.setdefault(key_fn(it), {"현지인": 0.0, "외지인": 0.0, "외국인": 0.0})
        row[TOU_DIV[it["touDivCd"]]] += float(it["touNum"])
    for row in out.values():
        row["total"] = row["현지인"] + row["외지인"] + row["외국인"]
    return out


def collect_province_visitors(start_ymd: str, end_ymd: str) -> dict[str, dict]:
    """시도별 방문자수 합산(YYYYMMDD~YYYYMMDD). TOUR_API_KEY 없으면 dry-run으로 {} 반환."""
    if not tourapi_configured():
        print("  📭 [DRY-RUN] TOUR_API_KEY 미설정 — 시도별 방문자수 수집 생략")
        return {}
    items = _tourapi_items("metcoRegnVisitrDDList", start_ymd, end_ymd)
    return _aggregate_visitors(items, lambda it: it["areaNm"])


def collect_district_visitors(start_ymd: str, end_ymd: str) -> dict[str, dict]:
    """
    시군구별 방문자수 합산(YYYYMMDD~YYYYMMDD). 키는 localdata.py와 같은 "시도 시군구"
    형식(예: "서울특별시 마포구") — signguCode로 시도를 역산해 붙인다(SIGUNGU_SIDO_PREFIX
    참고, API 응답 자체엔 시도명이 없어 동명 시군구를 구분 못 하기 때문). TOUR_API_KEY
    없으면 dry-run으로 {} 반환.
    """
    if not tourapi_configured():
        print("  📭 [DRY-RUN] TOUR_API_KEY 미설정 — 시군구별 방문자수 수집 생략")
        return {}
    items = _tourapi_items("locgoRegnVisitrDDList", start_ymd, end_ymd)
    return _aggregate_visitors(items, lambda it: f"{_sido_of(it['signguCode'])} {it['signguNm']}")

# DIV_NM -> (표시 이름, 단위: count=명, won=원)
METRICS = {
    "54": ("숙박 여행객수", "count"),
    "12": ("방한 외래객수", "count"),
    "13": ("외국인 관광소비", "won"),
    "52": ("내국인 여행객수", "count"),
    "53": ("내국인 관광소비", "won"),
}


def fmt_count(v: float) -> str:
    """179599761 -> '1억 7,960만명' — 데이터랩 자체 표시 포맷과 동일(반올림 검증됨)."""
    v = round(v)
    eok, rem = divmod(v, 100_000_000)
    man = round(rem / 10_000)
    if eok:
        return f"{eok}억 {man:,}만명" if man else f"{eok}억명"
    if man:
        return f"{man:,}만명"
    return f"{v:,}명"


def fmt_won(v: float) -> str:
    """10038890733106 -> '10조 389억원' — 데이터랩 자체 표시 포맷과 동일(반올림 검증됨)."""
    v = round(v)
    jo, rem = divmod(v, 1_000_000_000_000)
    eok = round(rem / 100_000_000)
    if jo:
        return f"{jo}조 {eok:,}억원" if eok else f"{jo}조원"
    if eok:
        return f"{eok:,}억원"
    return f"{v:,}원"


def collect() -> dict[str, dict]:
    """METRICS에 등록된 5개 지표만 뽑아 {DIV_NM: {name, value, display, rate, ym}} 로 반환."""
    r = requests.post(URL, headers=UA, data={"qid": "MN_01_01_026"}, timeout=15)
    r.raise_for_status()
    out = {}
    for item in r.json().get("list", []):
        div = item.get("DIV_NM")
        if div not in METRICS:
            continue
        name, unit = METRICS[div]
        fmt = fmt_won if unit == "won" else fmt_count
        out[div] = {
            "name": name,
            "value": item["VALUE"],
            "display": fmt(item["VALUE"]),
            "rate": item["RATE"],
            "ym": item["MAX_BASE_YM"],
        }
    return out


if __name__ == "__main__":
    for div, m in collect().items():
        print(f"  {m['name']:10} {m['display']:>16}  ({m['rate']:+.1f}% · {m['ym']} 연간누적)")

    if tourapi_configured():
        from datetime import date, timedelta
        ymd = (date.today() - timedelta(days=30)).strftime("%Y%m%d")  # 발행 지연 약 30일(위 주석 참고)
        print(f"\n  시도별 방문자수 상위 5 ({ymd})")
        provinces = collect_province_visitors(ymd, ymd)
        for area, v in sorted(provinces.items(), key=lambda kv: -kv[1]["total"])[:5]:
            print(f"    {area:10} {v['total']:>12,.0f}명")
