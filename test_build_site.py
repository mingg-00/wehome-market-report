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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
