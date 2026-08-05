#!/usr/bin/env python3
"""
district_map.py 자체 점검. `python3 test_district_map.py`(또는 pytest)로 실행. 네트워크 없음.

이 모듈은 실측 QA로만 잡힌 버그가 두 번 있었다 — (1) class=nodata를 따옴표 없이 써서
자기닫힘 태그가 깨지고 뒤따르는 형제 요소가 전부 그 안에 잘못 중첩된 버그, (2) path와
text를 번갈아 그려서 나중 도형이 이전 라벨을 덮어쓴 z-order 버그. 둘 다 브라우저에서
직접 눌러보기 전엔 코드만 봐서는 안 보였다 — 그래서 최소한 "생성된 SVG가 유효한
XML인가"는 자동으로 잡히게 해 둔다(첫 번째 버그의 정확한 회귀 테스트).
"""

import json
import xml.etree.ElementTree as ET

import district_map as dm

# scale=1,translate=0(항등 변환)이라 델타 누적합이 곧 좌표다 — 손으로 검산하기 쉽게.
# arc 0/1/2는 각각 (0,0)-(1,1), (2,0)-(3,1), (4,0)-(5,1) 정사각형(닫힌 링, 첫점=끝점).
FIXTURE_TOPO = {
    "transform": {"scale": [1, 1], "translate": [0, 0]},
    "arcs": [
        [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]],
        [[2, 0], [1, 0], [0, 1], [-1, 0], [0, -1]],
        [[4, 0], [1, 0], [0, 1], [-1, 0], [0, -1]],
    ],
    "objects": {
        "test_districts": {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "MultiPolygon", "arcs": [[[0]]], "properties": {"name": "가상구", "code": "11999"}},
                {"type": "MultiPolygon", "arcs": [[[1]]], "properties": {"name": "예시구", "code": "11998"}},
                {"type": "MultiPolygon", "arcs": [[[2]]], "properties": {"name": "미매칭구", "code": "11997"}},
            ],
        }
    },
}

REGIONS = [
    {"sido": "서울특별시", "sigungu": "가상구"},
    {"sido": "서울특별시", "sigungu": "예시구"},
]


def test_decode_arcs_applies_transform_and_cumsum():
    topo = {"transform": {"scale": [2, 3], "translate": [10, 20]},
            "arcs": [[[1, 1], [2, -1]]]}
    arcs = dm._decode_arcs(topo)
    # 누적합: (1,1)->(3,0). 변환: x*2+10, y*3+20.
    assert arcs == [[(1 * 2 + 10, 1 * 3 + 20), (3 * 2 + 10, 0 * 3 + 20)]]


def test_ring_coords_forward_and_reversed_arc():
    arcs = dm._decode_arcs(FIXTURE_TOPO)
    forward = dm._ring_coords([0], arcs)
    assert forward == arcs[0]
    # 음수 인덱스 ~i는 arc i를 뒤집어서 쓴다(topojson 스펙의 공유 경계 재사용 규칙).
    reversed_ring = dm._ring_coords([~0], arcs)
    assert reversed_ring == list(reversed(arcs[0]))


def test_ring_coords_stitches_and_dedups_shared_point():
    """arc 여러 개를 이어 붙일 때, 뒤 arc의 첫 점(앞 arc의 끝점과 같아야 함)은 버려야
    한다 — 안 버리면 이어 붙인 자리마다 중복 좌표가 생겨 폴리곤이 미묘하게 어긋난다."""
    arcs = [[(0, 0), (1, 0)], [(1, 0), (1, 1)]]
    stitched = dm._ring_coords([0, 1], arcs)
    assert stitched == [(0, 0), (1, 0), (1, 1)]


def test_match_our_name_exact():
    assert dm._match_our_name("마포구", ["마포구", "용산구"]) == "마포구"


def test_match_our_name_prefix_for_split_subdistricts():
    """수원시는 지도에선 자치구 단위(수원시장안구...)로 쪼개져 있는데 우리 데이터는
    "수원시" 하나로 뭉쳐 있다 — 접두어 매칭으로 묶여야 한다."""
    assert dm._match_our_name("수원시장안구", ["수원시", "고양시"]) == "수원시"


def test_match_our_name_no_match_returns_none():
    assert dm._match_our_name("전혀다른이름", ["마포구"]) is None


def test_declutter_labels_separates_overlapping_pair():
    labels = [
        {"x": 100, "y": 100, "w": 20, "h": 10},
        {"x": 102, "y": 100, "w": 20, "h": 10},
    ]
    dm._declutter_labels(labels)
    a, b = labels
    ox = min(a["x"] + a["w"] / 2, b["x"] + b["w"] / 2) - max(a["x"] - a["w"] / 2, b["x"] - b["w"] / 2)
    oy = min(a["y"] + a["h"] / 2, b["y"] + b["h"] / 2) - max(a["y"] - a["h"] / 2, b["y"] - b["h"] / 2)
    assert ox <= 0 or oy <= 0, "겹치는 라벨 쌍은 decluttering 후 겹치지 않아야 한다"


def test_declutter_labels_leaves_non_overlapping_alone():
    labels = [{"x": 0, "y": 0, "w": 10, "h": 10}, {"x": 1000, "y": 1000, "w": 10, "h": 10}]
    dm._declutter_labels(labels)
    assert labels == [{"x": 0, "y": 0, "w": 10, "h": 10}, {"x": 1000, "y": 1000, "w": 10, "h": 10}]


def test_build_district_maps_matches_and_marks_unmatched(tmp_path):
    topo_path = tmp_path / "fixture_topo.json"
    topo_path.write_text(json.dumps(FIXTURE_TOPO), encoding="utf-8")

    maps = dm.build_district_maps(REGIONS, topo_path=topo_path)
    assert "서울특별시" in maps
    root = ET.fromstring(maps["서울특별시"])
    ns = "{http://www.w3.org/2000/svg}"
    paths = {p.get("data-name"): p for p in root.findall(f"{ns}path")}
    assert set(paths) == {"가상구", "예시구", None}, "매칭 안 된 미매칭구는 data-name 없이(클릭 불가) 남아야 한다"
    assert paths[None].get("class") == "nodata"
    # 라벨(text)은 매칭 여부와 무관하게 전부 이름표가 붙는다(선택 시 강조 동기화용) —
    # 클릭 가능 여부를 좌우하는 건 path의 data-name 유무뿐이다.
    texts = {t.get("data-name") for t in root.findall(f"{ns}text")}
    assert texts == {"가상구", "예시구", "미매칭구"}


def test_build_district_maps_single_sigungu_uses_source_map_label(tmp_path):
    """세종처럼 우리 시군구가 하나뿐인 시도는 문자열 매칭 없이 지도 조각 전부를 그
    하나에 묶고, 화면 라벨은 우리 쪽 이상한 값(도로명 등) 대신 지도 자체의 이름을
    쓴다(둘 다 하위 구역 개념이 없어 매칭이 원천적으로 불가능하기 때문)."""
    single_region = [{"sido": "서울특별시", "sigungu": "아무이름"}]
    topo_path = tmp_path / "fixture_topo.json"
    # 이 시도엔 지도 조각이 3개(가상구·예시구·미매칭구) 있지만 우리 시군구는 1개뿐.
    topo_path.write_text(json.dumps(FIXTURE_TOPO), encoding="utf-8")
    maps = dm.build_district_maps(single_region, topo_path=topo_path)
    root = ET.fromstring(maps["서울특별시"])
    ns = "{http://www.w3.org/2000/svg}"
    paths = root.findall(f"{ns}path")
    assert len(paths) == 1, "단일 시군구 시도는 지도 조각(3개) 전부가 path 하나로 합쳐져야 한다"
    assert paths[0].get("data-name") == "아무이름"
    assert root.find(f"{ns}text").text == "서울특별시", "라벨은 우리 쪽 값이 아니라 지도 자체의 이름을 써야 한다"


def test_build_district_maps_skips_sido_without_topo_coverage(tmp_path):
    no_coverage = [{"sido": "제주특별자치도", "sigungu": "제주시"}]
    topo_path = tmp_path / "fixture_topo.json"
    topo_path.write_text(json.dumps(FIXTURE_TOPO), encoding="utf-8")
    maps = dm.build_district_maps(no_coverage, topo_path=topo_path)
    assert maps == {}


def test_render_svg_output_is_well_formed_xml():
    """회귀 테스트: class=nodata를 따옴표 없이 쓰면 자기닫힘이 깨져 뒤 형제 요소가
    전부 그 태그 안에 중첩되는 버그가 있었다(2026-07-30 실측 발견). 매칭 안 된
    도형(nodata)이 하나라도 있으면 재현됐다 — XML로 파싱해 구조가 안 깨졌는지 확인."""
    shapes = [
        ("가상구", [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]], True, "가상구"),
        ("미매칭구", [[(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)]], False, "미매칭구"),
        ("예시구", [[(4, 0), (5, 0), (5, 1), (4, 1), (4, 0)]], True, "예시구"),
    ]
    svg = dm._render_svg(shapes, 480, 480)
    root = ET.fromstring(svg)  # 파싱 자체가 실패하면 태그 구조가 깨졌다는 뜻
    ns = "{http://www.w3.org/2000/svg}"
    paths = root.findall(f"{ns}path")
    texts = root.findall(f"{ns}text")
    assert len(paths) == 3, "path 3개가 서로 안에 중첩되지 않고 svg의 형제로 남아야 한다"
    assert len(texts) == 3
    assert all(t.get("data-name") for t in texts)


def test_render_svg_bbox_ignores_unmatched_far_shapes():
    """인천의 강화군·옹진군처럼 매칭 안 된 도형이 멀리 있으면, 확대 범위를 매칭된
    도형만으로 잡아야 한다(안 그러면 실제 데이터 있는 도심부가 지도 구석에 쪼그라든다,
    2026-07-30 실측 발견)."""
    shapes = [
        ("가상구", [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]], True, "가상구"),
        ("먼섬", [[(1000, 1000), (1001, 1000), (1001, 1001), (1000, 1001), (1000, 1000)]], False, "먼섬"),
    ]
    svg = dm._render_svg(shapes, 480, 480)
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    matched_path = next(p for p in root.findall(f"{ns}path") if p.get("data-name") == "가상구")
    # d 속성의 좌표가 480 캔버스 안에서 유의미한 크기(수백 유닛)를 차지해야 한다 —
    # 먼섬까지 범위에 넣었다면 가상구는 몇 픽셀짜리 점으로 쪼그라들었을 것이다.
    coords = [float(v) for tok in matched_path.get("d").replace("M", "").replace("Z", "").split("L")
              for v in tok.strip().split(",")]
    xs = coords[0::2]
    assert max(xs) - min(xs) > 100, "매칭된 도형이 뷰박스를 채울 만큼 확대돼야 한다"


if __name__ == "__main__":
    # 다른 test_*.py와 달리 여기엔 pytest의 tmp_path 픽스처를 받는 테스트가 섞여 있다 —
    # 그것만 tempfile로 직접 채워준다. 러너가 아예 없던 탓에 `python3 test_district_map.py`가
    # 13개를 하나도 안 돌리고 조용히 exit 0을 내던 걸 8/6에 발견해서 붙였다.
    import inspect
    import tempfile
    from pathlib import Path

    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                needs_tmp = "tmp_path" in inspect.signature(fn).parameters
                fn(**({"tmp_path": Path(d)} if needs_tmp else {}))
            print(f"✅ {name}")
    print("\n통과.")
