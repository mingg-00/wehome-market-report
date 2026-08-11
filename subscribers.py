#!/usr/bin/env python3
"""
구독자 저장 — SQLite.

어제(8/4) subscribers.json 파일로 임시로 만들었는데, 여러 요청이 겹치면
읽기-수정-쓰기 사이에 유실될 수 있는 실제 버그였다(그때도 주석으로 남겨둠).
동시 쓰기를 안전하게 처리하는 게 stdlib sqlite3 하나로 되니 새 의존성 없이
바로 교체한다.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# 영구 볼륨을 안 붙이면 컨테이너 재배포마다 로컬 파일이 초기화돼 구독자가 전부
# 날아간다. Railway는 볼륨을 서비스에 붙이면 RAILWAY_VOLUME_MOUNT_PATH를 자동으로
# 심어준다(사람이 값을 안 넣어도 됨) — 우선 그걸 쓰고, 없으면(Render 등 다른 호스트라
# 이 env var가 없는 경우) SUBSCRIBERS_DB_PATH를 수동으로 넣게 한다. 둘 다 없으면
# 로컬 개발 기본 경로.
_railway_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
DB_PATH = Path(
    os.getenv("SUBSCRIBERS_DB_PATH")
    or (f"{_railway_volume}/subscribers.db" if _railway_volume else None)
    or str(Path(__file__).parent / "subscribers.db")
)

# 분야별 구독 선택지 — build_site.py의 구독 폼 체크박스가 이 딕셔너리를 그대로
# 써서 렌더링한다(단일 소스, 폼과 저장 로직이 따로 놀지 않게). "market"이 실제로
# 주기 발송되는 유일한 카테고리다(digest.py) — 발송 주기는 아래 FREQUENCIES에서
# 구독자가 직접 고른다. policy·news는 저장만 되고 어떤 메일 발송에도 아직 안 쓰인다.
CATEGORIES = {"policy": "정책·규제 동향", "market": "시장 통계·리포트", "news": "업계 뉴스"}

# 8/11부터 "market" 구독자가 수신 주기를 직접 고른다 — 원본 등록 데이터는 월 단위로만
# 갱신되니(build_site.py) monthly가 실제 데이터 갱신 주기와 가장 가깝고, 안 고르거나
# 잘못된 값이 오면(add()) monthly로 떨어진다 — 매주 스팸처럼 느껴지는 쪽보다 덜 놀라운
# 기본값. dict 순서가 그대로 구독 폼의 라디오 버튼 순서가 된다.
FREQUENCIES = {"weekly": "매주", "biweekly": "2주마다", "monthly": "매달"}
_INTERVAL_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    consented_at TEXT NOT NULL,
    source TEXT NOT NULL,
    confirmed_at TEXT,
    unsubscribed_at TEXT,
    categories TEXT,
    frequency TEXT,
    last_sent_at TEXT
)
"""


@contextmanager
def _conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(subscribers)")}
        if "confirmed_at" not in cols:
            # 더블 옵트인 도입 전 가입자는 이미 (구식) 단일 단계 동의를 거쳤으니 인증된
            # 걸로 간주해 그대로 구독 유지시킨다 — 이 ALTER가 도는 순간엔 테이블에 있는
            # 행 전부가 "도입 전" 가입자라(신규 코드가 아직 pending 행을 만든 적이 없으니)
            # 안전하게 일괄 백필할 수 있다. 컬럼이 이미 있으면(다음 연결부터) 건너뛴다.
            c.execute("ALTER TABLE subscribers ADD COLUMN confirmed_at TEXT")
            c.execute("UPDATE subscribers SET confirmed_at = consented_at WHERE unsubscribed_at IS NULL")
        if "categories" not in cols:
            # 분야별 선택 도입 전 가입자는 전체 수신으로 간주 — NULL을 "선택 없음(전체 수신)"
            # 으로 다루는 건 to_category_list()가 처리한다, 여기선 컬럼만 추가하면 된다.
            c.execute("ALTER TABLE subscribers ADD COLUMN categories TEXT")
        if "frequency" not in cols:
            # 주기 선택 도입 전 가입자는 기존에 실제로 약속했던 주기(매주 발송,
            # DEPLOY.md 8/7 항목)를 그대로 유지한다 — 마이그레이션 한 번으로 조용히
            # 발송 빈도가 줄면 안 된다.
            c.execute("ALTER TABLE subscribers ADD COLUMN frequency TEXT")
            c.execute("UPDATE subscribers SET frequency = 'weekly' WHERE unsubscribed_at IS NULL")
        if "last_sent_at" not in cols:
            c.execute("ALTER TABLE subscribers ADD COLUMN last_sent_at TEXT")
        yield c
        c.commit()
    finally:
        c.close()


def add(email: str, source: str = "landing_page", categories: list[str] | None = None,
        frequency: str | None = None) -> str:
    """
    더블 옵트인 — 여기서 바로 활성 구독자가 되지 않는다. 반환값:
      "pending_confirm"            신규 등록(또는 미인증 상태에서 재요청) — 인증 메일 발송 필요
      "resubscribe_pending_confirm" 탈퇴했다가 재신청 — 다시 인증 필요(탈퇴 전 인증은 무효화)
      "already_active"             이미 인증 완료 후 구독 중 — 메일 다시 안 보냄
    호출부가 이 값으로 인증 메일을 보낼지 말지 정한다.

    categories: CATEGORIES 키 목록. None/빈 리스트/전부 모르는 키면 NULL로 저장되고
    "전체 수신"으로 취급한다(관심사를 안 좁힌 사람은 다 받는 게 기본값) — 알 수 없는
    키는 조용히 걸러낸다.

    frequency: FREQUENCIES 키(weekly/biweekly/monthly). 모르는 값이거나 안 주면
    monthly로 떨어진다 — 원본 데이터 갱신 주기와 가장 가깝고, 매주 스팸처럼
    느껴지는 쪽보다 덜 놀라운 기본값.
    """
    cats = ",".join(k for k in (categories or []) if k in CATEGORIES) or None
    freq = frequency if frequency in FREQUENCIES else "monthly"
    with _conn() as c:
        row = c.execute("SELECT confirmed_at, unsubscribed_at FROM subscribers WHERE email=?",
                         (email,)).fetchone()
        now = datetime.now().isoformat(timespec="seconds")
        if row is None:
            c.execute("INSERT INTO subscribers(email, consented_at, source, categories, frequency) "
                      "VALUES (?,?,?,?,?)", (email, now, source, cats, freq))
            return "pending_confirm"
        if row["unsubscribed_at"] is not None:
            c.execute("UPDATE subscribers SET consented_at=?, source=?, categories=?, frequency=?, "
                      "unsubscribed_at=NULL, confirmed_at=NULL, last_sent_at=NULL WHERE email=?",
                      (now, source, cats, freq, email))
            return "resubscribe_pending_confirm"
        if row["confirmed_at"] is None:
            return "pending_confirm"
        return "already_active"


def confirm(email: str) -> bool:
    """인증 링크 클릭 시 호출. 이미 인증됐거나, 탈퇴했거나, 존재하지 않으면 False."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE subscribers SET confirmed_at=? WHERE email=? "
            "AND confirmed_at IS NULL AND unsubscribed_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), email))
        return cur.rowcount > 0


def unsubscribe(email: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE subscribers SET unsubscribed_at=? WHERE email=? AND unsubscribed_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), email))
        return cur.rowcount > 0


def active_subscribers(category: str | None = None) -> list[str]:
    """실제 메일을 받는 사람 — 인증 완료 + 탈퇴 안 함.

    category를 주면 그 분야를 받는 사람만 거른다. categories가 NULL인 행(가입 시
    분야를 안 좁힌 사람, 또는 분야 선택 도입 전 가입자)은 "전체 수신"으로 취급해
    어떤 category를 넣어도 포함된다 — add()의 독스트링에 적어둔 규칙과 같다."""
    with _conn() as c:
        rows = c.execute(
            "SELECT email, categories FROM subscribers WHERE confirmed_at IS NOT NULL "
            "AND unsubscribed_at IS NULL ORDER BY consented_at")
        if category is None:
            return [r["email"] for r in rows]
        return [r["email"] for r in rows
                if r["categories"] is None or category in r["categories"].split(",")]


def due_for_digest(category: str, now: datetime | None = None) -> list[str]:
    """category를 받는 활성 구독자 중, 각자 고른 frequency만큼 last_sent_at 이후
    시간이 지난 사람만 골라 돌려준다. 한 번도 안 보낸 사람(last_sent_at NULL)은
    무조건 대상 — 발송 여부는 호출부가 mark_sent()로 기록해야 다음 날에도
    같은 사람이 계속 대상으로 잡히지 않는다."""
    now = now or datetime.now()
    with _conn() as c:
        rows = c.execute(
            "SELECT email, categories, frequency, last_sent_at FROM subscribers "
            "WHERE confirmed_at IS NOT NULL AND unsubscribed_at IS NULL ORDER BY consented_at")
        due = []
        for r in rows:
            if r["categories"] is not None and category not in r["categories"].split(","):
                continue
            if r["last_sent_at"] is not None:
                days = _INTERVAL_DAYS.get(r["frequency"], _INTERVAL_DAYS["monthly"])
                if (now - datetime.fromisoformat(r["last_sent_at"])).days < days:
                    continue
            due.append(r["email"])
        return due


def mark_sent(emails: list[str], when: datetime | None = None) -> None:
    """due_for_digest()가 고른 사람들에게 실제로 보낸 뒤 호출 — 다음 조회부터
    각자의 frequency만큼 다시 쉬게 한다."""
    if not emails:
        return
    ts = (when or datetime.now()).isoformat(timespec="seconds")
    with _conn() as c:
        c.executemany("UPDATE subscribers SET last_sent_at=? WHERE email=?",
                       [(ts, e) for e in emails])


def pending() -> list[dict]:
    """관리자 점검용 — 미인증 상태로 남아 있는 이메일과 등록 시각. 실 방문자의
    가입 시도인지 테스트 흔적인지 지우기 전에 구분할 수 있어야 해서 만든다."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT email, consented_at, source FROM subscribers "
            "WHERE confirmed_at IS NULL AND unsubscribed_at IS NULL ORDER BY consented_at")]


def delete(email: str) -> bool:
    """행을 완전히 지운다(탈퇴와 다름 — 탈퇴는 unsubscribed_at만 채우고 기록은
    남긴다). 관리자가 테스트로 만든 미인증 행을 치울 때만 쓴다."""
    with _conn() as c:
        cur = c.execute("DELETE FROM subscribers WHERE email=?", (email,))
        return cur.rowcount > 0


def stats() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        active = c.execute(
            "SELECT COUNT(*) FROM subscribers WHERE confirmed_at IS NOT NULL "
            "AND unsubscribed_at IS NULL").fetchone()[0]
        pending = c.execute(
            "SELECT COUNT(*) FROM subscribers WHERE confirmed_at IS NULL "
            "AND unsubscribed_at IS NULL").fetchone()[0]
        unsubscribed = c.execute(
            "SELECT COUNT(*) FROM subscribers WHERE unsubscribed_at IS NOT NULL").fetchone()[0]
    return {"total": total, "active": active, "pending_confirm": pending, "unsubscribed": unsubscribed}


if __name__ == "__main__":
    print(f"DB: {DB_PATH}")
    print(stats())
