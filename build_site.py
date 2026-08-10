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
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv

import district_map
import localdata
import news
import regulation
import safestay
import subscribers
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
# sitemap.xml·robots.txt·OG 태그의 절대 URL 기준. email_sender.py의 SITE_BASE_URL과
# 같은 env var — 비어 있으면(로컬 개발 중) sitemap/OG url을 만들지 않고 조용히 뺀다.
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "").rstrip("/")
# GA4 측정 ID(G-XXXXXXXXXX, analytics.google.com에서 발급). 실행계획 08-10 "KPI 계측
# 세팅"의 전제 — 위홈 유입·트래픽 집계가 이 스크립트에 의존한다. 미설정이면(로컬
# 개발, 계정 발급 전) 조용히 빼서 개발 중 로컬 트래픽이 실제 속성에 안 섞이게 한다.
GA4_MEASUREMENT_ID = os.getenv("GA4_MEASUREMENT_ID", "").strip()
# 네이버 서치어드바이저(searchadvisor.naver.com) 소유 확인 코드. 국내 검색의 절반이
# 네이버인데 외부 사이트는 여기 등록하지 않으면 아예 색인되지 않는다 — 등록 후 노출까지
# 2주 걸리므로 값이 생기는 즉시 .env에 넣어야 한다. 미설정이면 조용히 뺀다.
NAVER_SITE_VERIFICATION = os.getenv("NAVER_SITE_VERIFICATION", "").strip()
CATEGORY_ORDER = ["foreigner_city_homestays", "hanok_experience", "tourist_pensions",
                   "tourist_accommodations", "rural_homestays"]
SEOUL, BUSAN = "서울특별시", "부산광역시"

# 위홈 호스트 등록 CTA. 실제 호스트 등록/가입 딥링크 경로는 이 저장소는 물론 형제
# 프로젝트(wehome-newsletter, wehome-marketing-engine) 어디에도 없다 — 확실히 아는 건
# 홈페이지(kstay.py의 UA 문자열, wehome-marketing-engine/knowledge/wehome_intro.md)뿐이라
# 일단 홈페이지로 건다. 실제 경로가 정해지면 .env의 WEHOME_HOST_SIGNUP_URL 하나만 바꾸면 된다.
WEHOME_HOST_SIGNUP_URL = os.getenv("WEHOME_HOST_SIGNUP_URL", "https://www.wehome.me")


def wehome_cta_url(content: str) -> str:
    """
    위홈 CTA 클릭 추적용 UTM 링크. utm_content로 클릭 위치를 구분한다(landing_hero/
    dashboard_banner/report_detail/estimate_result) — KPI '위홈 유입'(REPORT_SPEC.md,
    실행일정 xlsx의 07-30 항목)을 위치별로 쪼개 보려면 이게 있어야 한다.
      utm_source=wehome_market_report — 이 사이트가 출처임을 고정
      utm_medium=referral            — 유료 광고가 아닌 자체 사이트 유입
      utm_campaign=market_report     — 캠페인 단위(추후 발행호별로 나눌 수도 있음)
      utm_content=<content>          — 클릭한 위치
    """
    params = {"utm_source": "wehome_market_report", "utm_medium": "referral",
              "utm_campaign": "market_report", "utm_content": content}
    return f"{WEHOME_HOST_SIGNUP_URL}?{urlencode(params)}"


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
    visitors: dict
    entry_index: list[dict]


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
    print("TourAPI 지역별 방문자수 수집 중...")
    visit_ymd = (date.today() - timedelta(days=30)).strftime("%Y%m%d")  # 발행 지연 약 30일
    visitors = {
        "ymd": visit_ymd,
        "province": tourism_demand.collect_province_visitors(visit_ymd, visit_ymd),
        "district": tourism_demand.collect_district_visitors(visit_ymd, visit_ymd),
    }
    print(f"  시도 {len(visitors['province'])}곳 · 시군구 {len(visitors['district'])}곳")
    # district 방문자수가 비면(TOUR_API_KEY 미설정 등) None을 넘겨 entry_index가 3축으로
    # 자동 강등되게 한다 — {}를 그대로 넘기면 전 지역이 "매칭 없음"으로 빠져 결과가 통째로 빈다.
    entry_idx = localdata.entry_index(categories, visitors=visitors["district"] or None)
    print(f"  진입 적합도 지수: {len(entry_idx)}개 구 ({'4축(수요 포함)' if entry_idx and 'demand' in entry_idx[0] else '3축'})")
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
        visitors=visitors,
        entry_index=entry_idx,
    )


def mom_delta(cur: Issue, prev: Issue | None) -> int | None:
    return None if prev is None else cur.flagship.active - prev.flagship.active


# ─────────────────────────────────────────────────────── 차트

SVG_FONT = "-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Pretendard',sans-serif"


def _nice_ticks(vmax: float, count: int = 4) -> list[float]:
    """0..vmax를 대략 count개 구간으로 나누는 '보기 좋은' 눈금값(1/2/2.5/5/10 배수)."""
    if vmax <= 0:
        return [0, 1]
    raw_step = vmax / count
    mag = 10 ** math.floor(math.log10(raw_step))
    step = 10 * mag
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw_step:
            step = m * mag
            break
    n = math.ceil(vmax / step)
    return [round(step * i, 6) for i in range(n + 1)]


def chart_registrations_trend(monthly: list[tuple[str, int]]) -> str:
    months, counts = zip(*monthly)
    n = len(counts)
    W, H = 720, 340
    ml, mr, mt, mb = 44, 10, 34, 66
    pw, ph = W - ml - mr, H - mt - mb
    ticks = _nice_ticks(max(counts))
    vmax = ticks[-1] or 1
    slot = pw / n
    bw = slot * 0.68
    base_y = mt + ph

    grid = "".join(
        f'<line x1="{ml}" x2="{W - mr}" y1="{base_y - (t / vmax) * ph:.1f}" y2="{base_y - (t / vmax) * ph:.1f}" stroke="var(--line)"/>'
        f'<text x="{ml - 8}" y="{base_y - (t / vmax) * ph + 4:.1f}" font-size="10" fill="var(--muted)" text-anchor="end">{int(t):,}</text>'
        for t in ticks
    )
    bars = "".join(
        f'<rect class="cbar" x="{ml + i * slot + (slot - bw) / 2:.1f}" y="{base_y - (c / vmax) * ph:.1f}" '
        f'width="{bw:.1f}" height="{(c / vmax) * ph:.1f}" rx="2" fill="{"var(--mint)" if i == n - 1 else "var(--navy)"}" '
        f'style="transition-delay:{i * 22}ms"><title>{m} {c:,}건</title></rect>'
        for i, (m, c) in enumerate(zip(months, counts))
    )
    labels = "".join(
        f'<text x="{ml + i * slot + slot / 2:.1f}" y="{base_y + 14}" font-size="9" fill="var(--muted)" '
        f'text-anchor="end" transform="rotate(-60 {ml + i * slot + slot / 2:.1f} {base_y + 14})">{m}</text>'
        for i, m in enumerate(months)
    )
    return (f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{SVG_FONT}">'
            f'<text x="{ml}" y="20" font-size="14" font-weight="700" fill="var(--fg)">외국인관광 도시민박업 월별 신규등록 추이 (24개월)</text>'
            f'{grid}{bars}{labels}</svg>')


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
    years = sorted(cohort_survival, key=int)
    W, H = 720, 460
    ml, mr, mt, mb = 40, 50, 34, 40
    pw, ph = W - ml - mr, H - mt - mb
    if not years:
        return (f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{SVG_FONT}">'
                f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle" fill="var(--muted)">데이터 없음</text></svg>')

    n = len(years)
    end_points = []  # (year, ts, vals, color) — 선 다 그린 뒤 한 번에 라벨 배치(겹침 방지)
    max_age = 0
    for i, year in enumerate(years):
        curve = cohort_survival[year]
        ts = sorted((int(t) for t in curve), key=int)
        vals = [curve[str(t)] * 100 for t in ts]
        max_age = max(max_age, ts[-1])
        color = viz.NAVY if n == 1 else _lerp_color(viz.NAVY, viz.MINT, i / (n - 1))
        end_points.append((year, ts, vals, color))

    xmax = max_age + 1.3  # 연도 라벨 들어갈 여백

    def px(age: float) -> float: return ml + (age / xmax) * pw
    def py_(val: float) -> float: return mt + ph - (val / 100) * ph

    lines = []
    for i, (year, ts, vals, color) in enumerate(end_points):
        pts = [(px(t), py_(v)) for t, v in zip(ts, vals)]
        length = sum(math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:])) or 1
        d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        lines.append(f'<path class="cline" d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
                      f'stroke-dasharray="{length:.1f}" stroke-dashoffset="{length:.1f}" style="transition-delay:{i * 40}ms"/>')

    # 오른쪽 끝에서 값이 비슷한 코호트가 여럿이면 라벨이 겹친다(실측 확인) — y값 오름차순으로
    # 훑으면서 이전 라벨과 min_gap(percentage point) 미만이면 그만큼 밀어 올린다. 원래 값(y_true)과
    # 라벨 위치(y_label)가 밀렸으면 가는 선으로 이어줘서 어떤 선인지 헷갈리지 않게 한다.
    min_gap = 3.2
    labels = []
    y_label = None
    for year, ts, vals, color in sorted(end_points, key=lambda p: p[2][-1]):
        x, y_true = ts[-1], vals[-1]
        y_label = y_true if y_label is None else max(y_label + min_gap, y_true)
        x_px, y_true_px, y_label_px = px(x), py_(y_true), py_(y_label)
        if abs(y_label - y_true) > 0.5:
            labels.append(f'<line class="clabel" x1="{x_px:.1f}" y1="{y_true_px:.1f}" x2="{x_px + 4:.1f}" '
                           f'y2="{y_label_px:.1f}" stroke="{color}" stroke-width=".6" opacity=".6"/>')
        labels.append(f'<text class="clabel" x="{x_px + 7:.1f}" y="{y_label_px + 3:.1f}" font-size="9.5" '
                       f'fill="{color}">{year}</text>')

    yticks = [0, 20, 40, 60, 80, 100]
    grid = "".join(
        f'<line x1="{ml}" x2="{W - mr}" y1="{py_(t):.1f}" y2="{py_(t):.1f}" stroke="var(--line)"/>'
        f'<text x="{ml - 8}" y="{py_(t) + 4:.1f}" font-size="10" fill="var(--muted)" text-anchor="end">{t}</text>'
        for t in yticks
    )
    xstep = max(1, round(max_age / min(max_age, 10) if max_age else 1))
    xlabels = "".join(
        f'<text x="{px(a):.1f}" y="{mt + ph + 16}" font-size="9" fill="var(--muted)" text-anchor="middle">{a}</text>'
        for a in range(0, max_age + 1, xstep)
    )

    return (f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{SVG_FONT}">'
            f'<text x="{ml}" y="20" font-size="14" font-weight="700" fill="var(--fg)">등록연도 코호트별 생존곡선 (우변절단 반영)</text>'
            f'{grid}{"".join(lines)}{"".join(labels)}{xlabels}'
            f'<text x="{W / 2}" y="{H - 4}" font-size="10" fill="var(--muted)" text-anchor="middle">등록 후 경과연수 →</text>'
            f'</svg>')


def _hbar_chart(rows: list[tuple[str, int]], title: str, *, log: bool = False,
                 mint=None, label_w: float = 60) -> str:
    """가로 막대그래프 공용 렌더러 — 자치구·시도·카테고리 순위가 축·그리드·애니메이션
    구조는 같고 로그스케일 여부·강조(mint) 규칙만 달라 여기 하나로 모았다."""
    n = len(rows)
    W, row_h = 720, 30
    ml, mr, mt, mb = label_w, 46, 34, 10
    ph = n * row_h
    H = mt + ph + mb
    pw = W - ml - mr
    mint = mint or (lambda label, i: i == n - 1)
    vals = [v for _, v in rows]

    if log:
        lo, hi = 1, max(vals) or 1
        def scale(v): return (math.log10(max(v, 1) / lo) / math.log10(hi / lo)) if hi > lo else 1.0
        grid_at = []
        p = 1
        while p <= hi:
            grid_at.append(p)
            p *= 10
        if len(grid_at) < 2:
            grid_at = [lo, hi]
    else:
        vmax = (_nice_ticks(max(vals) if vals else 0))[-1] or 1
        def scale(v): return v / vmax
        grid_at = _nice_ticks(max(vals) if vals else 0)[1:]

    grid = "".join(
        f'<line x1="{ml + scale(t) * pw:.1f}" x2="{ml + scale(t) * pw:.1f}" y1="{mt}" y2="{mt + ph}" stroke="var(--line)"/>'
        for t in grid_at
    )
    bars, labels, vlabels = [], [], []
    for i, (label, v) in enumerate(rows):
        y = mt + i * row_h
        bw = scale(v) * pw
        color = "var(--mint)" if mint(label, i) else "var(--navy)"
        bars.append(f'<rect class="cbarh" x="{ml:.1f}" y="{y + 5:.1f}" width="{bw:.1f}" height="{row_h - 10}" rx="2" '
                     f'fill="{color}" style="transition-delay:{i * 26}ms"><title>{label} {v:,}</title></rect>')
        labels.append(f'<text x="{ml - 8:.1f}" y="{y + row_h / 2 + 4:.1f}" font-size="11" fill="var(--fg)" '
                       f'text-anchor="end">{label}</text>')
        vlabels.append(f'<text class="clabel" x="{ml + bw + 6:.1f}" y="{y + row_h / 2 + 4:.1f}" font-size="10.5" '
                        f'font-weight="700" fill="var(--fg)" style="transition-delay:{i * 26 + 250}ms">{v:,}</text>')

    return (f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{SVG_FONT}">'
            f'<text x="0" y="20" font-size="14" font-weight="700" fill="var(--fg)">{title}</text>'
            f'{grid}{"".join(bars)}{"".join(labels)}{"".join(vlabels)}</svg>')


def chart_district_rank(flagship: localdata.CategoryStats, sido: str = SEOUL, top_n: int = 15) -> str:
    top = flagship.district_rank(sido, top_n)[::-1]
    return _hbar_chart(top, f"서울 자치구별 영업중 호스트 TOP {top_n}", label_w=54)


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
    finite = [(gu, active, g) for gu, active, _, g in sat if g != float("inf")]
    skipped = len(sat) - len(finite)
    W, H = 720, 520
    ml, mr, mt, mb = 54, 20, 34, 32
    pw, ph = W - ml - mr, H - mt - mb
    if not finite:
        return (f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{SVG_FONT}">'
                f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle" fill="var(--muted)">데이터 없음</text></svg>')

    actives = [a for _, a, _ in finite]
    growths = [g * 100 for _, _, g in finite]
    x_mid = statistics.median(actives)

    xlo, xhi = min(actives) * 0.8, max(actives) * 1.3
    yspan = max(growths) - min(growths) or 20
    ylo, yhi = min(growths) - yspan * .15, max(growths) + yspan * .15
    if ylo > 0:
        ylo = -yspan * .1
    if yhi < 0:
        yhi = yspan * .1

    def xpx(v: float) -> float:
        return ml + (math.log10(max(v, xlo)) - math.log10(xlo)) / (math.log10(xhi) - math.log10(xlo)) * pw

    def ypx(v: float) -> float:
        return mt + ph - (v - ylo) / (yhi - ylo) * ph

    dots, labels = [], []
    for i, (gu, active, g) in enumerate(finite):
        cx, cy = xpx(active), ypx(g * 100)
        dots.append(f'<circle class="cdot" cx="{cx:.1f}" cy="{cy:.1f}" r="4.6" fill="var(--navy)" fill-opacity=".85" '
                     f'style="transition-delay:{i * 18}ms"><title>{gu} 영업중 {active:,} 직전6개월대비 {g:+.1%}</title></circle>')
        labels.append(f'<text class="clabel" x="{cx + 6:.1f}" y="{cy - 5:.1f}" font-size="8.5" fill="var(--navy)" '
                       f'style="transition-delay:{i * 18 + 150}ms">{gu.removesuffix("구")}</text>')

    qx0, qx1 = ml + pw * .08, ml + pw * .82
    qy_top, qy_bot = mt + ph * .12, mt + ph * .94
    quadrants = "".join(
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10" font-weight="700" fill="var(--muted)" '
        f'opacity=".85" text-anchor="middle">{t}</text>'
        for x, y, t in [(qx1, qy_top, "성장"), (qx1, qy_bot, "포화"), (qx0, qy_top, "기회"), (qx0, qy_bot, "침체")]
    )

    title = "포화 신호 산점도 — 밀도 vs 최근 6개월 증감률"
    if skipped:
        title += f" ({skipped}개 구 제외)"

    return (f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{SVG_FONT}">'
            f'<text x="{ml}" y="20" font-size="13" font-weight="700" fill="var(--fg)">{title}</text>'
            f'<line x1="{ml}" x2="{W - mr}" y1="{ypx(0):.1f}" y2="{ypx(0):.1f}" stroke="var(--line)"/>'
            f'<line x1="{xpx(x_mid):.1f}" x2="{xpx(x_mid):.1f}" y1="{mt}" y2="{mt + ph}" stroke="var(--line)"/>'
            f'{quadrants}{"".join(dots)}{"".join(labels)}'
            f'<text x="{ml}" y="{H - 6}" font-size="9.5" fill="var(--muted)">영업중 호스트 수(로그스케일) · 세로축: 직전 6개월 대비 증감률 →</text>'
            f'</svg>')


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
    top = flagship.sido_rank(top_n)[::-1]
    return _hbar_chart(top, f"전국 시도별 영업중 호스트 TOP {top_n}", label_w=80)


def chart_category_compare(categories: dict[str, localdata.CategoryStats]) -> str:
    """5종 카테고리 규모 비교. 농어촌민박이 압도적으로 커서 로그스케일."""
    items = [(categories[k].name_ko, categories[k].active) for k in CATEGORY_ORDER if k in categories]
    items.sort(key=lambda kv: kv[1])
    return _hbar_chart(items, "5종 공유숙박 카테고리 규모 비교 (영업중, 로그스케일)", log=True,
                        mint=lambda label, i: label == "외국인관광도시민박업", label_w=168)


def _bar_cell(value: float, max_value: float, fmt: str = "{:,.0f}", color: str = "var(--mint)") -> str:
    """표 셀 안에 넣는 인라인 막대 — estimate.html의 .rankrow/.eiBar와 같은 시각 언어를
    대시보드·리포트 표에도 맞춘다(숫자만 나열되면 읽기 어렵다는 피드백에 대한 대응).
    표 하나에 여러 칼럼이 있어도 이야기의 핵심 칼럼 하나에만 쓴다 — 칸마다 막대를 넣으면
    오히려 산만해진다(포화 신호 표가 growth% 칼럼 하나만 색칠하는 것과 같은 원칙)."""
    pct = round(value / max_value * 100) if max_value else 0
    return (f'<div class="tblbar"><div class="bw"><div class="bf" style="width:{pct}%;background:{color}"></div></div>'
            f'<span>{fmt.format(value)}</span></div>')


def perf_table_html(perf: dict[str, dict]) -> str:
    """
    숙박업 실적 지표(평균 객단가·객실 점유율·객실당매출) 테이블 — 대시보드와 월간
    리포트 상세가 같은 마크업을 쓴다(중복 방지). estimate.html의 지역별 조회는
    같은 데이터를 다른 레이아웃(KPI 카드)으로 보여주므로 여기 재사용하지 않는다.
    """
    max_revpar = max((s["revpar"] for s in perf.values()), default=1)
    rows = "".join(
        f'<tr><td>{region}</td><td class=n>{s["adr"]:,.0f}원</td>'
        f'<td class=n>{s["occ"]:.1f}%</td><td class=n>{_bar_cell(s["revpar"], max_revpar, "{:,.0f}원")}</td></tr>'
        for region, s in sorted(perf.items(), key=lambda kv: -kv[1]["revpar"])
    )
    ym = next(iter(perf.values()))["ym"] if perf else None
    return f"""
<h2>숙박업 실적 지표</h2>
<div class="h2sub">공유숙박 부문 평균 객단가·객실 점유율·객실당매출 — {(ym[:4] + '-' + ym[4:]) if ym else '데이터 없음'} 기준,
객실당매출 내림차순. 출처: 야놀자리서치 국내 숙박업 실적 지표(NOL·AirDNA·산하정보기술 블렌딩,
광역 권역 평균 — 개별 매물 수익이 아님). <a href="estimate.html">지역별 시장 지표에서 지역 선택해 보기 →</a></div>
<div class="mapwrap reveal">{render_revpar_map(perf)}</div>
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
        f'<div class="kpi reveal"><div class="l">{demand[k]["name"]}</div>'
        f'<div class="v" style="font-size:20px">{demand[k]["display"]}</div>'
        f'<div class="d {"up" if demand[k]["rate"] >= 0 else "down"}">{demand[k]["rate"]:+.1f}% 전년동기대비</div></div>'
        for k in order if k in demand
    )
    return f"""
<h2>관광 수요 지표</h2>
<div class="h2sub">{ym} 기준 연간누적 · 전년 동기 대비. 출처: 한국관광 데이터랩(한국관광공사).
등록 호스트 수(공급)·야놀자리서치 실적 지표(가격)와 달리 이건 수요 쪽 규모를 보여준다.</div>
<div class="kpis">{cards}</div>"""


def visitor_demand_html(visitors: dict) -> str:
    """
    TourAPI 시도/시군구별 방문자수(현지인·외지인·외국인 합산) — demand_kpis_html의
    전국 합계 5개 지표와 달리 지역별로 방문 수요가 어디 몰리는지 보여준다. 발행 지연이
    약 30일이라(tourism_demand.py 주석 참고) 이번 달이 아니라 그보다 한 달 전 특정일
    스냅샷이다. 시군구 키는 tourism_demand.collect_district_visitors가 signguCode로
    시도를 역산해 "시도 시군구" 형식으로 준다(동명 지역 구분됨) — 전국 TOP 10 표. 등록
    데이터와 실제로 합쳐 순위를 매기는 건 entry_index_html(진입 적합도 지수) 쪽.
    """
    province, district, ymd = visitors.get("province"), visitors.get("district"), visitors.get("ymd")
    if not province:
        return ""
    prov_top = sorted(province.items(), key=lambda kv: -kv[1]["total"])[:10]
    max_prov = prov_top[0][1]["total"] if prov_top else 1
    prov_rows = "".join(
        f"<tr><td>{i}</td><td>{area}</td><td class=n>{_bar_cell(v['total'], max_prov)}</td></tr>"
        for i, (area, v) in enumerate(prov_top, 1)
    )
    dist_top = sorted(district.items(), key=lambda kv: -kv[1]["total"])[:10]
    max_dist = dist_top[0][1]["total"] if dist_top else 1
    dist_rows = "".join(
        f"<tr><td>{i}</td><td>{area}</td><td class=n>{_bar_cell(v['total'], max_dist)}</td></tr>"
        for i, (area, v) in enumerate(dist_top, 1)
    )
    ymd_disp = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    return f"""
<h2>지역별 방문자수</h2>
<div class="h2sub">{ymd_disp} 기준 일일 방문자수(현지인+외지인+외국인 합산). 출처: 한국관광공사 TourAPI
관광빅데이터(이동통신 기반, 발행 지연 약 30일). 등록 호스트 수(공급)와 별개로 실제 방문 수요
분포를 보여준다. 시군구는 "시도 시군구" 형식으로 표기해 동명 지역(예: 부산 중구·대구 중구)을
구분한다.</div>
<div class="scroll"><table><tr><th>#</th><th>시도</th><th style="text-align:right">방문자수</th></tr>{prov_rows}</table></div>
<div class="scroll"><table><tr><th>#</th><th>시군구</th><th style="text-align:right">방문자수</th></tr>{dist_rows}</table></div>"""


def entry_index_html(entry_idx: list[dict]) -> str:
    """
    localdata.entry_index() 결과 — 등록 데이터(성장·생존·적합도)와 TourAPI 방문자수(수요)를
    합쳐 만드는 자체 지수라, 위 두 섹션(등록 추이/서울 자치구 순위, 지역별 방문자수)과
    달리 원천 데이터를 그대로 보여주는 게 아니라 이 리포트가 직접 계산해 내놓는 값이다
    — 그래서 표 아래에 산식을 명시한다. 수요 축은 visitors가 있을 때만 붙으므로(entry_idx
    첫 행에 "demand" 키 존재 여부로 판별) TOUR_API_KEY 미설정 시엔 3축으로 자동 강등된다.
    """
    if not entry_idx:
        return ""
    has_demand = "demand" in entry_idx[0]
    demand_th = "<th style=\"text-align:right\">수요</th>" if has_demand else ""
    rows = "".join(
        f"<tr><td>{i}</td><td>{r['sido']} {r['sigungu']}</td>"
        f"<td class=n>{r['pct_growth']}</td><td class=n>{r['pct_survival']}</td><td class=n>{r['pct_fit']}</td>"
        + (f"<td class=n>{r['pct_demand']}</td>" if has_demand else "")
        + f"<td class=n>{_bar_cell(r['index'], 100)}</td></tr>"
        for i, r in enumerate(entry_idx[:10], 1)
    )
    axes_desc = ("성장·생존·적합도·수요 4축" if has_demand else
                 "성장·생존·적합도 3축(TOUR_API_KEY 미설정 — 수요 축 제외)")
    return f"""
<h2>진입 적합도 지수</h2>
<div class="h2sub">외도민업 신규 진입 시 다른 구 대비 상대적으로 유리한 정도를 {axes_desc}(전부 백분위,
동일가중)으로 계산. 성장=최근/직전 6개월 신규등록 증감률, 생존=1-누적 폐업률, 적합도=5종 카테고리
전체 공급 중 외도민업 비중{"," if has_demand else ""}{" 수요=(외지인+외국인 방문자수)/영업중 호스트 수" if has_demand else ""}.
호스트 수(규모)는 의도적으로 뺐다 — 넣으면 지수가 자치구 순위 재탕이 된다. 표본 부족·판단 불가 구는 제외.</div>
<div class="scroll"><table><tr><th>#</th><th>구</th><th style="text-align:right">성장</th>
<th style="text-align:right">생존</th><th style="text-align:right">적합도</th>{demand_th}
<th style="text-align:right">지수</th></tr>{rows}</table></div>"""


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
.navlinks a{text-decoration:none;color:var(--muted);white-space:nowrap}
.navlinks a.active{color:var(--fg)}
@media(max-width:680px){.navin{overflow-x:auto;-webkit-overflow-scrolling:touch}
 .brand{flex:none}.navlinks{flex:none}}
.wrap{max-width:var(--maxw);margin:0 auto;padding:36px 20px 80px}
.kicker{color:var(--mint);font-weight:800;letter-spacing:.14em;font-size:11.5px;text-transform:uppercase}
h1{font-size:32px;line-height:1.22;margin:.35em 0 .15em;letter-spacing:-.02em}
h2{font-size:19px;margin:46px 0 6px;padding-top:22px;border-top:1px solid var(--line)}
.h2sub{color:var(--muted);font-size:13.5px;margin-bottom:16px}
.sub{color:var(--muted);font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:26px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi .l{font-size:12px;color:var(--muted)}
/* nowrap이 없으면 좁은 화면에서 "142,813원"이 숫자와 "원"으로 쪼개져 두 줄이 된다
   (374px 실측). 대신 카드 폭이 모자라면 글자 크기를 줄여서 한 줄을 지킨다. */
.kpi .v{font-size:clamp(20px,5.4vw,27px);font-weight:800;letter-spacing:-.02em;margin-top:4px;
 font-variant-numeric:tabular-nums;white-space:nowrap}
.kpi .d{font-size:12px;font-weight:700;margin-top:2px}
.kpi .d.up{color:var(--mint)} .kpi .d.down{color:#E2574C}
.chart{width:100%;height:auto;display:block;margin:6px 0;overflow:visible}
.reveal .cbar{transform-box:fill-box;transform-origin:bottom;transform:scaleY(0)}
.reveal.in .cbar{transform:scaleY(1);transition:transform .8s cubic-bezier(.16,1,.3,1)}
.reveal .cbarh{transform-box:fill-box;transform-origin:left;transform:scaleX(0)}
.reveal.in .cbarh{transform:scaleX(1);transition:transform .8s cubic-bezier(.16,1,.3,1)}
.reveal .cdot{transform-box:fill-box;transform-origin:center;transform:scale(0);opacity:0}
.reveal.in .cdot{transform:scale(1);opacity:1;transition:transform .5s cubic-bezier(.34,1.56,.64,1),opacity .3s}
.reveal .cline{transition:stroke-dashoffset 1.1s cubic-bezier(.16,1,.3,1)}
.reveal.in .cline{stroke-dashoffset:0}
.reveal .clabel{opacity:0;transition:opacity .5s}
.reveal.in .clabel{opacity:1}
@media(prefers-reduced-motion:reduce){.reveal .cbar,.reveal .cbarh,.reveal .cdot{transform:none!important}
 .reveal .cline{transition:none!important;stroke-dashoffset:0!important}}
.reveal{opacity:0;transform:translateY(16px);transition:opacity .6s ease,transform .6s ease}
.reveal.in{opacity:1;transform:translateY(0)}
.mapwrap.reveal{transform:scale(.96)}
.mapwrap.reveal.in{transform:scale(1)}
.kpi.reveal:nth-child(1){transition-delay:0ms} .kpi.reveal:nth-child(2){transition-delay:70ms}
.kpi.reveal:nth-child(3){transition-delay:140ms} .kpi.reveal:nth-child(4){transition-delay:210ms}
.kpi.reveal:nth-child(5){transition-delay:280ms}
@media(prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}}
.mapwrap{max-width:480px;margin:12px auto}
.mapwrap svg{width:100%;height:auto;display:block}
.mapwrap path{transition:opacity .15s;cursor:default}
.mapwrap path:hover{opacity:.72}
#lkMapWrap path{fill:var(--card);stroke:var(--bg);stroke-width:1;transition:opacity .15s,fill .15s}
#lkMapWrap path[data-name]{cursor:pointer}
#lkMapWrap path.nodata{fill:var(--line)}
#lkMapWrap path.sel{fill:var(--mint)}
#lkMapWrap text{fill:var(--muted);text-anchor:middle;pointer-events:none}
#lkMapWrap text.sel{fill:var(--bg);font-weight:800}
#lkPin{pointer-events:none}
#lkPin .pinbody{fill:var(--mint);stroke:var(--bg);stroke-width:1.5}
#lkPin .pinhole{fill:var(--bg)}
#lkPin .pinbadge{fill:var(--mint);stroke:var(--bg);stroke-width:1.5}
#lkPin .pinbadgetext{fill:#fff;font-size:13px;font-weight:800;text-anchor:middle;dominant-baseline:central}
.statusbar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--card);margin:10px 0 8px}
.statusbar .seg{height:100%}
.statusbar .seg.active{background:var(--mint)}
.statusbar .seg.pause{background:#F0A93E}
.statusbar .seg.closed{background:var(--line)}
.statuslegend{display:flex;gap:16px;font-size:12.5px;color:var(--muted);flex-wrap:wrap;margin-bottom:4px}
.statuslegend .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
.statuslegend .dot.active{background:var(--mint)}
.statuslegend .dot.pause{background:#F0A93E}
.statuslegend .dot.closed{background:var(--line)}
.trendspark{display:flex;gap:4px;margin:14px 0 4px}
.trendspark .tcol{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;min-width:0}
.trendspark .tcol.tboundary{border-left:1px dashed var(--line);margin-left:3px;padding-left:3px}
.trendspark .tval{font-size:10px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums}
.trendspark .tcol.trecent .tval{color:var(--fg)}
.trendspark .tbarwrap{width:100%;height:56px;background:var(--card);border-radius:3px;
 position:relative;overflow:hidden}
.trendspark .tbar{position:absolute;bottom:0;left:0;width:100%;background:var(--muted);
 border-radius:2px 2px 0 0;min-height:2px;opacity:.55}
.trendspark .tbar.trecentbar{background:var(--mint);opacity:1}
.trendspark .tlabel{font-size:9px;color:var(--muted);white-space:nowrap}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--muted);font-weight:700;font-size:11.5px;text-transform:uppercase}
td.n{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.tblbar{display:inline-flex;align-items:center;justify-content:flex-end;gap:8px}
.tblbar .bw{width:56px;height:6px;background:var(--card);border-radius:3px;overflow:hidden;flex-shrink:0}
.tblbar .bf{display:block;height:100%;border-radius:3px}
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
/* scroll-margin은 selectGu()의 scrollIntoView가 sticky 네비 밑으로 결과를 밀어넣지
   않게 하려는 것 — 없으면 판정 카드 윗부분이 네비에 가린다. */
.lkResult{margin:8px 0 22px;scroll-margin-top:78px}
.verdictCard{border-radius:18px;padding:24px 26px 22px;margin:8px 0 22px;
 border:1px solid var(--line);background:var(--card)}
/* 판정 톤은 배지 하나에만 싣는다 — 예전엔 카드 테두리(1px×둘레 전체)와 문구 색까지
   같이 칠했는데, 색을 넓게 얇게 펴면 총면적만 커지고 밀도가 낮아 강조가 아니라 장식용
   프레임으로 읽힌다(눈에 안 들어오면서 산만하기까지 한 최악의 조합). 좁고 진하게 모으면
   색 총량은 줄면서 주목도는 올라간다. */
.vcTop{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px 14px}
.vcRegion{font-size:23px;font-weight:800;letter-spacing:-.02em}
.vcRank{font-size:12.5px;font-weight:700;color:var(--muted);white-space:nowrap}
.vcVerdict{font-size:16px;font-weight:600;line-height:1.65;margin-top:14px;letter-spacing:-.01em;
 color:var(--fg)}
/* neutral(성숙 시장·틈새 시장·신규 진입)은 "판단 신호 없음"이라 제일 조용해야 한다 —
   기본값을 흰 글씨 채움이 아니라 옅은 회색 칩으로 둔다. 네이비 채움을 쓰다가 다크모드에서
   어두운 카드에 그대로 묻히는 것도 확인했다(--fg 기준이라 두 테마 다 자동으로 맞는다). */
.vcBadge{display:inline-block;font-size:12.5px;font-weight:800;letter-spacing:0;
 padding:4px 10px;border-radius:999px;margin-right:9px;white-space:nowrap;
 vertical-align:2px;color:var(--muted);background:color-mix(in srgb,var(--fg) 9%,transparent)}
.verdictCard[data-tone="positive"] .vcBadge{color:#fff;background:color-mix(in srgb,var(--mint) 88%,#000)}
.verdictCard[data-tone="warning"] .vcBadge{color:#fff;background:#E2574C}
.verdictCard[data-tone="caution"] .vcBadge{color:#fff;background:color-mix(in srgb,#F0A93E 90%,#000)}
.vcStats{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:16px;
 margin-top:20px;padding-top:18px;border-top:1px solid var(--line)}
.vsV{font-size:22px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.vsV.up{color:var(--mint)} .vsV.down{color:#E2574C}
.vsL{font-size:11.5px;color:var(--muted);margin-top:3px}
.eiScore{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap}
.eiScoreV{font-size:32px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.eiScoreMax{font-size:14px;color:var(--muted);font-weight:700}
.eiScoreRank{margin-left:6px;font-size:12.5px;color:var(--muted);font-weight:600}
.eiAxes{display:flex;flex-direction:column;gap:9px;margin-top:16px}
.eiRow{display:grid;grid-template-columns:52px 1fr 30px;gap:10px;align-items:center;font-size:12.5px}
.eiRow .eiL{color:var(--muted)}
.eiBarWrap{background:var(--card);border-radius:5px;height:8px;overflow:hidden}
.eiBar{background:var(--mint);height:100%;border-radius:5px}
.eiRow .eiN{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
/* 이 축은 발산형(diverging)이다 — 50점이 의미 있는 중간값이고 양 끝이 서로 반대되는
   상태(포화 vs 기회)라, 평균 대비 단색 램프는 한쪽 끝을 배경색으로 지워버려 그 구간의
   해상도를 통째로 잃는다(3점과 25점이 똑같이 안 보임). 신호등(빨강/주황/민트)은 그
   문제는 피했지만 이 축에 없는 위험 판단("포화=나쁨")을 빌려왔었다. 그래서 발산은
   유지하되 이미 쓰는 두 브랜드색으로: navy(밀집·기성) — 중립 — mint(여백·기회). */
.eiGauge{position:relative;height:10px;border-radius:5px;margin:16px 2px 8px;
 background:linear-gradient(to right,
   color-mix(in srgb,var(--navy) 45%,var(--card)) 0%,
   var(--card) 50%,
   color-mix(in srgb,var(--mint) 55%,var(--card)) 100%)}
.eiGaugeAvg{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--fg);opacity:.35}
.eiGaugeMark{position:absolute;top:-5px;width:20px;height:20px;margin-left:-10px;
 border-radius:50%;background:var(--fg);border:3px solid var(--bg);box-shadow:0 1px 4px rgba(0,0,0,.3)}
.eiGaugeLabels{display:flex;justify-content:space-between;font-size:10.5px;color:var(--muted);margin:0 2px}
.eiGaugeCompare{font-size:12.5px;color:var(--muted);margin-top:10px}
.ranklist{display:flex;flex-direction:column;gap:2px;margin-top:14px}
.rankrow{display:grid;grid-template-columns:26px 1fr 3fr auto;gap:10px;align-items:center;
 padding:7px 8px;border-radius:8px;cursor:pointer;font-size:13px}
.rankrow:hover{background:var(--card)}
.rankrow:focus-visible{outline:2px solid var(--mint);outline-offset:-2px;background:var(--card)}
#lkMapWrap path[data-name]:focus-visible{outline:2px solid var(--mint);outline-offset:1px}
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
.hero{padding:8px 0 4px;display:grid;grid-template-columns:1fr 300px;gap:8px;align-items:center}
.hero h1{font-size:38px}
.heroSub{font-size:16px;color:var(--muted);max-width:52ch;margin-top:6px}
.heroArt{width:100%;height:auto;-webkit-mask-image:linear-gradient(to right,transparent,#000 42%);
 mask-image:linear-gradient(to right,transparent,#000 42%)}
@media(max-width:760px){.hero{grid-template-columns:1fr}.heroArt{display:none}}
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
.catpicker{display:flex;gap:14px;flex-wrap:wrap;margin-top:14px;font-size:13px;color:var(--muted)}
.catpicker label{display:flex;align-items:center;gap:6px;cursor:pointer}
.formMsg{font-size:13px;margin-top:10px;font-weight:600}
.formMsg.ok{color:var(--mint)} .formMsg.err{color:#E2574C}
.wehomeCta{display:inline-block;margin-top:14px;padding:12px 22px;border-radius:10px;
 background:var(--navy);color:#fff;font-weight:700;font-size:14px;text-decoration:none}
:root[data-theme="dark"] .wehomeCta{background:var(--mint);color:#04211c}
@media(prefers-color-scheme:dark){.wehomeCta{background:var(--mint);color:#04211c}}
.ctaBanner{background:var(--card);border:1px solid var(--line);border-radius:16px;
 padding:22px 24px;margin:24px 0;text-align:center}
.ctaBanner .t{font-weight:750;font-size:16px}
.ctaBanner .d{font-size:13.5px;color:var(--muted);margin-top:6px}
.searchBox{position:relative;margin:20px 0}
.searchBox input{width:100%;padding:11px 14px;border-radius:10px;border:1px solid var(--line);
 background:var(--bg);color:var(--fg);font-size:14px}
.searchResults{display:none;position:absolute;top:calc(100% + 6px);left:0;right:0;
 background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px;
 max-height:360px;overflow-y:auto;z-index:5;box-shadow:0 8px 24px rgba(0,0,0,.15)}
.searchResultItem{display:block;padding:9px 10px;border-radius:8px;text-decoration:none;
 color:var(--fg);font-size:13.5px;line-height:1.4}
.searchResultItem:hover{background:var(--bg)}
.searchResultItem .srType{display:block;font-size:10.5px;color:var(--mint);font-weight:700;margin-bottom:2px}
"""


def nav(active: str, depth: int = 0) -> str:
    p = "../" * depth
    items = [("landing", "홈", f"{p}index.html"),
             ("dashboard", "대시보드", f"{p}dashboard.html"),
             ("estimate", "지역별 시장 지표", f"{p}estimate.html"),
             ("news", "뉴스", f"{p}news.html"),
             ("competitors", "글로벌 OTA 뉴스룸", f"{p}competitors.html"),
             ("reports", "월간 리포트", f"{p}reports.html")]
    links = "".join(
        f'<a href="{url}" class="{"active" if key == active else ""}">{label}</a>'
        for key, label, url in items
    )
    return f"""<nav><div class="navin">
<a class="brand" href="{p}index.html">{TITLE} <span>·</span> WEHOST</a>
<div class="navlinks">{links}</div>
</div></nav>"""


def page(title: str, active: str, depth: int, body: str, description: str = "", wide: bool = False,
          path: str = "", jsonld: dict | None = None) -> str:
    """
    path는 이 페이지의 site/ 루트 기준 상대경로(예: "dashboard.html", "report/2026-08.html",
    루트면 "") — canonical·OG url을 절대경로로 만드는 데만 쓴다. SITE_BASE_URL이 없으면
    (로컬 개발) 이 태그들을 아예 안 낸다 — 가짜 도메인을 SNS 공유 미리보기에 노출시키지
    않기 위해서다.

    jsonld는 이 페이지의 구조화 데이터(schema.org). 생성형 검색엔진이 인용할 때 쓰는
    신호라 데이터를 싣는 페이지에만 붙인다 — 목록성 페이지엔 붙일 게 없어 None이다.
    """
    full_title = f"{title} · {TITLE}"
    ga4 = ""
    if GA4_MEASUREMENT_ID:
        ga4 = (f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA4_MEASUREMENT_ID}"></script>\n'
               f"<script>window.dataLayer=window.dataLayer||[];"
               f"function gtag(){{dataLayer.push(arguments)}}"
               f"gtag('js',new Date());gtag('config','{GA4_MEASUREMENT_ID}');</script>\n")
    og = ""
    if SITE_BASE_URL:
        url = f"{SITE_BASE_URL}/{path}"
        og = (f'<link rel="canonical" href="{url}">\n'
              f'<meta property="og:type" content="website">\n'
              f'<meta property="og:site_name" content="{TITLE}">\n'
              f'<meta property="og:title" content="{full_title}">\n'
              f'<meta property="og:description" content="{description}">\n'
              f'<meta property="og:url" content="{url}">\n'
              f'<meta property="og:image" content="{SITE_BASE_URL}/og.png">\n'
              f'<meta property="og:image:width" content="1200">\n'
              f'<meta property="og:image:height" content="630">\n'
              f'<meta name="twitter:card" content="summary_large_image">\n')
    if NAVER_SITE_VERIFICATION:
        og += f'<meta name="naver-site-verification" content="{NAVER_SITE_VERIFICATION}">\n'
    if jsonld:
        og += ('<script type="application/ld+json">'
               f"{json.dumps(jsonld, ensure_ascii=False)}</script>\n")
    return f"""<title>{full_title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{description}">
{ga4}{og}<style>{CSS}</style>
{nav(active, depth)}
<div class="wrap{' wide' if wide else ''}">
{body}
</div>"""


DATA_PROVENANCE = ("행정안전부 지방행정 인허가 데이터(file.localdata.go.kr) 직접 수집·집계, "
                    "공공누리 제4유형")


def dataset_ld(name: str, description: str, path: str, ym: str) -> dict | None:
    """
    데이터를 싣는 페이지용 schema.org Dataset. 생성형 검색엔진에 인용되려면 숫자가
    어디서 왔고 언제 기준인지가 기계가 읽는 형태로 있어야 한다 — 본문에만 적어두면
    사람만 본다. SITE_BASE_URL이 없으면 절대 URL을 못 만들어 통째로 생략한다.
    """
    if not SITE_BASE_URL:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name,
        "description": description,
        "url": f"{SITE_BASE_URL}/{path}",
        "temporalCoverage": ym,
        "isAccessibleForFree": True,
        "creator": {"@type": "Organization", "name": "위홈", "url": "https://www.wehome.me"},
        "license": "https://www.kogl.or.kr/info/license.do",
        "spatialCoverage": {"@type": "Place", "name": "대한민국"},
        "includedInDataCatalog": {"@type": "DataCatalog", "name": "행정안전부 지방행정 인허가 데이터"},
    }


def report_ld(iss: Issue) -> dict | None:
    """월간 리포트 한 호 = 발행물 하나. Dataset이 아니라 Article로 잡는다."""
    if not SITE_BASE_URL:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"공유숙박 마켓리포트 {iss.ym}",
        "description": f"{iss.ym} 기준 외국인관광도시민박업 영업중 {iss.flagship.active:,}곳, "
                       f"서울 비중 {iss.seoul_share:.0%}.",
        "url": f"{SITE_BASE_URL}/report/{iss.ym}.html",
        "datePublished": f"{iss.ym}-01",
        "author": {"@type": "Organization", "name": "위홈", "url": "https://www.wehome.me"},
        "publisher": {"@type": "Organization", "name": TITLE},
        "isAccessibleForFree": True,
    }


def sitemap_entries(issue_yms: list[str], current_ym: str) -> list[tuple[str, str]]:
    """
    (경로, lastmod) 목록. lastmod가 없으면 크롤러는 이 사이트가 매달 갱신된다는 걸
    모른다. 지난 호 리포트는 발행 후 안 바뀌므로 자기 달 1일을 주고, 이번 호와
    나머지 페이지는 매 빌드 갱신되므로 오늘 날짜를 준다.
    """
    today = date.today().isoformat()
    entries = [(p, today) for p in
               ("", "dashboard.html", "reports.html", "news.html", "competitors.html", "estimate.html")]
    return entries + [(f"report/{ym}.html", today if ym == current_ym else f"{ym}-01")
                      for ym in issue_yms]


def write_og_image(iss: Issue) -> None:
    """
    SNS·카카오톡 공유 미리보기용 대표 이미지(1200×630) — site/og.png.
    이게 없으면 링크를 어디에 붙여도 썸네일이 안 뜬다. 로고 에셋이 없으므로 이번 호
    핵심 숫자를 그대로 표지로 쓴다(매달 빌드마다 자동 갱신).
    """
    fig = viz.plt.figure(figsize=(12, 6.3), dpi=100)
    fig.patch.set_facecolor(viz.NAVY)
    fig.text(.06, .80, "SHARED STAY MARKET REPORT", color=viz.MINT, fontsize=17, fontweight="bold")
    fig.text(.06, .62, "공유숙박 시장, 숫자로 읽습니다", color="white", fontsize=40, fontweight="bold")
    fig.text(.06, .34, f"{iss.flagship.active:,}", color=viz.MINT, fontsize=66, fontweight="bold")
    fig.text(.06, .24, f"외도민업 영업중 · 서울 비중 {iss.seoul_share:.0%} · {iss.ym} 기준",
             color="white", fontsize=19)
    fig.text(.06, .10, "행정안전부 원본 등록 데이터 직접 집계", color=viz.GREY, fontsize=15)
    fig.savefig(SITE / "og.png", facecolor=viz.NAVY)
    viz.plt.close(fig)


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
  const categories = [...document.querySelectorAll('.subCategory:checked')].map(el => el.value);
  const msg = document.getElementById('subMsg');
  if (!consent) {{ msg.textContent = '수신 동의가 필요합니다.'; msg.className = 'formMsg err'; return; }}
  try {{
    const res = await fetch('{SUBSCRIBE_ENDPOINT}', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{email, consent, categories}})
    }});
    const data = await res.json();
    if (res.ok) {{
      if (data.status === 'already_active') {{
        msg.textContent = '이미 구독 중인 이메일입니다.';
      }} else if (data.mail && data.mail.dry_run) {{
        msg.textContent = '인증 메일을 보내드리려 했지만, 지금은 개발 환경이라 실제 발송되지 않았습니다.';
      }} else if (data.mail && data.mail.sent) {{
        msg.textContent = '이메일함에서 인증 링크를 눌러주세요 — 인증을 완료하면 구독이 시작됩니다.';
      }} else {{
        msg.textContent = '요청은 저장됐지만 인증 메일 발송에 실패했습니다: ' + (data.mail && data.mail.error || '');
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


def _hero_art(monthly: list[tuple[str, int]]) -> str:
    """히어로 우측 장식 그래픽 — 스톡사진 대신 실제 등록추이 데이터를 수치 없이 실루엣만
    보여준다. 이 사이트의 정체성이 '추정치 없는 원본 데이터'라, 사진보다 실데이터로 만든
    그래픽이 메시지에 맞는다는 판단. 텍스트와 겹치는 왼쪽은 CSS mask로 흐리게 뺀다."""
    vals = [v for _, v in monthly]
    n = len(vals)
    W, H = 320, 240
    vmax = max(vals) or 1
    pts = [(i / (n - 1) * W, H * .1 + (1 - v / vmax) * H * .7) for i, v in enumerate(vals)]
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = line + f" L {W:.1f},{H:.1f} L 0,{H:.1f} Z"
    return f"""<svg class="heroArt" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<defs><linearGradient id="heroFade" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="var(--mint)" stop-opacity=".35"/>
<stop offset="100%" stop-color="var(--mint)" stop-opacity="0"/>
</linearGradient></defs>
<path d="{area}" fill="url(#heroFade)"/>
<path d="{line}" fill="none" stroke="var(--mint)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
</svg>"""


def render_landing(d: SiteData) -> str:
    c = d.current
    top3 = c.flagship.district_rank(SEOUL, 3)
    top3_txt = "·".join(f"{gu} {cnt:,}곳" for gu, cnt in top3)
    latest = d.all_issues[0]

    body = f"""
<div class="hero">
  <div>
    <div class="kicker">SHARED STAY MARKET REPORT</div>
    <h1>공유숙박 시장,<br>숫자로 읽습니다</h1>
    <div class="heroSub">행정안전부 원본 등록 데이터를 매달 직접 받아 집계합니다.
    추정치·샘플 데이터 없이, 등록 추이·지역별 밀도·포화 신호·규제 동향을 한 곳에서 확인하세요.</div>
    <a class="wehomeCta" href="{wehome_cta_url('landing_hero')}" target="_blank" rel="noopener">위홈에 호스트로 등록하기 →</a>
  </div>
  {_hero_art(c.flagship.recent_months(24))}
</div>

<div class="previewCard">
  <div class="kicker">이번 달 미리보기 · {c.ym}</div>
  <div class="kpis">
    <div class="kpi"><div class="l">외도민업 영업중</div><div class="v">{c.flagship.active:,}</div></div>
    <div class="kpi"><div class="l">서울 비중</div><div class="v">{c.seoul_share:.0%}</div></div>
    <div class="kpi"><div class="l">상위 3개구</div><div class="v" style="font-size:16px;margin-top:8px;white-space:normal;line-height:1.4">{top3_txt}</div></div>
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
  <h2 style="margin-top:8px;padding-top:0;border-top:none">이메일로 받아보기</h2>
  <div class="h2sub" style="margin-bottom:0">발행 즉시 이메일로 보내드립니다. 언제든 수신거부할 수 있습니다.</div>
  <form id="subForm">
    <input type="email" id="subEmail" placeholder="you@example.com" required>
    <button type="submit">구독하기</button>
  </form>
  <div class="catpicker">
    {"".join(f'<label><input type="checkbox" class="subCategory" value="{k}" checked>{v}</label>'
             for k, v in subscribers.CATEGORIES.items())}
  </div>
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
                f"외도민업 영업중 {c.flagship.active:,}곳, 서울 {c.seoul_share:.0%}.", path="",
                jsonld=dataset_ld(
                    "공유숙박(외국인관광도시민박업) 등록 현황",
                    f"{c.ym} 기준 전국 외국인관광도시민박업 영업중 {c.flagship.active:,}곳. "
                    f"{DATA_PROVENANCE}.", "", c.ym))


def bills_html(reg_bills: dict[str, list[regulation.BillMatch]]) -> str:
    """TRACKED_ACTS(현재 관광진흥법 하나) 계류 법안 카드 — 대시보드와 estimate.html이 같은
    마크업을 쓴다(perf_table_html과 동일한 이유로 중복 방지). 법 개정은 전국 공통이라
    지역을 어디로 고르든 똑같이 적용된다."""
    out = ""
    for act, matches in reg_bills.items():
        for b in matches:
            out += (f'<div class="billcard"><b>⚖️ {b.title}</b>'
                     f'<div class="meta">의안번호 {b.bill_no} · {b.committee} · '
                     f'조회 {b.views:,} · <a href="{b.url}" target="_blank" rel="noopener">원문</a></div></div>')
    if not out:
        out = f'<div class="sub">추적 중인 법률({", ".join(reg_bills)}) 개정안이 현재 계류 중이지 않습니다.</div>'
    return out


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

    bills_block = bills_html(d.reg_bills)

    body = f"""
<div class="kicker">MARKET DASHBOARD</div>
<h1>공유숙박 마켓 대시보드</h1>
<div class="sub">{c.ym} 기준 · {date.today():%Y-%m-%d} 갱신 · 행안부 원본 데이터 직접 수집</div>

<div class="kpis">
  <div class="kpi reveal"><div class="l">외도민업 영업중</div><div class="v" data-count>{c.flagship.active:,}</div>{delta_html}</div>
  <div class="kpi reveal"><div class="l">폐업률</div><div class="v" data-count>{c.flagship.closure_rate:.1%}</div>
    <div class="d">누적 {c.flagship.total:,}건 중 {c.flagship.closed:,}건</div></div>
  <div class="kpi reveal"><div class="l">서울 비중</div><div class="v" data-count>{c.seoul_share:.0%}</div>
    <div class="d">{c.seoul_active:,}곳</div></div>
  <div class="kpi reveal"><div class="l">상위 3개구 집중도</div><div class="v" data-count>{c.concentration(3):.0%}</div>
    <div class="d">{top3_txt}</div></div>
</div>

<h2>등록 추이</h2>
<div class="h2sub">최근 24개월 월별 신규등록(인허가일자 기준, 현재 상태 무관). 최신월 강조.</div>
<div class="reveal">{chart_registrations_trend(c.flagship.recent_months(24))}</div>

<h2>등록연도별 생존곡선</h2>
<div class="h2sub">아직 폐업하지 않은 곳을 우변절단으로 반영한 실제 생존율 — "폐업 건만 본
존속기간"과 달리 생존편향이 없다. 짙은 남색일수록 오래된 등록연도, 민트에 가까울수록
최근 연도(선 끝에 연도 표기). 표본 30건 미만인 코호트는 뺐다.</div>
<div class="reveal">{chart_cohort_survival(c.flagship.cohort_survival)}</div>

<h2>전국 시도별 현황</h2>
<div class="h2sub">서울에 국한하지 않은 전국 17개 시도 영업중 호스트 순위. 진할수록 밀도가 높은 지역 —
지도에 마우스를 올리면 시도별 수치가 뜬다.</div>
<div class="mapwrap reveal">{render_sido_map(c.flagship)}</div>
<div class="reveal">{chart_sido_rank(c.flagship)}</div>

<h2>서울 자치구 순위</h2>
<div class="h2sub">영업중 호스트 수 기준. 상위 3개 구가 전체의 {c.concentration(3):.0%}를 차지.</div>
<div class="reveal">{chart_district_rank(c.flagship)}</div>

<h2>포화 신호</h2>
<div class="h2sub">밀도(영업중 호스트 수)와 최근 6개월 증감률을 산점도 4분면으로 — 오른쪽 위(성장)는
이미 크면서 더 크는 중, 오른쪽 아래(포화)는 크지만 유입이 식는 중, 왼쪽 위(기회)는 아직
작지만 빠르게 크는 중. 아래 표는 상위 8개 구의 정확한 수치.</div>
<div class="reveal">{chart_saturation_scatter(c.flagship.saturation_signal(SEOUL))}</div>
<div class="scroll"><table><tr><th>구</th><th style="text-align:right">영업중</th>
<th style="text-align:right">최근 6개월 신규</th><th style="text-align:right">직전 6개월 대비</th></tr>{sat_rows}</table></div>

<h2>카테고리 비교</h2>
<div class="h2sub">공유숙박 5종 등록 규모. 농어촌민박이 절대 우위지만 도시 시장은 별개 축.</div>
<div class="reveal">{chart_category_compare(c.categories)}</div>

{demand_kpis_html(d.demand)}

{visitor_demand_html(d.visitors)}

{entry_index_html(d.entry_index)}

{perf_table_html(d.perf)}

<h2>이번 달 계류 법안</h2>
<div class="h2sub">국회 입법예고 기준 상시 추적.</div>
{bills_block}

<h2>규제·정책 동향</h2>
<div class="h2sub">문체부·정책브리핑 자동 수집 · 공유숙박 키워드 매칭</div>
{news_html}

<div class="ctaBanner">
  <div class="t">지금 이 시장에 뛰어들고 싶다면</div>
  <div class="d">위에서 본 등록 추이·구별 순위·포화 신호를 직접 확인했으니, 위홈에서 호스트로 시작해보세요.</div>
  <a class="wehomeCta" href="{wehome_cta_url('dashboard_banner')}" target="_blank" rel="noopener">위홈에 호스트로 등록하기 →</a>
</div>

<h2>데이터 신뢰도</h2>
<div class="note">{d.reconcile_note}</div>
<div class="note warn">K-STAY의 /analysis 페이지에 있는 "Airbnb 리스팅 대비 미등록률" 수치는
페이지 자체 표기대로 시뮬레이션된 데모용 샘플입니다. 이 사이트는 그 수치를 인용하지 않고
행정안전부 원본 등록 데이터를 직접 받아 집계합니다(k-stay API 미사용).</div>

{FOOTER}
<script>
(function() {{
  const els = document.querySelectorAll('.reveal');
  if (!('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
    els.forEach(e => e.classList.add('in'));
    return;
  }}
  // KPI 숫자는 data-count 붙은 것만 카운트업한다 — 관광 수요 지표처럼 "1억 7,960만명" 같은
  // 복합 단위 문자열은 앞자리 "1"만 숫자로 오인해 애니메이션이 이상해진다(실측 확인).
  function animateCount(el) {{
    const raw = el.textContent.trim();
    const m = raw.match(/^(-?[\\d,]+(?:\\.\\d+)?)(.*)$/);
    if (!m) return;
    const target = parseFloat(m[1].replace(/,/g, ''));
    const suffix = m[2];
    const decimals = (m[1].split('.')[1] || '').length;
    const start = performance.now();
    const dur = 900;
    requestAnimationFrame(function frame(now) {{
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      const val = Number((target * eased).toFixed(decimals));
      el.textContent = val.toLocaleString(undefined,
        {{minimumFractionDigits: decimals, maximumFractionDigits: decimals}}) + suffix;
      if (t < 1) requestAnimationFrame(frame); else el.textContent = raw;
    }});
  }}
  const obs = new IntersectionObserver((entries) => {{
    entries.forEach(entry => {{
      if (!entry.isIntersecting) return;
      entry.target.classList.add('in');
      const num = entry.target.querySelector('.v[data-count]');
      if (num) animateCount(num);
      obs.unobserve(entry.target);
    }});
  }}, {{threshold: .2}});
  els.forEach(e => obs.observe(e));
  // 안전망: 백그라운드 탭으로 열려 있다가 포커스를 영영 안 받는 등, IntersectionObserver가
  // 어떤 이유로든 끝내 안 터지면 콘텐츠가 opacity:0인 채 영원히 안 보이게 된다 — 애니메이션이
  // 실패하는 것보다 콘텐츠가 아예 안 보이는 게 훨씬 나쁘다. 일정 시간 후 강제로 다 보여준다.
  setTimeout(() => {{ els.forEach(e => e.classList.add('in')); }}, 3000);
}})();
</script>"""
    return page("대시보드", "dashboard", 0, body,
                f"외국인관광도시민박업 영업중 {c.flagship.active:,}곳, 서울 {c.seoul_share:.0%}. "
                "행정안전부 공공데이터 기반 공유숙박 시장 대시보드.", path="dashboard.html",
                jsonld=dataset_ld(
                    "공유숙박 시장 대시보드 — 등록 추이·지역별 분포",
                    f"{c.ym} 기준 외국인관광도시민박업 영업중 {c.flagship.active:,}곳, "
                    f"서울 비중 {c.seoul_share:.0%}. 카테고리별·시군구별 집계. "
                    f"{DATA_PROVENANCE}.", "dashboard.html", c.ym))


# ─────────────────────────────────────────────────────── 리포트 아카이브

def search_box_html() -> str:
    """
    뉴스+리포트 통합 검색 — build()가 만드는 site/search-index.json을 fetch해
    클라이언트에서 필터링한다. 정적 사이트라 서버 검색엔진을 새로 두는 대신,
    이 규모(수백 건)엔 클라이언트 필터링으로 충분하다고 판단. news.html·
    reports.html 둘 다 depth 0(사이트 루트)라 상대경로 하나로 같이 쓴다.
    """
    return """
<div class="searchBox">
  <input type="search" id="siteSearch" placeholder="뉴스·리포트 검색…" autocomplete="off">
  <div id="siteSearchResults" class="searchResults"></div>
</div>
<script>
(function() {
  let INDEX = null;
  const input = document.getElementById('siteSearch');
  const results = document.getElementById('siteSearchResults');
  input.addEventListener('input', async () => {
    const q = input.value.trim().toLowerCase();
    if (!q) { results.style.display = 'none'; results.innerHTML = ''; return; }
    if (!INDEX) INDEX = await fetch('search-index.json').then(r => r.json());
    const matches = INDEX.filter(it =>
      it.title.toLowerCase().includes(q) || (it.source || '').toLowerCase().includes(q)
    ).slice(0, 20);
    results.innerHTML = matches.length
      ? matches.map(it => `<a class="searchResultItem" href="${it.url}" ${it.type === 'news' ? 'target="_blank" rel="noopener"' : ''}>
          <span class="srType">${it.type === 'news' ? it.source : '월간 리포트'}</span>${it.title}</a>`).join('')
      : '<div class="sub" style="padding:10px 4px">검색 결과가 없습니다.</div>';
    results.style.display = 'block';
  });
})();
</script>"""


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
{search_box_html()}
<div class="archive" style="margin-top:24px">{cards}</div>
{FOOTER}"""
    return page("월간 리포트", "reports", 0, body, "공유숙박 시장 월간 리포트 발행 이력.", path="reports.html")


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
{search_box_html()}
<div class="newsgrid">{cols}</div>
{FOOTER}"""
    return page("뉴스", "news", 0, body,
                f"공유숙박·숙박업 산업 뉴스 자동 수집 아카이브. {len(by_source)}개 소스, "
                f"{len(d.news_items):,}건.", wide=True, path="news.html")


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
                "위홈·에어비앤비·아고다·부킹닷컴·클룩 공식 뉴스룸 보도자료 자동 수집.", wide=True,
                path="competitors.html")


# ─────────────────────────────────────────────────────── 지역별 시장 지표

def compute_regions(d: SiteData) -> tuple[list[dict], dict[str, list[dict]]]:
    """
    시군구 단위 지표 — estimate.html(SPA 조회 도구)과 area/*.html(지역별 정적 페이지,
    render_district_page) 양쪽이 쓰는 공통 계산. 등급(tier)·추세(trend)·판정(verdict)·
    전국/시도 순위·진입 적합도(entry)까지 여기서 다 붙여서 두 렌더러가 같은 걸 두 번
    계산하지 않게 한다.

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

    def verdict(tier_: str, trend_: str) -> tuple[str, str]:
        """
        규모(tier)×증감(trend) 조합을 "포화 주의"류 한 줄 결론으로 — 숫자만 보여주고
        판단은 사용자에게 떠넘기지 않는다("검색했을 때 100% 만족해야" 요청에 대한 응답).
        tone은 결론 카드 색을 정하는 신호등 — positive=진입 유리, warning=회피 신호,
        caution=유리하지만 주의, neutral=판단 재료 부족/무난. 새 색을 만들지 않고 사이트에
        이미 있는 4가지 의미색(mint=up, red=down, amber=warn, navy=중립)만 재사용한다.
        """
        if trend_ == "신규 진입":
            return "신규 진입 지역 — 등록 이력이 막 생기기 시작해 아직 판단하기엔 이릅니다.", "neutral"
        big = tier_ in ("대형", "중형")
        if big and trend_ == "위축":
            return "포화 주의 — 이미 호스트가 많은데 최근 신규 유입은 둔화됐습니다.", "warning"
        if big and trend_ == "성장":
            return "경쟁 치열 — 이미 큰 시장인데도 계속 성장하고 있습니다.", "caution"
        if big and trend_ == "안정":
            return "성숙 시장 — 규모가 크고 안정적으로 유지되고 있습니다.", "neutral"
        if not big and trend_ == "성장":
            return "성장 기회 — 아직 진입자가 적은데 최근 유입이 늘고 있습니다.", "positive"
        if not big and trend_ == "위축":
            return "관망 필요 — 진입자도 적고 최근 유입도 둔화됐습니다.", "caution"
        return "틈새 시장 — 소규모지만 꾸준히 유지되고 있습니다.", "neutral"

    for r in regions:
        r["tier"] = tier(r["active"])
        r["trend"] = trend(r["growth"])
        r["growth_pct"] = None if r["growth"] == float("inf") else round(r["growth"] * 100)
        del r["growth"]
        r["ynj_region"] = yanolja_perf.SIDO_TO_REGION.get(r["sido"])
        # 판정 문구는 전부 "라벨 — 설명." 형태다. 라벨만 떼어 배지로 띄운다 — 판정은
        # 카테고리 정보라 색을 실을 자리가 여기지, 카드 테두리가 아니다.
        _full, r["verdict_tone"] = verdict(r["tier"], r["trend"])
        r["verdict_label"], _, r["verdict"] = _full.partition(" — ")

    regions.sort(key=lambda r: -r["active"])
    for i, r in enumerate(regions, 1):
        r["national_rank"] = i

    by_sido_group: dict[str, list[dict]] = {}
    for r in regions:
        by_sido_group.setdefault(r["sido"], []).append(r)
    for group in by_sido_group.values():
        group.sort(key=lambda r: -r["active"])
        for i, r in enumerate(group, 1):
            r["sido_rank"] = i
        for r in group:
            r["sido_total"] = len(group)

    # 진입 적합도 지수(localdata.entry_index, d.entry_index) — 대시보드 TOP10 표와 같은
    # 원본을 여기 구 단위 조회에도 붙인다. min_active 미만·growth=inf·방문자수 매칭 없음 등
    # 으로 지수 계산에서 빠진 구는 entry가 None — 0점으로 채우지 않고 "산정 불가"로 안내한다.
    entry_by_region = {f"{e['sido']} {e['sigungu']}": {**e, "ei_rank": i}
                        for i, e in enumerate(d.entry_index, 1)}
    for r in regions:
        r["entry"] = entry_by_region.get(f"{r['sido']} {r['sigungu']}")

    return regions, by_sido_group


def render_estimate(d: SiteData, regions: list[dict]) -> str:
    """
    "지역을 고르면 예상 수익을 보여달라" 요청에 대한 응답 — 단 AirDNA류의
    "예상 수익(원/박)" 숫자를 우리가 만들어내진 않는다. 등록 밀도·증감률(자체 집계)에
    더해, 야놀자리서치가 이미 공개 발행한 ADR·OCC·RevPAR(공유숙박 부문, NOL+AirDNA+
    산하정보기술 블렌딩 — yanolja_perf.py)을 있는 그대로 얹는다. 다만 이 API는 광역
    10개 권역 단위라 시군구별로 나오지 않는다 — 선택한 시도가 매핑되는 권역이 있을
    때만 보여주고, 없으면 조용히 숨긴다(억지로 인접 권역 수치를 끼워 보여주지 않는다).

    regions는 compute_regions(d)가 이미 계산해둔 것 — build()가 area/*.html과 공유하려고
    한 번만 계산해 넘긴다.
    """
    national_total = len(regions)
    ei_total = len(d.entry_index)
    ei_avg = round(statistics.mean(e["index"] for e in d.entry_index)) if d.entry_index else 0

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
    # 원래는 " · …도 함께 표시됩니다."를 첫 문장 중간에 이어 붙였다 — 값 설명 문장 하나에
    # 용어 3개(등록 밀도·증감률·외도민업)가 한꺼번에 몰려 첫 화면부터 읽기 무거웠다.
    # 독립 문장으로 떼어 순서를 "뭘 보여주는지 → 무슨 기준인지"로 정리한다. "원화
    # 예상수익이 아닙니다" 한 줄도 뺐다 — 아직 지역도 안 골랐는데 결과부터 부인하는
    # 순서였고, 같은 내용을 실제 수치가 보이는 자리(아래 note.warn)에서 더 자세히 설명한다.
    perf_note = (f" 선택한 권역이 야놀자리서치 실적 지표 대상이면 평균 객단가·점유율·객실당매출도 "
                 f"함께 보여드립니다({perf_ym[:4]}-{perf_ym[4:]} 기준, 광역 {len(d.perf)}개 권역)." if perf_ym else "")
    bills_block = bills_html(d.reg_bills)

    body = f"""
<div class="kicker">HOST MARKET LOOKUP</div>
<h1>지역별 시장 지표</h1>
<div class="sub">시도·시군구를 선택하면 그 지역에 등록된 호스트 수, 최근 증감 추이, 진입 적합도를
보여드립니다. 외국인관광 도시민박업(외도민업) 등록 기준입니다.{perf_note}</div>

<div class="lookup">
  <select id="lkSido"><option value="">시도 선택</option>{sido_options}</select>
  <select id="lkGu" disabled><option value="">시군구 선택</option></select>
</div>

<div id="lkResult" class="lkResult" style="display:none">
  <div class="verdictCard" id="lkVerdictCard">
    <div class="vcTop">
      <div class="vcRegion" id="lkRegionName">-</div>
      <div class="vcRank" id="lkRankBadge">-</div>
    </div>
    <div class="vcVerdict"><span class="vcBadge" id="lkVerdictBadge">-</span><span id="lkVerdict">-</span></div>
    <div class="vcStats">
      <div><div class="vsV" id="lkActive">-</div><div class="vsL">영업중 호스트</div></div>
      <div><div class="vsV" id="lkTier">-</div><div class="vsL">시장 규모</div></div>
      <div><div class="vsV" id="lkRecent">-</div><div class="vsL">최근 6개월 신규</div></div>
      <div><div class="vsV" id="lkGrowth">-</div><div class="vsL">직전 6개월 대비</div></div>
    </div>
  </div>

  <h2 style="margin-top:26px">진입 적합도 지수</h2>
  <div class="sub" style="margin:-4px 0 6px">등록 데이터(성장·생존·적합도)와 방문자수(수요)를 결합한
  자체 지수, 백분위 평균입니다. 규모(호스트 수)는 의도적으로 제외했습니다.</div>
  <div id="lkEi">
    <div class="eiScore">
      <span class="eiScoreV" id="lkEiScore">-</span><span class="eiScoreMax">/100</span>
      <span class="eiScoreRank" id="lkEiRank"></span>
    </div>
    <div class="eiGauge" id="lkEiGauge">
      <div class="eiGaugeAvg" id="lkEiGaugeAvg"></div>
      <div class="eiGaugeMark" id="lkEiGaugeMark"></div>
    </div>
    <div class="eiGaugeLabels"><span>포화·위축</span><span>성장 기회</span></div>
    <div class="eiGaugeCompare" id="lkEiCompare"></div>
    <div class="eiAxes" id="lkEiAxes"></div>
  </div>
  <div id="lkEiEmpty" class="sub" style="display:none">영업중 호스트가 너무 적거나 최근 6개월
  신규등록이 없어(비교 불가) 이 지역은 지수를 계산할 수 없습니다.</div>

  <h2 style="margin-top:36px;padding-top:18px">영업 현황(등록 이력 전체 기준)</h2>
  <div class="statusbar" id="lkStatusBar"></div>
  <div class="statuslegend" id="lkStatusLegend"></div>

  <h2 style="margin-top:36px;padding-top:18px">최근 12개월 신규등록 추이</h2>
  <div class="sub" id="lkTrendCaveat" style="display:none;margin:-4px 0 6px"></div>
  <div class="trendspark" id="lkTrendSpark"></div>

  <div id="lkPerf" style="display:none">
    <h2 style="margin-top:28px">공유숙박 실적 지표 <span id="lkPerfRegion"></span></h2>
    <div class="h2sub">출처: 야놀자리서치 국내 숙박업 실적 지표(<span id="lkPerfYm"></span> 기준). 자세한 산출 방식과
    "개별 매물 예상 수익이 아닌 이유"는 아래 안내를 참고하세요.</div>
    <div class="kpis">
      <div class="kpi"><div class="l">평균 객단가</div><div class="v" id="lkAdr">-</div></div>
      <div class="kpi"><div class="l">객실 점유율</div><div class="v" id="lkOcc">-</div></div>
      <div class="kpi"><div class="l">객실당매출</div><div class="v" id="lkRevpar">-</div></div>
    </div>
  </div>
  <div id="lkPerfEmpty" class="sub" style="display:none;margin-top:20px">
    이 권역은 야놀자리서치 실적 지표 커버리지 밖입니다(대구·대전·인천·울산·세종).</div>

  <h2 style="margin-top:36px;padding-top:18px">관광진흥법 개정 동향</h2>
  <div class="sub" style="margin:-4px 0 6px">법 개정은 전국 공통이라 어느 지역을 고르든 똑같이 적용됩니다 —
  이 지역만의 규제가 아닙니다. <a href="dashboard.html">대시보드에서 규제·정책 동향 전체 보기 →</a></div>
  {bills_block}
</div>
<div id="lkEmpty" class="sub" style="display:none">이 지역은 등록 표본이 없습니다.</div>

<!-- 지도·순위는 "어디를 볼지 고르는" 탐색 도구지 답이 아니다 — 시도만 고른 상태에선
     선택창 바로 아래에 와서 주 콘텐츠가 되고, 시군구까지 골랐으면 답(lkResult) 아래로
     내려가 "다른 지역 둘러보기" 역할이 된다. 예전엔 이게 항상 위에 있어서, 고른 지역의
     판정이 나오기까지 지도(480px)와 남의 구 24개 순위(934px)를 지나 1,600px을 스크롤해야
     했다 — 방금 던진 질문의 답이 화면 두 개 아래 있었다는 뜻. -->
<div id="lkRankBox" style="display:none">
  <h2>시도 내 시군구 순위</h2>
  <div class="h2sub">지도나 막대를 눌러도 해당 시군구를 바로 조회할 수 있습니다.</div>
  <div class="mapwrap" id="lkMapWrap" style="display:none"></div>
  <div id="lkRankList" class="ranklist"></div>
</div>

<!-- CTA는 lkResult 밖에 둔다 — 순위 블록 위에 놓으려고 lkResult 안에 넣으면, 시도만
     고른 탐색 단계에서 lkResult가 통째로 숨을 때 순위 블록까지 같이 사라진다. 대신
     표시 여부는 lkResult와 항상 같이 움직인다(지역을 안 골랐는데 "방금 확인한 …를
     바탕으로"라고 말할 순 없으니). 그래서 결과를 켜고 끄는 세 자리에서 함께 토글한다. -->
<div class="ctaBanner" id="lkCta" style="display:none">
  <div class="t">이 지역에서 시작해보고 싶다면</div>
  <div class="d">방금 확인한 등록 밀도·증감률을 바탕으로, 위홈에서 호스트로 등록해보세요.</div>
  <a class="wehomeCta" href="{wehome_cta_url('estimate_result')}" target="_blank" rel="noopener">위홈에 호스트로 등록하기 →</a>
</div>

<div class="note warn">등록 밀도·증감률은 행정안전부 등록 건수 기반이라 실제 숙박요금·점유율을
반영한 예상 수익이 아닙니다. 평균 객단가·객실 점유율·객실당매출은 실제 실적 지표이지만 개별 매물이 아닌 권역
평균값입니다 — 이 사이트는 AirDNA 원본 데이터를 그들의 이용약관(크롤링·경쟁 서비스 제작 금지)상
직접 끌어와 쓸 수 없어, 이미 공개 발행된 야놀자리서치 지표로 대신합니다.</div>

{FOOTER}
<script>
const LK_DATA = {data_json};
const YNJ_DATA = {perf_json};
const NATIONAL_TOTAL = {national_total};
const EI_TOTAL = {ei_total};
const EI_AVG = {ei_avg};
const MAP_SIDOS = new Set({map_sidos_json});
const sidoEl = document.getElementById('lkSido');
const guEl = document.getElementById('lkGu');
const result = document.getElementById('lkResult');
const cta = document.getElementById('lkCta');
const empty = document.getElementById('lkEmpty');
const perfBox = document.getElementById('lkPerf');
const perfEmpty = document.getElementById('lkPerfEmpty');
const rankBox = document.getElementById('lkRankBox');
const rankList = document.getElementById('lkRankList');
const mapWrap = document.getElementById('lkMapWrap');
const PIN_SVG = '<g id="lkPin" style="display:none">'
  + '<path class="pinbody" d="M0,0 C-3,-8 -15,-14 -15,-24 A15,15 0 1,1 15,-24 C15,-14 3,-8 0,0 Z"/>'
  + '<circle class="pinhole" cx="0" cy="-24" r="7"/>'
  + '<rect class="pinbadge" x="-32" y="-76" width="64" height="22" rx="11"/>'
  + '<text class="pinbadgetext" x="0" y="-65"></text></g>';

// 클릭한 시군구 위치에 핀을 찍고 그 안(위 배지)에 영업중 호스트 수를 숫자로 보여준다.
// 핀은 path의 실제 중심 좌표(data-cx/cy)에 꽂는다 — 라벨 좌표는 겹침 방지로 밀렸을 수
// 있어 쓰면 핀이 엉뚱한 데 찍힌다. 지도를 새로 불러올 때마다(innerHTML 통째로 교체)
// 핀 <g>도 같이 날아가므로 그때마다 다시 붙여야 한다.
function showPin(name) {{
  const svg = mapWrap.querySelector('svg');
  const path = svg && svg.querySelector('path[data-name="' + name + '"]');
  if (!svg || !path) {{ const old = mapWrap.querySelector('#lkPin'); if (old) old.style.display = 'none'; return; }}
  let pin = svg.querySelector('#lkPin');
  if (!pin) {{
    svg.insertAdjacentHTML('beforeend', PIN_SVG);
    pin = svg.querySelector('#lkPin');
  }}
  const r = LK_DATA.find(x => x.sido === sidoEl.value && x.sigungu === name);
  pin.querySelector('.pinbadgetext').textContent = r ? r.active.toLocaleString() + '곳' : '';
  // 배지가 핀 위로 76유닛 튀어나오고 좌우로 32유닛 벌어진다 — 지도 위쪽·가장자리
  // 시군구(도봉구 등)를 찍으면 그대로 잘려 나간다. 뷰박스 안에 들어오게 자리를 민다.
  const vb = svg.viewBox.baseVal;
  const cx = Math.min(Math.max(parseFloat(path.dataset.cx), vb.x + 34), vb.x + vb.width - 34);
  const cy = Math.max(parseFloat(path.dataset.cy), vb.y + 80);
  pin.setAttribute('transform', `translate(${{cx}},${{cy}})`);
  pin.style.display = 'block';
}}

function selectGu(name) {{
  guEl.value = name; guEl.dispatchEvent(new Event('change'));
  // 지도·순위를 결과 아래로 내렸으니, 거기서 고르면 결과는 화면 위쪽 바깥에 그려진다 —
  // 스크롤을 안 옮기면 눌러도 아무 일도 안 일어난 것처럼 보인다. 드롭다운으로 고를 땐
  // 결과가 바로 밑에 나오고 그 경로는 selectGu를 안 거치므로 여기서만 처리하면 된다.
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  result.scrollIntoView({{behavior: reduce ? 'auto' : 'smooth', block: 'start'}});
}}

mapWrap.addEventListener('click', e => {{
  const p = e.target.closest('path[data-name]');
  if (p) selectGu(p.dataset.name);
}});
mapWrap.addEventListener('keydown', e => {{
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const p = e.target.closest('path[data-name]');
  if (p) {{ e.preventDefault(); selectGu(p.dataset.name); }}
}});

sidoEl.addEventListener('change', () => {{
  guEl.innerHTML = '<option value="">시군구 선택</option>';
  result.style.display = 'none'; cta.style.display = 'none'; empty.style.display = 'none';
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
    <div class="rankrow" data-gu="${{r.sigungu}}" tabindex="0" role="button"
         aria-label="${{r.sigungu}} 선택, 영업중 ${{r.active.toLocaleString()}}곳">
      <div class="rn">${{r.sido_rank}}</div>
      <div class="rname">${{r.sigungu}}</div>
      <div class="rbarwrap"><div class="rbar" style="width:${{Math.max(4, r.active / max * 100)}}%"></div></div>
      <div class="rcount">${{r.active.toLocaleString()}}곳</div>
    </div>`).join('');
  rankList.querySelectorAll('.rankrow').forEach(el => {{
    el.addEventListener('click', () => selectGu(el.dataset.gu));
    el.addEventListener('keydown', e => {{
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault(); selectGu(el.dataset.gu);
    }});
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
          mapWrap.querySelectorAll('path[data-name="' + guEl.value + '"], text[data-name="' + guEl.value + '"]')
            .forEach(el => el.classList.add('sel'));
          showPin(guEl.value);
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
  mapWrap.querySelectorAll('path[data-name], text[data-name]').forEach(el => {{
    el.classList.toggle('sel', el.dataset.name === guEl.value);
  }});
  if (guEl.value) {{
    showPin(guEl.value);
  }} else {{
    const pin = mapWrap.querySelector('#lkPin');
    if (pin) pin.style.display = 'none';
  }}
  if (!r) {{ result.style.display = 'none'; cta.style.display = 'none';
             empty.style.display = guEl.value ? 'block' : 'none'; return; }}
  document.getElementById('lkVerdictCard').dataset.tone = r.verdict_tone;
  document.getElementById('lkRegionName').textContent = `${{r.sido}} ${{r.sigungu}}`;
  document.getElementById('lkRankBadge').textContent =
    `전국 ${{r.national_rank}}위(${{NATIONAL_TOTAL.toLocaleString()}}곳 중) · ${{r.sido}} 내 ${{r.sido_rank}}위(${{r.sido_total}}곳 중)`;
  document.getElementById('lkVerdictBadge').textContent = r.verdict_label;
  document.getElementById('lkVerdict').textContent = r.verdict;
  document.getElementById('lkActive').textContent = r.active.toLocaleString() + '곳';
  document.getElementById('lkTier').textContent = r.tier;
  document.getElementById('lkRecent').textContent = r.recent6.toLocaleString() + '건';
  const growthEl = document.getElementById('lkGrowth');
  growthEl.textContent = r.growth_pct === null ? '신규'
    : (r.growth_pct >= 0 ? `▲+${{r.growth_pct}}%` : `▼${{Math.abs(r.growth_pct)}}%`);
  growthEl.classList.toggle('up', r.growth_pct !== null && r.growth_pct >= 0);
  growthEl.classList.toggle('down', r.growth_pct !== null && r.growth_pct < 0);

  const eiBox = document.getElementById('lkEi');
  const eiEmpty = document.getElementById('lkEiEmpty');
  if (r.entry) {{
    document.getElementById('lkEiScore').textContent = r.entry.index;
    document.getElementById('lkEiRank').textContent =
      `상위 ${{r.entry.ei_rank}}위 (지수 산정 가능 ${{EI_TOTAL.toLocaleString()}}곳 중)`;
    document.getElementById('lkEiGaugeMark').style.left = r.entry.index + '%';
    document.getElementById('lkEiGaugeAvg').style.left = EI_AVG + '%';
    const diff = r.entry.index - EI_AVG;
    document.getElementById('lkEiCompare').textContent = diff === 0
      ? `지수 산정 가능 지역 평균(${{EI_AVG}}점)과 같습니다.`
      : `지수 산정 가능 지역 평균(${{EI_AVG}}점)보다 ${{Math.abs(diff)}}점 ${{diff > 0 ? '높습니다' : '낮습니다'}}.`;
    const axes = [['성장', r.entry.pct_growth], ['생존', r.entry.pct_survival], ['적합도', r.entry.pct_fit]];
    if (r.entry.pct_demand !== undefined) axes.push(['수요', r.entry.pct_demand]);
    document.getElementById('lkEiAxes').innerHTML = axes.map(([label, v]) => `
      <div class="eiRow"><span class="eiL">${{label}}</span>
        <div class="eiBarWrap"><div class="eiBar" style="width:${{v}}%"></div></div>
        <span class="eiN">${{v}}</span></div>`).join('');
    eiBox.style.display = 'block'; eiEmpty.style.display = 'none';
  }} else {{
    eiBox.style.display = 'none'; eiEmpty.style.display = 'block';
  }}

  const statusTotal = r.active + r.pause + r.closed;
  const statusBar = document.getElementById('lkStatusBar');
  const segs = [['active', '영업중', r.active], ['pause', '휴업', r.pause], ['closed', '폐업', r.closed]];
  statusBar.innerHTML = segs.map(([cls, , n]) =>
    `<div class="seg ${{cls}}" style="width:${{statusTotal ? n / statusTotal * 100 : 0}}%"></div>`).join('');
  document.getElementById('lkStatusLegend').innerHTML = segs.map(([cls, label, n]) =>
    `<span><span class="dot ${{cls}}"></span>${{label}} ${{n.toLocaleString()}}곳 (${{statusTotal ? Math.round(n / statusTotal * 100) : 0}}%)</span>`
  ).join('');

  const maxMonthly = Math.max(1, ...r.monthly.map(m => m.n));
  // 위 "최근 6개월 신규"(recent6)가 실제로 합산한 달만 강조한다 — 단순히 "마지막 6칸"이
  // 아니다. 이번 달처럼 아직 등록 0건인 달은 recent6 계산에서 통째로 빠지고 그만큼 앞으로
  // 밀리는데, 달력칸(monthly)은 항상 마지막 12개월을 꽉 채워 보여주기 때문이다 — 그대로
  // "마지막 6칸=최근 6개월"로 칠하면 위 숫자와 안 맞는 걸 실측 중 발견했다(localdata.py의
  // recent6_yms 주석 참고).
  const recentYms = new Set(r.recent6_yms || []);
  const trendCaveat = document.getElementById('lkTrendCaveat');
  const last6CalendarYms = r.monthly.slice(-6).map(m => m.ym);
  const isStandardWindow = last6CalendarYms.every(ym => recentYms.has(ym)) && recentYms.size === 6;
  if (isStandardWindow) {{
    trendCaveat.style.display = 'none';
  }} else {{
    trendCaveat.textContent = '이번 달처럼 아직 등록 이력이 없는 달은 "최근 6개월"(진한 막대) 계산에서 빠지고, ' +
      '그 앞 달까지가 최근 구간으로 잡힙니다 — 위 "최근 6개월 신규"·"직전 6개월 대비"와 같은 기준입니다.';
    trendCaveat.style.display = 'block';
  }}
  let prevRecent = false;
  document.getElementById('lkTrendSpark').innerHTML = r.monthly.map((m, i) => {{
    const isRecent = recentYms.has(m.ym);
    const boundary = isRecent && !prevRecent && i > 0;
    prevRecent = isRecent;
    return `
    <div class="tcol ${{isRecent ? 'trecent' : ''}} ${{boundary ? 'tboundary' : ''}}">
      <div class="tval">${{m.n}}</div>
      <div class="tbarwrap" title="${{m.ym}}: ${{m.n}}건">
        <div class="tbar ${{isRecent ? 'trecentbar' : ''}}" style="height:${{m.n / maxMonthly * 100}}%"></div>
      </div>
      <div class="tlabel">${{i % 3 === 0 ? m.ym.slice(2) : ''}}</div>
    </div>`;
  }}).join('');

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

  result.style.display = 'block'; cta.style.display = 'block'; empty.style.display = 'none';
}});
</script>"""
    return page("지역별 시장 지표", "estimate", 0, body,
                "지역 선택 시 외도민업 등록 밀도·증감률과 야놀자리서치 평균 객단가·점유율·객실당매출 지표를 보여줍니다.",
                path="estimate.html",
                jsonld=dataset_ld(
                    "지역별 공유숙박 시장 지표 — 전국 시군구",
                    "전국 시군구별 외국인관광도시민박업 등록 밀도·증감률과 "
                    "권역별 평균 객단가·객실점유율·객실당매출(RevPAR). "
                    f"{DATA_PROVENANCE}, 야놀자리서치 교차검증.",
                    "estimate.html", d.current.ym))


# ─────────────────────────────────────────────────────── 지역별 정적 페이지 (pSEO)

DISTRICT_PAGE_MIN_ACTIVE = 20
# estimate.html은 URL이 하나뿐이라(드롭다운으로 클라이언트에서 데이터만 바꿔치기) 검색
# 엔진이 "마포구 공유숙박"류 지역 검색어로 이 사이트의 어떤 페이지도 색인할 수 없다 —
# 지역마다 별도 정적 URL(area/*.html)을 내야 색인·순위가 가능하다. 다만 2026년 3월
# 구글 코어 업데이트로 "scaled content abuse"(템플릿만 다르고 값이 없는 대량 페이지)가
# 명시적 위반이 됐다 — 그래서 표본이 너무 작아 지표(성장률·진입지수 등)가 사실상
# 의미 없는 지역은 아예 페이지를 안 만든다(전국 250여개 중 20곳 이상만, 대략 40~60개).
# entry_index()의 min_active 기본값과 같은 기준을 그대로 쓴다.


def district_slug(sido: str, sigungu: str) -> str:
    return f"{sido}-{sigungu}"


def render_district_page(r: dict, d: SiteData, sido_group: list[dict],
                          national_total: int, ei_total: int, ei_avg: int) -> str:
    """
    시군구 하나짜리 정적 페이지. 데이터는 전부 compute_regions()가 이미 계산해둔 r
    하나에서 나온다 — estimate.html의 인터랙티브 조회 결과와 같은 숫자를, 지역마다
    별도 URL로 서버에서 완성된 HTML로 내보낸다(클라이언트 JS 없이도 전체 내용이 이미
    응답에 들어있다 — 크롤러가 JS를 안 돌려도 색인할 수 있게).
    """
    sido, sigungu = r["sido"], r["sigungu"]
    slug = district_slug(sido, sigungu)

    growth_txt = ("신규 진입 지역이라 증감률을 계산할 수 없습니다" if r["growth_pct"] is None
                  else f"직전 6개월 대비 {'+' if r['growth_pct'] >= 0 else ''}{r['growth_pct']}%"
                       f"{'증가' if r['growth_pct'] >= 0 else '감소'}")
    intro = (f"{sido} {sigungu}에는 외국인관광 도시민박업이 {r['active']:,}곳 영업 중입니다. "
             f"전국 {national_total:,}곳 중 {r['national_rank']}위, {sido} 내에서는 "
             f"{r['sido_total']}곳 중 {r['sido_rank']}위입니다. {growth_txt}.")

    growth_badge = ("신규" if r["growth_pct"] is None
                     else (f"▲+{r['growth_pct']}%" if r["growth_pct"] >= 0 else f"▼{abs(r['growth_pct'])}%"))
    verdict_card = f"""
<div class="verdictCard" data-tone="{r['verdict_tone']}">
  <div class="vcTop">
    <div class="vcRegion">{sido} {sigungu}</div>
    <div class="vcRank">전국 {r['national_rank']}위({national_total:,}곳 중) · {sido} 내 {r['sido_rank']}위({r['sido_total']}곳 중)</div>
  </div>
  <div class="vcVerdict"><span class="vcBadge">{r['verdict_label']}</span>{r['verdict']}</div>
  <div class="vcStats">
    <div><div class="vsV">{r['active']:,}곳</div><div class="vsL">영업중 호스트</div></div>
    <div><div class="vsV">{r['tier']}</div><div class="vsL">시장 규모</div></div>
    <div><div class="vsV">{r['recent6']:,}건</div><div class="vsL">최근 6개월 신규</div></div>
    <div><div class="vsV {'up' if r['growth_pct'] is not None and r['growth_pct'] >= 0 else 'down' if r['growth_pct'] is not None else ''}">{growth_badge}</div>
    <div class="vsL">직전 6개월 대비</div></div>
  </div>
</div>"""

    if r["entry"]:
        e = r["entry"]
        axes = [("성장", e["pct_growth"]), ("생존", e["pct_survival"]), ("적합도", e["pct_fit"])]
        if "pct_demand" in e:
            axes.append(("수요", e["pct_demand"]))
        axes_html = "".join(
            f'<div class="eiRow"><span class="eiL">{label}</span>'
            f'<div class="eiBarWrap"><div class="eiBar" style="width:{v}%"></div></div>'
            f'<span class="eiN">{v}</span></div>' for label, v in axes)
        diff = e["index"] - ei_avg
        compare_txt = (f"지수 산정 가능 지역 평균({ei_avg}점)과 같습니다." if diff == 0 else
                       f"지수 산정 가능 지역 평균({ei_avg}점)보다 {abs(diff)}점 {'높습니다' if diff > 0 else '낮습니다'}.")
        ei_html = f"""
<h2 style="margin-top:36px;padding-top:18px">진입 적합도 지수</h2>
<div class="sub" style="margin:-4px 0 6px">등록 데이터(성장·생존·적합도)와 방문자수(수요)를 결합한
자체 지수, 백분위 평균입니다. 규모(호스트 수)는 의도적으로 제외했습니다.</div>
<div class="eiScore"><span class="eiScoreV">{e['index']}</span><span class="eiScoreMax">/100</span>
<span class="eiScoreRank">상위 {e['ei_rank']}위 (지수 산정 가능 {ei_total:,}곳 중)</span></div>
<div class="eiGauge"><div class="eiGaugeAvg" style="left:{ei_avg}%"></div>
<div class="eiGaugeMark" style="left:{e['index']}%"></div></div>
<div class="eiGaugeLabels"><span>포화·위축</span><span>성장 기회</span></div>
<div class="eiGaugeCompare">{compare_txt}</div>
<div class="eiAxes">{axes_html}</div>"""
    else:
        ei_html = """
<h2 style="margin-top:36px;padding-top:18px">진입 적합도 지수</h2>
<div class="sub">영업중 호스트가 너무 적거나 최근 6개월 신규등록이 없어(비교 불가) 이 지역은
지수를 계산할 수 없습니다.</div>"""

    status_total = r["active"] + r["pause"] + r["closed"]
    segs = [("active", "영업중", r["active"]), ("pause", "휴업", r["pause"]), ("closed", "폐업", r["closed"])]
    status_bar = "".join(
        f'<div class="seg {cls}" style="width:{(n / status_total * 100) if status_total else 0}%"></div>'
        for cls, _, n in segs)
    status_legend = "".join(
        f'<span><span class="dot {cls}"></span>{label} {n:,}곳 '
        f'({round(n / status_total * 100) if status_total else 0}%)</span>'
        for cls, label, n in segs)

    recent_yms = set(r["recent6_yms"])
    max_monthly = max((m["n"] for m in r["monthly"]), default=0) or 1
    trend_spark = "".join(
        f'<div class="tcol {"trecent" if m["ym"] in recent_yms else ""}">'
        f'<div class="tval">{m["n"]}</div>'
        f'<div class="tbarwrap" title="{m["ym"]}: {m["n"]}건">'
        f'<div class="tbar {"trecentbar" if m["ym"] in recent_yms else ""}" '
        f'style="height:{m["n"] / max_monthly * 100:.0f}%"></div></div>'
        f'<div class="tlabel">{m["ym"][2:] if i % 3 == 0 else ""}</div></div>'
        for i, m in enumerate(r["monthly"]))

    perf = d.perf.get(r["ynj_region"]) if r["ynj_region"] else None
    perf_html = ""
    if perf:
        perf_html = f"""
<h2 style="margin-top:36px;padding-top:18px">공유숙박 실적 지표 <span class="sub">({r['ynj_region']} 권역)</span></h2>
<div class="h2sub">출처: 야놀자리서치 국내 숙박업 실적 지표({perf['ym'][:4]}-{perf['ym'][4:]} 기준). 시군구가 아닌
{r['ynj_region']} 권역 평균값입니다.</div>
<div class="kpis">
  <div class="kpi"><div class="l">평균 객단가</div><div class="v">{perf['adr']:,.0f}원</div></div>
  <div class="kpi"><div class="l">객실 점유율</div><div class="v">{perf['occ']:.1f}%</div></div>
  <div class="kpi"><div class="l">객실당매출</div><div class="v">{perf['revpar']:,.0f}원</div></div>
</div>"""

    # 인접 지역 링크 — 같은 시도 안에서 순위 바로 위/아래. 방문자의 다음 클릭이자,
    # area/ 페이지끼리 서로를 가리켜 사이트맵 없이도 크롤러가 페이지를 발견·연결할
    # 통로가 된다(사이트맵은 존재를 알리고, 페이지 간 링크는 권위를 흘려보낸다).
    idx = r["sido_rank"] - 1
    neighbors = [g for g in (sido_group[idx - 1] if idx > 0 else None,
                              sido_group[idx + 1] if idx + 1 < len(sido_group) else None)
                 if g and g["active"] >= DISTRICT_PAGE_MIN_ACTIVE]
    neighbor_links = "".join(
        f'<div><a href="{district_slug(n["sido"], n["sigungu"])}.html">{n["sigungu"]}</a> '
        f'({n["active"]:,}곳)</div>' for n in neighbors)

    body = f"""
<div class="kicker">HOST MARKET LOOKUP</div>
<div class="sub"><a href="../estimate.html">지역별 시장 지표</a> · {sido}</div>
<h1>{sido} {sigungu} 공유숙박 시장 지표</h1>
<div class="sub">{intro} {d.current.ym} 기준.</div>

{verdict_card}
{ei_html}

<h2 style="margin-top:36px;padding-top:18px">영업 현황(등록 이력 전체 기준)</h2>
<div class="statusbar">{status_bar}</div>
<div class="statuslegend">{status_legend}</div>

<h2 style="margin-top:36px;padding-top:18px">최근 12개월 신규등록 추이</h2>
<div class="trendspark">{trend_spark}</div>
{perf_html}

<h2 style="margin-top:36px;padding-top:18px">관광진흥법 개정 동향</h2>
<div class="sub">법 개정은 전국 공통이라 어느 지역이든 똑같이 적용됩니다 — {sido} {sigungu}만의
규제가 아닙니다. <a href="../dashboard.html">대시보드에서 규제·정책 동향 전체 보기 →</a></div>

<h2 style="margin-top:36px;padding-top:18px">{sido} 내 다른 지역</h2>
<div class="sub">{neighbor_links or '같은 시도 내 비교 대상이 없습니다.'}</div>
<div class="sub" style="margin-top:8px"><a href="../estimate.html">전국 시군구 전체 조회 →</a></div>

<div class="ctaBanner">
  <div class="t">{sido} {sigungu}에서 시작해보고 싶다면</div>
  <div class="d">방금 확인한 등록 밀도·증감률을 바탕으로, 위홈에서 호스트로 등록해보세요.</div>
  <a class="wehomeCta" href="{wehome_cta_url('district_page')}" target="_blank" rel="noopener">위홈에 호스트로 등록하기 →</a>
</div>

<div class="note warn">등록 밀도·증감률은 행정안전부 등록 건수 기반이라 실제 숙박요금·점유율을
반영한 예상 수익이 아닙니다.{' 평균 객단가·객실 점유율·객실당매출은 실제 실적 지표이지만 개별 매물이 아닌 권역 평균값입니다.' if perf else ''}</div>

{FOOTER}"""

    description = (f"{sido} {sigungu} 외국인관광도시민박업 영업중 {r['active']:,}곳, 전국 {r['national_rank']}위. "
                    f"{d.current.ym} 기준 등록 밀도·증감률·진입 적합도 지수.")
    return page(f"{sido} {sigungu} 공유숙박 시장 지표", "estimate", 1, body, description,
                path=f"area/{slug}.html",
                jsonld=dataset_ld(f"{sido} {sigungu} 공유숙박 등록 현황", description,
                                   f"area/{slug}.html", d.current.ym))


# ─────────────────────────────────────────────────────── 월간 리포트 상세

def render_report_detail(iss: Issue, prev: Issue | None, inbound: dict, perf: dict[str, dict],
                          demand: dict[str, dict], visitors: dict, entry_idx: list[dict]) -> str:
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
{visitor_demand_html(visitors)}
{entry_index_html(entry_idx)}
{inbound_html}
{perf_table_html(perf)}

<h2>이전 호 대비</h2>
<div class="note">{('전월(' + prev.ym + ') 대비 외도민업 영업중 ' + format(delta, "+,") + '곳 변화') if prev else '첫 발행호라 비교 대상 없음.'}</div>

<div class="ctaBanner">
  <div class="t">지금 이 시장에 뛰어들고 싶다면</div>
  <div class="d">이번 호에서 본 데이터를 바탕으로, 위홈에서 호스트로 시작해보세요.</div>
  <a class="wehomeCta" href="{wehome_cta_url('report_detail')}" target="_blank" rel="noopener">위홈에 호스트로 등록하기 →</a>
</div>

{FOOTER}"""
    return page(f"{iss.ym} 리포트", "reports", 1, body,
                f"{iss.ym} 공유숙박 시장 리포트. 외도민업 영업중 {iss.flagship.active:,}곳.",
                path=f"report/{iss.ym}.html", jsonld=report_ld(iss))


# ─────────────────────────────────────────────────────── 빌드

def build() -> None:
    d = gather()

    SITE.mkdir(exist_ok=True)
    (SITE / "report").mkdir(exist_ok=True)
    (SITE / "area").mkdir(exist_ok=True)

    regions, by_sido_group = compute_regions(d)

    (SITE / "index.html").write_text(render_landing(d), encoding="utf-8")
    (SITE / "dashboard.html").write_text(render_dashboard(d), encoding="utf-8")
    (SITE / "reports.html").write_text(render_reports_index(d), encoding="utf-8")
    (SITE / "news.html").write_text(render_news(d), encoding="utf-8")
    (SITE / "competitors.html").write_text(render_competitors(d), encoding="utf-8")
    (SITE / "estimate.html").write_text(render_estimate(d, regions), encoding="utf-8")

    for i, iss in enumerate(d.all_issues):
        prev = d.all_issues[i + 1] if i + 1 < len(d.all_issues) else None
        (SITE / "report" / f"{iss.ym}.html").write_text(
            render_report_detail(iss, prev, d.inbound, d.perf, d.demand, d.visitors, d.entry_index),
            encoding="utf-8")

    # 지역별 정적 페이지(pSEO) — 등록 20곳 이상인 시군구만. render_district_page의 상단
    # 주석 참고: 표본이 너무 작은 지역까지 다 내면 2026년 구글 scaled content abuse
    # 기준에 걸릴 얇은 페이지가 된다.
    national_total = len(regions)
    ei_total = len(d.entry_index)
    ei_avg = round(statistics.mean(e["index"] for e in d.entry_index)) if d.entry_index else 0
    district_pages: list[tuple[dict, str]] = []  # (region, "area/xxx.html") — sitemap·검색인덱스가 같이 씀
    for r in regions:
        if r["active"] < DISTRICT_PAGE_MIN_ACTIVE or not r["sigungu"]:
            continue
        slug = district_slug(r["sido"], r["sigungu"])
        path = f"area/{slug}.html"
        (SITE / "area" / f"{slug}.html").write_text(
            render_district_page(r, d, by_sido_group[r["sido"]], national_total, ei_total, ei_avg),
            encoding="utf-8")
        district_pages.append((r, path))

    # sitemap.xml·robots.txt — SITE_BASE_URL 없으면(로컬 개발) 가짜 도메인으로 된
    # sitemap을 만들지 않고 조용히 생략한다(page()의 OG 태그 생략과 같은 이유).
    write_og_image(d.current)

    if SITE_BASE_URL:
        today = date.today().isoformat()
        urls = "".join(f"<url><loc>{SITE_BASE_URL}/{p}</loc><lastmod>{mod}</lastmod></url>\n"
                       for p, mod in sitemap_entries([i.ym for i in d.all_issues], d.current.ym))
        urls += "".join(f"<url><loc>{SITE_BASE_URL}/{p}</loc><lastmod>{today}</lastmod></url>\n"
                        for _, p in district_pages)
        (SITE / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n',
            encoding="utf-8")
        (SITE / "robots.txt").write_text(
            f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE_URL}/sitemap.xml\n", encoding="utf-8")
    else:
        print("  ⚠️ SITE_BASE_URL 미설정 — sitemap.xml/robots.txt 생성 생략")

    # 사이트 내 검색(news.html·reports.html의 search_box_html())용 인덱스 — 뉴스는
    # 원문 URL 그대로(새 탭으로 열림), 리포트는 ym을 제목·날짜 삼아 합성한다(개별
    # 제목·날짜 필드가 없는 Issue라서).
    search_index = [
        {"type": "news", "title": i.title, "url": i.url, "source": i.source, "date": i.date or ""}
        for i in d.news_items
    ] + [
        {"type": "report", "title": f"{iss.ym} 리포트", "url": f"report/{iss.ym}.html",
         "source": "월간 리포트", "date": iss.ym}
        for iss in d.all_issues
    ] + [
        {"type": "district", "title": f"{r['sido']} {r['sigungu']}", "url": p,
         "source": "지역별 시장 지표", "date": d.current.ym}
        for r, p in district_pages
    ]
    (SITE / "search-index.json").write_text(json.dumps(search_index, ensure_ascii=False), encoding="utf-8")

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
    print(f"   index.html(랜딩), dashboard.html, reports.html, report/*.html ({len(d.all_issues)}개 호), "
          f"area/*.html ({len(district_pages)}개 시군구, 등록 {DISTRICT_PAGE_MIN_ACTIVE}곳 이상)")
    delta = mom_delta(d.current, d.previous)
    print(f"   최신: {d.current.ym} 영업중 {d.current.flagship.active:,} "
          f"({(format(delta, '+,') if delta is not None else '비교 대상 없음(첫 스냅샷)')} "
          f"vs {d.previous.ym if d.previous else '-'})")


if __name__ == "__main__":
    build()
