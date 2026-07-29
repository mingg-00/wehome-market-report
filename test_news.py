#!/usr/bin/env python3
"""news.py 파서 자체 점검. `python test_news.py` 로 실행. 네트워크 없음."""

import news

# ── RSS fixture: 실제 응답에서 확인된 세 가지 포맷 편차를 다 담는다.
RSS_PLAIN = """<item>
<title>클룩, 2026 여름 해외여행 트렌드 발표</title>
<link>https://www.hotelrestaurant.co.kr/news/articleView.html?idxno=1</link>
<pubDate>2026-07-28 10:53:41</pubDate>
<description><![CDATA[전 세계 숙박·교통·액티비티 예약 플랫폼]]></description>
</item>"""

RSS_CDATA_TITLE = """<item>
<title><![CDATA[하늘 나는 택시 첫선…제주서 K-UAM 상용화]]></title>
<link><![CDATA[https://www.hankyung.com/article/1]]></link>
<pubDate>Tue, 28 Jul 2026 11:55:36 +0900</pubDate>
</item>"""

RSS_NO_LINK = """<item>
<title>제목만 있고 링크가 없는 비정상 항목</title>
<pubDate>2026-07-28 00:00:00</pubDate>
</item>"""

RSS_MEDIA_CONTENT = """<item>
<title>서울 외국인 부동산 소유자, 1년 새 1000명 넘게 늘었다</title>
<link>https://www.mk.co.kr/news/realestate/1</link>
<pubDate>Tue, 28 Jul 2026 14:35:30 +0900</pubDate>
<media:content url="https://mkimg.mk.co.kr/meet/neds/2026/07/image_readtop_1.jpg" medium="image"/>
</item>"""

RSS_ENCLOSURE = """<item>
<title><![CDATA[레일유럽 인수한 회사, 여행 플랫폼이라고?]]></title>
<link>https://hitchhickr.substack.com/p/1</link>
<pubDate>Fri, 17 Jul 2026 02:51:33 GMT</pubDate>
<enclosure url="https://substackcdn.com/image/fetch/cover.png" length="0" type="image/jpeg"/>
</item>"""

SUKBAK_HTML = """<li>
<H2 class="titles"><a href="/news/articleView.html?idxno=68972" target="_top">6월 방한 외국인 199만명</a></H2>
<em class="info dated">07.28 14:57</em>
</li>
<li>
<H2 class="titles"><a href="/news/articleView.html?idxno=68971" target="_top">대출 거절 경험 59.4%</a></H2>
<em class="info dated">07.28 14:30</em>
</li>"""

YANOLJA_HTML = """<a class="inner" href="https://www.yanolja-research.com/trend/view/758?lang=kr">
<div class="feed_thumb feed_type2 default_image_2" style="background-image:url('https://diff-yanolja.s3.ap-northeast-2.amazonaws.com/cover.png');"></div>
<div class="feed_desc"><h4>2026년 1분기 국내 숙박업 동향 보고서</h4></div></a>"""

ONDA_HTML = """<a href="/blog/weeklyon-vol-168" class="link-block-29 w-inline-block"><img alt="" loading="lazy" src="https://cdn.prod.website-files.com/cover168.png"/>
<div class="text-block-58">vol-168</div>
<div class="text-block-57">AI가 내 호텔을 추천하게 만드는 법</div></a>
<a href="/blog/weeklyon-vol-166" target="_blank" class="link-block-30 w-inline-block"><img alt="" loading="lazy" src="https://cdn.prod.website-files.com/cover166.png"/>
<div class="text-block-61">vol-166</div>
<div class="text-block-60">유류할증료 폭등, 내 숙소에 생길 일들</div></a>"""

AIRBNB_HTML = """<div data-post-id="459441" data-timestamp="1780959600">
<div class="postTeaser__imageWrap"><a class="postTeaser__imageLink" href="x"><img alt="" width="1100" src="https://news.airbnb.com/wp-content/uploads/lolla.jpg"/></a></div>
<h3 class="postTeaser__title postTeaser__title--featured">
<a class="postTeaser__titleLink" href="https://news.airbnb.com/ko/2026lollapalooza/">
에어비앤비, 롤라팔루자 백스테이지 체험 공개</a></h3></div>
<div data-post-id="459440" data-timestamp="1780959000">
<h3 class="postTeaser__title"><a class="postTeaser__titleLink" href="https://news.airbnb.com/ko/about-us/">
이미지 없는 게시물(회사소개)</a></h3></div>"""

STO_HTML = """<tr>
<td class="mo-none">1229</td>
<td> <a href="/press/관광축제이벤트팀-낮엔-신나는-물놀이_/16468"> (관광축제이벤트팀) 낮엔 신나는 물놀이, 밤엔 힙한 DJ 공연<span class="blt-new"><span class="blind">new</span></span> </a> </td>
<td>홍보팀</td> <td class="mo-none">2026-07-28</td> <td class="mo-none">16</td>
</tr>
<tr>
<td class="mo-none">1228</td>
<td> <a href="/press/MICE전략팀-서울관광재단-DI-2026_/16467"> (MICE전략팀) 서울관광재단, DI 2026 연례총회 참가 </a> </td>
<td>홍보팀</td> <td class="mo-none">2026-07-27</td> <td class="mo-none">13</td>
</tr>"""


def test_rss_plain_format():
    items = news.parse_rss(RSS_PLAIN, "호텔앤레스토랑")
    assert len(items) == 1
    assert items[0].title == "클룩, 2026 여름 해외여행 트렌드 발표"
    assert items[0].date == "2026-07-28"
    assert "예약 플랫폼" in items[0].summary
    assert items[0].image is None, "호텔앤레스토랑 RSS엔 이미지 필드가 없다 — None이어야 한다"


def test_rss_media_content_image():
    items = news.parse_rss(RSS_MEDIA_CONTENT, "매일경제")
    assert items[0].image == "https://mkimg.mk.co.kr/meet/neds/2026/07/image_readtop_1.jpg"


def test_rss_enclosure_image():
    items = news.parse_rss(RSS_ENCLOSURE, "히치하이커")
    assert items[0].image == "https://substackcdn.com/image/fetch/cover.png"


def test_rss_cdata_title_and_link():
    items = news.parse_rss(RSS_CDATA_TITLE, "한국경제")
    assert len(items) == 1
    assert items[0].title == "하늘 나는 택시 첫선…제주서 K-UAM 상용화"
    assert items[0].url == "https://www.hankyung.com/article/1"
    assert items[0].date == "2026-07-28", "RFC822 포맷도 파싱돼야 한다"


def test_rss_item_without_link_is_skipped():
    assert news.parse_rss(RSS_NO_LINK, "x") == []


def test_sukbak_parses_two_items_with_absolute_url():
    items = news.parse_sukbak(SUKBAK_HTML)
    assert len(items) == 2
    assert items[0].title == "6월 방한 외국인 199만명"
    assert items[0].url == "https://www.sukbakmagazine.com/news/articleView.html?idxno=68972"
    assert items[0].date == "07.28 14:57"


def test_yanolja_extracts_title_from_h4():
    items = news.parse_yanolja(YANOLJA_HTML)
    assert len(items) == 1
    assert items[0].title == "2026년 1분기 국내 숙박업 동향 보고서"
    assert items[0].source == "야놀자리서치"
    assert items[0].image == "https://diff-yanolja.s3.ap-northeast-2.amazonaws.com/cover.png"


def test_onda_extracts_title_not_volume_label():
    items = news.parse_onda(ONDA_HTML)
    assert len(items) == 2, "featured(text-block-57)와 일반(text-block-60) 스타일 둘 다 잡혀야 한다"
    assert items[0].title == "AI가 내 호텔을 추천하게 만드는 법", "vol-168(볼륨 라벨)이 아니라 실제 제목이 잡혀야 한다"
    assert items[0].url == "https://www.onda.me/blog/weeklyon-vol-168"
    assert items[0].image == "https://cdn.prod.website-files.com/cover168.png"
    assert items[1].image == "https://cdn.prod.website-files.com/cover166.png"
    assert items[1].title == "유류할증료 폭등, 내 숙소에 생길 일들"


def test_airbnb_news_extracts_title():
    items = news.parse_airbnb_news(AIRBNB_HTML)
    assert len(items) == 2, "이미지 있는 게시물과 없는 게시물 둘 다 잡혀야 한다"
    assert items[0].title == "에어비앤비, 롤라팔루자 백스테이지 체험 공개"
    assert items[0].source == "에어비앤비 뉴스룸"
    assert items[0].image == "https://news.airbnb.com/wp-content/uploads/lolla.jpg"
    assert items[1].title == "이미지 없는 게시물(회사소개)"
    assert items[1].image is None


def test_sto_extracts_title_without_new_badge_text():
    items = news.parse_sto(STO_HTML)
    assert len(items) == 2
    assert items[0].title == "(관광축제이벤트팀) 낮엔 신나는 물놀이, 밤엔 힙한 DJ 공연", \
        "<span class='blt-new'>new</span> 배지 텍스트가 제목에 섞이면 안 된다"
    assert items[0].url == "https://www.sto.or.kr/press/관광축제이벤트팀-낮엔-신나는-물놀이_/16468"
    assert items[0].date == "2026-07-28"


def test_sto_handles_row_without_new_badge():
    items = news.parse_sto(STO_HTML)
    assert items[1].title == "(MICE전략팀) 서울관광재단, DI 2026 연례총회 참가"
    assert items[1].date == "2026-07-27"


MOTEL_HTML = """<ul class="li_body holder">
<li class="link_area"><a href="/73/?q=abc&bmode=view&idx=1&t=board"></a></li>
<li class="tit"><a class="list_text_title" href="/73/?q=abc&bmode=view&idx=1&t=board">
<span> 檢, '광고 갑질 의혹' 야놀자-여기어때 압수수색 </span></a></li>
<li class="name">관리자</li>
<li class="time" title="2026-03-11 10:51">2026-03-11</li>
<li class="tit"><a class="list_text_title" href="/73/?q=def&bmode=view&idx=2&t=board">
<span> 중앙회 "플랫폼 광고비와 수수료, 심의기구 마련해야" </span></a></li>
<li class="name">관리자</li>
<li class="time" title="2026-01-16 19:16">2026-01-16</li>
</ul>"""

BTO_HTML = """<tr >
<td class="num"> 1159 </td>
<td class="subject"> <p class="stitle">
<a href='?mCode=MN047&mode=view&mgr_seq=22&board_seq=5340'> 부산관광공사, 중화권 관광객 대상 부산병 치유 프로모션 전격 추진 </a>
</p> </td>
<td class="writer">부산관광공사</td> <td class="date">2026-07-27</td> <td class="cnt">17</td>
</tr>"""


def test_motel_zips_titles_with_dates_in_order():
    items = news.parse_motel(MOTEL_HTML)
    assert len(items) == 2
    assert items[0].title == "檢, '광고 갑질 의혹' 야놀자-여기어때 압수수색"
    assert items[0].url == "https://www.motel.or.kr/73/?q=abc&bmode=view&idx=1&t=board"
    assert items[0].date == "2026-03-11"
    assert items[1].title == "중앙회 \"플랫폼 광고비와 수수료, 심의기구 마련해야\""
    assert items[1].date == "2026-01-16"


def test_bto_extracts_title_and_date_from_table_row():
    items = news.parse_bto(BTO_HTML)
    assert len(items) == 1
    assert items[0].title == "부산관광공사, 중화권 관광객 대상 부산병 치유 프로모션 전격 추진"
    assert items[0].date == "2026-07-27"
    assert items[0].url == "https://bto.or.kr/kor/CMS/Board/Board.do?mCode=MN047&mode=view&mgr_seq=22&board_seq=5340"


def test_naver_dry_run_without_credentials():
    import os
    saved = {k: os.environ.pop(k, None) for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")}
    try:
        assert news.naver_configured() is False
        assert news.fetch_naver_news("공유숙박") == []
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_enrich_fills_only_missing_images():
    """image가 이미 있는 항목은 상세페이지를 다시 안 긁어야 한다(불필요 요청 방지)."""
    from regulation import Item
    a = Item(source="X", title="이미지 있음", url="https://x.com/1", image="https://x.com/already.jpg")
    b = Item(source="X", title="이미지 없음", url="https://x.com/2")
    original_fetch = news._fetch_og_image
    calls = []
    news._fetch_og_image = lambda url, headers: (calls.append(url), "https://x.com/fetched.jpg")[1]
    try:
        news._enrich([a, b], news.UA)
    finally:
        news._fetch_og_image = original_fetch
    assert calls == ["https://x.com/2"], "이미지 없는 항목만 요청해야 한다"
    assert a.image == "https://x.com/already.jpg", "기존 이미지는 덮어쓰지 않아야 한다"
    assert b.image == "https://x.com/fetched.jpg"


def test_enrich_handles_fetch_failure_gracefully():
    """개별 기사 요청이 실패해도 나머지 수집이 죽지 않고 image=None으로 남아야 한다."""
    from regulation import Item
    a = Item(source="X", title="실패할 항목", url="https://x.com/dead")
    original_fetch = news._fetch_og_image
    news._fetch_og_image = lambda url, headers: None
    try:
        news._enrich([a], news.UA)
    finally:
        news._fetch_og_image = original_fetch
    assert a.image is None


def test_enrich_uses_custom_fetch_fn_when_given():
    """fetch_fn을 명시하면(부산관광공사 전용 이미지 로직) 그걸 써야 한다."""
    from regulation import Item
    a = Item(source="부산관광공사", title="t", url="https://bto.or.kr/x")
    news._enrich([a], news.UA, fetch_fn=lambda url, headers: "https://bto.or.kr/custom.jpg")
    assert a.image == "https://bto.or.kr/custom.jpg"


BTO_BODY_HTML = """<img src="/resources/_Img/Common/logo.png"/>
<img src="/attach/IMAGE/Board/22/2026/7/3JIbSR3bUVVUdlal.PNG"/>
<img src="/resources/_Img/Board/default/ico_img.gif"/>"""


def test_fetch_bto_image_parses_from_html(monkeypatch=None):
    """실제 네트워크 없이 정규식만 검증 — requests.get을 임시로 바꿔치기."""
    import requests
    class FakeResp:
        text = BTO_BODY_HTML
    original_get = requests.get
    requests.get = lambda *a, **kw: FakeResp()
    try:
        img = news._fetch_bto_image("https://bto.or.kr/x", news.UA)
    finally:
        requests.get = original_get
    assert img == "https://bto.or.kr/attach/IMAGE/Board/22/2026/7/3JIbSR3bUVVUdlal.PNG", \
        "로고·아이콘 공통 이미지가 아니라 본문 첨부(/attach/IMAGE/Board/) 이미지를 잡아야 한다"


def test_collect_dedupes_by_url():
    """서로 다른 소스라도 URL이 같으면 하나만 남아야 한다(재게시·크로스포스팅 대비)."""
    from regulation import Item
    a = Item(source="A", title="t1", url="https://x.com/1")
    b = Item(source="B", title="t2", url="https://x.com/1")
    c = Item(source="A", title="t3", url="https://x.com/2")
    seen = set()
    out = [i for i in [a, b, c] if not (i.url in seen or seen.add(i.url))]
    assert len(out) == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
