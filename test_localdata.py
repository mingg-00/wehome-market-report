#!/usr/bin/env python3
"""파싱·dedup·집계 자체 점검. `python test_localdata.py` 로 실행. 네트워크 없음."""

from datetime import date

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


def test_by_sigungu_status_counts_regardless_of_active_filter():
    """by_sigungu(활성만)와 달리, 구별 영업상태 분포는 폐업·휴업도 다 잡아야 한다
    ("영업중/휴업/폐업 비율" 시각화의 원자료)."""
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    assert s.by_sigungu_status["서울특별시 마포구"] == {"closed": 1, "active": 1}
    assert s.by_sigungu_status["서울특별시 용산구"] == {"pause": 1}
    assert s.by_sigungu_status["부산광역시 수영구"] == {"closed": 1}


def test_last_n_months_zero_fills_and_ends_at_given_date():
    assert ld._last_n_months(3, end=date(2026, 3, 15)) == ["2026-01", "2026-02", "2026-03"]
    assert ld._last_n_months(4, end=date(2026, 1, 31)) == ["2025-10", "2025-11", "2025-12", "2026-01"]


def test_regional_stats_includes_status_and_zero_filled_trend():
    s = ld.aggregate("foreigner_city_homestays", ROWS)
    regions = {(r["sido"], r["sigungu"]): r for r in s.regional_stats(trend_months=3, today=date(2025, 6, 15))}
    mapo = regions[("서울특별시", "마포구")]
    assert mapo["closed"] == 1 and mapo["pause"] == 0
    assert mapo["monthly"] == [
        {"ym": "2025-04", "n": 0}, {"ym": "2025-05", "n": 0}, {"ym": "2025-06", "n": 1},
    ], "등록 이력 없는 달(4·5월)도 0으로 채워져야 스파크라인 폭이 일정하다"


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


def test_cohort_survival_life_table_with_right_censoring():
    """
    2020년 코호트 6건, 기준일 2023-01-01로 고정:
      - 2건: 2020-06-01 폐업 (0년차에 사망)
      - 1건: 2021-06-01 폐업 (1년차에 사망)
      - 3건: 아직 영업중 (기준일까지 3년 생존 관찰, 우변절단)
    손으로 계산한 생존율과 대조 — 아직 안 닫힌 3건이 "폐업"으로 잘못 카운트되면
    분자가 커져 생존율이 실제보다 낮게 나온다(생존편향 반대 방향의 버그).
    """
    from datetime import date
    rows = [
        {"인허가일자": "2020-01-01", "폐업일자": "2020-06-01", "영업상태명": "폐업"},
        {"인허가일자": "2020-01-01", "폐업일자": "2020-06-01", "영업상태명": "폐업"},
        {"인허가일자": "2020-01-01", "폐업일자": "2021-06-01", "영업상태명": "폐업"},
        {"인허가일자": "2020-01-01", "영업상태명": "영업중"},
        {"인허가일자": "2020-01-01", "영업상태명": "영업중"},
        {"인허가일자": "2020-01-01", "영업상태명": "영업중"},
    ]
    curve = ld.cohort_survival(rows, min_cohort_size=1, today=date(2023, 1, 1))["2020"]
    assert curve["0"] == 1.0
    assert curve["1"] == 0.6667   # 6명 중 2명 0년차 사망 -> 4/6
    assert curve["2"] == 0.5      # 남은 4명 중 1명 1년차 사망 -> 0.6667*3/4
    assert curve["3"] == 0.5      # 2년차 사망 없음, 생존율 유지
    assert curve["4"] == 0.5      # 3명 전원 censored(영업중) — 사망으로 안 잡혀야 함
    assert "5" not in curve       # 기준일까지 4년차에 도달한 표본이 없어 곡선이 여기서 끊김


def test_cohort_survival_excludes_small_cohorts():
    rows = [{"인허가일자": "2020-01-01", "영업상태명": "영업중"}] * 5
    assert ld.cohort_survival(rows, min_cohort_size=30) == {}


def test_entry_index_ranks_by_percentile_not_by_size():
    """규모(active)가 가장 큰 구라도 성장·생존·적합도가 나쁘면 지수 1위가 아니어야 한다 —
    지수가 그냥 자치구 순위(district_rank) 재탕이면 이 테스트가 깨진다(P2 방지 가드)."""
    months = ld._last_n_months(12)

    def monthly(recent_n: int) -> dict:
        return {ym: (recent_n if i >= 6 else 1) for i, ym in enumerate(months)}

    flagship = ld.CategoryStats(
        "foreigner_city_homestays", "외국인관광도시민박업", active=0, closed=0, pause=0, total=0,
        by_sigungu={"서울특별시 성장구": 25, "서울특별시 폐업구": 100, "서울특별시 안정구": 25},
        by_sigungu_monthly={
            "서울특별시 성장구": monthly(5),
            "서울특별시 폐업구": monthly(1),
            "서울특별시 안정구": monthly(1),
        },
        by_sigungu_status={
            "서울특별시 성장구": {"active": 25, "closed": 0, "pause": 0},
            "서울특별시 폐업구": {"active": 100, "closed": 100, "pause": 0},  # 규모 최대지만 폐업률 50%
            "서울특별시 안정구": {"active": 25, "closed": 0, "pause": 0},
        },
    )
    other = ld.CategoryStats(
        "rural_homestays", "농어촌민박", active=0, closed=0, pause=0, total=0,
        by_sigungu={"서울특별시 폐업구": 900},  # 폐업구는 공급 대부분이 다른 카테고리(적합도 낮음)
    )
    categories = {"foreigner_city_homestays": flagship, "rural_homestays": other}

    idx = ld.entry_index(categories, min_active=20)
    ranked = [r["sigungu"] for r in idx]

    assert ranked[0] == "성장구", "규모가 가장 작아도 성장·생존·적합도가 제일 좋으면 1위여야 한다"
    assert ranked[-1] == "폐업구", "규모가 가장 커도 폐업률 높고 적합도 낮으면 꼴찌여야 한다"
    assert all(0 <= r["index"] <= 100 for r in idx)


def test_entry_index_excludes_thin_samples():
    s = ld.CategoryStats("foreigner_city_homestays", "외국인관광도시민박업", 0, 0, 0, 0,
                          by_sigungu={"서울특별시 소형구": 5},
                          by_sigungu_monthly={"서울특별시 소형구": {"2026-01": 1}},
                          by_sigungu_status={"서울특별시 소형구": {"active": 5, "closed": 0, "pause": 0}})
    assert ld.entry_index({"foreigner_city_homestays": s}, min_active=20) == []


def test_entry_index_demand_axis_normalizes_by_active_and_excludes_residents():
    """방문자수를 active로 나누지 않고 그냥 쓰면(규모 재탕) 매물 많은 구가 유리해진다 —
    active 축을 의도적으로 뺀 것과 같은 이유로 demand도 정규화해야 한다(P2 방지 가드).
    현지인(거주자) 방문은 관광 수요가 아니므로 반영되면 안 된다(현지인만 압도적으로
    큰 매물많은구가 그걸로 1위가 되면 이 테스트가 깨진다)."""
    months = ld._last_n_months(12)

    def monthly(recent_n: int) -> dict:
        return {ym: (recent_n if i >= 6 else 1) for i, ym in enumerate(months)}

    flagship = ld.CategoryStats(
        "foreigner_city_homestays", "외국인관광도시민박업", active=0, closed=0, pause=0, total=0,
        by_sigungu={"서울특별시 매물많은구": 100, "서울특별시 매물적은구": 20},
        by_sigungu_monthly={
            "서울특별시 매물많은구": monthly(3),
            "서울특별시 매물적은구": monthly(3),
        },
        by_sigungu_status={
            "서울특별시 매물많은구": {"active": 100, "closed": 0, "pause": 0},
            "서울특별시 매물적은구": {"active": 20, "closed": 0, "pause": 0},
        },
    )
    categories = {"foreigner_city_homestays": flagship}
    visitors = {
        # 매물많은구는 현지인(거주자)이 압도적으로 많지만 외지인·외국인(진짜 관광 수요)은
        # active 대비로 보면 매물적은구보다 적다.
        "서울특별시 매물많은구": {"현지인": 9_000_000, "외지인": 900, "외국인": 100},  # /100 = 10
        "서울특별시 매물적은구": {"현지인": 500_000, "외지인": 350, "외국인": 50},      # /20 = 20
    }

    idx = ld.entry_index(categories, min_active=20, visitors=visitors)
    by_gu = {r["sigungu"]: r for r in idx}

    assert by_gu["매물적은구"]["demand"] == 20.0
    assert by_gu["매물많은구"]["demand"] == 10.0
    assert by_gu["매물적은구"]["pct_demand"] > by_gu["매물많은구"]["pct_demand"]


def test_entry_index_excludes_region_without_visitor_match():
    """visitors를 줬는데 특정 구가 TourAPI 커버리지 밖이면(매칭 없음) '수요 0'이 아니라
    '판단 불가'로 보고 그 구 자체를 뺀다 — min_active/growth=inf와 같은 원칙."""
    months = ld._last_n_months(12)

    def monthly(recent_n: int) -> dict:
        return {ym: (recent_n if i >= 6 else 1) for i, ym in enumerate(months)}

    flagship = ld.CategoryStats(
        "foreigner_city_homestays", "외국인관광도시민박업", active=0, closed=0, pause=0, total=0,
        by_sigungu={"서울특별시 매칭구": 25, "서울특별시 미매칭구": 25},
        by_sigungu_monthly={
            "서울특별시 매칭구": monthly(3),
            "서울특별시 미매칭구": monthly(3),
        },
        by_sigungu_status={
            "서울특별시 매칭구": {"active": 25, "closed": 0, "pause": 0},
            "서울특별시 미매칭구": {"active": 25, "closed": 0, "pause": 0},
        },
    )
    categories = {"foreigner_city_homestays": flagship}
    visitors = {"서울특별시 매칭구": {"현지인": 0, "외지인": 100, "외국인": 0}}

    idx = ld.entry_index(categories, min_active=20, visitors=visitors)
    assert [r["sigungu"] for r in idx] == ["매칭구"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
