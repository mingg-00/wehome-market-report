#!/usr/bin/env python3
"""파싱·dedup·집계 자체 점검. `python test_localdata.py` 로 실행. 네트워크 없음."""

import localdata as ld

# 같은 업체(mgt=A001, 같은 주소)가 등록→폐업 이벤트로 row 2개, 최종수정시점 다름.
ROWS = [
    {"관리번호": "A001", "도로명주소": "서울특별시 마포구 연남동 1", "영업상태명": "영업중",
     "인허가일자": "2024-03-15", "최종수정시점": "20240315090000"},
    {"관리번호": "A001", "도로명주소": "서울특별시 마포구 연남동 1", "영업상태명": "폐업",
     "인허가일자": "2024-03-15", "최종수정시점": "20250601120000"},
    {"관리번호": "A002", "도로명주소": "서울특별시 마포구 성산동 2", "영업상태명": "영업중",
     "인허가일자": "2025-06-01", "최종수정시점": "20250601090000"},
    {"관리번호": "A003", "도로명주소": "서울특별시 용산구 이태원동 3", "영업상태명": "휴업",
     "인허가일자": "2025-06-10", "최종수정시점": "20250610090000"},
    {"관리번호": "A004", "도로명주소": "부산광역시 수영구 광안동 4", "영업상태명": "직권말소",
     "인허가일자": "2023-01-01", "최종수정시점": "20230101090000"},
]


def test_dedup_keeps_latest_status_per_business():
    """A001은 등록중→폐업 두 이벤트를 가졌으니 최신(폐업)만 남아야 한다."""
    out = ld.dedup(ROWS)
    a001 = [r for r in out if r["관리번호"] == "A001"]
    assert len(a001) == 1, "동일 업체가 중복으로 남으면 active count가 부풀려진다"
    assert a001[0]["영업상태명"] == "폐업"


def test_classify_buckets():
    assert ld.classify("영업중") == "active"
    assert ld.classify("폐업") == "closed"
    assert ld.classify("직권말소") == "closed"
    assert ld.classify("등록취소") == "closed"
    assert ld.classify("휴업") == "pause"
    assert ld.classify("존재하지않는상태") == "active", "정의 안 된 상태는 active로 폴백"


def test_parse_region_splits_sido_sigungu():
    assert ld.parse_region({"도로명주소": "서울특별시 마포구 연남동 1"}) == ("서울특별시", "마포구")
    assert ld.parse_region({"도로명주소": "", "지번주소": "부산광역시 수영구 광안동 4"}) == ("부산광역시", "수영구")
    assert ld.parse_region({"도로명주소": ""}) == ("", "")


def test_license_ym_extracts_year_month():
    assert ld.license_ym({"인허가일자": "2024-03-15"}) == "2024-03"
    assert ld.license_ym({"인허가일자": ""}) is None
    assert ld.license_ym({"인허가일자": "잘못된형식"}) is None


def test_aggregate_counts_after_dedup():
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    assert s.total == 4, "5개 row 중 A001 중복 제거로 4곳이어야 함"
    assert s.active == 1   # A002만 (A001은 최신 상태가 폐업으로 덮어써짐)
    assert s.closed == 2   # A001(폐업), A004(직권말소)
    assert s.pause == 1    # A003


def test_aggregate_district_only_counts_active():
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    # A001은 폐업 처리라 by_sigungu에 없어야 하고, A002(마포구, active)만 잡혀야 한다.
    assert s.by_sigungu.get("서울특별시 마포구") == 1
    assert "서울특별시 용산구" not in s.by_sigungu, "휴업 상태는 구별 활성 카운트에서 빠져야 한다"
    assert "부산광역시 수영구" not in s.by_sigungu, "폐업(직권말소) 상태도 빠져야 한다"


def test_aggregate_monthly_counts_all_history_regardless_of_status():
    """월별 신규등록은 현재 상태와 무관하게 인허가일자 기준으로 전부 잡혀야 한다."""
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    assert s.monthly_registrations.get("2024-03") == 1  # A001, 지금은 폐업이지만 등록은 2024-03
    assert s.monthly_registrations.get("2025-06") == 2  # A002, A003


def test_district_rank_sorts_and_filters_by_sido():
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    seoul = s.district_rank("서울특별시")
    assert seoul == [("마포구", 1)]
    assert s.district_rank("부산광역시") == []  # 유일한 부산 row는 폐업 처리라 active 아님


def test_recent_months_slices_from_end():
    s = ld.CategoryStats("x", "x", 0, 0, 0, 0,
                          monthly_registrations={f"2025-0{i}": i for i in range(1, 6)})
    assert s.recent_months(2) == [("2025-04", 4), ("2025-05", 5)]


def test_by_sigungu_monthly_tracks_all_history_regardless_of_status():
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    # A001은 지금 폐업이지만 등록 시점(2024-03) 자체는 마포구 이력에 남아야 한다.
    assert s.by_sigungu_monthly["서울특별시 마포구"] == {"2024-03": 1, "2025-06": 1}


def test_saturation_signal_flags_growth_slowdown():
    """이미 크고(active 충분) 최근 유입이 준 구가 상단에, 증감률이 마이너스로 잡혀야 한다."""
    monthly = {}
    for i, m in enumerate(range(1, 13)):
        ym = f"2025-{m:02d}"
        monthly[ym] = 10 if m <= 6 else 2  # 상반기 왕성 → 하반기 급감
    s = ld.CategoryStats(
        "x", "x", active=0, closed=0, pause=0, total=0,
        by_sigungu={"서울특별시 마포구": 100},
        by_sigungu_monthly={"서울특별시 마포구": monthly},
    )
    signal = s.saturation_signal("서울특별시", recent_n=6, min_active=20)
    assert len(signal) == 1
    name, active, recent, growth = signal[0]
    assert name == "마포구" and active == 100
    assert recent == 12          # 하반기(7~12월) 합 = 2*6
    assert growth == -0.8        # (12-60)/60


def test_saturation_signal_excludes_small_sample_districts():
    s = ld.CategoryStats("x", "x", 0, 0, 0, 0,
                          by_sigungu={"서울특별시 금천구": 5},
                          by_sigungu_monthly={"서울특별시 금천구": {"2025-01": 1}})
    assert s.saturation_signal("서울특별시", min_active=20) == []


def test_dedup_skips_rows_without_address():
    rows = [{"관리번호": "Z", "도로명주소": "", "지번주소": "", "영업상태명": "영업중",
              "최종수정시점": "1"}]
    assert ld.dedup(rows) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
