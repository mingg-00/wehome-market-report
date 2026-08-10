#!/usr/bin/env python3
"""
Tier 2(산업 미디어) · Tier 3(수요·경쟁) 수집기.

regulation.py 가 정부·국회(Tier 1)를 담당한다면, 이쪽은 sources.py 에 이름만
등록돼 있던 나머지 소스들이다 — 지금까지는 등록만 해두고 실제로 긁어온 적이
없었다("왜 기사 볼 곳이 없냐"는 질문에 대한 직접적인 답).

18개 후보 중 실측으로 13개 구현(RSS 5 + HTML 7 + 네이버뉴스 API 1, 단 API는
크레덴셜 없이는 dry-run만 가능). 나머지는 왜 뺐는지 DROPPED 에 남긴다 —
Tier1 때처럼, 사유 없이 빼면 나중에 또 붙이려는 시도가 반복된다.

2026-07-28 재조사로 세 곳을 복구했다:
  - 대한숙박업중앙회: 처음엔 "로그인 게이트"로 오판했다. 실제로는 홈페이지
    최상단 600자만 보고 판단한 실수 — 실제 게시판은 imweb CMS 구조라
    <li class="list_text_title">(제목)과 <li class="time">(날짜)가 각각
    별도 형제 <li>로 흩어져 있어서 자동 스크래퍼가 못 찾았을 뿐, 콘텐츠
    자체는 정적으로 다 내려온다.
  - 부산관광공사: 이전에 조사했던 mCode=MN036 은 "황령산 전망쉼터"라는
    관광지 소개 페이지였다(엉뚱한 mCode). 올바른 보도자료 게시판은
    mCode=MN047(알림마당>보도자료).
  - 서울관광재단은 별도 커밋에서 이미 복구(구 URL pressReleaseList.do 404).

반면 한국관광 데이터랩은 재조사 후에도 드랍 유지 — 이건 판단 실수가 아니라
사이트 자체가 JS로 콘텐츠를 채우는 SPA라 정적 fetch로 게시글 목록에 아예
도달할 수 없다(페이지 HTML엔 메뉴 wrapper만 있고 실제 리스트는 클라이언트
사이드 렌더). 애초에 "뉴스"가 아니라 통계 대시보드라 성격도 안 맞았다.

RSS 5개는 필드 포맷이 소스마다 미묘하게 다르다(title이 CDATA로 감싸져 있거나
아니거나, pubDate가 "YYYY-MM-DD HH:MM:SS"거나 RFC822거나) — 그 편차를
parse_rss() 하나가 흡수해서, 소스 4곳(호텔앤레스토랑·여행신문·한국경제·매일경제)
은 URL만 바꾼 동일 호출로 끝난다. 히치하이커만 title도 CDATA라 그 하나도
같은 파서로 처리된다.
"""

from __future__ import annotations

import concurrent.futures as cf
import html
import re
from email.utils import parsedate_to_datetime

import requests
from dotenv import load_dotenv

from regulation import Item  # source/title/url/date/summary — Tier1과 동일 형태 재사용

load_dotenv()  # build_site.py 를 거치지 않고 `python3 news.py` 단독 실행해도 NAVER_* 를 읽게

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
CURL_UA = {"User-Agent": "curl/8.16.0"}  # 한국경제는 브라우저 UA를 403으로 막는다

RSS_SOURCES = {
    "호텔앤레스토랑": ("https://www.hotelrestaurant.co.kr/rss/allArticle.xml", UA),
    "여행신문": ("https://www.traveltimes.co.kr/rss/allArticle.xml", UA),
    "히치하이커": ("https://hitchhickr.substack.com/feed", UA),
    "한국경제": ("https://www.hankyung.com/feed/realestate", CURL_UA),
    "매일경제": ("https://www.mk.co.kr/rss/50300009/", UA),
}


def _get(url: str, headers: dict) -> str:
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def _tag(block: str, name: str) -> str:
    """<title>x</title> 와 <title><![CDATA[x]]></title> 둘 다 처리."""
    m = re.search(rf"<{name}[^>]*>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</{name}>", block, re.S)
    if not m:
        return ""
    return html.unescape((m.group(1) or m.group(2) or "").strip())


def _parse_pubdate(raw: str) -> str | None:
    if not raw:
        return None
    try:  # RFC822: "Tue, 28 Jul 2026 11:55:36 +0900"
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)  # "2026-07-28 10:53:41"
    return m.group(1) if m else None


# ───────────────────────────── og:image 보강
#
# 호텔앤레스토랑·여행신문·한국경제·숙박매거진·대한숙박업중앙회·네이버뉴스는
# 목록/RSS 자체엔 이미지가 없지만 기사 상세 페이지엔 og:image 메타태그가
# 있다(실측 확인, 2026-07-28 — 네이버뉴스는 원문 링크 10곳을 무작위로 찔러
# 10/10 성공). <head> 메타태그 하나만 보면 되는 가벼운 작업이라, 처음 판단
# ("건마다 크롤링은 과하다")을 뒤집는다. 소스당 최대 50건이라 병렬로 받으면
# 월 1회 빌드에서 감당된다.
#
# 서울관광재단은 일부러 여기 안 넣었다 — 확인해보니 og:image가 모든 기사에서
# 똑같은 사이트 로고(/humanframe/theme/sto/assets/images/logo_sto.jpg)만
# 반환한다. 이건 실제 기사 사진이 아니라 이 사이트의 기본 폴백이라, 넣으면
# 12장이 전부 똑같은 로고로 도배되는 꼴이 된다 — 안 넣는 게 낫다.
#
# 부산관광공사는 og:image 자체가 없다(메타태그 미지원). 대신 본문에 첨부
# 이미지가 /attach/IMAGE/Board/... 경로로 실려 있어 별도 함수(_fetch_bto_image)
# 로 처리한다.

ENRICH_SOURCES = {"호텔앤레스토랑", "여행신문", "한국경제", "숙박매거진", "대한숙박업중앙회", "클룩 뉴스룸"}

# 경쟁사(OTA) 뉴스룸 — 공식 보도자료·마케팅 발표. 경쟁사 동향 페이지(competitors.html)가
# 이 순서·구성으로 4등분 화면을 만든다.
COMPETITOR_SOURCES = ["위홈 공식블로그", "에어비앤비 뉴스룸", "아고다 뉴스룸", "부킹닷컴 뉴스룸", "클룩 뉴스룸"]


def _fetch_og_image(url: str, headers: dict) -> str | None:
    try:
        r = requests.get(url, headers=headers, timeout=10)
        m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None


def _fetch_bto_image(url: str, headers: dict) -> str | None:
    """og:image가 없어 본문 첨부 이미지 중 첫 번째(/attach/IMAGE/Board/...)를 대신 쓴다."""
    try:
        r = requests.get(url, headers=headers, timeout=10)
        m = re.search(r'<img[^>]*src="(/attach/IMAGE/Board/[^"]+)"', r.text)
        return "https://bto.or.kr" + m.group(1) if m else None
    except Exception:
        return None


def _enrich(items: list[Item], headers: dict, fetch_fn=None, max_workers: int = 12) -> None:
    """
    image 가 없는 항목만 상세 페이지에서 이미지를 보강한다(제자리 수정).
    fetch_fn 기본값을 None 으로 두고 여기서 _fetch_og_image 를 참조하는 이유:
    함수 시그니처 기본값(=_fetch_og_image)으로 두면 정의 시점에 참조가 고정돼서,
    테스트에서 news._fetch_og_image 를 몽키패치해도 반영이 안 된다(실제로 이
    버그를 겪었다) — 매 호출 시점에 모듈 네임스페이스에서 다시 찾아야 한다.
    """
    fetch_fn = fetch_fn or _fetch_og_image
    targets = [i for i in items if not i.image]
    if not targets:
        return
    with cf.ThreadPoolExecutor(max_workers) as ex:
        images = list(ex.map(lambda i: fetch_fn(i.url, headers), targets))
    for item, img in zip(targets, images):
        item.image = img


def _parse_rss_image(block: str) -> str | None:
    """
    이미지가 실려오는 방식이 소스마다 다르다(실측 확인, 2026-07-28):
    매일경제는 media:content, 히치하이커는 enclosure, 그 외(호텔앤레스토랑·
    여행신문·한국경제)는 RSS 자체에 이미지가 없다(목록 피드에 안 실어줌 —
    상세 페이지를 따로 긁어야 하는데 건당 요청이라 지금은 안 한다).
    """
    m = re.search(r'<media:content[^>]*url="([^"]+)"', block)
    if m:
        return m.group(1)
    m = re.search(r'<enclosure[^>]*url="([^"]+)"', block)
    if m:
        return m.group(1)
    return None


# <author> 태그에 사람 이름이 아니라 매체명 그 자체를 넣는 곳이 있다(실측: 매일경제는
# <author>매일경제</author>). 그건 "누구에게 피칭할지"에 아무 정보가 없는 것과 같으니
# reporter로 남기지 않는다 — source와 대소문자 무관 일치, 또는 개인이 아닌 부서명이면 버린다.
_GENERIC_BYLINES = {"편집부", "편집팀", "취재팀", "뉴스룸", "기자단", "관리자"}


def _normalize_reporter(raw: str, source: str) -> str | None:
    name = raw.strip()
    if name.endswith("기자"):
        name = name[:-2].strip()
    if not name or name == source or name in _GENERIC_BYLINES:
        return None
    return name


def parse_rss(xml_text: str, source: str) -> list[Item]:
    items = []
    for block in re.findall(r"<item[ >].*?</item>", xml_text, re.S):
        title = _tag(block, "title")
        link = _tag(block, "link")
        if not (title and link):
            continue
        author = _tag(block, "author")
        items.append(Item(
            source=source, title=title, url=link,
            date=_parse_pubdate(_tag(block, "pubDate")),
            summary=_tag(block, "description")[:200],
            image=_parse_rss_image(block),
            reporter=_normalize_reporter(author, source) if author else None,
        ))
    return items


def fetch_rss(source: str) -> list[Item]:
    url, headers = RSS_SOURCES[source]
    return parse_rss(_get(url, headers), source)


# ───────────────────────────── HTML 소스 (사이트마다 구조가 달라 개별 파서)

SUKBAK_URL = "https://www.sukbakmagazine.com/news/articleList.html?view_type=sm"
YANOLJA_URL = "https://www.yanolja-research.com/trend/list?lang=kr"
ONDA_URL = "https://www.onda.me/newsletter"
AIRBNB_NEWS_URL = "https://news.airbnb.com/ko/"
AGODA_NEWS_URL = "https://www.agoda.com/press/ko-kr/newsroom/"
BOOKING_NEWS_URL = "https://news.booking.com/ko-ko/"
KLOOK_NEWS_URL = "https://www.klook.com/newsroom/"
WEHOME_BLOG_URL = "https://rss.blog.naver.com/wehomeme.xml"  # 위홈 공식 네이버블로그 RSS
STO_URL = "https://www.sto.or.kr/press"
# 이전 URL(sto.or.kr/press/pressReleaseList.do)은 404 — 2026-07-28 재조사로 교체.
# 새 URL도 /press/{제목slug}_/{nttNo} 형태라 사이트 개편 시 또 바뀔 수 있는 값이다.
MOTEL_URL = "https://www.motel.or.kr/73"  # "소식 > 중앙회·지회·지부소식" 메뉴 코드
BTO_URL = "https://bto.or.kr/kor/CMS/Board/Board.do?mCode=MN047"  # 알림마당>보도자료


def parse_sukbak(page: str) -> list[Item]:
    """<h2 class="titles"><a href="/news/articleView.html?idxno=N">제목</a></h2> ... <em class="info dated">MM.DD HH:MM</em>"""
    items = []
    for li in re.findall(r"<li>\s*<H2 class=\"titles\">.*?</li>", page, re.S):
        title_m = re.search(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', li)
        date_m = re.search(r'class="info dated">([^<]+)<', li)
        if not title_m:
            continue
        href, title = title_m.groups()
        items.append(Item(
            source="숙박매거진", title=html.unescape(title.strip()),
            url="https://www.sukbakmagazine.com" + href if href.startswith("/") else href,
            date=date_m.group(1).strip() if date_m else None,
        ))
    return items


def parse_yanolja(page: str) -> list[Item]:
    """<a class="inner" href=".../trend/view/N?lang=kr"><div class="feed_thumb" style="background-image:url('...')">...<h4>제목</h4>..."""
    items = []
    for m in re.finditer(r'<a class="inner" href="([^"]+)">(.*?)</a>', page, re.S):
        href, inner = m.groups()
        title_m = re.search(r"<h4>([^<]+)</h4>", inner)
        if not title_m:
            continue
        img_m = re.search(r"background-image:url\('([^']+)'\)", inner)
        items.append(Item(source="야놀자리서치", title=html.unescape(title_m.group(1).strip()), url=href,
                           image=img_m.group(1) if img_m else None))
    return items


def parse_onda(page: str) -> list[Item]:
    """
    Webflow CMS 컬렉션이라 최신 2개(featured)는 text-block-57, 나머지는
    text-block-60 클래스로 렌더링된다 — 하나만 잡으면 최신 2건 빼고 다 놓친다
    (처음 구현 때 실제로 이렇게 20건 중 2건만 잡혔다). 썸네일은 href 바로 뒤에
    오는 <img src="..."> 하나뿐이라 같은 블록 안에서 함께 뽑는다.
    """
    items = []
    for m in re.finditer(
        r'<a href="(/blog/[a-z0-9-]+)"[^>]*><img[^>]*src="([^"]+)"[^>]*/>.*?'
        r'class="text-block-(?:57|60)[^"]*">([^<]+)</div>', page, re.S
    ):
        href, img, title = m.groups()
        items.append(Item(source="온다", title=html.unescape(title.strip()),
                           url="https://www.onda.me" + href, image=img))
    return items


def parse_airbnb_news(page: str) -> list[Item]:
    """
    한 게시물이 data-post-id="..." 로 시작하는 postTeaser 블록 하나에 이미지와
    제목이 같이 들어있다 — 이전엔 제목(postTeaser__titleLink)만 정규식으로
    바로 찾아서 이미지 블록과 묶이지 않았다. 블록 단위로 잘라 그 안에서 둘 다 찾는다.
    """
    items = []
    for block in re.split(r'(?=data-post-id="\d+")', page)[1:]:
        title_m = re.search(r'class="postTeaser__titleLink" href="([^"]+)">([^<]+)</a>', block)
        if not title_m:
            continue
        href, title = title_m.groups()
        img_m = re.search(r'<img[^>]*src="([^"]+)"', block)
        items.append(Item(source="에어비앤비 뉴스룸", title=html.unescape(title.strip()), url=href,
                           image=img_m.group(1) if img_m else None))
    return items


def parse_agoda_news(page: str) -> list[Item]:
    """<article class="posts-item..."><a href="URL"><div class="post-image..."><img src="IMG" alt="제목전문"/>..."""
    items = []
    for block in re.findall(r'<article class="posts-item.*?</article>', page, re.S):
        href_m = re.search(r'<a href="([^"]+)"', block)
        img_m = re.search(r'<img src="([^"]+)" alt="([^"]+)"', block)
        date_m = re.search(r'<span>([^<]+)</span>\s*/', block)
        if not (href_m and img_m):
            continue
        items.append(Item(
            source="아고다 뉴스룸", title=html.unescape(img_m.group(2).strip()),
            url=href_m.group(1), image=img_m.group(1),
            date=date_m.group(1).strip() if date_m else None,
        ))
    return items


def parse_booking_news(page: str) -> list[Item]:
    """<a class="pp-block-item-container" href="URL" title="제목전문"><div class="pp_blockheadlines_thumb..." style="background-image:url(IMG)"...>
    같은 기사가 '특별 기획'·'최신 소식' 등 여러 섹션에 중복 노출돼 href로 dedup한다."""
    items, seen = [], set()
    for m in re.finditer(
        r'<a class="pp-block-item-container" href="([^"]+)" title="([^"]+)">'
        r'<div class="pp_blockheadlines_thumb[^"]*"[^>]*style="background-image:url\(([^)]+)\)"', page):
        href, title, img = m.groups()
        if href in seen:
            continue
        seen.add(href)
        items.append(Item(source="부킹닷컴 뉴스룸", title=html.unescape(title.strip()), url=href, image=img))
    return items


def parse_klook_news(page: str) -> list[Item]:
    """
    목록 카드엔 이미지가 없다(상단 hot-news 하나만 예외) — URL은
    data-spm-module="NewsArticle_LIST?oid=slug_XXX&..." 의 slug로 조립하고,
    이미지는 news.py 기존 og:image 보강 파이프라인(ENRICH_SOURCES)에 맡긴다.
    """
    items = []
    for m in re.finditer(
        r'data-spm-module="NewsArticle_LIST\?oid=slug_([^&"]+)&.*?'
        r'<div class="title"[^>]*>\s*([^<]+?)\s*</div>', page, re.S):
        slug, title = m.groups()
        items.append(Item(source="클룩 뉴스룸", title=html.unescape(title.strip()),
                           url=f"https://www.klook.com/newsroom/{slug}/"))
    return items


def parse_wehome_blog(xml_text: str) -> list[Item]:
    """
    위홈 공식 네이버블로그 RSS. 다른 RSS 소스와 달리 이미지가 <enclosure>나
    <media:content>가 아니라 <description> CDATA 본문 안에 <img src="..."> 로
    박혀 있다 — parse_rss()의 범용 이미지 추출로는 못 잡아서 여기서 따로 뽑는다.
    <link>엔 ?fromRss=true&trackingCode=rss 추적 파라미터가 붙어 있어 <guid>(원본
    URL)를 대신 쓴다.
    """
    items = []
    for block in re.findall(r"<item[ >].*?</item>", xml_text, re.S):
        title = _tag(block, "title")
        guid = _tag(block, "guid")
        if not (title and guid):
            continue
        desc = _tag(block, "description")
        img_m = re.search(r'<img[^>]*src="([^"]+)"', desc)
        items.append(Item(
            source="위홈 공식블로그", title=title, url=guid,
            date=_parse_pubdate(_tag(block, "pubDate")),
            summary=re.sub(r"<[^>]+>", "", desc).strip()[:200],
            image=img_m.group(1) if img_m else None,
        ))
    return items


def parse_sto(page: str) -> list[Item]:
    """
    <tr><td>번호</td><td><a href="/press/제목slug_/nttNo">제목<span class="blt-new">...</span></a></td>
    <td>작성팀</td><td class="mo-none">YYYY-MM-DD</td><td class="mo-none">조회수</td></tr>
    제목 뒤 <span class="blt-new">new</span> 배지가 붙으므로 title은 첫 텍스트 노드([^<]+)만 잡는다
    — span 태그까지 먹으면 "new"라는 글자가 제목에 섞여버린다.
    """
    items = []
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if "/press/" not in row:
            continue
        title_m = re.search(r'<a href="(/press/[^"]+)">\s*([^<]+)', row)
        date_m = re.search(r'class="mo-none">(\d{4}-\d{2}-\d{2})</td>', row)
        if not title_m:
            continue
        href, title = title_m.groups()
        items.append(Item(
            source="서울관광재단", title=html.unescape(title.strip()),
            url="https://www.sto.or.kr" + href,
            date=date_m.group(1) if date_m else None,
        ))
    return items


def parse_motel(page: str) -> list[Item]:
    """
    imweb CMS 게시판 — 한 게시글이 <li class="list_text_title">(제목),
    <li class="name">(글쓴이), <li class="time" title="...">(날짜) 로 형제
    <li> 여러 개에 흩어져 있다(표를 <ul>/<li>로 흉내). 하나의 <li> 블록
    안에서 제목+날짜가 다 안 잡히는 구조라, title 목록과 date 목록을 각각
    뽑아 순서대로 짝짓는다 — imweb 템플릿이 항상 title→name→time 순서로
    반복 렌더링하는 걸 실측으로 확인했다(순서가 깨지면 이 zip이 잘못된
    쌍을 만들 수 있다는 게 이 방식의 유일한 약점).
    """
    titles = re.findall(
        r'class="list_text_title[^"]*"[^>]*href="([^"]+)"[^>]*>\s*<span>\s*([^<]+?)\s*</span>', page)
    dates = re.findall(r'class="time" title="(\d{4}-\d{2}-\d{2}) [\d:]+"', page)
    items = []
    for (href, title), date in zip(titles, dates):
        items.append(Item(
            source="대한숙박업중앙회", title=html.unescape(title.strip()),
            url="https://www.motel.or.kr" + href, date=date,
        ))
    return items


def parse_bto(page: str) -> list[Item]:
    """<td class="subject"><a href='?...board_seq=N'>제목</a>...</td> ... <td class="date">YYYY-MM-DD</td>"""
    items = []
    for row in re.findall(r"<tr\s*>.*?</tr>", page, re.S):
        if "board_seq=" not in row:
            continue
        title_m = re.search(r"<a href='([^']+)'>\s*([^<]+?)\s*</a>", row)
        date_m = re.search(r'class="date">([^<]+)<', row)
        if not title_m:
            continue
        href, title = title_m.groups()
        items.append(Item(
            source="부산관광공사", title=html.unescape(title.strip()),
            url="https://bto.or.kr/kor/CMS/Board/Board.do" + href,
            date=date_m.group(1).strip() if date_m else None,
        ))
    return items


HTML_SOURCES = {
    "숙박매거진": (SUKBAK_URL, parse_sukbak),
    "야놀자리서치": (YANOLJA_URL, parse_yanolja),
    "온다": (ONDA_URL, parse_onda),
    "에어비앤비 뉴스룸": (AIRBNB_NEWS_URL, parse_airbnb_news),
    "아고다 뉴스룸": (AGODA_NEWS_URL, parse_agoda_news),
    "부킹닷컴 뉴스룸": (BOOKING_NEWS_URL, parse_booking_news),
    "클룩 뉴스룸": (KLOOK_NEWS_URL, parse_klook_news),
    "위홈 공식블로그": (WEHOME_BLOG_URL, parse_wehome_blog),
    "서울관광재단": (STO_URL, parse_sto),
    "대한숙박업중앙회": (MOTEL_URL, parse_motel),
    "부산관광공사": (BTO_URL, parse_bto),
}

# 각 소스 컬럼에 5건만 보여주고, "더보기"는 페이지네이션을 새로 만드는 대신
# 원본 사이트로 바로 보낸다 — 우리가 몇백 건씩 다 떠안고 보여줄 이유가 없고,
# 원본 소스에 트래픽을 돌려주는 게 맞다. HTML 소스는 이미 위에서 쓰는 목록
# URL을 그대로 재사용하고, RSS 소스만 홈페이지 URL을 따로 정의한다.
SOURCE_SITE_URL = {
    "호텔앤레스토랑": "https://www.hotelrestaurant.co.kr",
    "여행신문": "https://www.traveltimes.co.kr",
    "히치하이커": "https://hitchhickr.substack.com",
    "한국경제": "https://www.hankyung.com/realestate",
    "매일경제": "https://www.mk.co.kr/news/realestate/",
    "숙박매거진": SUKBAK_URL,
    "야놀자리서치": YANOLJA_URL,
    "온다": ONDA_URL,
    "에어비앤비 뉴스룸": AIRBNB_NEWS_URL,
    "아고다 뉴스룸": AGODA_NEWS_URL,
    "부킹닷컴 뉴스룸": BOOKING_NEWS_URL,
    "클룩 뉴스룸": KLOOK_NEWS_URL,
    "위홈 공식블로그": "https://blog.naver.com/wehomeme",
    "서울관광재단": STO_URL,
    "대한숙박업중앙회": MOTEL_URL,
    "부산관광공사": BTO_URL,
    # 네이버뉴스는 단일 매체가 아니라 검색 결과 모음이라, "원본 사이트" 대신
    # 같은 검색어로 네이버 뉴스 검색 결과 페이지로 보낸다.
    "네이버뉴스": "https://search.naver.com/search.naver?where=news&query=%EA%B3%B5%EC%9C%A0%EC%88%99%EB%B0%95",
}


# ───────────────────────────── 네이버 뉴스 검색 API
#
# 크레덴셜(NAVER_CLIENT_ID/SECRET)이 필요하다 — 네이버 개발자센터에 앱을
# 등록해야 발급되는데, 그건 사용자 본인 네이버 계정 로그인이 있어야 하는
# 절차라 내가 대신 할 수 없다(계정 로그인 대행 금지). email_sender.py 의
# SMTP dry-run과 같은 패턴 — 키가 없으면 실제로 호출하지 않고 그 사실을
# 그대로 알린다.

NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"


def naver_configured() -> bool:
    import os
    return bool(os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"))


def fetch_naver_news(query: str = "공유숙박") -> list[Item]:
    import os
    if not naver_configured():
        print(f"  📭 [DRY-RUN] NAVER_CLIENT_ID/SECRET 미설정 — '{query}' 검색 생략")
        return []
    headers = {
        "X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
        "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"],
    }
    r = requests.get(NAVER_API_URL, params={"query": query, "display": 20, "sort": "date"},
                      headers=headers, timeout=15)
    r.raise_for_status()
    items = []
    for x in r.json().get("items", []):
        title = re.sub(r"</?b>", "", html.unescape(x["title"]))
        items.append(Item(source="네이버뉴스", title=title, url=x["originallink"] or x["link"],
                           date=_parse_pubdate(x.get("pubDate", ""))))
    return items


def fetch_html(source: str) -> list[Item]:
    url, parser = HTML_SOURCES[source]
    return parser(_get(url, UA))


# ───────────────────────────── 통합 수집

def collect() -> list[Item]:
    """
    필터 없이 전부 반환한다 — news.html 아카이브는 전체를 보여주고, 대시보드
    쪽에서만 CORE_KEYWORDS 로 추려 쓴다(build_site.py). 여기서 걸러버리면
    "관광 산업 전반" 같은 배경 정보성 기사가 아카이브에서도 사라진다.
    """
    items: list[Item] = []
    for name in RSS_SOURCES:
        try:
            got = fetch_rss(name)
            if name in ENRICH_SOURCES:
                _enrich(got, RSS_SOURCES[name][1])
            print(f"  [{name}] {len(got)}건 (이미지 {sum(1 for i in got if i.image)}건)")
            items.extend(got)
        except Exception as e:
            print(f"  ⚠️ {name} 수집 실패: {type(e).__name__}: {e}")
    for name in HTML_SOURCES:
        try:
            got = fetch_html(name)
            if name == "부산관광공사":
                _enrich(got, UA, fetch_fn=_fetch_bto_image)
            elif name in ENRICH_SOURCES:
                _enrich(got, UA)
            has_enrich = name in ENRICH_SOURCES or name == "부산관광공사"
            print(f"  [{name}] {len(got)}건" + (f" (이미지 {sum(1 for i in got if i.image)}건)" if has_enrich else ""))
            items.extend(got)
        except Exception as e:
            print(f"  ⚠️ {name} 수집 실패: {type(e).__name__}: {e}")

    try:
        naver = fetch_naver_news()
        if naver:
            _enrich(naver, UA)
            print(f"  [네이버뉴스] {len(naver)}건 (이미지 {sum(1 for i in naver if i.image)}건)")
        items.extend(naver)
    except Exception as e:
        print(f"  ⚠️ 네이버뉴스 수집 실패: {type(e).__name__}: {e}")

    seen: set[str] = set()
    return [i for i in items if not (i.url in seen or seen.add(i.url))]


if __name__ == "__main__":
    all_items = collect()
    print(f"\n전체 {len(all_items)}건")
    for i in all_items[:5]:
        print(f"  [{i.source}] {i.title} ({i.date or '-'})")
