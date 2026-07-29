#!/usr/bin/env python3
"""
구독자 저장 — SQLite.

어제(8/4) subscribers.json 파일로 임시로 만들었는데, 여러 요청이 겹치면
읽기-수정-쓰기 사이에 유실될 수 있는 실제 버그였다(그때도 주석으로 남겨둠).
동시 쓰기를 안전하게 처리하는 게 stdlib sqlite3 하나로 되니 새 의존성 없이
바로 교체한다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "subscribers.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    consented_at TEXT NOT NULL,
    source TEXT NOT NULL,
    unsubscribed_at TEXT
)
"""


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def add(email: str, source: str = "landing_page") -> str:
    """
    반환값: "new"(신규 등록) | "already_active"(이미 구독 중) | "resubscribed"(재구독).
    호출부가 "즉시 발송을 할지 말지"를 이 값으로 결정한다 — 이미 구독 중이면
    다시 이메일 보내지 않는다.
    """
    with _conn() as c:
        row = c.execute("SELECT unsubscribed_at FROM subscribers WHERE email=?", (email,)).fetchone()
        now = datetime.now().isoformat(timespec="seconds")
        if row is None:
            c.execute("INSERT INTO subscribers(email, consented_at, source) VALUES (?,?,?)",
                      (email, now, source))
            return "new"
        if row["unsubscribed_at"] is not None:
            c.execute("UPDATE subscribers SET consented_at=?, source=?, unsubscribed_at=NULL WHERE email=?",
                      (now, source, email))
            return "resubscribed"
        return "already_active"


def unsubscribe(email: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE subscribers SET unsubscribed_at=? WHERE email=? AND unsubscribed_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), email))
        return cur.rowcount > 0


def active_subscribers() -> list[str]:
    with _conn() as c:
        return [r["email"] for r in c.execute(
            "SELECT email FROM subscribers WHERE unsubscribed_at IS NULL ORDER BY consented_at")]


def stats() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        active = c.execute("SELECT COUNT(*) FROM subscribers WHERE unsubscribed_at IS NULL").fetchone()[0]
    return {"total": total, "active": active, "unsubscribed": total - active}


if __name__ == "__main__":
    print(f"DB: {DB_PATH}")
    print(stats())
