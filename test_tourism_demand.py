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


def test_tourapi_dry_run_without_key():
    import os
    saved = os.environ.pop("TOUR_API_KEY", None)
    try:
        assert td.tourapi_configured() is False
        assert td.collect_province_visitors("20260101", "20260101") == {}
        assert td.collect_district_visitors("20260101", "20260101") == {}
    finally:
        if saved is not None:
            os.environ["TOUR_API_KEY"] = saved


def test_aggregate_visitors_sums_by_area_and_toudiv():
    items = [
        {"areaNm": "서울특별시", "touDivCd": "1", "touNum": "100"},
        {"areaNm": "서울특별시", "touDivCd": "2", "touNum": "50"},
        {"areaNm": "서울특별시", "touDivCd": "2", "touNum": "25"},  # 다른 날짜, 같은 구분 → 합산
        {"areaNm": "부산광역시", "touDivCd": "3", "touNum": "10"},
    ]
    out = td._aggregate_visitors(items, lambda it: it["areaNm"])
    assert out["서울특별시"] == {"현지인": 100.0, "외지인": 75.0, "외국인": 0.0, "total": 175.0}
    assert out["부산광역시"]["total"] == 10.0


def test_sido_of_disambiguates_same_named_districts():
    """"중구"는 부산(26)에도 대구(27)에도 있다 — signguCode 앞자리로만 구분 가능해야 한다."""
    assert td._sido_of("26110") == "부산광역시"
    assert td._sido_of("27110") == "대구광역시"
    assert td._sido_of("11140") == "서울특별시"


def test_sido_of_unknown_prefix_marks_visibly_instead_of_guessing():
    assert td._sido_of("99999") == "미상(99)"


def test_collect_district_visitors_keys_by_sido_and_signgu():
    """중구가 부산(26)·대구(27) 양쪽에서 와도 합쳐지지 않고 "시도 시군구"로 갈라져야 한다."""
    import requests as req_module

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    body = {"response": {"body": {"numOfRows": 1000, "pageNo": 1, "totalCount": 2, "items": {"item": [
        {"signguCode": "26110", "signguNm": "중구", "touDivCd": "1", "touNum": "10"},
        {"signguCode": "27110", "signguNm": "중구", "touDivCd": "1", "touNum": "20"},
    ]}}}}

    original_get = req_module.get
    req_module.get = lambda url, params, timeout: FakeResp(body)
    import os
    original_key = os.environ.get("TOUR_API_KEY")
    os.environ["TOUR_API_KEY"] = "dummy"
    try:
        out = td.collect_district_visitors("20260101", "20260101")
    finally:
        req_module.get = original_get
        if original_key is None:
            os.environ.pop("TOUR_API_KEY", None)
        else:
            os.environ["TOUR_API_KEY"] = original_key

    assert out["부산광역시 중구"]["total"] == 10.0
    assert out["대구광역시 중구"]["total"] == 20.0


def test_tourapi_items_paginates_and_normalizes_single_item():
    """totalCount가 numOfRows보다 크면 다음 페이지를 더 받아야 하고, 결과가 1건이면
    item이 dict(리스트 아님)로 오는 공공데이터포털 특유의 응답도 정상 처리해야 한다."""
    import requests as req_module
    pages = {
        1: {"response": {"body": {"numOfRows": 1, "pageNo": 1, "totalCount": 2,
                                   "items": {"item": {"areaNm": "A", "touDivCd": "1", "touNum": "1"}}}}},
        2: {"response": {"body": {"numOfRows": 1, "pageNo": 2, "totalCount": 2,
                                   "items": {"item": {"areaNm": "B", "touDivCd": "1", "touNum": "2"}}}}},
    }

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["pageNo"])
        return FakeResp(pages[params["pageNo"]])

    original_get = req_module.get
    req_module.get = fake_get
    original_key = __import__("os").environ.get("TOUR_API_KEY")
    __import__("os").environ["TOUR_API_KEY"] = "dummy"
    try:
        items = td._tourapi_items("metcoRegnVisitrDDList", "20260101", "20260101")
    finally:
        req_module.get = original_get
        if original_key is None:
            __import__("os").environ.pop("TOUR_API_KEY", None)
        else:
            __import__("os").environ["TOUR_API_KEY"] = original_key

    assert calls == [1, 2]
    assert [i["areaNm"] for i in items] == ["A", "B"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
