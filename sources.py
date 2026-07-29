#!/usr/bin/env python3
"""
마켓리포트 소스 레지스트리 — 호스트레터에서 물려받은 소스를 전수 재감사한 결과.

재설계의 핵심 원칙 하나:

  수치는 크롤링하지 않는다. API로 받는다.

호스트레터는 기사에서 수치를 주워 썼지만, 마켓리포트는 k-stay API와 세이프스테이가
정량을 전담한다(kstay.py / safestay.py). 그래서 크롤러의 역할이 '뉴스 수집'에서
**'규제·정책 변화 감지'**로 좁아진다. 아래 티어는 그 기준으로 다시 짠 것이다.

호스트레터에서 잘라낸 소스와 사유는 DROPPED 에 남겨 뒀다. 나중에 "왜 뺐지?" 하고
다시 붙이는 걸 막기 위한 기록이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    tier: int              # 1=규제·정책(최우선) 2=산업미디어 3=수요·경쟁
    kind: str              # rss | html | api
    why: str               # 마켓리포트에 왜 필요한가
    keywords: list[str] = field(default_factory=list)
    max_items: int = 10
    verify_ssl: bool = True
    user_agent: str | None = None


# 리포트 본문에 쓸 기사를 거르는 키워드. 호스트레터의 '호스팅 팁' 계열 단어를 빼고
# 제도·시장 단어로 갈아끼웠다.
CORE_KEYWORDS = [
    "공유숙박", "도시민박", "외국인관광 도시민박", "외도민",
    "관광진흥법", "생활숙박", "생숙", "숙박업", "민박",
    "에어비앤비", "단기임대", "빈집", "규제샌드박스", "실증특례",
    "관광객", "인바운드", "방한", "숙박세일", "관광정책",
]

# "방한"(訪韓)은 "관광객 방한"과 "대통령 국빈 방한"에 똑같이 걸리는 동형이의어다.
# 단순 부분일치로 두면 외교 기사가 오탐으로 섞여 들어온다 — 2026-07-29 실측:
# "한국과브라질 중소기업 분야 협력..." 기사가 본문의 "국빈 방한"에 걸려
# 규제·정책 동향에 잘못 들어왔다. 이 키워드가 나온 지점 근처(±15자)에 아래 문맥
# 단어가 하나라도 있어야 진짜 매치로 인정한다(Item.matches_keywords 참고).
AMBIGUOUS_KEYWORDS: dict[str, list[str]] = {
    "방한": ["관광", "여행", "외국인", "인바운드"],
}


# ─────────────────────────────────────────────────────────────
# Tier 1 — 규제·정책 1차 출처.
# 마켓리포트가 호스트레터와 갈리는 지점이 여기다. 내국인도시민박업처럼 시장 구조를
# 바꾸는 변화는 기사로 나오기 전에 입법예고·의안 단계에서 먼저 보인다.
# 기사를 좇으면 항상 한 박자 늦는다.
# ─────────────────────────────────────────────────────────────
TIER1_REGULATION = [
    Source("문체부 보도자료", "https://www.mcst.go.kr/site/s_notice/press/pressList.jsp",
           1, "html", "외도민업 주무부처. 제도 발표가 여기서 먼저 난다. "
                      "(kor/ 경로는 site/ 로 302 리다이렉트 — 최종 URL을 바로 씀)"),
    Source("문체부 입법·행정예고", "https://www.mcst.go.kr/site/s_notice/notice/noticeList.jsp",
           1, "html", "관광진흥법 시행령 개정안이 올라오는 곳. 내국인도시민박업 추적 지점."),
    Source("국회 입법예고(관광진흥법 추적)", "https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/list.do",
           1, "api", "billName= 쿼리로 관광진흥법 계류 법안을 추적. 정부안보다 먼저 뜨는 경우가 있다. "
                     "2026-07-28 검증: 의안 2219898 '관광진흥법 일부개정법률안(김성원의원 등 10인)' "
                     "계류 확인(문화체육관광위원회)."),
    Source("정책브리핑", "https://www.korea.kr/briefing/pressReleaseList.do", 1, "html",
           "부처 통합 발표 채널. 국토부·문체부 등 전 부처 보도자료가 여기 다 모인다 — "
           "국토부 자체 사이트가 WAF로 막혀 있어(아래 DROPPED) 이 경로로 대신 커버."),
]

# ─────────────────────────────────────────────────────────────
# Tier 2 — 산업 미디어. 제도의 '해석'과 업계 반응이 여기서 나온다.
# 대한숙박업중앙회를 넣은 건 반대 진영 동향을 빼면 규제 전망이 한쪽으로 기울기 때문.
# ─────────────────────────────────────────────────────────────
TIER2_INDUSTRY = [
    Source("호텔앤레스토랑", "https://www.hotelrestaurant.co.kr/rss/allArticle.xml",
           2, "rss", "제도 심층 해설이 가장 빠르고 깊다. 내국인도시민박업 가이드라인 단독도 여기.",
           keywords=["숙박", "민박", "관광", "호텔", "규제", "생활숙박"]),
    Source("숙박매거진", "https://www.sukbakmagazine.com/news/articleList.html?view_type=sm",
           2, "html", "업계 이해관계자 시각. 공유숙박 반대 논리의 1차 소스.",
           keywords=["공유숙박", "민박", "숙박업", "규제"]),
    Source("여행신문", "https://www.traveltimes.co.kr/rss/allArticle.xml",
           2, "rss", "인바운드 통계 보도가 가장 정확하고 빠르다.",
           keywords=["관광객", "인바운드", "방한", "숙박", "관광"]),
    Source("히치하이커", "https://hitchhickr.substack.com/feed",
           2, "rss", "숙박시장 구조 분석. 단기임대·장기체류 앵글이 마켓리포트와 맞는다.",
           keywords=["숙박", "단기임대", "여행", "플랫폼"], max_items=5),
    Source("대한숙박업중앙회", "https://www.motel.or.kr/73", 2, "html",
           "반대 진영. 규제 전망을 한쪽으로만 보지 않기 위해 넣는다. "
           "(홈 URL은 메뉴만 있고 실제 소식 게시판은 /73 — 2026-07-28 재조사로 교체, news.py 구현됨)"),
    Source("에어비앤비 뉴스룸 KR", "https://news.airbnb.com/ko/", 2, "html",
           "지배적 플랫폼의 공식 입장·로비 방향. 이해관계자 문서로 읽을 것."),
    Source("아고다 뉴스룸 KR", "https://www.agoda.com/press/ko-kr/newsroom/", 2, "html",
           "경쟁 OTA 공식 발표. 에어비앤비와 같은 이해관계자 문서 취급."),
    Source("부킹닷컴 뉴스룸 KR", "https://news.booking.com/ko-ko/", 2, "html",
           "경쟁 OTA 공식 발표. 국내 여행지 트렌드 보도자료가 잦다."),
    Source("클룩 뉴스룸", "https://www.klook.com/newsroom/", 2, "html",
           "액티비티·티켓 OTA 공식 발표. 한국어판이 없어 영문 그대로 노출됨."),
]

# ─────────────────────────────────────────────────────────────
# Tier 3 — 수요·경쟁. 시장 규모와 수요 서사에 쓴다.
# ─────────────────────────────────────────────────────────────
TIER3_DEMAND = [
    Source("야놀자리서치", "https://www.yanolja-research.com/trend/list?lang=kr",
           3, "html", "분기 숙박업 동향 보고서. 국내 유일 정기 산업 리포트."),
    Source("온다 뉴스레터", "https://www.onda.me/newsletter", 3, "html",
           "OSI 지수 + 실거래 기반. 국내 온라인 객실거래 60~70% 커버."),
    Source("서울관광재단", "https://www.sto.or.kr/press", 3, "html",
           "서울 집중 리포트라 필수. 행사·축제가 수요 스파이크를 만든다. "
           "(구 URL pressReleaseList.do 는 404 — 2026-07-28 재조사로 교체, news.py 구현됨)"),
    Source("부산관광공사", "https://bto.or.kr/kor/CMS/Board/Board.do?mCode=MN047", 3, "html",
           "서울 다음 시장. 샌드박스 특례 지역. "
           "(구 mCode=MN036 은 관광지 소개 페이지였음 — 올바른 보도자료 게시판 mCode=MN047로 교체, news.py 구현됨)"),
    Source("한국경제 부동산", "https://www.hankyung.com/feed/realestate", 3, "rss",
           "생숙·빈집·단기임대. 공급 측 기사.",
           keywords=["숙박", "생활숙박", "빈집", "단기임대", "공유숙박"],
           user_agent="curl/8.16.0"),   # Chrome UA는 403. curl UA는 통과.
    Source("매일경제 부동산", "https://www.mk.co.kr/rss/50300009/", 3, "rss",
           "위와 동일 목적. 중복은 dedup이 걸러낸다.",
           keywords=["숙박", "생활숙박", "빈집", "단기임대", "공유숙박"]),
    Source("네이버 뉴스 API", "https://openapi.naver.com/v1/search/news.json", 3, "api",
           "키워드 전수 탐색. 위 소스가 놓친 지역지·전문지를 줍는다.",
           keywords=CORE_KEYWORDS),
]

SOURCES = TIER1_REGULATION + TIER2_INDUSTRY + TIER3_DEMAND


# ─────────────────────────────────────────────────────────────
# 잘라낸 소스. 사유를 남기지 않으면 반년 뒤에 누가 또 붙인다.
# 2026-07-28 전수 프로브 결과 기준.
# ─────────────────────────────────────────────────────────────
DROPPED = {
    "캐릿": "MZ 마케팅 트렌드. 숙박 키워드 적중 0/8. 호스트레터 톤용이지 시장 분석과 무관.",
    "뉴닉": "일반 시사 요약. 숙박·관광 비중이 너무 낮다.",
    "오픈애즈": "광고·마케팅 업계. 마켓리포트 독자(예비 호스트·투자자)와 안 맞는다.",
    "리테일온": "ConnectionError로 사이트 자체가 응답 없음. 내용도 유통 노하우라 무관.",
    "Trend A Word": "유행어 수집. 뉴스레터 문체용이지 리포트에 쓸 데가 없다.",
    "tourgo": "HTTP 200이지만 본문 0KB. 사실상 죽은 소스.",
    "동아일보 여행": "소비자 여행기사 위주. 제도·시장 기사 밀도가 낮아 노이즈만 는다.",
    "트래블바이크뉴스": "RSS는 살아있으나(item=50) 서버가 중간 인증서를 안 보내 SSL 검증 실패. "
                  "내용도 소비자 여행이라 검증을 완화하면서까지 넣을 값어치가 없다.",
    "제주관광공사": "제주는 외도민 통계 자체가 미집계(특별자치도 별도 관리)라 "
               "리포트에서 수치를 못 쓴다. 제주 편입 시 재검토.",
    "국세청/부가세 환급": "단발성 공고. 정책브리핑에서 걸린다.",
    "한국관광 데이터랩": "2026-07-28 재조사. 로그인 게이트나 URL 문제가 아니라 사이트 자체가 "
                    "JS로 콘텐츠를 채우는 SPA — 정적 fetch로는 메뉴 wrapper만 내려오고 "
                    "실제 게시글 리스트엔 도달 불가. 애초에 '기사'가 아니라 통계 대시보드라 "
                    "뉴스 아카이브 성격과도 안 맞았다. Selenium/Playwright급 렌더링이 필요해지면 재검토.",
    # 2026-07-28 Tier1 구현 중 실측으로 추가 확인.
    "법제처 통합 입법예고": "opinion.lawmaking.go.kr, lawmaking.go.kr 양쪽 다 시도. "
                    "포털 홈(gcom/ogLmPp)은 메뉴 껍데기뿐이고 실제 목록 URL을 "
                    "구글링·파라미터 추정 다 동원해도 못 찾음(legacy jQuery 폼, 404). "
                    "문체부 입법예고 + 국회 입법예고로 이미 커버되는 범위라 여기서 접는다.",
    "국회 의안정보시스템(likms)": "포털 개편으로 mainPage.do가 검색결과 없는 빈 템플릿만 반환, "
                          "신규 검색엔드포인트(portal/signl/search)도 에러 페이지로 리다이렉트. "
                          "국회 입법예고(pal.assembly.go.kr)의 billName= 검색이 "
                          "'계류 법안 추적'이라는 같은 역할을 이미 하고 있어 중복 제거.",
    "국토교통부 보도자료(직접)": "molit.go.kr 는 F5 BIG-IP WAF가 모든 요청을 307로 되돌린다 "
                        "(쿠키 발급 후 재시도해도 404). 정책브리핑이 국토부 보도자료를 "
                        "통합해서 걸어주므로 그쪽으로 우회.",
}

# 수치를 담당하는 곳. 크롤링 대상이 아니라는 걸 명시해 두려고 여기 적는다.
QUANTITATIVE = {
    "k-stay API": "https://k-stay.ai/api/  (인증 불필요, CORS 허용, OpenAPI 3.0). "
                  "서울 25개 구·부산 14개 구 단위, 24개월 추이, 5종 카테고리, 인바운드. "
                  "행안부 인허가 데이터 매일 04:00 KST 갱신, 원본 CSV 직접 대조로 실데이터 확인함. "
                  "→ kstay.py. 단 k-stay.ai/analysis 페이지의 'Airbnb 미등록률'·'가격 샘플'은 "
                  "하드코딩 난수로 만든 가짜(사이트 자체 면책 문구 있음) — 절대 인용 금지, "
                  "kstay.py는 그 엔드포인트를 애초에 안 씀.",
    "세이프스테이": "https://safestay.visitkorea.or.kr  한국관광공사. "
               "k-stay와 독립된 계통이라 교차검증용으로 값어치가 있다. → safestay.py",
}


def by_tier(tier: int) -> list[Source]:
    return [s for s in SOURCES if s.tier == tier]


def summary() -> str:
    lines = [f"소스 {len(SOURCES)}개 (T1 규제 {len(by_tier(1))} / "
             f"T2 산업 {len(by_tier(2))} / T3 수요 {len(by_tier(3))}), 제외 {len(DROPPED)}개"]
    for t, label in ((1, "규제·정책"), (2, "산업 미디어"), (3, "수요·경쟁")):
        lines.append(f"\n[Tier {t}] {label}")
        for s in by_tier(t):
            lines.append(f"  {s.kind:4} {s.name:<18} {s.why}")
    lines.append("\n[제외]")
    for n, why in DROPPED.items():
        lines.append(f"  ✕ {n:<16} {why}")
    lines.append("\n[정량 — 크롤링 아님]")
    for n, why in QUANTITATIVE.items():
        lines.append(f"  ◆ {n}: {why}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
