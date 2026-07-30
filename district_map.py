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
  - 세종은 우리 데이터도(주소 파싱이 도로명까지 내려가 "만남1북로"로 잡힘), 지도
    데이터도(구획 없이 시 전체가 폴리곤 1개) 의미 있는 하위 구역이 없어 지도 자체를
    만들지 않는다(build_district_maps가 조용히 제외 — 막대 리스트만 제공).
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
    반환: {시도명: SVG 문자열}. 지도 데이터에 유의미한 하위 구역이 없는 시도(세종)는
    아예 빠진다 — estimate.html은 그런 시도엔 막대 리스트만 보여준다.
    """
    if not TOPO_PATH.exists():
        return {}
    topo = json.loads(TOPO_PATH.read_text(encoding="utf-8"))
    arcs = _decode_arcs(topo)
    geoms = topo["objects"][next(iter(topo["objects"]))]["geometries"]

    our_names_by_sido: dict[str, list[str]] = {}
    for r in regions:
        our_names_by_sido.setdefault(r["sido"], []).append(r["sigungu"])

    matched_rings: dict[str, dict[str, list]] = {}
    unmatched_shapes: dict[str, list[tuple[str, list]]] = {}
    for g in geoms:
        sido = CODE_TO_SIDO.get(g["properties"]["code"][:2])
        if sido not in our_names_by_sido:
            continue
        rings = [_ring_coords(ring, arcs) for poly in g["arcs"] for ring in poly]
        matched = _match_our_name(g["properties"]["name"], our_names_by_sido[sido])
        if matched:
            matched_rings.setdefault(sido, {}).setdefault(matched, []).extend(rings)
        else:
            unmatched_shapes.setdefault(sido, []).append((g["properties"]["name"], rings))

    out = {}
    for sido in our_names_by_sido:
        mgroups = matched_rings.get(sido, {})
        if len(mgroups) < 2:  # 세종처럼 하위 구역이 사실상 없으면 지도 자체를 스킵
            continue
        shapes = [(name, rings, True) for name, rings in mgroups.items()]
        shapes += [(name, rings, False) for name, rings in unmatched_shapes.get(sido, [])]
        out[sido] = _render_svg(shapes, width, height)
    return out


def _render_svg(shapes: list[tuple[str, list, bool]], width: int, height: int) -> str:
    """
    확대 범위는 클릭 가능한(매칭된) 시군구 좌표만으로 잡는다 — 인천처럼 매칭 안 되는
    행정구역이 강화군·옹진군 같은 먼 섬이면, 그걸 포함해 범위를 잡는 순간 정작 우리
    데이터가 있는 도심부가 지도 한구석에 작게 몰린다(실측으로 발견). 매칭 안 된
    구역은 이 범위 밖으로 잘려도 상관없다 — 어차피 클릭도 안 되는 참고용 회색 영역.
    """
    pad = 8
    bbox_shapes = [s for s in shapes if s[2]] or shapes
    all_pts = [pt for _, rings, _ in bbox_shapes for ring in rings for pt in ring]
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

    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    for name, rings, clickable in shapes:
        d = "".join(
            "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (proj(lon, lat) for lon, lat in ring)) + "Z "
            for ring in rings
        )
        attr = f' data-name="{name}"' if clickable else " class=nodata"
        parts.append(f'<path d="{d}"{attr}/>')
        largest = max(rings, key=len)
        xs = [proj(lon, lat)[0] for lon, lat in largest]
        ys = [proj(lon, lat)[1] for lon, lat in largest]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        parts.append(f'<text x="{cx:.1f}" y="{cy:.1f}">{name}</text>')
    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    import build_site
    d = build_site.gather()
    regions = d.current.flagship.regional_stats()
    maps = build_district_maps(regions)
    for sido, svg in maps.items():
        print(f"  {sido}: {len(svg):,} bytes")
    print(f"총 {len(maps)}개 시도 지도 생성, 합계 {sum(len(s) for s in maps.values()):,} bytes")
