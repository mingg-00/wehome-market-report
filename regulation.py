#!/usr/bin/env python3
"""
Tier 1 규제·정책 수집기.

sources.py 의 TIER1_REGULATION 4개를 실제로 긁는다. 나머지 3개(법제처 통합
입법예고, 국회 의안정보시스템, 국토교통부 직접)는 사이트 자체가 막혀 있거나
(WAF·레거시 폼) 다른 소스와 역할이 겹쳐서 뺐다 — 사유는 sources.py 의 DROPPED
에 있다.

사이트마다 구조가 달라 파서를 하나로 묶을 수 없다. 대신 전부 "원본 HTML 문자열
→ Item 리스트"인 순수 함수로 짜서, 네트워크 없이 고정 HTML로 테스트할 수 있게
했다(test_regulation.py). 이 판의 정부·국회 사이트는 마크업이 예고 없이 바뀌므로
셀렉터가 죽으면 조용히 0건을 내는 대신 빈 리스트 + 개수를 로그로 남겨 알아채게 한다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import requests

from sources import AMBIGUOUS_KEYWORDS, CORE_KEYWORDS

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

MCST_PRESS = "https://www.mcst.go.kr/site/s_notice/press/pressList.jsp"
MCST_NOTICE = "https://www.mcst.go.kr/site/s_notice/notice/noticeList.jsp"
KOREA_BRIEFING = "https://www.korea.kr/briefing/pressReleaseList.do"
ASSEMBLY_BILL_SEARCH = "https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/list.do"

# 국회 입법예고에서 상시 추적할 법률명. billName= 은 부분일치라 "관광진흥법"으로
# "관광진흥법 일부개정법률안" 같은 게 다 잡힌다. 필요해지면 추가.
TRACKED_ACTS = ["관광진흥법"]


@dataclass
class Item:
    source: str
    title: str
    url: str
    date: str | None = None
    summary: str = ""
    image: str | None = None

    def matches_keywords(self, keywords: list[str] = CORE_KEYWORDS) -> bool:
        """
        대부분의 키워드는 부분일치로 충분하지만, AMBIGUOUS_KEYWORDS에 등록된
        동형이의어(예: "방한" = 관광객 방한 vs 대통령 국빈 방한)는 등장 지점
        근처(±15자)에 문맥 단어가 있어야 진짜 매치로 인정한다 — 그래야 외교
        기사가 "방한" 하나로 오탐되지 않는다.
        """
        text = f"{self.title} {self.summary}"
        for k in keywords:
            ctx_words = AMBIGUOUS_KEYWORDS.get(k)
            if ctx_words is None:
                if k in text:
                    return True
                continue
            start = 0
            while (idx := text.find(k, start)) != -1:
                window = text[max(0, idx - 15):idx + len(k) + 15]
                if any(c in window for c in ctx_words):
                    return True
                start = idx + 1
        return False


@dataclass
class BillMatch:
    """국회 계류 법안 1건. 일반 기사 Item과 필드가 달라 따로 둔다."""
    bill_no: str
    title: str
    url: str
    committee: str
    proposer_type: str
    views: int


def _text(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", cell)).strip()


def _get(url: str, **params) -> str:
    r = requests.get(url, params=params or None, headers=UA, timeout=20)
    r.raise_for_status()
    return r.text


# ───────────────────────────── 문체부 (보도자료·입법예고 공용, table.board)

def parse_mcst_board(page: str, view_page: str) -> list[Item]:
    """
    문체부 site/s_notice/{press,notice} 는 마크업이 동일한 table.board 다.
    view_page 는 "pressView.jsp" 또는 "noticeView.jsp" — 상세 링크 조립용.
    """
    m = re.search(r'<table class="board".*?</table>', page, re.S)
    if not m:
        return []
    items = []
    for row in re.findall(r"<tr>.*?</tr>", m.group(0), re.S):
        title_m = re.search(r'<a href="[^"]*pSeq=(\d+)"[^>]*title="([^"]+)"', row)
        date_m = re.search(r'aria-label="게시일">([^<]+)<', row)
        if not title_m:
            continue
        pseq, title = title_m.groups()
        items.append(Item(
            source="문체부", title=title.strip(),
            url=f"https://www.mcst.go.kr/site/s_notice/{'press' if 'press' in view_page else 'notice'}/{view_page}?pSeq={pseq}",
            date=(date_m.group(1).rstrip(".").replace(".", "-") if date_m else None),
        ))
    return items


def fetch_mcst_press() -> list[Item]:
    return parse_mcst_board(_get(MCST_PRESS), "pressView.jsp")


def fetch_mcst_notice() -> list[Item]:
    return parse_mcst_board(_get(MCST_NOTICE), "noticeView.jsp")


# ───────────────────────────── 정책브리핑 (부처 통합, li > a[pressReleaseView])

def parse_korea_briefing(page: str) -> list[Item]:
    items = []
    for li in re.findall(r"<li>\s*<a href=\"[^\"]*pressReleaseView\.do[^\"]*\".*?</a>\s*</li>", page, re.S):
        url_m = re.search(r'href="([^"]*pressReleaseView\.do[^"]*)"', li)
        title_m = re.search(r"<strong>(.*?)</strong>", li, re.S)
        lead_m = re.search(r'<span class="lead">(.*?)</span>', li, re.S)
        if not (url_m and title_m):
            continue
        items.append(Item(
            source="정책브리핑",
            title=_text(title_m.group(1)),
            url="https://www.korea.kr" + html.unescape(url_m.group(1)),
            summary=_text(lead_m.group(1)) if lead_m else "",
        ))
    return items


def fetch_korea_briefing() -> list[Item]:
    return parse_korea_briefing(_get(KOREA_BRIEFING))


# ───────────────────────────── 국회 입법예고 (billName= 부분일치 검색)

def parse_assembly_bills(page: str) -> list[BillMatch]:
    out = []
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if "board_subject" not in row:
            continue
        no_m = re.search(r"<td>(\d+)</td>", row)
        title_m = re.search(r'class="board_subject">([^<]+)<', row)
        # href와 class 사이에 개행이 오는 게 실제 페이지의 기본 포맷 — \s+ 로 유연하게.
        href_m = re.search(r'<a href="([^"]+)"\s+class="board_subject"', row)
        proposer_m = re.search(r"</td>\s*<td>(의원|정부)</td>", row)
        committee_m = re.search(r'class="board_text">([^<]+)<', row)
        views_m = re.search(r'class="align_right">([\d,]+)</td>', row)
        if not (no_m and title_m and href_m):
            continue
        out.append(BillMatch(
            bill_no=no_m.group(1),
            title=title_m.group(1).strip(),
            url="https://pal.assembly.go.kr" + href_m.group(1),
            committee=committee_m.group(1).strip() if committee_m else "",
            proposer_type=proposer_m.group(1) if proposer_m else "",
            views=int(views_m.group(1).replace(",", "")) if views_m else 0,
        ))
    return out


def fetch_assembly_bills(act_name: str) -> list[BillMatch]:
    """act_name 이 제목에 부분일치하는 계류 법안을 찾는다. 부분일치라 정확한 법률명을 쓸 것."""
    return parse_assembly_bills(_get(ASSEMBLY_BILL_SEARCH, billName=act_name))


# ───────────────────────────── 통합 수집

def collect() -> dict:
    """
    Tier1 전체를 긁고 CORE_KEYWORDS 로 걸러 반환.
    개수를 print 로 남기는 이유: 정부 사이트 마크업이 바뀌면 파서가 0건을 조용히
    내는데, 그게 '오늘 마침 기사가 없다'인지 '셀렉터가 죽었다'인지 구분이 안 되면
    아무도 눈치 못 챈다. 최소한 개수는 매번 눈에 보이게 한다.
    """
    fetchers = {
        "문체부 보도자료": fetch_mcst_press,
        "문체부 입법·행정예고": fetch_mcst_notice,
        "정책브리핑": fetch_korea_briefing,
    }
    all_items: list[Item] = []
    for name, fn in fetchers.items():
        try:
            items = fn()
            print(f"  {name}: {len(items)}건 수집")
            all_items.extend(items)
        except Exception as e:
            print(f"  ⚠️ {name} 수집 실패: {type(e).__name__}: {e}")

    matched = [i for i in all_items if i.matches_keywords()]
    seen: set[str] = set()
    dedup = [i for i in matched if not (i.url in seen or seen.add(i.url))]

    bills: dict[str, list[BillMatch]] = {}
    for act in TRACKED_ACTS:
        try:
            bills[act] = fetch_assembly_bills(act)
            print(f"  국회 계류법안({act}): {len(bills[act])}건")
        except Exception as e:
            print(f"  ⚠️ 국회 입법예고 수집 실패: {type(e).__name__}: {e}")
            bills[act] = []

    return {"items": dedup, "bills": bills, "total_scanned": len(all_items)}


if __name__ == "__main__":
    r = collect()
    print(f"\n전체 {r['total_scanned']}건 중 키워드 매칭 {len(r['items'])}건\n")
    for i in r["items"][:10]:
        print(f"  [{i.source}] {i.title}  ({i.date or '-'})")
    for act, matches in r["bills"].items():
        print(f"\n[{act}] 계류 법안 {len(matches)}건")
        for b in matches:
            print(f"  {b.bill_no} {b.title} · {b.committee} · 조회 {b.views:,}")
