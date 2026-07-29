#!/usr/bin/env python3
"""
파싱 자체 점검. `python test_safestay.py` 로 실행.

지키려는 것은 하나다: 주석 처리된 <th>(광주/전남) 때문에 지역이 한 칸씩 밀려
붙는 사고. 이게 나면 리포트는 조용히 틀린 숫자를 싣는다.
"""

import safestay as ss

# 실제 페이지 구조 그대로 — 광주와 전남이 주석 안에 들어 있는 것이 핵심.
AREA_PERIOD_HTML = """
<table><thead><tr>
  <th>년월</th><th>서울</th><th>부산</th><th>대구</th><th>인천</th>
  <!-- <th>광주</th> -->
  <th>대전</th><th>울산</th><th>세종</th><th>경기</th><th>강원</th>
  <th>충북</th><th>충남</th><th>전북</th>
  <!-- <th>전남</th> -->
  <th>경북</th><th>경남</th><th>제주</th><th>전남광주</th>
</tr></thead><tbody>
  <tr><td>2026.07</td><td>9,689</td><td>1,431</td><td>421</td><td>364</td><td>357</td>
      <td>92</td><td>8</td><td>878</td><td>402</td><td>60</td><td>64</td><td>492</td>
      <td>353</td><td>232</td><td>0</td><td>486</td></tr>
</tbody></table>
"""

TYPE_PERIOD_HTML = """
<table><tr>
  <th>년월</th><th>외국인관광도시민박업</th><th>한옥체험업</th><th>관광펜션업</th><th>호스텔업</th>
</tr><tr>
  <th>운영</th><th>휴업</th><th>폐업</th><th>취소 등</th>
  <th>운영</th><th>휴업</th><th>폐업</th><th>취소 등</th>
  <th>운영</th><th>휴업</th><th>폐업</th><th>취소 등</th>
  <th>운영</th><th>휴업</th><th>폐업</th><th>취소 등</th>
</tr><tr>
  <td>2026.07</td><td>11,957</td><td>65</td><td>3,206</td><td>97</td>
  <td>2,884</td><td>10</td><td>538</td><td>17</td>
  <td>1,312</td><td>5</td><td>266</td><td>50</td>
  <td>1,809</td><td>13</td><td>115</td><td>4</td>
</tr></table>
"""


def test_comments_shift_columns():
    """주석을 지우지 않으면 헤더가 18개로 읽혀 지역이 밀린다 — 그 사고를 재현해 둔다."""
    raw_headers = ss._rows(AREA_PERIOD_HTML)[0]
    assert len(raw_headers) == 19, f"주석 미제거 시 19개여야 재현됨: {len(raw_headers)}"
    assert "광주" in raw_headers, "주석 안의 광주가 그대로 읽히는 상황"

    clean = ss._strip_comments(AREA_PERIOD_HTML)
    headers = ss._rows(clean)[0]
    assert len(headers) == 17, f"주석 제거 후 년월+16지역=17: {len(headers)}"
    assert "광주" not in headers, "주석 처리된 광주는 사라져야 한다"


def test_area_period_maps_regions_correctly():
    got = ss.parse_area_period(ss._strip_comments(AREA_PERIOD_HTML))["2026.07"]
    assert got["서울"] == 9689
    assert got["대전"] == 357, f"대전이 357이 아니면 열이 밀렸다: {got['대전']}"
    assert got["전남광주"] == 486, "병합 컬럼은 맨 끝"
    assert got["제주"] == 0, "제주 미집계 — 값 자체는 0으로 들어온다"
    assert len(got) == 16


def test_header_change_raises():
    """세이프스테이가 주석을 풀면 조용히 틀리는 대신 터져야 한다."""
    tampered = AREA_PERIOD_HTML.replace("<!-- <th>광주</th> -->", "<th>광주</th>")
    try:
        ss.parse_area_period(ss._strip_comments(tampered))
    except ValueError as e:
        assert "지역 헤더" in str(e)
    else:
        raise AssertionError("헤더가 바뀌었는데 통과했다 — 가드가 죽었다")


def test_type_period_splits_status_blocks():
    got = ss.parse_type_period(ss._strip_comments(TYPE_PERIOD_HTML))["2026.07"]
    assert got["외국인관광도시민박업"]["운영"] == 11957
    assert got["외국인관광도시민박업"]["폐업"] == 3206
    assert got["호스텔업"]["운영"] == 1809, "마지막 업종 블록까지 정확히 잘려야 한다"
    assert got["한옥체험업"]["취소 등"] == 17


def test_operating_vs_cumulative_are_different():
    """이 둘을 섞는 것이 이 데이터의 가장 흔한 오독이다."""
    snap = ss.Snapshot(type_period=ss.parse_type_period(ss._strip_comments(TYPE_PERIOD_HTML)))
    assert snap.operating("2026.07") == 11957
    assert snap.cumulative("2026.07") == 11957 + 65 + 3206 + 97 == 15325
    assert snap.cumulative("2026.07") > snap.operating("2026.07")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
