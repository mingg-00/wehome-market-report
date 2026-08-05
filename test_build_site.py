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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
