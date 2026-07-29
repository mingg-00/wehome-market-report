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

import requests

URL = "https://datalab.visitkorea.or.kr/visualize/getTempleteData.do"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

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
