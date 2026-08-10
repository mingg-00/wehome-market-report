#!/usr/bin/env python3
"""
공유숙박 데이터 저널리즘 PR용 기자 피칭 리스트 — GROWTH.md Week 1-4 "PR 채널".

news.py가 이미 매달 17개 소스를 긁는다. 그중 RSS 5개(호텔앤레스토랑·여행신문·
한국경제·매일경제·히치하이커)만 <author> 태그로 개인 바이라인을 준다 — 나머지
12개(HTML 스크랩·경쟁사 뉴스룸·네이버뉴스 API)는 목록 페이지에 기자 이름이 아예
없거나(공식 보도자료 게시판) API 자체가 바이라인을 안 준다. 그래서 이 스크립트는
RSS 소스만 본다 — 없는 데이터를 억지로 만들어내지 않는다.

CORE_KEYWORDS(sources.py, news.html이 "공유숙박" 태그를 붙일 때 쓰는 것과 동일)로
걸러서 "숙박업계 전반"이 아니라 "공유숙박·도시민박·관광진흥법 규제"를 실제로 쓴
기자만 남긴다 — 카지노 대회 소식을 쓴 기자에게 우리 데이터를 피칭하면 시간 낭비다.

이메일 주소는 안 담는다 — 뉴스와이어 등 유료 기자 DB나 언론사 홈페이지에서
개별로 찾아야 한다(이 스크립트는 "누구에게" 보낼지만 좁혀준다, "어떻게 연락할지"는
아니다). 출력은 marketing/press_list.csv — 열어서 최근 기사 1~2건 언급하며
개인화된 피칭 메일을 쓰는 데 바로 쓸 수 있다.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # news.py·regulation.py는 저장소 루트에 있다

import news  # noqa: E402
from regulation import Item  # noqa: E402

OUT = Path(__file__).parent / "press_list.csv"


@dataclass
class Reporter:
    source: str
    name: str
    articles: list[Item] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.articles)

    @property
    def latest_date(self) -> str:
        dated = [a.date for a in self.articles if a.date]
        return max(dated) if dated else ""


def collect_reporters() -> list[Reporter]:
    by_key: dict[tuple[str, str], Reporter] = {}
    for source in news.RSS_SOURCES:
        print(f"  [{source}] 수집 중...")
        for item in news.fetch_rss(source):
            if not item.reporter or not item.matches_keywords():
                continue
            key = (item.source, item.reporter)
            by_key.setdefault(key, Reporter(item.source, item.reporter)).articles.append(item)
    return list(by_key.values())


def write_csv(reporters: list[Reporter]) -> None:
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀에서 한글 안 깨지게
        w = csv.writer(f)
        w.writerow(["매체", "기자", "매칭 기사 수", "최신 매칭일", "최근 기사 제목", "최근 기사 URL"])
        for r in reporters:
            latest = sorted(r.articles, key=lambda a: a.date or "", reverse=True)[:3]
            w.writerow([r.source, r.name, r.count, r.latest_date,
                        " | ".join(a.title for a in latest),
                        " | ".join(a.url for a in latest)])


def main() -> None:
    print("RSS 5개 소스에서 공유숙박 관련 기사 바이라인 수집 중...")
    reporters = collect_reporters()
    reporters.sort(key=lambda r: (-r.count, r.name))
    write_csv(reporters)

    print(f"\n✅ {len(reporters)}명 · {OUT}")
    print(f"{'매체':10} {'기자':8} {'기사수':>5}  최신 매칭일")
    for r in reporters:
        print(f"{r.source:10} {r.name:8} {r.count:>5}  {r.latest_date}")
    if not reporters:
        print("매칭된 기자가 없다 — CORE_KEYWORDS에 걸리는 기사가 이번 수집 범위(각 소스 최신분)에"
              " 없었을 수 있다. 다음 달에 다시 돌려보거나 max_items를 늘리는 걸 고려.")


if __name__ == "__main__":
    main()
