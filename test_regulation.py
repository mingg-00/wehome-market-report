#!/usr/bin/env python3
"""
파서 자체 점검. `python test_regulation.py` 로 실행. 네트워크 없이 고정 HTML로 검증한다.

fixture는 2026-07-28 실제 응답에서 그대로 뜯어온 조각이다(요약을 위해 필드는
유지하고 반복 행만 줄임). 정부 사이트가 마크업을 바꾸면 이 테스트가 먼저 죽어야
한다 — 조용히 0건 나오는 것보다 낫다.
"""

import regulation as reg

MCST_PRESS_HTML = """
<table class="board" > <caption>보도자료</caption>
<tbody>
<tr> <td aria-label="번호">13156</td> <td aria-label="제목" class="tit_wrap">
<a href="pressView.jsp?pSeq=22609" title="올해 상반기 방한 외국인 관광객 1,071만 명 돌파, 케이-관광 양적·질적 성장 지속"
onclick="fnView(22609);return false;" > <p class="tit">
<span class="krds-badge basic">새글</span> 올해 상반기 방한 외국인 관광객 1,071만 명 돌파
</p> </a> </td> <td aria-label="게시일">2026.07.28.</td> <td aria-label="조회수">143</td> </tr>
<tr> <td aria-label="번호">13154</td> <td aria-label="제목" class="tit_wrap">
<a href="pressView.jsp?pSeq=22607" title="당신과 한국문화의 첫 만남을 콘텐츠로 보여주세요"
onclick="fnView(22607);return false;" > <p class="tit"> 당신과 한국문화의 첫 만남 </p> </a>
</td> <td aria-label="게시일">2026.07.27.</td> <td aria-label="조회수">387</td> </tr>
</tbody></table>
"""

KOREA_BRIEFING_HTML = """
<li> <a href="/briefing/pressReleaseView.do?newsId=156772405&amp;pageIndex=1">
<span class="text"> <strong>독자 AI 파운데이션 모델 프로젝트 국민 평가단 공모</strong>
<span class="lead">7월 28일부터 8월 4일까지 일반 국민 200명 공모</span> </span> </a> </li>
<li> <a href="/briefing/pressReleaseView.do?newsId=156772406&amp;pageIndex=1">
<span class="text"> <strong>외국인관광 도시민박업 등록 절차 간소화</strong>
<span class="lead">문체부, 관광진흥법 시행령 개정 통해 등록 요건 완화</span> </span> </a> </li>
"""

ASSEMBLY_BILLS_HTML = """
<tr>
    <td>2219898</td>
    <td class="align_left td_block">
        <a href="/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=PRC_N2N6M0K7L0T8"
           class="board_subject">관광진흥법 일부개정법률안 (김성원의원 등 10인)</a>
<div class="m_subject"><ul class="m_date"><li>2219898</li><li>문화체육관광위원회</li></ul></div>
</td> <td>의원</td> <td class="board_text">문화체육관광위원회</td>
<td><a href="javascript:openPopup(...)" class="btn_sm">새창열기</a></td>
<td><a href="...">한글</a><a href="...">PDF</a></td>
<td><a href="javascript:openPopup(...)" class="btn_board_preview">미리보기</a></td>
<td class="align_right">1,624</td> <td><a class="btn-favorite">관심입법 해제됨</a></td> </tr>
<tr>
    <td>2220163</td>
    <td class="align_left td_block">
        <a href="/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=PRC_F2F6N0O7M2"
           class="board_subject">근로기준법 일부개정법률안 (이강일의원 등 10인)</a>
</td> <td>의원</td> <td class="board_text">기후에너지환경노동위원회</td>
<td></td><td></td><td></td> <td class="align_right">43</td> <td></td> </tr>
"""


def test_mcst_board_extracts_title_url_date():
    items = reg.parse_mcst_board(MCST_PRESS_HTML, "pressView.jsp")
    assert len(items) == 2
    assert items[0].title.startswith("올해 상반기 방한 외국인 관광객")
    assert items[0].url == "https://www.mcst.go.kr/site/s_notice/press/pressView.jsp?pSeq=22609"
    assert items[0].date == "2026-07-28"


def test_mcst_board_keyword_filter():
    items = reg.parse_mcst_board(MCST_PRESS_HTML, "pressView.jsp")
    assert items[0].matches_keywords(), "관광객 언급인데도 안 걸리면 필터가 죽은 것"
    assert not items[1].matches_keywords(), "문화 일반 기사까지 걸리면 필터가 너무 헐렁한 것"


def test_ambiguous_keyword_needs_tourism_context():
    """
    "방한"(訪韓)은 "관광객 방한"과 "대통령 국빈 방한"에 둘 다 걸리는 동형이의어다.
    2026-07-29 실측: 한-브라질 중소기업 협력 기사가 본문의 "국빈 방한" 하나로
    규제·정책 동향에 잘못 들어왔다 — 그 회귀 방지용 테스트.
    """
    diplomatic = reg.Item(
        source="정책브리핑", title="한국과브라질 중소기업 분야 협력 추진", url="x",
        summary="올해 2월 룰라 브라질 대통령의 국빈 방한 시 체결한 MOU의 후속 조치를 논의했다.",
    )
    tourism = reg.Item(
        source="문체부", title="올해 상반기 방한 외국인 관광객 1,071만 명 돌파", url="x",
    )
    assert not diplomatic.matches_keywords(), "대통령 국빈 방한 기사까지 걸리면 오탐"
    assert tourism.matches_keywords(), "관광객 방한 기사는 여전히 걸려야 한다"


def test_korea_briefing_extracts_title_and_lead():
    items = reg.parse_korea_briefing(KOREA_BRIEFING_HTML)
    assert len(items) == 2
    assert items[1].title == "외국인관광 도시민박업 등록 절차 간소화"
    assert "관광진흥법 시행령" in items[1].summary
    assert items[1].url == "https://www.korea.kr/briefing/pressReleaseView.do?newsId=156772406&pageIndex=1"


def test_korea_briefing_keyword_filter_uses_summary_too():
    items = reg.parse_korea_briefing(KOREA_BRIEFING_HTML)
    matched = [i for i in items if i.matches_keywords()]
    assert len(matched) == 1, "제목엔 AI 얘기뿐이라 본문(lead)까지 봐야 도시민박 기사만 걸러진다"
    assert matched[0].title == "외국인관광 도시민박업 등록 절차 간소화"


def test_assembly_bills_parses_all_fields():
    bills = reg.parse_assembly_bills(ASSEMBLY_BILLS_HTML)
    assert len(bills) == 2
    b = bills[0]
    assert b.bill_no == "2219898"
    assert b.title == "관광진흥법 일부개정법률안 (김성원의원 등 10인)"
    assert b.committee == "문화체육관광위원회"
    assert b.proposer_type == "의원"
    assert b.views == 1624
    assert b.url == "https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=PRC_N2N6M0K7L0T8"


def test_assembly_href_across_newline_from_class():
    """
    실제 페이지는 href="..." 와 class="board_subject" 사이에 개행이 들어간다
    (pretty-printed JSP). 한 줄로 뭉갠 fixture로만 테스트하면 이 회귀를 놓친다 —
    2026-07-28 라이브에서 정확히 이걸로 0건이 나왔던 버그의 재발 방지 테스트.
    """
    row = '''<tr><td>1</td><td class="align_left td_block">
        <a href="/x?id=Y"
           class="board_subject">개행 사이에 낀 제목</a></td>
        <td>의원</td><td class="board_text">위원회</td><td></td><td></td><td></td>
        <td class="align_right">1</td><td></td></tr>'''
    bills = reg.parse_assembly_bills(row)
    assert len(bills) == 1
    assert bills[0].url == "https://pal.assembly.go.kr/x?id=Y"


def test_assembly_bills_row_without_optional_fields_still_parses():
    """미리보기·다운로드 링크가 비어 있는 행(위원회 배정 직후 등)도 죽지 않아야 한다."""
    bills = reg.parse_assembly_bills(ASSEMBLY_BILLS_HTML)
    assert bills[1].bill_no == "2220163"
    assert bills[1].views == 43


def test_empty_page_returns_empty_not_crash():
    assert reg.parse_mcst_board("<html>없음</html>", "pressView.jsp") == []
    assert reg.parse_korea_briefing("<html>없음</html>") == []
    assert reg.parse_assembly_bills("<html>없음</html>") == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
