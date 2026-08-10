#!/usr/bin/env python3
"""build_site.py 자체 점검. 현재는 page()의 GA4 스크립트 삽입 분기만 — 나머지는
지금까지 실제 빌드·브라우저 확인으로 검증해왔다(이 파일은 새로 추가된 조건부
로직에 대한 최소 회귀 테스트)."""

import build_site as bs


def test_page_omits_ga4_script_when_measurement_id_unset():
    original = bs.GA4_MEASUREMENT_ID
    bs.GA4_MEASUREMENT_ID = ""
    try:
        html = bs.page("제목", "landing", 0, "<p>본문</p>")
        assert "googletagmanager.com/gtag/js" not in html
    finally:
        bs.GA4_MEASUREMENT_ID = original


def test_page_includes_ga4_script_when_measurement_id_set():
    original = bs.GA4_MEASUREMENT_ID
    bs.GA4_MEASUREMENT_ID = "G-TESTID123"
    try:
        html = bs.page("제목", "landing", 0, "<p>본문</p>")
        assert "googletagmanager.com/gtag/js?id=G-TESTID123" in html
        assert "gtag('config','G-TESTID123')" in html
    finally:
        bs.GA4_MEASUREMENT_ID = original


def test_page_omits_og_tags_and_jsonld_without_base_url():
    original = bs.SITE_BASE_URL
    bs.SITE_BASE_URL = ""
    try:
        html = bs.page("제목", "landing", 0, "<p>본문</p>", path="dashboard.html",
                       jsonld=bs.dataset_ld("이름", "설명", "dashboard.html", "2026-08"))
        assert "og:image" not in html
        assert "application/ld+json" not in html  # dataset_ld가 None을 돌려줘야 한다
    finally:
        bs.SITE_BASE_URL = original


def test_page_includes_og_image_and_jsonld_with_base_url():
    original = bs.SITE_BASE_URL
    bs.SITE_BASE_URL = "https://example.com"
    try:
        html = bs.page("제목", "landing", 0, "<p>본문</p>", path="dashboard.html",
                       jsonld=bs.dataset_ld("이름", "설명", "dashboard.html", "2026-08"))
        assert '<meta property="og:image" content="https://example.com/og.png">' in html
        assert '<meta name="twitter:card" content="summary_large_image">' in html
        assert '"@type": "Dataset"' in html
        assert '"temporalCoverage": "2026-08"' in html
    finally:
        bs.SITE_BASE_URL = original


def test_page_includes_naver_verification_only_when_set():
    original = bs.NAVER_SITE_VERIFICATION
    try:
        bs.NAVER_SITE_VERIFICATION = ""
        assert "naver-site-verification" not in bs.page("제목", "landing", 0, "<p>x</p>")
        bs.NAVER_SITE_VERIFICATION = "abc123"
        assert '<meta name="naver-site-verification" content="abc123">' in \
            bs.page("제목", "landing", 0, "<p>x</p>")
    finally:
        bs.NAVER_SITE_VERIFICATION = original


def test_sitemap_lastmod_freezes_past_issues_but_not_the_current_one():
    from datetime import date
    today = date.today().isoformat()
    entries = dict(bs.sitemap_entries(["2026-08", "2026-07"], "2026-08"))
    assert entries["report/2026-08.html"] == today   # 이번 호는 매 빌드 갱신됨
    assert entries["report/2026-07.html"] == "2026-07-01"  # 지난 호는 안 바뀜
    assert entries[""] == today


def test_regions_csv_is_excel_safe_and_blanks_unavailable_values():
    """엑셀 한글 깨짐(BOM)과 '값 없음'을 0으로 채우지 않는 것 — 둘 다 눈으로는 안 보이는 자리다."""
    import csv
    import tempfile
    from pathlib import Path
    regions = [
        {"sido": "서울특별시", "sigungu": "마포구", "active": 1933, "pause": 3, "closed": 637,
         "recent6": 322, "growth_pct": 35, "tier": "대형", "trend": "성장",
         "verdict_label": "경쟁 치열", "national_rank": 1, "sido_rank": 1,
         "entry": {"index": 53, "ei_rank": 28}},
        # 신규 진입 = 직전 6개월이 0이라 증감률이 없고, 표본이 작아 적합도 지수도 안 나온다
        {"sido": "경상북도", "sigungu": "구미시", "active": 7, "pause": 0, "closed": 2,
         "recent6": 9, "growth_pct": None, "tier": "소형", "trend": "신규 진입",
         "verdict_label": "신규 진입 지역", "national_rank": 98, "sido_rank": 4, "entry": None},
    ]
    original = bs.SITE
    with tempfile.TemporaryDirectory() as tmp:
        bs.SITE = Path(tmp)
        try:
            raw = (bs.SITE / bs.write_regions_csv(regions, "2026-08")).read_bytes()
        finally:
            bs.SITE = original

    assert raw.startswith(b"\xef\xbb\xbf")  # BOM 빠지면 윈도우 엑셀에서 한글이 전부 깨진다
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0][0] == "시도" and len(rows[0]) == 14
    assert rows[1][:2] == ["서울특별시", "마포구"] and rows[1][12:] == ["53", "28"]
    # 없는 값을 0으로 채우면 엑셀에선 "증감 0%", "지수 0점"으로 읽힌다 — 공란이어야 한다
    assert rows[2][6] == "" and rows[2][12:] == ["", ""]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
