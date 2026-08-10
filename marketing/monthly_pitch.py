#!/usr/bin/env python3
"""
"이달의 발견" PR 패키지 생성 — GROWTH.md Week 1-4 "데이터 저널리즘 PR".

localdata.CategoryStats.sido_growth()로 시도별 최근/직전 6개월 신규등록 증감을
뽑아 가로 막대 차트 + 피칭용 마크다운을 만든다. 네트워크 호출 없음 — history/의
최신 스냅샷(build_site.py가 매달 쌓아둔 것)만 읽는다.

2026-08 실행 결과가 이 스크립트를 만든 계기: 서울·부산 딱 두 곳만 증가하고
나머지 15개 시도는 감소하거나 제자리였다(GROWTH.md §PR 채널 참고). 매달 이
패턴을 다시 확인해 "이달의 발견"이 여전히 같은 얘기인지, 다른 지역으로
옮겨갔는지 보려고 재사용 가능한 스크립트로 뽑았다.

주의: 행안부 원본 데이터는 제주처럼 특정 시도가 통째로 미집계인 사례가 실제로
있다(safestay.py의 JEJU_NOT_REPORTED 참고). "감소"가 실제 시장 위축이 아니라
데이터 지연일 가능성을 피칭 문서에 캐비엇으로 항상 남긴다 — 확인 없이 기자에게
"시장이 무너지고 있다"고 단정하지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # build_site.py·localdata.py는 저장소 루트

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import build_site as b
import viz

OUT_DIR = Path(__file__).parent


def render_chart(rows: list[dict], ym: str, out_path: Path) -> None:
    """시도별 증감률 가로 막대 — 성장(양수)은 mint, 감소는 navy로 이분한다.
    별도 신호등(빨강 등)을 안 쓰는 이유: "위축=나쁨" 판단을 이 차트가 내리는 게
    아니라 성장/감소라는 사실만 보여주는 게 목적이라서(estimate.html의 발산형
    eiGauge와 같은 이유 — build_site.py 참고)."""
    finite = [r for r in rows if r["growth"] != float("inf")]
    finite.sort(key=lambda r: r["growth"])
    names = [r["sido"].replace("특별자치도", "").replace("특별자치시", "")
             .replace("광역시", "").replace("특별시", "").replace("전남광주통합", "전남·광주") for r in finite]
    vals = [r["growth"] * 100 for r in finite]

    fig, ax = plt.subplots(figsize=(8.5, 6), dpi=144)
    colors = [viz.MINT if v >= 0 else viz.NAVY for v in vals]
    ax.barh(names, vals, color=colors)
    ax.axvline(0, color=viz.GREY, linewidth=1)
    ax.set_title(f"시도별 신규등록 증감률 — 직전 6개월 대비 ({ym} 기준)", fontsize=13, fontweight="bold", loc="left")
    for name, v in zip(names, vals):
        ax.text(v + (2 if v >= 0 else -2), name, f"{v:+.0f}%", va="center",
                 ha="left" if v >= 0 else "right", fontsize=9)
    # 막대가 길게 뻗는 왼쪽·오른쪽 끝에서 퍼센트 라벨이 축 여백 밖으로 밀려 잘리는 걸
    # 실측으로 확인했다(충남 -78%가 y축 지역명 라벨과 겹침) — 여백을 넉넉히 준다.
    ax.margins(x=0.18)
    viz.strip_spines(ax, keep=())
    ax.set_xticks([])
    fig.tight_layout()
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)


def render_pitch_md(rows: list[dict], ym: str, chart_path: Path, out_path: Path) -> None:
    finite = [r for r in rows if r["growth"] != float("inf")]
    # 0%(대전처럼 직전·최근이 똑같은 경우)는 "늘었다"도 "줄었다"도 아니다 — 이걸 growing에
    # 넣으면 "N곳만 늘었다"는 문장이 실제로 성장한 곳보다 많은 곳을 성장으로 세게 된다.
    growing = sorted((r for r in finite if r["growth"] > 0), key=lambda r: -r["growth"])
    declining = sorted((r for r in finite if r["growth"] < 0), key=lambda r: r["growth"])
    flat = [r for r in finite if r["growth"] == 0]

    growing_line = ", ".join(f"{r['sido']} {r['growth']*100:+.0f}%" for r in growing)
    top_declines = ", ".join(f"{r['sido']} {r['growth']*100:.0f}%" for r in declining[:5])
    n_total = len(finite)

    table_rows = "\n".join(
        f"| {r['sido']} | {r['active']:,} | {r['prior']:,} | {r['recent']:,} | "
        f"{'+' if r['growth']>=0 else ''}{r['growth']*100:.0f}% |"
        for r in sorted(rows, key=lambda r: -r["recent"]) if r["growth"] != float("inf")
    )

    md = f"""# 이달의 발견 — {ym}

## 헤드라인 후보
1. 공유숙박, {'·'.join(r['sido'][:2] for r in growing)}만 늘고 나머지 지방은 줄었다
2. 전국 {n_total}개 시도 중 {len(declining)}곳에서 공유숙박 신규등록이 줄어드는 동안,
   {growing[0]['sido'][:2] if growing else '-'}은 여전히 급성장했다

## 인용 가능한 한 문장
> 최근 6개월(직전 6개월 대비) 신규 등록 기준, 전국 {n_total}개 시도 중 {growing_line}
> {len(growing)}곳만 늘었고 {len(declining)}곳은 줄었다{f'(나머지 {len(flat)}곳은 제자리)' if flat else ''}
> — {top_declines} 등.

## 데이터
데이터: 행정안전부 지방행정 인허가 데이터(file.localdata.go.kr) 직접 수집·집계,
공공누리 제4유형. 기준월: {ym}. 산출: `localdata.CategoryStats.sido_growth()`
(직전 6개월 vs 최근 6개월, 진행 중인 이번 달은 양쪽 창에서 제외).

| 시도 | 영업중 | 직전 6개월 | 최근 6개월 | 증감률 |
|---|---:|---:|---:|---:|
{table_rows}

## 차트
`{chart_path.name}` — 이 문서와 같은 폴더.

## 원본 데이터 링크
- 전국 대시보드: https://wehome-market-report.vercel.app/dashboard.html
- 지역별 조회(시군구 단위): https://wehome-market-report.vercel.app/estimate.html
- 이번 호 리포트: https://wehome-market-report.vercel.app/report/{ym}.html

## ⚠️ 캐비엇 (피칭 메일에 반드시 포함)
행정안전부 원본 데이터는 특정 지자체가 별도 시스템으로 관리해 이 포털에
통째로 미집계인 사례가 실제로 있다(제주가 그렇다 — 외도민업이 0건으로 잡혀
리포트에서 아예 제외했다). "감소"로 잡힌 지역이 실제 시장 위축인지, 지자체
데이터 업로드 지연인지는 이 데이터만으로 100% 확정할 수 없다. 피칭할 땐
"행안부 등록 데이터 기준"이라는 전제를 명확히 하고, 특정 지자체를 겨냥한
단정적 표현(예: "OO시 시장이 무너졌다")은 피한다 — "등록 추세가 이렇게
나타난다"까지만 말한다.

## 타깃
`marketing/press_list.csv` 참고 — 공유숙박·관광진흥법 키워드로 실제 기사를
쓴 기자 10명 중, 지역균형발전·관광 정책 쪽 기사를 쓴 기자에게 먼저 피칭.
"""
    out_path.write_text(md, encoding="utf-8")


def main() -> None:
    snapshots = b.load_all_snapshots()
    if not snapshots:
        print("history/ 에 스냅샷이 없다 — build_site.py를 먼저 한 번 돌려야 한다.")
        return
    ym, cats = snapshots[0]
    fs = cats[b.localdata.FLAGSHIP]
    rows = fs.sido_growth(min_active=20)  # saturation_signal·area 페이지와 같은 기준 — 표본 노이즈 제외

    chart_path = OUT_DIR / f"pitch_{ym}_chart.png"
    md_path = OUT_DIR / f"pitch_{ym}.md"
    render_chart(rows, ym, chart_path)
    render_pitch_md(rows, ym, chart_path, md_path)

    growing = sum(1 for r in rows if r["growth"] > 0)
    declining = sum(1 for r in rows if r["growth"] < 0)
    print(f"✅ {ym} 기준 {len(rows)}개 시도(영업중 20곳 이상) 중 {growing}곳 성장, {declining}곳 감소, "
          f"{len(rows)-growing-declining}곳 제자리")
    print(f"   {md_path}")
    print(f"   {chart_path}")


if __name__ == "__main__":
    main()
