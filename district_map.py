#!/usr/bin/env python3
"""
시도별 시군구 경계 지도(SVG) 생성기 — estimate.html "시도 내 시군구 순위" 시각화용.

assets/kr_sigungu_topo.json 은 southkorea/southkorea-maps(GitHub)의
kostat/2018/json/skorea-municipalities-2018-topo-simple.json 을 그대로 받아온 것이다.
원출처는 통계청 통계지리정보서비스(SGIS), 공공누리 제1유형(출처표시만 하면 상업적
이용·변형·재배포 자유) 라이선스로 2018-12-24 수집. TopoJSON 파서(topojson, shapely
등)를 새 의존성으로 추가하는 대신 스펙대로 직접 디코딩한다 — 델타 인코딩된 정수
좌표를 arc 단위로 누적합하고 transform(scale/translate)을 곱해 WGS84 좌표로 복원하는
정도라 라이브러리 없이도 충분하다.

시도 코드 접두어는 행정표준코드(11=서울...)가 아니라 이 데이터셋 자체의 순번이다
(실측으로 확인해 매핑, CODE_TO_SIDO). "전남광주통합특별시"는 build_site.py의
SIDO_TO_KR_CODE와 같은 이유로 광주(24)+전남(36) 두 접두어를 하나로 묶는다 — 원본
행정안전부 데이터가 두 시도를 한 라벨로 합쳐서 내려주는 소스 쪽 특이사항.

시군구 이름 매칭이 완벽하지 않다:
  - 수원시·고양시·성남시 등은 이 지도가 자치구 단위(수원시장안구...)로 쪼개져
    있는데 우리 데이터(주소 2번째 토큰 파싱, localdata.parse_region)는 "수원시"
    하나로 뭉쳐 있다. 자치구 폴리곤 여러 개를 같은 data-name으로 묶어 하나처럼
    강조되게 한다 — 폴리곤 병합(shapely 등 필요) 대신 "같은 속성값 공유"로 대체,
    시각적으로는 충분하고 새 의존성이 필요 없다.
  - 인천은 2026년 행정구역 개편(영종구·검단구 신설, 제물포구=중구+동구 통합 등)이
    2018년 지도에 반영이 안 돼 있어 상당수가 매칭 실패한다. 매칭 안 되는 시군구는
    지도에서 회색·클릭 불가로 남는다(막대 리스트는 원본 데이터 그대로라 정상 작동).
  - 세종은 우리 데이터(주소 파싱이 도로명까지 내려가 "만남1북로"로 잡힘)도 지도
    데이터(구획 없이 시 전체가 폴리곤 1개)도 하위 구역이 없어 문자열 매칭이 애초에
    불가능하다 — "우리 시군구 목록이 하나뿐인 시도는 지도 조각을 전부 그 하나에
    묶는다"는 규칙으로 처리한다(이름이 아니라 "선택지가 하나뿐"이라는 사실로 매칭).
    지도 위 표시 라벨은 그 도로명 대신 지도 데이터의 원래 이름("세종시")을 쓴다.

밀집 지역(서울 종로구·성북구, 경기 성남시·의왕시 등)은 작은 시군구가 다닥다닥
붙어 있어 라벨이 서로 겹친다(실측 확인 — 겹치면 글자가 뭉개져 "잘려 보이는" 것처럼
읽힌다). _declutter_labels()가 겹치는 라벨 쌍을 반복적으로 밀어내 해결한다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
TOPO_PATH = ASSETS / "kr_sigungu_topo.json"

CODE_TO_SIDO = {
    "11": "서울특별시", "21": "부산광역시", "22": "대구광역시", "23": "인천광역시",
    "24": "전남광주통합특별시", "25": "대전광역시", "26": "울산광역시", "29": "세종특별자치시",
    "31": "경기도", "32": "강원특별자치도", "33": "충청북도", "34": "충청남도",
    "35": "전북특별자치도", "36": "전남광주통합특별시", "37": "경상북도", "38": "경상남도",
    "39": "제주특별자치도",
}


def _decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def _ring_coords(arc_idx_list: list[int], arcs: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for idx in arc_idx_list:
        pts = arcs[idx] if idx >= 0 else list(reversed(arcs[~idx]))
        coords.extend(pts[1:] if coords else pts)
    return coords


def _match_our_name(topo_name: str, our_names: list[str]) -> str | None:
    """정확히 같거나(대부분), 우리 이름으로 시작하면(수원시장안구 -> 수원시) 매칭."""
    if topo_name in our_names:
        return topo_name
    for n in our_names:
        if topo_name.startswith(n):
            return n
    return None


def build_district_maps(regions: list[dict], width: int = 480, height: int = 480) -> dict[str, str]:
    """
    regions: CategoryStats.regional_stats() 결과(전국 시도·시군구 목록).
    반환: {시도명: SVG 문자열}. 지도 데이터 자체가 없는 시도(현재는 제주뿐 — 커버리지
    밖)는 빠진다 — estimate.html은 그런 시도엔 막대 리스트만 보여준다.
    """
    if not TOPO_PATH.exists():
        return {}
    topo = json.loads(TOPO_PATH.read_text(encoding="utf-8"))
    arcs = _decode_arcs(topo)
    geoms = topo["objects"][next(iter(topo["objects"]))]["geometries"]

    our_names_by_sido: dict[str, list[str]] = {}
    for r in regions:
        our_names_by_sido.setdefault(r["sido"], []).append(r["sigungu"])

    topo_by_sido: dict[str, list[tuple[str, list]]] = {}
    for g in geoms:
        sido = CODE_TO_SIDO.get(g["properties"]["code"][:2])
        if sido not in our_names_by_sido:
            continue
        rings = [_ring_coords(ring, arcs) for poly in g["arcs"] for ring in poly]
        topo_by_sido.setdefault(sido, []).append((g["properties"]["name"], rings))

    out = {}
    for sido, our_names in our_names_by_sido.items():
        topo_shapes = topo_by_sido.get(sido, [])
        if not topo_shapes:
            continue

        if len(our_names) == 1:
            # 세종 같은 단일 시군구 시도 — 문자열 매칭이 불가능하니 지도 조각을
            # 전부 그 하나뿐인 시군구 값에 묶는다. 화면 라벨은 지도 데이터의
            # 원래 이름을 쓴다(우리 쪽 도로명보다 훨씬 읽기 쉽다).
            all_rings = [r for _, rings in topo_shapes for r in rings]
            topo_names = {n for n, _ in topo_shapes}
            label = next(iter(topo_names)) if len(topo_names) == 1 else sido
            shapes = [(our_names[0], all_rings, True, label)]
            out[sido] = _render_svg(shapes, width, height)
            continue

        matched: dict[str, list] = {}
        unmatched: list[tuple[str, list]] = []
        for topo_name, rings in topo_shapes:
            m = _match_our_name(topo_name, our_names)
            if m:
                matched.setdefault(m, []).extend(rings)
            else:
                unmatched.append((topo_name, rings))
        if not matched:
            continue
        shapes = [(name, rings, True, name) for name, rings in matched.items()]
        shapes += [(name, rings, False, name) for name, rings in unmatched]
        out[sido] = _render_svg(shapes, width, height)
    return out


def _declutter_labels(labels: list[dict], iterations: int = 150) -> None:
    """
    labels의 x,y를 제자리에서 조정한다. 겹치는 라벨 쌍을 찾아, 겹침이 더 적은 축
    (가로/세로 중) 방향으로 반씩 밀어낸다 — 몇 번 반복하면 밀집 지역도 수렴한다.
    """
    for _ in range(iterations):
        moved = False
        for i in range(len(labels)):
            a = labels[i]
            ax0, ax1 = a["x"] - a["w"] / 2, a["x"] + a["w"] / 2
            ay0, ay1 = a["y"] - a["h"] / 2, a["y"] + a["h"] / 2
            for j in range(i + 1, len(labels)):
                b = labels[j]
                bx0, bx1 = b["x"] - b["w"] / 2, b["x"] + b["w"] / 2
                by0, by1 = b["y"] - b["h"] / 2, b["y"] + b["h"] / 2
                ox = min(ax1, bx1) - max(ax0, bx0)
                oy = min(ay1, by1) - max(ay0, by0)
                if ox <= 0 or oy <= 0:
                    continue
                moved = True
                if ox < oy:
                    shift = ox / 2 + 0.3
                    if a["x"] <= b["x"]:
                        a["x"] -= shift; b["x"] += shift
                    else:
                        a["x"] += shift; b["x"] -= shift
                else:
                    shift = oy / 2 + 0.3
                    if a["y"] <= b["y"]:
                        a["y"] -= shift; b["y"] += shift
                    else:
                        a["y"] += shift; b["y"] -= shift
                ax0, ax1 = a["x"] - a["w"] / 2, a["x"] + a["w"] / 2
                ay0, ay1 = a["y"] - a["h"] / 2, a["y"] + a["h"] / 2
        if not moved:
            break


def _render_svg(shapes: list[tuple[str, list, bool, str]], width: int, height: int) -> str:
    """
    확대 범위는 클릭 가능한(매칭된) 시군구 좌표만으로 잡는다 — 인천처럼 매칭 안 되는
    행정구역이 강화군·옹진군 같은 먼 섬이면, 그걸 포함해 범위를 잡는 순간 정작 우리
    데이터가 있는 도심부가 지도 한구석에 작게 몰린다(실측으로 발견). 매칭 안 된
    구역은 이 범위 밖으로 잘려도 상관없다 — 어차피 클릭도 안 되는 참고용 회색 영역.
    """
    pad = 8
    bbox_shapes = [s for s in shapes if s[2]] or shapes
    all_pts = [pt for _, rings, _, _ in bbox_shapes for ring in rings for pt in ring]
    lons = [p[0] for p in all_pts]
    lats = [p[1] for p in all_pts]
    lon0, lon1 = min(lons), max(lons)
    lat0, lat1 = min(lats), max(lats)
    cos_lat = math.cos(math.radians((lat0 + lat1) / 2))
    w_span = max((lon1 - lon0) * cos_lat, 1e-9)
    h_span = max(lat1 - lat0, 1e-9)
    scale = min((width - 2 * pad) / w_span, (height - 2 * pad) / h_span)

    def proj(lon: float, lat: float) -> tuple[float, float]:
        return (lon - lon0) * cos_lat * scale + pad, (lat1 - lat) * scale + pad

    # path_strs[i]와 labels[i]는 같은 시군구를 가리킨다 — 아래서 짝지어 번갈아 출력해야
    # CSS의 인접 형제 선택자(path.sel+text, 선택된 시군구만 글자색을 반전시키는 데 씀)가
    # 맞는 <text>를 집어낼 수 있다.
    path_strs = []
    labels = []
    for name, rings, clickable, label in shapes:
        d = "".join(
            "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (proj(lon, lat) for lon, lat in ring)) + "Z "
            for ring in rings
        )
        attr = f' data-name="{name}"' if clickable else " class=nodata"
        path_strs.append(f'<path d="{d}"{attr}/>')

        largest = max(rings, key=len)
        xs = [proj(lon, lat)[0] for lon, lat in largest]
        ys = [proj(lon, lat)[1] for lon, lat in largest]
        shape_w, shape_h = max(xs) - min(xs), max(ys) - min(ys)
        # 아주 작은 시군구엔 큰 글자가 어울리지 않고 옆 라벨과도 덜 겹친다 — 모양 크기에
        # 비례해 6.5~9px 사이로 조정(실측으로 이 범위가 자연스러웠다).
        font_size = max(6.5, min(9.0, min(shape_w, shape_h) * 0.5))
        labels.append({
            "text": label, "x": (min(xs) + max(xs)) / 2, "y": (min(ys) + max(ys)) / 2,
            "w": len(label) * font_size * 0.95, "h": font_size * 1.1, "fs": font_size,
        })

    _declutter_labels(labels)
    parts = []
    for path_str, lb in zip(path_strs, labels):
        x = min(max(lb["x"], lb["w"] / 2 + 1), width - lb["w"] / 2 - 1)
        y = min(max(lb["y"], pad), height - pad)
        parts.append(path_str)
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{lb["fs"]:.1f}">{lb["text"]}</text>')

    return f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">' + "".join(parts) + "</svg>"


if __name__ == "__main__":
    import build_site
    d = build_site.gather()
    regions = d.current.flagship.regional_stats()
    maps = build_district_maps(regions)
    for sido, svg in maps.items():
        print(f"  {sido}: {len(svg):,} bytes")
    print(f"총 {len(maps)}개 시도 지도 생성, 합계 {sum(len(s) for s in maps.values()):,} bytes")
