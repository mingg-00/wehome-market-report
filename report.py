#!/usr/bin/env python3
"""
주간 공유숙박 마켓리포트 생성.

  python report.py            # 수집 -> 지표 -> 차트 -> HTML
  python report.py --no-fetch # history.json 의 마지막 스냅샷으로 재생성

세이프스테이는 최근 4개월치만 노출한다. 그래서 매 실행마다 스냅샷을 history.json 에
append 해 자체 시계열을 쌓는다 — 몇 달 지나면 어디서도 못 구하는 YoY 시리즈가 된다.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import safestay

OUT = Path(__file__).parent / "output"
HISTORY = Path(__file__).parent / "history.json"
ODM = "외국인관광도시민박업"

# macOS 기본 한글 폰트. 없으면 네모로 깨지므로 실패 시 바로 알린다.
plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False

WEHOME_NAVY, WEHOME_MINT, GREY = "#1B2A4A", "#00C2A8", "#B9C2CE"


# ---------------------------------------------------------------- 지표

def metrics(snap: safestay.Snapshot) -> dict:
    """리포트 본문에 쓰는 수치. 전부 여기서 계산하고 렌더는 계산하지 않는다."""
    months = snap.months
    now, prev = months[0], months[1]

    operating = snap.operating(now)
    closed = snap.type_period[now][ODM]["폐업"]
    cumulative = snap.cumulative(now)

    # 지역별 순증: 지역별 표는 누적치라 월간 차이가 곧 신규 등록 순증이다.
    growth = {
        r: snap.area_period[now][r] - snap.area_period[prev][r]
        for r in snap.area_period[now]
        if r != safestay.JEJU_NOT_REPORTED  # 미집계 지역은 순위에서 뺀다
    }
    ranked = sorted(growth.items(), key=lambda kv: -kv[1])

    seoul = snap.area_period[now]["서울"]
    return {
        "month": now,
        "prev_month": prev,
        "operating": operating,
        "operating_mom": operating - snap.operating(prev),
        "cumulative": cumulative,
        "closed": closed,
        "closure_rate": closed / cumulative if cumulative else 0.0,
        "seoul": seoul,
        "seoul_share": seoul / sum(snap.area_period[now].values()),
        "growth_ranked": ranked,
        "by_type": {t: snap.type_period[now][t]["운영"] for t in safestay.LODGE_TYPES},
        "series": {r: [snap.area_period[m][r] for m in reversed(months)]
                   for r in ("서울", "부산", "경기")},
        "months_asc": list(reversed(months)),
    }


# ---------------------------------------------------------------- 차트

def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=144, bbox_inches="tight", transparent=True)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def chart_trend(m: dict) -> str:
    fig, ax = plt.subplots(figsize=(7, 3.4))
    for name, color in (("서울", WEHOME_NAVY), ("경기", WEHOME_MINT), ("부산", GREY)):
        ax.plot(m["months_asc"], m["series"][name], marker="o", lw=2.4, label=name, color=color)
        ax.annotate(f'{m["series"][name][-1]:,}', (len(m["months_asc"]) - 1, m["series"][name][-1]),
                    textcoords="offset points", xytext=(6, 0), fontsize=9, color=color, weight="bold")
    ax.set_title("외국인관광 도시민박업 누적 등록 추이", fontsize=12, weight="bold", pad=12)
    ax.legend(frameon=False, fontsize=9)
    ax.grid(axis="y", alpha=.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _png(fig)


def chart_growth(m: dict) -> str:
    top = [kv for kv in m["growth_ranked"] if kv[1] != 0][:8]
    fig, ax = plt.subplots(figsize=(7, 3.4))
    names = [k for k, _ in top][::-1]
    vals = [v for _, v in top][::-1]
    ax.barh(names, vals, color=[WEHOME_MINT if v > 0 else "#E2574C" for v in vals], height=.62)
    for i, v in enumerate(vals):
        ax.annotate(f"{v:+,}", (v, i), textcoords="offset points",
                    xytext=(6 if v >= 0 else -30, -4), fontsize=9, weight="bold")
    ax.set_title(f'지역별 신규 등록 순증 ({m["prev_month"]} → {m["month"]})',
                 fontsize=12, weight="bold", pad=12)
    ax.grid(axis="x", alpha=.25)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    return _png(fig)


def chart_mix(m: dict) -> str:
    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    items = sorted(m["by_type"].items(), key=lambda kv: -kv[1])
    colors = [WEHOME_NAVY, WEHOME_MINT, "#7A8AA5", GREY]
    ax.pie([v for _, v in items], labels=[k.replace("업", "") for k, _ in items],
           autopct="%1.0f%%", colors=colors, startangle=90,
           wedgeprops={"width": .42, "edgecolor": "white", "linewidth": 2},
           textprops={"fontsize": 9})
    ax.set_title("운영 중 민박업 구성", fontsize=12, weight="bold", pad=12)
    return _png(fig)


# ---------------------------------------------------------------- 렌더

def render(m: dict, charts: dict[str, str]) -> str:
    def img(key):
        return f'<img src="data:image/png;base64,{charts[key]}" alt="">'

    rank = "".join(
        f"<tr><td>{i}</td><td>{r}</td><td class=n>{v:+,}</td></tr>"
        for i, (r, v) in enumerate(m["growth_ranked"][:5], 1))

    return f"""<title>공유숙박 마켓리포트 {m['month']}</title>
<style>
:root{{--navy:{WEHOME_NAVY};--mint:{WEHOME_MINT};--bg:#fff;--fg:#1a1a1a;--muted:#667;--line:#e6e9ee;--card:#f7f9fb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f141c;--fg:#e8edf4;--muted:#93a1b5;--line:#243043;--card:#161e2b}}}}
:root[data-theme=dark]{{--bg:#0f141c;--fg:#e8edf4;--muted:#93a1b5;--line:#243043;--card:#161e2b}}
:root[data-theme=light]{{--bg:#fff;--fg:#1a1a1a;--muted:#667;--line:#e6e9ee;--card:#f7f9fb}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
 font:16px/1.65 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",sans-serif}}
.wrap{{max-width:820px;margin:0 auto;padding:40px 20px 72px}}
.kicker{{color:var(--mint);font-weight:700;letter-spacing:.14em;font-size:12px}}
h1{{font-size:30px;line-height:1.25;margin:.3em 0 .1em;letter-spacing:-.02em}}
.sub{{color:var(--muted);font-size:14px;margin-bottom:32px}}
h2{{font-size:19px;margin:44px 0 14px;padding-top:22px;border-top:1px solid var(--line)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.kpi .l{{font-size:12px;color:var(--muted)}}
.kpi .v{{font-size:26px;font-weight:750;letter-spacing:-.02em;margin-top:4px}}
.kpi .d{{font-size:12px;color:var(--mint);font-weight:650}}
img{{max-width:100%;height:auto;display:block;margin:8px 0}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:600;font-size:12px}}
td.n{{text-align:right;font-variant-numeric:tabular-nums;font-weight:650}}
.note{{background:var(--card);border-left:3px solid var(--mint);border-radius:0 8px 8px 0;
 padding:14px 16px;font-size:13.5px;color:var(--muted);margin:20px 0}}
footer{{margin-top:52px;padding-top:20px;border-top:1px solid var(--line);
 font-size:12.5px;color:var(--muted)}}
</style>
<div class=wrap>
<div class=kicker>WEEKLY MARKET REPORT</div>
<h1>공유숙박 마켓리포트<br>{m['month']}</h1>
<div class=sub>외국인관광 도시민박업 등록 동향 · 발행 {date.today():%Y-%m-%d} · 위홈</div>

<div class=kpis>
  <div class=kpi><div class=l>운영 중 외도민</div><div class=v>{m['operating']:,}</div>
    <div class=d>{m['operating_mom']:+,} vs {m['prev_month']}</div></div>
  <div class=kpi><div class=l>누적 등록</div><div class=v>{m['cumulative']:,}</div>
    <div class=d>폐업률 {m['closure_rate']:.1%}</div></div>
  <div class=kpi><div class=l>서울 누적</div><div class=v>{m['seoul']:,}</div>
    <div class=d>전국의 {m['seoul_share']:.0%}</div></div>
</div>

<h2>등록 추이</h2>
{img('trend')}

<h2>이번 달 어디가 늘었나</h2>
{img('growth')}
<div class=scroll><table>
<tr><th>#</th><th>지역</th><th style="text-align:right">순증</th></tr>{rank}
</table></div>

<h2>업종 구성</h2>
{img('mix')}

<div class=note>
<b>데이터 읽는 법.</b> 지역별 수치는 폐업·취소를 포함한 <b>누적 등록 수</b>이고,
KPI의 '운영 중'은 실제 영업 중인 업소만 센 것이다. 두 값을 섞으면 시장 규모가
약 1.3배 부풀려진다. 제주는 특별자치도 별도 관리로 미집계라 순위에서 제외했다.
전남·광주는 원자료가 병합 제공되어 분리할 수 없다.
</div>

<footer>
출처: 한국관광공사 세이프스테이(safestay.visitkorea.or.kr) · 기준 {m['month']}<br>
자동 생성 — 위홈 마켓리포트 파이프라인
</footer>
</div>"""


# ---------------------------------------------------------------- 실행

def save_history(snap: safestay.Snapshot) -> None:
    """세이프스테이가 4개월치만 주므로, 매주 쌓아 자체 장기 시계열을 만든다."""
    log = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    today = str(date.today())
    log = [e for e in log if e["fetched"] != today] + [{"fetched": today, **asdict(snap)}]
    HISTORY.write_text(json.dumps(log, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="history.json 의 마지막 스냅샷 사용")
    args = ap.parse_args()

    if args.no_fetch:
        entry = json.loads(HISTORY.read_text())[-1]
        snap = safestay.Snapshot(entry["area_period"], entry["area_type"], entry["type_period"])
    else:
        snap = safestay.collect()
        save_history(snap)

    m = metrics(snap)
    charts = {"trend": chart_trend(m), "growth": chart_growth(m), "mix": chart_mix(m)}

    OUT.mkdir(exist_ok=True)
    path = OUT / f"marketreport_{m['month'].replace('.', '')}.html"
    path.write_text(render(m, charts), encoding="utf-8")

    print(f"\n✅ {path}")
    print(f"   운영중 {m['operating']:,} ({m['operating_mom']:+,})  "
          f"폐업률 {m['closure_rate']:.1%}  서울비중 {m['seoul_share']:.0%}")
    print(f"   증가 1위: {m['growth_ranked'][0][0]} {m['growth_ranked'][0][1]:+,}")


if __name__ == "__main__":
    main()
