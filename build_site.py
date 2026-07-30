#!/usr/bin/env python3
"""
공유숙박 마켓리포트 — 정적 웹사이트 생성기.

  python build_site.py     # site/ 아래 전체 페이지 생성

파일명이 site.py 가 아니라 build_site.py 인 이유: 파이썬은 인터프리터 시작 시
표준 라이브러리 site 모듈을 항상 먼저 sys.modules 에 올려둔다. 이 모듈을
site.py 로 두면 다른 스크립트에서 `import site` 하는 순간(직접이든 간접이든)
표준 라이브러리 쪽이 잡혀 이 파일은 조용히 무시된다 — 실제로 디버깅 중 이 이름
때문에 dataclass 가 이상한 자리에서 죽는 걸 겪었다.

핵심 지표(등록 현황·구별 순위·카테고리 비교)는 k-stay API 를 거치지 않는다.
k-stay 가 크롤링해오는 원천(file.localdata.go.kr)에서 이제 우리가 직접
받아 우리가 집계한다(localdata.py, 2026-07-28 전환). 남은 k-stay 의존은
kstay.fetch_inbound() 하나뿐 — 인바운드 관광객 통계는 k-stay 카탈로그에도
"manual"(KTO 데이터랩+법무부 통계연보를 손으로 큐레이션)로 표시돼 있어
애초에 "크롤링해오는 곳"이 없다.

k-stay API 를 안 쓰게 되면서 잃은 것 하나: k-stay가 이미 발행한 2026-05·06
두 호는 그쪽 집계 로직(비공개)으로 만든 숫자라 우리 파이프라인과 판정 기준이
다르다. 서로 다른 방식으로 만든 숫자를 같은 시계열에 섞어 archive를 이어붙이면
나중에 그래프가 이유 없이 꺾인 것처럼 보인다 — 그래서 이어붙이지 않고 오늘부터
새로 시작한다. history/ 아래 이번 실행 스냅샷을 매번 쌓아 자체 아카이브를
만든다(month 단위, 같은 달 재실행은 덮어씀).
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

import district_map
import localdata
import news
import regulation
import safestay
import tourism_demand
import viz
import yanolja_perf
from kstay import fetch_inbound  # 인바운드만 — 등록 통계는 더 이상 kstay API에 안 기댐

load_dotenv()

ROOT = Path(__file__).parent
SITE = ROOT / "site"
HISTORY = ROOT / "history"
ASSETS = ROOT / "assets"
TITLE = "공유숙박 마켓리포트"
# 구독 폼이 실제로 값을 보내는 곳 — .env 에서 읽는다. ngrok 무료 플랜은 터널을
# 새로 열 때마다 URL이 바뀌므로, 코드를 고치는 대신 .env의 SUBSCRIBE_ENDPOINT만
# 갈아끼우고 build_site.py 를 다시 돌리면 된다.
SUBSCRIBE_ENDPOINT = os.getenv("SUBSCRIBE_ENDPOINT", "http://localhost:5055/subscribe")
CATEGORY_ORDER = ["foreigner_city_homestays", "hanok_experience", "tourist_pensions",
                   "tourist_accommodations", "rural_homestays"]
SEOUL, BUSAN = "서울특별시", "부산광역시"


# ─────────────────────────────────────────────────────── 스냅샷 · 아카이브

def snapshot_path(ym: str) -> Path:
    return HISTORY / f"{ym}.json"


def save_snapshot(ym: str, categories: dict[str, localdata.CategoryStats]) -> None:
    HISTORY.mkdir(exist_ok=True)
    payload = {
        "ym": ym,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "categories": {slug: asdict(s) for slug, s in categories.items()},
    }
    snapshot_path(ym).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def load_snapshot(path: Path) -> tuple[str, dict[str, localdata.CategoryStats]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cats = {slug: localdata.CategoryStats(**v) for slug, v in data["categories"].items()}
    return data["ym"], cats


def load_all_snapshots() -> list[tuple[str, dict[str, localdata.CategoryStats]]]:
    """ym 내림차순(최신 먼저)."""
    if not HISTORY.exists():
        return []
    out = [load_snapshot(p) for p in HISTORY.glob("*.json")]
    return sorted(out, key=lambda t: t[0], reverse=True)


# ─────────────────────────────────────────────────────── 데이터 · 지표

@dataclass
class Issue:
    """월간 리포트 한 호. 스냅샷 하나에서 유도한 지표 뷰."""
    ym: str
    categories: dict[str, localdata.CategoryStats]

    @property
    def flagship(self) -> localdata.CategoryStats:
        return self.categories[localdata.FLAGSHIP]

    @property
    def seoul_active(self) -> int:
        return sum(c for _, c in self.flagship.district_rank(SEOUL))

    @property
    def seoul_share(self) -> float:
        return self.seoul_active / self.flagship.active if self.flagship.active else 0.0

    def concentration(self, n: int = 3) -> float:
        top = sum(c for _, c in self.flagship.district_rank(SEOUL, n))
        return top / self.seoul_active if self.seoul_active else 0.0


@dataclass
class SiteData:
    current: Issue
    previous: Issue | None
    all_issues: list[Issue]
    inbound: dict
    reg_items: list[regulation.Item]
    reg_bills: dict[str, list[regulation.BillMatch]]
    news_items: list[regulation.Item]
    reconcile_note: str
    perf: dict[str, dict]
    demand: dict[str, dict]


def gather() -> SiteData:
    ym = date.today().strftime("%Y-%m")
    print(f"등록 데이터 직접 수집 중 (file.localdata.go.kr, {len(localdata.CATEGORIES)}종)...")
    categories = localdata.collect()
    save_snapshot(ym, categories)

    reg = regulation.collect()
    print("산업·수요 뉴스 수집 중...")
    all_news = news.collect()
    ss = safestay.collect()
    print("야놀자리서치 실적 지표(ADR·OCC·RevPAR) 수집 중...")
    perf = yanolja_perf.collect()
    print(f"  {len(perf)}/{len(yanolja_perf.REGIONS)}개 권역")
    print("한국관광 데이터랩 관광 수요 지표 수집 중...")
    demand = tourism_demand.collect()
    print(f"  {len(demand)}/{len(tourism_demand.METRICS)}개 지표")
    flagship_active = categories[localdata.FLAGSHIP].active
    ss_active = ss.operating(ss.months[0])
    gap = ss_active - flagship_active
    note = (f"직접 수집(행안부 원본) {flagship_active:,}곳 vs 한국관광공사 세이프스테이 "
            f"{ss_active:,}곳, 격차 {gap:+,}곳({gap / max(flagship_active,1):+.1%}). "
            "집계 시점과 영업상태 판정 기준이 달라 생기는 차이로, 어느 한쪽을 단일 진실로 쓰지 않는다.")

    all_snaps = [(ym, categories)] + [s for s in load_all_snapshots() if s[0] != ym]
    all_issues = [Issue(y, c) for y, c in sorted(all_snaps, key=lambda t: t[0], reverse=True)]

    return SiteData(
        current=all_issues[0],
        previous=all_issues[1] if len(all_issues) > 1 else None,
        all_issues=all_issues,
        inbound=fetch_inbound(),
        reg_items=reg["items"],
        reg_bills=reg["bills"],
        news_items=all_news,
        reconcile_note=note,
        perf=perf,
        demand=demand,
    )


def mom_delta(cur: Issue, prev: Issue | None) -> int | None:
    return None if prev is None else cur.flagship.active - prev.flagship.active


# ─────────────────────────────────────────────────────── 차트

def chart_registrations_trend(monthly: list[tuple[str, int]]) -> str:
    import matplotlib.pyplot as plt
    months, counts = zip(*monthly)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    colors = [viz.MINT if i == len(counts) - 1 else viz.NAVY for i in range(len(counts))]
    ax.bar(months, counts, color=colors, width=0.68)
    ax.set_title("외국인관광 도시민박업 월별 신규등록 추이 (24개월)", fontsize=13, weight="bold", pad=14)
    ax.tick_params(axis="x", rotation=60, labelsize=8)
    ax.grid(axis="y", alpha=.25)
    viz.strip_spines(ax)
    fig.tight_layout()
    return viz.to_png(fig)


def chart_cohort_survival(cohort_survival: dict[str, dict[str, float]]) -> str:
    """
    등록연도 코호트별 생존곡선. localdata.CategoryStats.cohort_survival은 이미
    우변절단(right-censoring)을 반영한 값이다 — 아직 폐업 안 한 곳을 폐업으로
    안 세고, 그 나이까지 살아있었다는 사실만 분모에 반영한다. "폐업 건만 보고 낸
    존속기간 중앙값"(생존편향)과 달리 실제 생존율을 보여주는 게 이 차트의 핵심.

    코호트가 12~15개라 각각 범례를 달면 못 읽는다 — 등록연도 오래된 순(NAVY)
    ->최근(MINT) 그러데이션으로 색만 바꾸고, 선 끝에 연도를 직접 라벨링해서
    범례 상자 없이도 어떤 선이 몇 년도인지 읽히게 한다.
    """
    import matplotlib.pyplot as plt
    years = sorted(cohort_survival, key=int)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    if not years:
        ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center", transform=ax.transAxes)
    n = len(years)
    end_points = []  # (year, x_end, y_true, color) — 선 다 그린 뒤 한 번에 라벨 배치(겹침 방지)
    for i, year in enumerate(years):
        curve = cohort_survival[year]
        ts = sorted((int(t) for t in curve), key=int)
        vals = [curve[str(t)] * 100 for t in ts]
        color = viz.NAVY if n == 1 else _lerp_color(viz.NAVY, viz.MINT, i / (n - 1))
        ax.plot(ts, vals, color=color, linewidth=1.6)
        end_points.append((year, ts[-1], vals[-1], color))

    # 오른쪽 끝에서 값이 비슷한 코호트가 여럿이면 라벨이 겹친다(실측 확인) — y값 오름차순으로
    # 훑으면서 이전 라벨과 min_gap(percentage point) 미만이면 그만큼 밀어 올린다. 원래 값(y_true)과
    # 라벨 위치(y_label)가 밀렸으면 가는 선으로 이어줘서 어떤 선인지 헷갈리지 않게 한다.
    min_gap = 3.2
    end_points.sort(key=lambda p: p[2])
    y_label = None
    for year, x, y_true, color in end_points:
        y_label = y_true if y_label is None else max(y_label + min_gap, y_true)
        if abs(y_label - y_true) > 0.5:
            ax.plot([x, x + 0.15], [y_true, y_label], color=color, linewidth=.6, alpha=.6)
        ax.annotate(year, (x + 0.15, y_label), textcoords="offset points", xytext=(2, 0),
                     fontsize=7.5, color=color, va="center")

    ax.set_title("등록연도 코호트별 생존곡선 (우변절단 반영)", fontsize=13, weight="bold", pad=14)
    ax.set_xlabel("등록 후 경과연수", fontsize=9)
    ax.set_ylabel("생존율(%)", fontsize=9)
    ax.set_ylim(0, 105)
    if end_points:
        ax.set_xlim(right=max(p[1] for p in end_points) + 1.3)  # 연도 라벨 들어갈 여백
    ax.grid(axis="y", alpha=.25)
    viz.strip_spines(ax)
    fig.tight_layout()
    return viz.to_png(fig)


def chart_district_rank(flagship: localdata.CategoryStats, sido: str = SEOUL, top_n: int = 15) -> str:
    import matplotlib.pyplot as plt
    top = flagship.district_rank(sido, top_n)[::-1]
    fig, ax = plt.subplots(figsize=(7, 5.2))
    colors = ([viz.NAVY] * (len(top) - 1) + [viz.MINT])
    ax.barh([g for g, _ in top], [c for _, c in top], color=colors, height=.66)
    for i, (_, c) in enumerate(top):
        ax.annotate(f"{c:,}", (c, i), textcoords="offset points", xytext=(6, -4),
                    fontsize=9, weight="bold")
    ax.set_title(f"서울 자치구별 영업중 호스트 TOP {top_n}", fontsize=13, weight="bold", pad=14)
    ax.grid(axis="x", alpha=.25)
    viz.strip_spines(ax, keep=())
    fig.tight_layout()
    return viz.to_png(fig)


def chart_saturation_scatter(sat: list[tuple[str, int, int, float]]) -> str:
    """
    포화 신호를 표 대신 산점도로 — x=밀도(영업중, 로그스케일)·y=최근 증감률로 4분면을
    만들면 "우리 구가 어느 칸인가"가 표를 스캔하는 것보다 한눈에 들어온다.
    표(saturation_signal 상위 8개)와 달리 min_active를 넘는 구를 전부 그린다 —
    산점도는 점이 많아도 읽는 비용이 표보다 안 늘어난다.

    사분면 경계: x는 표시된 구들의 밀도 중앙값, y는 증감률 0%(성장/위축 갈림).
    growth가 inf인 경우(직전 6개월 신규가 0건이라 나눗셈이 안 되는 경우)는
    그래프가 깨지므로 빼고 각주로 몇 곳 빠졌는지만 밝힌다.
    """
    import statistics

    import matplotlib.pyplot as plt

    finite = [(gu, active, g) for gu, active, _, g in sat if g != float("inf")]
    skipped = len(sat) - len(finite)
    fig, ax = plt.subplots(figsize=(8, 6))
    if not finite:
        ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center", transform=ax.transAxes)
        fig.tight_layout()
        return viz.to_png(fig)

    actives = [a for _, a, _ in finite]
    growths = [g * 100 for _, _, g in finite]
    x_mid = statistics.median(actives)

    ax.scatter(actives, growths, s=46, color=viz.NAVY, alpha=.85, zorder=3)
    for gu, active, g in finite:
        ax.annotate(gu.removesuffix("구"), (active, g * 100), fontsize=7.5,
                    textcoords="offset points", xytext=(5, 4), color=viz.NAVY)

    ax.axhline(0, color=viz.GREY, linewidth=1, zorder=1)
    ax.axvline(x_mid, color=viz.GREY, linewidth=1, zorder=1)
    ax.set_xscale("log")
    corner = dict(fontsize=10, weight="bold", color=viz.GREY, alpha=.85)
    y0, y1 = ax.get_ylim()
    x0, x1 = ax.get_xlim()
    ax.text(x1 * .75, y1 * .92, "성장", ha="center", **corner)
    ax.text(x1 * .75, y0 * .92, "포화", ha="center", **corner)
    ax.text(x0 * 1.4, y1 * .92, "기회", ha="center", **corner)
    ax.text(x0 * 1.4, y0 * .92, "침체", ha="center", **corner)

    title = "포화 신호 산점도 — 밀도 vs 최근 6개월 증감률"
    if skipped:
        title += f" ({skipped}개 구는 직전 6개월 신규 0건이라 증감률 계산 불가로 제외)"
    ax.set_title(title, fontsize=12, weight="bold", pad=14)
    ax.set_xlabel("영업중 호스트 수(로그스케일)", fontsize=9)
    ax.set_ylabel("직전 6개월 대비 증감률(%)", fontsize=9)
    ax.grid(alpha=.2)
    viz.strip_spines(ax)
    fig.tight_layout()
    return viz.to_png(fig)


# localdata.py의 시도명(주소 파싱 결과) -> assets/kr_sido.svg의 path id(ISO 3166-2:KR).
# simplemaps.com 무료 SVG(17개 시도, id=KR11~KR50, name=영문명) — 라이선스: 상업·개인
# 무료 이용 가능, 재배포 전용 컬렉션화만 금지(https://simplemaps.com/resources/svg-license).
# "전남광주통합특별시"는 원본 데이터의 광주+전남 통합 표기라 두 지역(KR29·KR46)에
# 동일 값을 반영한다 — 실제 행정구역이 갈리면 이 매핑도 갈라야 한다.
SIDO_TO_KR_CODE: dict[str, list[str]] = {
    "서울특별시": ["KR11"], "부산광역시": ["KR26"], "대구광역시": ["KR27"],
    "인천광역시": ["KR28"], "광주광역시": ["KR29"], "대전광역시": ["KR30"],
    "울산광역시": ["KR31"], "경기도": ["KR41"], "강원특별자치도": ["KR42"],
    "충청북도": ["KR43"], "충청남도": ["KR44"], "전북특별자치도": ["KR45"],
    "전라남도": ["KR46"], "경상북도": ["KR47"], "경상남도": ["KR48"],
    "제주특별자치도": ["KR49"], "세종특별자치시": ["KR50"],
    "전남광주통합특별시": ["KR29", "KR46"],
}


def _lerp_color(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r, g, b = (round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t), round(b1 + (b2 - b1) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


def _svg_choropleth(values: dict[str, float], titles: dict[str, str],
                     lo_color: str = "#E3ECE9", hi_color: str = viz.NAVY,
                     log_scale: bool = True) -> str:
    """
    assets/kr_sido.svg(simplemaps.com 무료 시도 SVG)를 읽어 values에 있는 코드만
    lo_color~hi_color 그러데이션으로 칠하고 titles를 호버 툴팁으로 얹는다. 두 지도
    (render_sido_map, render_revpar_map)가 공유하는 렌더러 — 값의 의미(호스트 수·
    RevPAR)만 다르고 "SVG 읽어서 칠하기"는 같은 로직이라 여기 하나로 모았다.
    values에 없는 코드는 회색 "데이터 없음"으로 조용히 넘어간다.
    """
    present = list(values.values())
    if log_scale:
        present = [v for v in present if v > 0]
    lo, hi = (math.log1p(min(present)), math.log1p(max(present))) if (log_scale and present) \
        else (min(present), max(present)) if present else (0, 0)

    def repl(m: re.Match) -> str:
        code, eng_name = m.group("id"), m.group("name")
        if code in values:
            v = values[code]
            scaled = math.log1p(v) if log_scale else v
            t = (scaled - lo) / (hi - lo) if hi > lo else 1.0
            fill = _lerp_color(lo_color, hi_color, t)
            title = titles.get(code, f"{eng_name} {v:,.0f}")
        else:
            fill = "#E6E9EE"
            title = titles.get(code, f"{eng_name} 데이터 없음")
        return (f'<path d="{m.group("d")}" id="{code}" name="{eng_name}" '
                f'fill="{fill}"><title>{title}</title></path>')

    svg = (ASSETS / "kr_sido.svg").read_text(encoding="utf-8")
    return re.sub(
        r'<path\s+d="(?P<d>[^"]*)"\s+id="(?P<id>KR\d+)"\s+name="(?P<name>[^"]+)">\s*</path>',
        repl, svg)


def render_sido_map(flagship: localdata.CategoryStats) -> str:
    """
    전국 시도별 영업중 호스트 수를 단계구분도(choropleth)로 — chart_sido_rank()의
    막대그래프와 달리 지리적 분포(수도권 집중 등)가 한눈에 들어온다. 둘은 서로
    대체가 아니라 보완 관계라 대시보드에 같이 둔다(지도=어디, 막대=얼마나·정확한
    순위). 매핑에 없는 시도명이 나오면(행정구역 개편 등) 조용히 빠뜨리는 대신
    경고를 남긴다.
    """
    ranks = dict(flagship.sido_rank())
    values, titles = {}, {}
    for sido, count in ranks.items():
        codes = SIDO_TO_KR_CODE.get(sido)
        if not codes:
            print(f"  ⚠️ 지도 매핑 없음: {sido} — SIDO_TO_KR_CODE 갱신 필요")
            continue
        for code in codes:
            values[code] = count
            titles[code] = f"{sido} 영업중 {count:,}곳"
    return _svg_choropleth(values, titles)


def render_revpar_map(perf: dict[str, dict]) -> str:
    """
    야놀자리서치 RevPAR(객실당매출)을 지도로. 숙박업 실적 지표 표는 ADR·OCC·RevPAR
    3개 값을 다 보여주지만 지도는 색 하나로 값 하나만 표현할 수 있어, 셋 중 가장
    종합적인(가격×점유율) RevPAR만 고른다 — 나머지는 표에서 봐야 한다. 표=정확한
    3개 수치, 지도=어느 권역이 실속 있는지 한눈에. "전국" 합계는 특정 지역이
    아니라 지도에 칠할 수 없어 뺀다. 대구·대전·인천·울산·세종은 야놀자리서치
    권역 자체가 없어(yanolja_perf.SIDO_TO_REGION 참고) 데이터 없음으로 남는다.
    """
    code_to_region: dict[str, str] = {}
    for sido, region in yanolja_perf.SIDO_TO_REGION.items():
        for code in SIDO_TO_KR_CODE.get(sido, []):
            code_to_region[code] = region

    values, titles = {}, {}
    for code, region in code_to_region.items():
        stats = perf.get(region)
        if not stats:
            continue
        values[code] = stats["revpar"]
        titles[code] = f"{region} 권역 RevPAR {stats['revpar']:,.0f}원"
    return _svg_choropleth(values, titles, log_scale=False)


def chart_sido_rank(flagship: localdata.CategoryStats, top_n: int = 17) -> str:
    """전국 시도별 영업중 호스트 순위 — district_rank가 서울 안에서만 도는 것과 달리 전국 커버."""
    import matplotlib.pyplot as plt
    top = flagship.sido_rank(top_n)[::-1]
    fig, ax = plt.subplots(figsize=(7, 5.2))
    colors = ([viz.NAVY] * (len(top) - 1) + [viz.MINT])
    ax.barh([s for s, _ in top], [c for _, c in top], color=colors, height=.66)
    for i, (_, c) in enumerate(top):
        ax.annotate(f"{c:,}", (c, i), textcoords="offset points", xytext=(6, -4),
                    fontsize=9, weight="bold")
    ax.set_title(f"전국 시도별 영업중 호스트 TOP {top_n}", fontsize=13, weight="bold", pad=14)
    ax.grid(axis="x", alpha=.25)
    viz.strip_spines(ax, keep=())
    fig.tight_layout()
    return viz.to_png(fig)


def chart_category_compare(categories: dict[str, localdata.CategoryStats]) -> str:
    """5종 카테고리 규모 비교. 농어촌민박이 압도적으로 커서 로그스케일."""
    import matplotlib.pyplot as plt
    items = [(categories[k].name_ko, categories[k].active) for k in CATEGORY_ORDER if k in categories]
    items.sort(key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(7, 3.2))
    labels = [k for k, _ in items]
    vals = [v for _, v in items]
    colors = [viz.MINT if l == "외국인관광도시민박업" else viz.NAVY for l in labels]
    ax.barh(labels, vals, color=colors, height=.6)
    ax.set_xscale("log")
    for i, v in enumerate(vals):
        ax.annotate(f"{v:,}", (v, i), textcoords="offset points", xytext=(6, -4),
                    fontsize=9, weight="bold")
    ax.set_title("5종 공유숙박 카테고리 규모 비교 (영업중, 로그스케일)", fontsize=13, weight="bold", pad=14)
    ax.grid(axis="x", which="both", alpha=.2)
    viz.strip_spines(ax)
    fig.tight_layout()
    return viz.to_png(fig)


def perf_table_html(perf: dict[str, dict]) -> str:
    """
    숙박업 실적 지표(평균 객단가·객실 점유율·객실당매출) 테이블 — 대시보드와 월간
    리포트 상세가 같은 마크업을 쓴다(중복 방지). estimate.html의 지역별 조회는
    같은 데이터를 다른 레이아웃(KPI 카드)으로 보여주므로 여기 재사용하지 않는다.
    """
    rows = "".join(
        f'<tr><td>{region}</td><td class=n>{s["adr"]:,.0f}원</td>'
        f'<td class=n>{s["occ"]:.1f}%</td><td class=n>{s["revpar"]:,.0f}원</td></tr>'
        for region, s in sorted(perf.items(), key=lambda kv: -kv[1]["revpar"])
    )
    ym = next(iter(perf.values()))["ym"] if perf else None
    return f"""
<h2>숙박업 실적 지표</h2>
<div class="h2sub">공유숙박 부문 평균 객단가·객실 점유율·객실당매출 — {(ym[:4] + '-' + ym[4:]) if ym else '데이터 없음'} 기준,
객실당매출 내림차순. 출처: 야놀자리서치 국내 숙박업 실적 지표(NOL·AirDNA·산하정보기술 블렌딩,
광역 권역 평균 — 개별 매물 수익이 아님). <a href="estimate.html">지역별 시장 지표에서 지역 선택해 보기 →</a></div>
<div class="mapwrap">{render_revpar_map(perf)}</div>
<div class="sub" style="text-align:center;margin-top:-4px">진할수록 객실당매출(RevPAR)이 높은 권역 · 회색은 야놀자리서치 커버리지 밖</div>
<div class="scroll"><table><tr><th>권역</th><th style="text-align:right">평균 객단가</th>
<th style="text-align:right">객실 점유율</th><th style="text-align:right">객실당매출</th></tr>{rows}</table></div>"""


def demand_kpis_html(demand: dict[str, dict]) -> str:
    """
    한국관광 데이터랩(KTO) 관광 수요 지표 5종 — 대시보드와 월간 리포트 상세가
    같은 마크업을 쓴다(perf_table_html과 동일한 이유로 중복 방지). 숙박 여행객수를
    맨 앞에 둔다 — 공급(등록 호스트)·가격(야놀자리서치)은 이미 있고 이게 수요
    규모를 채워주는 지표라 이 리포트에서 제일 관련이 깊다.
    """
    if not demand:
        return ""
    order = ["54", "12", "13", "52", "53"]
    ym = next(iter(demand.values()))["ym"]
    cards = "".join(
        f'<div class="kpi"><div class="l">{demand[k]["name"]}</div>'
        f'<div class="v" style="font-size:20px">{demand[k]["display"]}</div>'
        f'<div class="d {"up" if demand[k]["rate"] >= 0 else "down"}">{demand[k]["rate"]:+.1f}% 전년동기대비</div></div>'
        for k in order if k in demand
    )
    return f"""
<h2>관광 수요 지표</h2>
<div class="h2sub">{ym} 기준 연간누적 · 전년 동기 대비. 출처: 한국관광 데이터랩(한국관광공사).
등록 호스트 수(공급)·야놀자리서치 실적 지표(가격)와 달리 이건 수요 쪽 규모를 보여준다.</div>
<div class="kpis">{cards}</div>"""


def chart_inbound(annual_totals: list[dict]) -> str:
    import matplotlib.pyplot as plt
    years = [str(a["year"]) for a in annual_totals]
    totals = [a["total"] / 10000 for a in annual_totals]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    colors = [viz.MINT if y == years[-1] else viz.NAVY for y in years]
    ax.bar(years, totals, color=colors, width=.62)
    ax.set_title("방한 외래관광객 연도별 추이 (만 명)", fontsize=13, weight="bold", pad=14)
    ax.grid(axis="y", alpha=.25)
    viz.strip_spines(ax)
    fig.tight_layout()
    return viz.to_png(fig)


# ─────────────────────────────────────────────────────── 공통 CSS·네비

CSS = """
:root{--navy:#1B2A4A;--mint:#00C2A8;--bg:#fff;--fg:#161b22;--muted:#5b6472;
 --line:#e6e9ee;--card:#f7f9fb;--maxw:900px}
@media(prefers-color-scheme:dark){:root{--bg:#0c1017;--fg:#e8edf4;--muted:#93a1b5;--line:#232c3a;--card:#141b26}}
:root[data-theme="dark"]{--bg:#0c1017;--fg:#e8edf4;--muted:#93a1b5;--line:#232c3a;--card:#141b26}
:root[data-theme="light"]{--bg:#fff;--fg:#161b22;--muted:#5b6472;--line:#e6e9ee;--card:#f7f9fb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:16px/1.65 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",sans-serif}
a{color:inherit}
nav{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 88%,transparent);
 backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.navin{max-width:var(--maxw);margin:0 auto;padding:14px 20px;display:flex;
 align-items:center;justify-content:space-between;gap:16px}
.brand{font-weight:800;letter-spacing:-.02em;font-size:15px;text-decoration:none}
.brand span{color:var(--mint)}
.navlinks{display:flex;gap:20px;font-size:13.5px;font-weight:600}
.navlinks a{text-decoration:none;color:var(--muted)}
.navlinks a.active{color:var(--fg)}
.wrap{max-width:var(--maxw);margin:0 auto;padding:36px 20px 80px}
.kicker{color:var(--mint);font-weight:800;letter-spacing:.14em;font-size:11.5px;text-transform:uppercase}
h1{font-size:32px;line-height:1.22;margin:.35em 0 .15em;letter-spacing:-.02em}
h2{font-size:19px;margin:46px 0 6px;padding-top:22px;border-top:1px solid var(--line)}
.h2sub{color:var(--muted);font-size:13.5px;margin-bottom:16px}
.sub{color:var(--muted);font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:26px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi .l{font-size:12px;color:var(--muted)}
.kpi .v{font-size:27px;font-weight:800;letter-spacing:-.02em;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .d{font-size:12px;font-weight:700;margin-top:2px}
.kpi .d.up{color:var(--mint)} .kpi .d.down{color:#E2574C}
img.chart{max-width:100%;height:auto;display:block;margin:6px 0}
.mapwrap{max-width:480px;margin:12px auto}
.mapwrap svg{width:100%;height:auto;display:block}
.mapwrap path{transition:opacity .15s;cursor:default}
.mapwrap path:hover{opacity:.72}
#lkMapWrap path{fill:var(--card);stroke:var(--bg);stroke-width:1;transition:opacity .15s,fill .15s}
#lkMapWrap path[data-name]{cursor:pointer}
#lkMapWrap path.nodata{fill:var(--line)}
#lkMapWrap path.sel{fill:var(--mint)}
#lkMapWrap text{font-size:9px;fill:var(--muted);text-anchor:middle;pointer-events:none}
#lkMapWrap path.sel+text{fill:var(--bg);font-weight:800}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:700;font-size:11.5px;text-transform:uppercase}
td.n{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.note{background:var(--card);border-left:3px solid var(--mint);border-radius:0 10px 10px 0;
 padding:14px 16px;font-size:13px;color:var(--muted);margin:18px 0;line-height:1.6}
.note.warn{border-left-color:#F0A93E}
.newsitem{padding:12px 0;border-bottom:1px solid var(--line)}
.newsitem:last-child{border-bottom:none}
.newsitem .src{font-size:11px;color:var(--mint);font-weight:800;letter-spacing:.04em}
.newsitem a{text-decoration:none;font-weight:650;display:block;margin-top:2px}
.newsitem .sum{font-size:13px;color:var(--muted);margin-top:3px}
.tag{display:inline-block;font-size:10.5px;font-weight:700;color:var(--mint);
 background:color-mix(in srgb,var(--mint) 14%,transparent);border-radius:5px;
 padding:2px 7px;margin-left:4px;vertical-align:middle}
.wrap.wide{max-width:1400px}
.newsgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:22px;margin-top:24px}
@media(max-width:1100px){.newsgrid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:800px){.newsgrid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.newsgrid{grid-template-columns:1fr}}
.newscol{min-width:0}
.newscol h3{font-size:13.5px;font-weight:800;margin:0 0 12px;padding-bottom:8px;
 border-bottom:2px solid var(--mint);display:flex;justify-content:space-between;align-items:baseline}
.newscol h3 .colarrow{font-size:13px;font-weight:800;color:var(--mint);text-decoration:none;
 flex-shrink:0;margin-left:8px}
.newscard{display:block;text-decoration:none;color:inherit;margin-bottom:16px}
.newscard img{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:9px;
 background:var(--card);margin-bottom:7px;display:block}
.newscard .noimg{width:100%;aspect-ratio:16/10;border-radius:9px;margin-bottom:7px;
 background:var(--card);border:1px solid var(--line);display:flex;align-items:center;
 justify-content:center;font-size:18px;font-weight:800;color:var(--line)}
.newscard .t{font-size:12.5px;font-weight:650;line-height:1.45;
 display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.newscard .d{font-size:11px;color:var(--muted);margin-top:4px}
.compgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px 40px;margin-top:24px}
@media(max-width:800px){.compgrid{grid-template-columns:1fr}}
.compcol h3{font-size:14px;font-weight:800;margin:0 0 4px;padding-bottom:10px;
 border-bottom:2px solid var(--mint);display:flex;justify-content:space-between;align-items:baseline}
.compcol h3 .colarrow{font-size:13px;font-weight:800;color:var(--mint);text-decoration:none;
 flex-shrink:0;margin-left:8px}
.comprow{display:flex;gap:14px;align-items:center;padding:16px 0;border-bottom:1px solid var(--line);
 text-decoration:none;color:inherit}
.compcol:nth-child(2n) .comprow{border-bottom:1px solid var(--line)}
.comprow:last-child{border-bottom:none}
.comprow .t{flex:1;font-size:15px;font-weight:800;line-height:1.4;letter-spacing:-.01em;
 display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.comprow img{width:84px;height:84px;object-fit:cover;border-radius:10px;flex-shrink:0;background:var(--card)}
.comprow .noimg{width:84px;height:84px;border-radius:10px;flex-shrink:0;background:var(--card);
 border:1px solid var(--line);display:flex;align-items:center;justify-content:center;
 font-size:15px;font-weight:800;color:var(--line)}
.lookup{display:flex;gap:10px;margin:22px 0;flex-wrap:wrap}
.lookup select{padding:11px 14px;border-radius:10px;border:1px solid var(--line);
 background:var(--bg);color:var(--fg);font-size:14px;min-width:160px}
.lookup select:disabled{opacity:.5}
.lkResult{margin:8px 0 22px}
.rankbadge{font-size:13px;font-weight:700;color:var(--mint);margin:0 0 16px}
.ranklist{display:flex;flex-direction:column;gap:2px;margin-top:14px}
.rankrow{display:grid;grid-template-columns:26px 1fr 3fr auto;gap:10px;align-items:center;
 padding:7px 8px;border-radius:8px;cursor:pointer;font-size:13px}
.rankrow:hover{background:var(--card)}
.rankrow.sel{background:color-mix(in srgb,var(--mint) 14%,transparent);font-weight:800}
.rankrow .rn{color:var(--muted);font-size:12px;text-align:right}
.rankrow .rname{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rankrow .rbarwrap{background:var(--card);border-radius:5px;height:8px;overflow:hidden}
.rankrow .rbar{background:var(--mint);height:100%;border-radius:5px}
.rankrow .rcount{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.billcard{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:14px 16px;margin:10px 0}
.billcard .meta{font-size:12px;color:var(--muted);margin-top:4px}
.archive{display:flex;flex-direction:column;gap:10px}
.issue{display:flex;align-items:center;justify-content:space-between;gap:12px;
 background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:16px 18px;text-decoration:none;color:inherit}
.issue .no{font-size:11px;color:var(--mint);font-weight:800}
.issue .t{font-weight:700;margin-top:2px}
.issue .arrow{color:var(--muted)}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
 font-size:12px;color:var(--muted);line-height:1.8}
.hero{padding:8px 0 4px}
.hero h1{font-size:38px}
.heroSub{font-size:16px;color:var(--muted);max-width:52ch;margin-top:6px}
.pitch{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:28px 0}
.pitch div{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.pitch .t{font-weight:750;font-size:14.5px}
.pitch .d{font-size:13px;color:var(--muted);margin-top:4px;line-height:1.6}
.previewCard{background:var(--card);border:1px solid var(--line);border-radius:16px;
 padding:22px 24px;margin:22px 0}
.previewCard .kpis{margin:16px 0 6px}
.cta{display:inline-block;margin-top:14px;font-weight:700;font-size:14px;color:var(--mint);text-decoration:none}
.subscribe{background:var(--card);border:1px solid var(--line);border-radius:16px;
 padding:24px;margin:24px 0}
.subscribe form{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.subscribe input[type=email]{flex:1;min-width:220px;padding:11px 14px;border-radius:10px;
 border:1px solid var(--line);background:var(--bg);color:var(--fg);font-size:14px}
.subscribe button{padding:11px 22px;border-radius:10px;border:none;background:var(--navy);
 color:#fff;font-weight:700;font-size:14px;cursor:pointer}
:root[data-theme="dark"] .subscribe button{background:var(--mint);color:#04211c}
@media(prefers-color-scheme:dark){.subscribe button{background:var(--mint);color:#04211c}}
.consent{display:flex;align-items:flex-start;gap:8px;font-size:12.5px;color:var(--muted);
 margin-top:12px;line-height:1.5}
.consent input{margin-top:3px}
.formMsg{font-size:13px;margin-top:10px;font-weight:600}
.formMsg.ok{color:var(--mint)} .formMsg.err{color:#E2574C}
"""


def nav(active: str, depth: int = 0) -> str:
    p = "../" * depth
    items = [("landing", "홈", f"{p}index.html"),
             ("dashboard", "대시보드", f"{p}dashboard.html"),
             ("reports", "월간 리포트", f"{p}reports.html"),
             ("news", "뉴스", f"{p}news.html"),
             ("competitors", "글로벌 OTA 뉴스룸", f"{p}competitors.html"),
             ("estimate", "지역별 시장 지표", f"{p}estimate.html")]
    links = "".join(
        f'<a href="{url}" class="{"active" if key == active else ""}">{label}</a>'
        for key, label, url in items
    )
    return f"""<nav><div class="navin">
<a class="brand" href="{p}index.html">{TITLE} <span>·</span> WEHOME</a>
<div class="navlinks">{links}</div>
</div></nav>"""


def page(title: str, active: str, depth: int, body: str, description: str = "", wide: bool = False) -> str:
    return f"""<title>{title} · {TITLE}</title>
<meta name="description" content="{description}">
<style>{CSS}</style>
{nav(active, depth)}
<div class="wrap{' wide' if wide else ''}">
{body}
</div>"""


FOOTER = """<footer>
데이터: 행정안전부 지방행정 인허가 데이터(file.localdata.go.kr) 직접 수집,
공공누리 제4유형(출처표시·상업적이용금지·변경금지) · 교차검증: 한국관광공사 세이프스테이<br>
규제·정책 동향: 문화체육관광부·국회 공개 자료 자동 수집<br>
자동 생성 — 위홈 마켓리포트
</footer>"""


# ─────────────────────────────────────────────────────── 랜딩

SUBSCRIBE_FORM_JS = f"""
<script>
document.getElementById('subForm').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const email = document.getElementById('subEmail').value.trim();
  const consent = document.getElementById('subConsent').checked;
  const msg = document.getElementById('subMsg');
  if (!consent) {{ msg.textContent = '수신 동의가 필요합니다.'; msg.className = 'formMsg err'; return; }}
  try {{
    const res = await fetch('{SUBSCRIBE_ENDPOINT}', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{email, consent}})
    }});
    const data = await res.json();
    if (res.ok) {{
      if (data.status === 'already_active') {{
        msg.textContent = '이미 구독 중인 이메일입니다.';
      }} else if (data.mail && data.mail.dry_run) {{
        msg.textContent = '구독 완료 — 단, 지금은 개발 환경이라 확인 메일은 실제 발송되지 않았습니다.';
      }} else if (data.mail && data.mail.sent) {{
        msg.textContent = '구독 완료 — 확인 메일을 보내드렸습니다.';
      }} else {{
        msg.textContent = '구독은 저장됐지만 메일 발송에 실패했습니다: ' + (data.mail && data.mail.error || '');
      }}
      msg.className = 'formMsg ok';
      e.target.reset();
    }} else {{
      msg.textContent = data.error || '처리 중 오류가 발생했습니다.';
      msg.className = 'formMsg err';
    }}
  }} catch (err) {{
    msg.textContent = '구독 서버에 연결할 수 없습니다 (로컬 개발 중에는 subscribe_server.py 실행 필요).';
    msg.className = 'formMsg err';
  }}
}});
</script>"""


def render_landing(d: SiteData) -> str:
    c = d.current
    top3 = c.flagship.district_rank(SEOUL, 3)
    top3_txt = "·".join(f"{gu} {cnt:,}곳" for gu, cnt in top3)
    latest = d.all_issues[0]

    body = f"""
<div class="hero">
  <div class="kicker">SHARED STAY MARKET REPORT</div>
  <h1>공유숙박 시장,<br>숫자로 읽습니다</h1>
  <div class="heroSub">행정안전부 원본 등록 데이터를 매달 직접 받아 집계합니다.
  추정치·샘플 데이터 없이, 등록 추이·지역별 밀도·포화 신호·규제 동향을 한 곳에서 확인하세요.</div>
</div>

<div class="previewCard">
  <div class="kicker">이번 달 미리보기 · {c.ym}</div>
  <div class="kpis">
    <div class="kpi"><div class="l">외도민업 영업중</div><div class="v">{c.flagship.active:,}</div></div>
    <div class="kpi"><div class="l">서울 비중</div><div class="v">{c.seoul_share:.0%}</div></div>
    <div class="kpi"><div class="l">상위 3개구</div><div class="v" style="font-size:16px;margin-top:8px">{top3_txt}</div></div>
  </div>
  <a class="cta" href="dashboard.html">전체 대시보드 보기 →</a>
</div>

<h2>최신 리포트</h2>
<a class="issue" href="report/{latest.ym}.html"><div>
<div class="no">{latest.ym}</div>
<div class="t">외도민업 영업중 {latest.flagship.active:,}곳 · 서울 {latest.seoul_share:.0%}</div>
</div><div class="arrow">→</div></a>
<a class="cta" href="reports.html" style="margin-top:14px">지난 리포트 전체 보기 →</a>

<h2>우리 동네는 어때요?</h2>
<a class="issue" href="estimate.html"><div>
<div class="no">HOST MARKET LOOKUP</div>
<div class="t">지역을 고르면 등록 밀도·증감률을 보여드려요 — 전국 시군구 커버</div>
</div><div class="arrow">→</div></a>

<div class="subscribe">
  <div class="kicker">SUBSCRIBE</div>
  <h2 style="margin-top:8px;padding-top:0;border-top:none">매달 이메일로 받아보기</h2>
  <div class="h2sub" style="margin-bottom:0">발행 즉시 이메일로 보내드립니다. 언제든 수신거부할 수 있습니다.</div>
  <form id="subForm">
    <input type="email" id="subEmail" placeholder="you@example.com" required>
    <button type="submit">구독하기</button>
  </form>
  <label class="consent">
    <input type="checkbox" id="subConsent" required>
    이메일 주소를 리포트 발송 목적으로만 수집·이용하는 데 동의합니다. 그 외 용도로 쓰지 않으며 언제든 삭제를 요청할 수 있습니다.
  </label>
  <div id="subMsg" class="formMsg"></div>
</div>

{FOOTER}
{SUBSCRIBE_FORM_JS}"""
    return page("숫자로 읽는 공유숙박 시장", "landing", 0, body,
                "행정안전부 원본 데이터 기반 공유숙박 시장 월간 리포트. "
                f"외도민업 영업중 {c.flagship.active:,}곳, 서울 {c.seoul_share:.0%}.")


# ─────────────────────────────────────────────────────── 대시보드

def render_dashboard(d: SiteData) -> str:
    c, p = d.current, d.previous
    delta = mom_delta(c, p)
    delta_html = "" if delta is None else (
        f'<div class="d {"up" if delta >= 0 else "down"}">{delta:+,} vs {p.ym}</div>')

    top3 = c.flagship.district_rank(SEOUL, 3)
    top3_txt = "·".join(f"{gu} {cnt:,}" for gu, cnt in top3)

    sat = c.flagship.saturation_signal(SEOUL)[:8]
    sat_rows = "".join(
        f'<tr><td>{gu}</td><td class=n>{active:,}</td><td class=n>{recent}</td>'
        f'<td class=n style="color:{"var(--mint)" if g >= 0 else "#E2574C"}">{g:+.0%}</td></tr>'
        for gu, active, recent, g in sat
    )

    news_html = "".join(
        f'<div class="newsitem"><div class="src">{i.source}</div>'
        f'<a href="{i.url}" target="_blank" rel="noopener">{i.title}</a>'
        + (f'<div class="sum">{i.summary}</div>' if i.summary else "") + "</div>"
        for i in d.reg_items[:8]
    ) or '<div class="sub">이번 갱신 기준 키워드에 매칭되는 새 발표가 없습니다.</div>'

    bills_html = ""
    for act, matches in d.reg_bills.items():
        for b in matches:
            bills_html += (f'<div class="billcard"><b>⚖️ {b.title}</b>'
                            f'<div class="meta">의안번호 {b.bill_no} · {b.committee} · '
                            f'조회 {b.views:,} · <a href="{b.url}" target="_blank" rel="noopener">원문</a></div></div>')
    if not bills_html:
        bills_html = f'<div class="sub">추적 중인 법률({", ".join(d.reg_bills)}) 개정안이 현재 계류 중이지 않습니다.</div>'

    body = f"""
<div class="kicker">MARKET DASHBOARD</div>
<h1>공유숙박 마켓 대시보드</h1>
<div class="sub">{c.ym} 기준 · {date.today():%Y-%m-%d} 갱신 · 행안부 원본 데이터 직접 수집</div>

<div class="kpis">
  <div class="kpi"><div class="l">외도민업 영업중</div><div class="v">{c.flagship.active:,}</div>{delta_html}</div>
  <div class="kpi"><div class="l">폐업률</div><div class="v">{c.flagship.closure_rate:.1%}</div>
    <div class="d">누적 {c.flagship.total:,}건 중 {c.flagship.closed:,}건</div></div>
  <div class="kpi"><div class="l">서울 비중</div><div class="v">{c.seoul_share:.0%}</div>
    <div class="d">{c.seoul_active:,}곳</div></div>
  <div class="kpi"><div class="l">상위 3개구 집중도</div><div class="v">{c.concentration(3):.0%}</div>
    <div class="d">{top3_txt}</div></div>
</div>

<h2>등록 추이</h2>
<div class="h2sub">최근 24개월 월별 신규등록(인허가일자 기준, 현재 상태 무관). 최신월 강조.</div>
<img class="chart" src="data:image/png;base64,{chart_registrations_trend(c.flagship.recent_months(24))}">

<h2>등록연도별 생존곡선</h2>
<div class="h2sub">아직 폐업하지 않은 곳을 우변절단으로 반영한 실제 생존율 — "폐업 건만 본
존속기간"과 달리 생존편향이 없다. 짙은 남색일수록 오래된 등록연도, 민트에 가까울수록
최근 연도(선 끝에 연도 표기). 표본 30건 미만인 코호트는 뺐다.</div>
<img class="chart" src="data:image/png;base64,{chart_cohort_survival(c.flagship.cohort_survival)}">

<h2>전국 시도별 현황</h2>
<div class="h2sub">서울에 국한하지 않은 전국 17개 시도 영업중 호스트 순위. 진할수록 밀도가 높은 지역 —
지도에 마우스를 올리면 시도별 수치가 뜬다.</div>
<div class="mapwrap">{render_sido_map(c.flagship)}</div>
<img class="chart" src="data:image/png;base64,{chart_sido_rank(c.flagship)}">

<h2>서울 자치구 순위</h2>
<div class="h2sub">영업중 호스트 수 기준. 상위 3개 구가 전체의 {c.concentration(3):.0%}를 차지.</div>
<img class="chart" src="data:image/png;base64,{chart_district_rank(c.flagship)}">

<h2>포화 신호</h2>
<div class="h2sub">밀도(영업중 호스트 수)와 최근 6개월 증감률을 산점도 4분면으로 — 오른쪽 위(성장)는
이미 크면서 더 크는 중, 오른쪽 아래(포화)는 크지만 유입이 식는 중, 왼쪽 위(기회)는 아직
작지만 빠르게 크는 중. 아래 표는 상위 8개 구의 정확한 수치.</div>
<img class="chart" src="data:image/png;base64,{chart_saturation_scatter(c.flagship.saturation_signal(SEOUL))}">
<div class="scroll"><table><tr><th>구</th><th style="text-align:right">영업중</th>
<th style="text-align:right">최근 6개월 신규</th><th style="text-align:right">직전 6개월 대비</th></tr>{sat_rows}</table></div>

<h2>카테고리 비교</h2>
<div class="h2sub">공유숙박 5종 등록 규모. 농어촌민박이 절대 우위지만 도시 시장은 별개 축.</div>
<img class="chart" src="data:image/png;base64,{chart_category_compare(c.categories)}">

{demand_kpis_html(d.demand)}

{perf_table_html(d.perf)}

<h2>이번 달 계류 법안</h2>
<div class="h2sub">국회 입법예고 기준 상시 추적.</div>
{bills_html}

<h2>규제·정책 동향</h2>
<div class="h2sub">문체부·정책브리핑 자동 수집 · 공유숙박 키워드 매칭</div>
{news_html}

<h2>데이터 신뢰도</h2>
<div class="note">{d.reconcile_note}</div>
<div class="note warn">K-STAY의 /analysis 페이지에 있는 "Airbnb 리스팅 대비 미등록률" 수치는
페이지 자체 표기대로 시뮬레이션된 데모용 샘플입니다. 이 사이트는 그 수치를 인용하지 않고
행정안전부 원본 등록 데이터를 직접 받아 집계합니다(k-stay API 미사용).</div>

{FOOTER}"""
    return page("대시보드", "dashboard", 0, body,
                f"외국인관광도시민박업 영업중 {c.flagship.active:,}곳, 서울 {c.seoul_share:.0%}. "
                "행정안전부 공공데이터 기반 공유숙박 시장 대시보드.")


# ─────────────────────────────────────────────────────── 리포트 아카이브

def render_reports_index(d: SiteData) -> str:
    cards = "".join(
        f'<a class="issue" href="report/{iss.ym}.html"><div>'
        f'<div class="no">{iss.ym}</div>'
        f'<div class="t">외도민업 영업중 {iss.flagship.active:,}곳 · 서울 {iss.seoul_share:.0%}</div>'
        f'</div><div class="arrow">→</div></a>'
        for iss in d.all_issues
    )
    body = f"""
<div class="kicker">MONTHLY REPORTS</div>
<h1>월간 리포트 아카이브</h1>
<div class="sub">매월 발행. 지난 호는 계속 보관됩니다.</div>
<div class="archive" style="margin-top:24px">{cards}</div>
{FOOTER}"""
    return page("월간 리포트", "reports", 0, body, "공유숙박 시장 월간 리포트 발행 이력.")


# ─────────────────────────────────────────────────────── 뉴스 아카이브

def render_news(d: SiteData) -> str:
    """
    소스별로 나누되, 세로로 쭉 이어지던 h2 섹션을 4열 그리드 컬럼으로 바꿨다
    — 12개 소스가 한 화면에 나란히 보여야 "가독성이 안 좋다"는 문제가
    풀린다. 소스 내부는 원래 응답 순서(대체로 최신순)를 그대로 믿는다 —
    282건을 발행일 하나로 통합 정렬하려 해도 소스마다 날짜 포맷·유무가
    달라(야놀자·에어비앤비는 날짜가 아예 없음) 억지로 한 줄 세우면 순서가
    뒤죽박죽이 된다.

    이미지는 실측으로 13개 소스 중 9개에 확보돼 있다(news.py 참고). 나머지
    (서울관광재단은 모든 기사가 같은 로고만 반환해 의도적으로 제외)는 소스
    이니셜 플레이스홀더로 채운다.

    소스당 5건만 보여준다 — 컬럼 하나에 12건씩 쌓아두면 스크롤이 너무
    길어져서 정작 "가독성"이 다시 나빠진다. 6번째 기사부터는 우리가
    페이지네이션을 새로 만드는 대신 원본 사이트로 그냥 보낸다(소스명 옆
    화살표) — 어차피 전문은 원본에 있고, 그쪽에 트래픽을 돌려주는 게 맞다.
    """
    from sources import CORE_KEYWORDS

    by_source: dict[str, list[regulation.Item]] = {}
    for i in d.news_items:
        by_source.setdefault(i.source, []).append(i)

    cols = ""
    for src, items in by_source.items():
        cards = "".join(
            f'<a class="newscard" href="{i.url}" target="_blank" rel="noopener">'
            + (f'<img src="{i.image}" loading="lazy" alt="" referrerpolicy="no-referrer">' if i.image
               else f'<div class="noimg">{src[:2]}</div>')
            + f'<div class="t">{i.title}'
            + (' <span class="tag">공유숙박</span>' if i.matches_keywords(CORE_KEYWORDS) else "")
            + '</div>'
            + (f'<div class="d">{i.date}</div>' if i.date else "")
            + '</a>'
            for i in items[:5]
        )
        site_url = news.SOURCE_SITE_URL.get(src)
        arrow = (f'<a class="colarrow" href="{site_url}" target="_blank" rel="noopener" '
                  f'title="{src} 사이트로 이동">→</a>') if site_url else ""
        cols += f'<div class="newscol"><h3>{src}{arrow}</h3>{cards}</div>'

    body = f"""
<div class="kicker">NEWS ARCHIVE</div>
<h1>수집 뉴스 아카이브</h1>
<div class="sub">산업 미디어 {len(by_source)}개 소스 자동 수집 · 소스당 5건 표시, 화살표를
누르면 해당 사이트로 이동 · 전체 {len(d.news_items):,}건 수집됨. "공유숙박" 표시는
규제·정책 키워드 매칭 여부.</div>
<div class="newsgrid">{cols}</div>
{FOOTER}"""
    return page("뉴스", "news", 0, body,
                f"공유숙박·숙박업 산업 뉴스 자동 수집 아카이브. {len(by_source)}개 소스, "
                f"{len(d.news_items):,}건.", wide=True)


# ─────────────────────────────────────────────────────── 경쟁사 뉴스룸 동향

def render_competitors(d: SiteData) -> str:
    """
    위홈(자사) + 에어비앤비·아고다·부킹닷컴·클룩 4개 글로벌 OTA 뉴스룸을 나란히
    놓는다. news.html의 카드형(이미지 위·텍스트 아래)과 달리 헤드라인이 굵고
    크게, 썸네일은 오른쪽 작은 정사각형으로 붙는 가로 리스트 포맷 — 소스당 5건.
    위홈을 news.COMPETITOR_SOURCES 맨 앞에 둬서 컬럼 순서가 자동으로 맨 앞에 온다.
    """
    by_source: dict[str, list[regulation.Item]] = {}
    for i in d.news_items:
        if i.source in news.COMPETITOR_SOURCES:
            by_source.setdefault(i.source, []).append(i)

    cols = ""
    for src in news.COMPETITOR_SOURCES:
        items = by_source.get(src, [])
        rows = "".join(
            f'<a class="comprow" href="{i.url}" target="_blank" rel="noopener">'
            f'<div class="t">{i.title}</div>'
            + (f'<img src="{i.image}" loading="lazy" alt="" referrerpolicy="no-referrer">' if i.image
               else f'<div class="noimg">{src[:2]}</div>')
            + '</a>'
            for i in items[:5]
        ) or '<div class="sub">수집된 항목이 없습니다.</div>'
        site_url = news.SOURCE_SITE_URL.get(src)
        arrow = (f'<a class="colarrow" href="{site_url}" target="_blank" rel="noopener" '
                  f'title="{src} 사이트로 이동">→</a>') if site_url else ""
        cols += f'<div class="compcol"><h3>{src}{arrow}</h3>{rows}</div>'

    body = f"""
<div class="kicker">GLOBAL OTA NEWSROOM</div>
<h1>글로벌 OTA 뉴스룸</h1>
<div class="sub">위홈·에어비앤비·아고다·부킹닷컴·클룩 공식 뉴스룸 자동 수집 · 소스당 5건 표시,
화살표를 누르면 해당 뉴스룸으로 이동.</div>
<div class="compgrid">{cols}</div>
{FOOTER}"""
    return page("글로벌 OTA 뉴스룸", "competitors", 0, body,
                "위홈·에어비앤비·아고다·부킹닷컴·클룩 공식 뉴스룸 보도자료 자동 수집.", wide=True)


# ─────────────────────────────────────────────────────── 지역별 시장 지표

def render_estimate(d: SiteData) -> str:
    """
    "지역을 고르면 예상 수익을 보여달라" 요청에 대한 응답 — 단 AirDNA류의
    "예상 수익(원/박)" 숫자를 우리가 만들어내진 않는다. 등록 밀도·증감률(자체 집계)에
    더해, 야놀자리서치가 이미 공개 발행한 ADR·OCC·RevPAR(공유숙박 부문, NOL+AirDNA+
    산하정보기술 블렌딩 — yanolja_perf.py)을 있는 그대로 얹는다. 다만 이 API는 광역
    10개 권역 단위라 시군구별로 나오지 않는다 — 선택한 시도가 매핑되는 권역이 있을
    때만 보여주고, 없으면 조용히 숨긴다(억지로 인접 권역 수치를 끼워 보여주지 않는다).

    ponytail: 성장/위축 경계값(±15%)과 규모 티어(사분위수)는 단순 임계값이다.
    """
    regions = d.current.flagship.regional_stats()
    q1, q2, q3 = statistics.quantiles(sorted(r["active"] for r in regions), n=4)

    def tier(active: int) -> str:
        return "대형" if active >= q3 else "중형" if active >= q2 else "소형" if active >= q1 else "초기"

    def trend(growth: float) -> str:
        if growth == float("inf"):
            return "신규 진입"
        return "성장" if growth >= 0.15 else "위축" if growth <= -0.15 else "안정"

    def verdict(tier_: str, trend_: str) -> str:
        """규모(tier)×증감(trend) 조합을 "포화 주의"류 한 줄 결론으로 — 숫자만 보여주고
        판단은 사용자에게 떠넘기지 않는다("검색했을 때 100% 만족해야" 요청에 대한 응답)."""
        if trend_ == "신규 진입":
            return "신규 진입 지역 — 등록 이력이 막 생기기 시작해 아직 판단하기엔 이릅니다."
        big = tier_ in ("대형", "중형")
        if big and trend_ == "위축":
            return "포화 주의 — 이미 호스트가 많은데 최근 신규 유입은 둔화됐습니다."
        if big and trend_ == "성장":
            return "경쟁 치열 — 이미 큰 시장인데도 계속 성장하고 있습니다."
        if big and trend_ == "안정":
            return "성숙 시장 — 규모가 크고 안정적으로 유지되고 있습니다."
        if not big and trend_ == "성장":
            return "성장 기회 — 아직 진입자가 적은데 최근 유입이 늘고 있습니다."
        if not big and trend_ == "위축":
            return "관망 필요 — 진입자도 적고 최근 유입도 둔화됐습니다."
        return "틈새 시장 — 소규모지만 꾸준히 유지되고 있습니다."

    for r in regions:
        r["tier"] = tier(r["active"])
        r["trend"] = trend(r["growth"])
        r["growth_pct"] = None if r["growth"] == float("inf") else round(r["growth"] * 100)
        del r["growth"]
        r["ynj_region"] = yanolja_perf.SIDO_TO_REGION.get(r["sido"])
        r["verdict"] = verdict(r["tier"], r["trend"])

    regions.sort(key=lambda r: -r["active"])
    for i, r in enumerate(regions, 1):
        r["national_rank"] = i
    national_total = len(regions)

    by_sido_group: dict[str, list[dict]] = {}
    for r in regions:
        by_sido_group.setdefault(r["sido"], []).append(r)
    for group in by_sido_group.values():
        group.sort(key=lambda r: -r["active"])
        for i, r in enumerate(group, 1):
            r["sido_rank"] = i
        for r in group:
            r["sido_total"] = len(group)

    # 시군구 지도는 시도당 최대 240KB(전남광주통합, 섬 많은 해안선 탓)라 estimate.html에
    # 그대로 박아 넣으면 페이지 전체가 그만큼 무거워진다 — 시도별 SVG 파일로 따로 써 두고
    # 선택 시점에만 fetch()로 가져온다(대부분 사용자는 1~2개 시도만 조회한다).
    district_maps = district_map.build_district_maps(regions)
    maps_dir = SITE / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    for sido, svg in district_maps.items():
        (maps_dir / f"{sido}.svg").write_text(svg, encoding="utf-8")
    map_sidos_json = json.dumps(sorted(district_maps.keys()), ensure_ascii=False)

    sidos = sorted({r["sido"] for r in regions})
    sido_options = "".join(f'<option value="{s}">{s}</option>' for s in sidos)
    data_json = json.dumps(regions, ensure_ascii=False)
    perf_json = json.dumps(d.perf, ensure_ascii=False)
    perf_ym = next(iter(d.perf.values()))["ym"] if d.perf else None
    perf_note = (f" · 야놀자리서치 실적 지표(공유숙박 부문, {perf_ym[:4]}-{perf_ym[4:]} 기준, "
                 f"광역 {len(d.perf)}개 권역)도 함께 표시됩니다." if perf_ym else "")

    body = f"""
<div class="kicker">HOST MARKET LOOKUP</div>
<h1>지역별 시장 지표</h1>
<div class="sub">시도·시군구를 선택하면 외국인관광 도시민박업(외도민업) 등록 밀도·증감률을
보여줍니다{perf_note} 등록 밀도는 원화 예상수익이 아닙니다.</div>

<div class="lookup">
  <select id="lkSido"><option value="">시도 선택</option>{sido_options}</select>
  <select id="lkGu" disabled><option value="">시군구 선택</option></select>
</div>

<div id="lkRankBox" style="display:none">
  <h2>시도 내 시군구 순위</h2>
  <div class="h2sub">지도나 막대를 눌러도 해당 시군구를 바로 조회할 수 있습니다.</div>
  <div class="mapwrap" id="lkMapWrap" style="display:none"></div>
  <div id="lkRankList" class="ranklist"></div>
</div>

<div id="lkResult" class="lkResult" style="display:none">
  <div class="rankbadge" id="lkRankBadge"></div>
  <div class="kpis">
    <div class="kpi"><div class="l">영업중 호스트</div><div class="v" id="lkActive">-</div></div>
    <div class="kpi"><div class="l">규모</div><div class="v" id="lkTier">-</div></div>
    <div class="kpi"><div class="l">최근 6개월 신규</div><div class="v" id="lkRecent">-</div></div>
    <div class="kpi"><div class="l">직전 6개월 대비</div><div class="v" id="lkGrowth">-</div></div>
  </div>
  <div class="note" id="lkTrendNote"></div>

  <div id="lkPerf" style="display:none">
    <h2 style="margin-top:28px">공유숙박 실적 지표 <span id="lkPerfRegion"></span></h2>
    <div class="h2sub">출처: 야놀자리서치 국내 숙박업 실적 지표(<span id="lkPerfYm"></span>, 공유숙박 부문 광역
    권역 평균). NOL(야놀자)·AirDNA·산하정보기술 데이터를 블렌딩해 야놀자리서치가 공개 발행한
    수치이며, 개별 매물의 예상 수익이 아니라 권역 평균입니다.</div>
    <div class="kpis">
      <div class="kpi"><div class="l">평균 객단가</div><div class="v" id="lkAdr">-</div></div>
      <div class="kpi"><div class="l">객실 점유율</div><div class="v" id="lkOcc">-</div></div>
      <div class="kpi"><div class="l">객실당매출</div><div class="v" id="lkRevpar">-</div></div>
    </div>
  </div>
  <div id="lkPerfEmpty" class="sub" style="display:none;margin-top:20px">
    이 권역은 야놀자리서치 실적 지표 커버리지 밖입니다(대구·대전·인천·울산·세종).</div>
</div>
<div id="lkEmpty" class="sub" style="display:none">이 지역은 등록 표본이 없습니다.</div>

<div class="note warn">등록 밀도·증감률은 행정안전부 등록 건수 기반이라 실제 숙박요금·점유율을
반영한 예상 수익이 아닙니다. 평균 객단가·객실 점유율·객실당매출은 실제 실적 지표이지만 개별 매물이 아닌 권역
평균값입니다 — 이 사이트는 AirDNA 원본 데이터를 그들의 이용약관(크롤링·경쟁 서비스 제작 금지)상
직접 끌어와 쓸 수 없어, 이미 공개 발행된 야놀자리서치 지표로 대신합니다.</div>

{FOOTER}
<script>
const LK_DATA = {data_json};
const YNJ_DATA = {perf_json};
const NATIONAL_TOTAL = {national_total};
const MAP_SIDOS = new Set({map_sidos_json});
const sidoEl = document.getElementById('lkSido');
const guEl = document.getElementById('lkGu');
const result = document.getElementById('lkResult');
const empty = document.getElementById('lkEmpty');
const perfBox = document.getElementById('lkPerf');
const perfEmpty = document.getElementById('lkPerfEmpty');
const rankBox = document.getElementById('lkRankBox');
const rankList = document.getElementById('lkRankList');
const mapWrap = document.getElementById('lkMapWrap');

mapWrap.addEventListener('click', e => {{
  const p = e.target.closest('path[data-name]');
  if (p) {{ guEl.value = p.dataset.name; guEl.dispatchEvent(new Event('change')); }}
}});

sidoEl.addEventListener('change', () => {{
  guEl.innerHTML = '<option value="">시군구 선택</option>';
  result.style.display = 'none'; empty.style.display = 'none';
  rankBox.style.display = 'none'; rankList.innerHTML = '';
  mapWrap.style.display = 'none'; mapWrap.innerHTML = '';
  if (!sidoEl.value) {{ guEl.disabled = true; return; }}
  const selectedSido = sidoEl.value;
  const group = LK_DATA.filter(r => r.sido === selectedSido).sort((a, b) => b.active - a.active);
  group.forEach(r => {{
    const opt = document.createElement('option');
    opt.value = r.sigungu;
    opt.textContent = `${{r.sigungu}} (${{r.active.toLocaleString()}}곳)`;
    guEl.appendChild(opt);
  }});
  guEl.disabled = false;

  const max = group[0] ? group[0].active : 1;
  rankList.innerHTML = group.map(r => `
    <div class="rankrow" data-gu="${{r.sigungu}}">
      <div class="rn">${{r.sido_rank}}</div>
      <div class="rname">${{r.sigungu}}</div>
      <div class="rbarwrap"><div class="rbar" style="width:${{Math.max(4, r.active / max * 100)}}%"></div></div>
      <div class="rcount">${{r.active.toLocaleString()}}곳</div>
    </div>`).join('');
  rankList.querySelectorAll('.rankrow').forEach(el => {{
    el.addEventListener('click', () => {{ guEl.value = el.dataset.gu; guEl.dispatchEvent(new Event('change')); }});
  }});
  rankBox.style.display = 'block';

  if (MAP_SIDOS.has(selectedSido)) {{
    fetch('maps/' + encodeURIComponent(selectedSido) + '.svg')
      .then(r => r.text())
      .then(svg => {{
        if (sidoEl.value !== selectedSido) return; // 응답 오는 사이 다른 시도로 바뀌었으면 버림
        mapWrap.innerHTML = svg;
        mapWrap.style.display = 'block';
        if (guEl.value) {{ // 지도가 늦게 로드되는 사이 이미 시군구를 선택했을 수 있다
          const p = mapWrap.querySelector('path[data-name="' + guEl.value + '"]');
          if (p) p.classList.add('sel');
        }}
      }})
      .catch(() => {{}});
  }}
}});

guEl.addEventListener('change', () => {{
  const r = LK_DATA.find(x => x.sido === sidoEl.value && x.sigungu === guEl.value);
  rankList.querySelectorAll('.rankrow').forEach(el => {{
    el.classList.toggle('sel', el.dataset.gu === guEl.value);
  }});
  mapWrap.querySelectorAll('path[data-name]').forEach(el => {{
    el.classList.toggle('sel', el.dataset.name === guEl.value);
  }});
  if (!r) {{ result.style.display = 'none'; empty.style.display = guEl.value ? 'block' : 'none'; return; }}
  document.getElementById('lkRankBadge').textContent =
    `전국 ${{r.national_rank}}위 (총 ${{NATIONAL_TOTAL.toLocaleString()}}곳 중) · ${{r.sido}} 내 ${{r.sido_rank}}위 (총 ${{r.sido_total}}곳 중)`;
  document.getElementById('lkActive').textContent = r.active.toLocaleString() + '곳';
  document.getElementById('lkTier').textContent = r.tier;
  document.getElementById('lkRecent').textContent = r.recent6.toLocaleString() + '건';
  document.getElementById('lkGrowth').textContent =
    r.growth_pct === null ? '신규' : (r.growth_pct >= 0 ? '+' : '') + r.growth_pct + '%';
  document.getElementById('lkTrendNote').textContent = r.verdict;

  const perf = r.ynj_region ? YNJ_DATA[r.ynj_region] : null;
  if (perf) {{
    document.getElementById('lkPerfRegion').textContent = `— ${{r.ynj_region}} 권역`;
    document.getElementById('lkPerfYm').textContent = `${{perf.ym.slice(0,4)}}-${{perf.ym.slice(4,6)}}`;
    document.getElementById('lkAdr').textContent = Math.round(perf.adr).toLocaleString() + '원';
    document.getElementById('lkOcc').textContent = perf.occ + '%';
    document.getElementById('lkRevpar').textContent = Math.round(perf.revpar).toLocaleString() + '원';
    perfBox.style.display = 'block'; perfEmpty.style.display = 'none';
  }} else {{
    perfBox.style.display = 'none'; perfEmpty.style.display = 'block';
  }}

  result.style.display = 'block'; empty.style.display = 'none';
}});
</script>"""
    return page("지역별 시장 지표", "estimate", 0, body,
                "지역 선택 시 외도민업 등록 밀도·증감률과 야놀자리서치 평균 객단가·점유율·객실당매출 지표를 보여줍니다.")


# ─────────────────────────────────────────────────────── 월간 리포트 상세

def render_report_detail(iss: Issue, prev: Issue | None, inbound: dict, perf: dict[str, dict],
                          demand: dict[str, dict]) -> str:
    delta = mom_delta(iss, prev)
    delta_txt = "" if delta is None else f" ({delta:+,} vs {prev.ym})"

    cat_rows = "".join(
        f"<tr><td>{iss.categories[k].name_ko}</td><td class=n>{iss.categories[k].active:,}</td>"
        f"<td class=n>{iss.categories[k].closed:,}</td><td class=n>{iss.categories[k].total:,}</td></tr>"
        for k in CATEGORY_ORDER if k in iss.categories
    )
    dist_rows = "".join(
        f"<tr><td>{i}</td><td>{gu}</td><td class=n>{cnt:,}</td></tr>"
        for i, (gu, cnt) in enumerate(iss.flagship.district_rank(SEOUL, 10), 1)
    )
    sido_rows = "".join(
        f"<tr><td>{i}</td><td>{sido}</td><td class=n>{cnt:,}</td></tr>"
        for i, (sido, cnt) in enumerate(iss.flagship.sido_rank(10), 1)
    )

    inbound_html = ""
    try:
        inbound_html = f"""
<h2>인바운드 수요</h2>
<div class="h2sub">방한 외래관광객 연도별 추이. 출처: {inbound['meta']['source']}</div>
<img class="chart" src="data:image/png;base64,{chart_inbound(inbound['annual_totals'])}">"""
    except Exception as e:
        print(f"  ⚠️ 인바운드 차트 생략({type(e).__name__}: {e}) — /api/tourism/inbound 응답 구조 확인 필요")

    body = f"""
<div class="kicker">{iss.ym}</div>
<h1>공유숙박 마켓리포트 {iss.ym}</h1>
<div class="sub">행안부 원본 데이터 직접 수집·집계</div>

<div class="kpis">
  <div class="kpi"><div class="l">외도민업 영업중</div><div class="v">{iss.flagship.active:,}</div>
    <div class="d up">{delta_txt.strip()}</div></div>
  <div class="kpi"><div class="l">폐업률</div><div class="v">{iss.flagship.closure_rate:.1%}</div></div>
  <div class="kpi"><div class="l">서울 비중</div><div class="v">{iss.seoul_share:.0%}</div></div>
</div>

<h2>전국 시도별 TOP 10</h2>
<div class="scroll"><table><tr><th>#</th><th>시도</th><th style="text-align:right">영업중</th></tr>{sido_rows}</table></div>

<h2>서울 자치구 TOP 10</h2>
<div class="scroll"><table><tr><th>#</th><th>구</th><th style="text-align:right">영업중</th></tr>{dist_rows}</table></div>

<h2>카테고리별 현황</h2>
<div class="scroll"><table><tr><th>카테고리</th><th style="text-align:right">영업중</th>
<th style="text-align:right">폐업</th><th style="text-align:right">누적</th></tr>{cat_rows}</table></div>

{demand_kpis_html(demand)}
{inbound_html}
{perf_table_html(perf)}

<h2>이전 호 대비</h2>
<div class="note">{('전월(' + prev.ym + ') 대비 외도민업 영업중 ' + format(delta, "+,") + '곳 변화') if prev else '첫 발행호라 비교 대상 없음.'}</div>

{FOOTER}"""
    return page(f"{iss.ym} 리포트", "reports", 1, body,
                f"{iss.ym} 공유숙박 시장 리포트. 외도민업 영업중 {iss.flagship.active:,}곳.")


# ─────────────────────────────────────────────────────── 빌드

def build() -> None:
    d = gather()

    SITE.mkdir(exist_ok=True)
    (SITE / "report").mkdir(exist_ok=True)

    (SITE / "index.html").write_text(render_landing(d), encoding="utf-8")
    (SITE / "dashboard.html").write_text(render_dashboard(d), encoding="utf-8")
    (SITE / "reports.html").write_text(render_reports_index(d), encoding="utf-8")
    (SITE / "news.html").write_text(render_news(d), encoding="utf-8")
    (SITE / "competitors.html").write_text(render_competitors(d), encoding="utf-8")
    (SITE / "estimate.html").write_text(render_estimate(d), encoding="utf-8")

    for i, iss in enumerate(d.all_issues):
        prev = d.all_issues[i + 1] if i + 1 < len(d.all_issues) else None
        (SITE / "report" / f"{iss.ym}.html").write_text(
            render_report_detail(iss, prev, d.inbound, d.perf, d.demand), encoding="utf-8")

    # 구독 즉시발송용 요약. subscribe_server.py 가 매 구독마다 크롤링을 다시
    # 돌리지 않도록, 빌드 시점에 딱 필요한 값만 여기 남겨둔다.
    top1 = d.current.flagship.district_rank(SEOUL, 1)
    (SITE / "latest_issue.json").write_text(json.dumps({
        "ym": d.current.ym,
        "active": d.current.flagship.active,
        "seoul_share": d.current.seoul_share,
        "top_district": top1[0][0] if top1 else "-",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n✅ {SITE}/ 생성 완료")
    print(f"   index.html(랜딩), dashboard.html, reports.html, report/*.html ({len(d.all_issues)}개 호)")
    delta = mom_delta(d.current, d.previous)
    print(f"   최신: {d.current.ym} 영업중 {d.current.flagship.active:,} "
          f"({(format(delta, '+,') if delta is not None else '비교 대상 없음(첫 스냅샷)')} "
          f"vs {d.previous.ym if d.previous else '-'})")


if __name__ == "__main__":
    build()
