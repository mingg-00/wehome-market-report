#!/usr/bin/env python3
"""fmt_count/fmt_won 검증. 실제 데이터랩 응답값으로 화면 표시와 대조했다(2026-07-29)."""

import tourism_demand as td


def test_fmt_count_matches_datalab_display():
    assert td.fmt_count(179_599_761) == "1억 7,960만명"  # 숙박 여행객수
    assert td.fmt_count(1_516_283_426) == "15억 1,628만명"  # 내국인 여행객수
    assert td.fmt_count(10_709_919) == "1,071만명"  # 방한 외래객수(억 미만)
    assert td.fmt_count(999) == "999명"  # 만 미만


def test_fmt_won_matches_datalab_display():
    assert td.fmt_won(10_038_890_733_106.61) == "10조 389억원"  # 외국인 관광소비
    assert td.fmt_won(80_048_587_236_915) == "80조 486억원"  # 내국인 관광소비
    assert td.fmt_won(50_000_000_000) == "500억원"  # 조 미만


if __name__ == "__main__":
    test_fmt_count_matches_datalab_display()
    test_fmt_won_matches_datalab_display()
    print("ok")
