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
# 써서 렌더링한다(단일 소스, 폼과 저장 로직이 따로 놀지 않게).
CATEGORIES = {"policy": "정책·규제 동향", "market": "시장 통계·리포트", "news": "업계 뉴스"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    consented_at TEXT NOT NULL,
    source TEXT NOT NULL,
    confirmed_at TEXT,
    unsubscribed_at TEXT,
    categories TEXT
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
        yield c
        c.commit()
    finally:
        c.close()


def add(email: str, source: str = "landing_page", categories: list[str] | None = None) -> str:
    """
    더블 옵트인 — 여기서 바로 활성 구독자가 되지 않는다. 반환값:
      "pending_confirm"            신규 등록(또는 미인증 상태에서 재요청) — 인증 메일 발송 필요
      "resubscribe_pending_confirm" 탈퇴했다가 재신청 — 다시 인증 필요(탈퇴 전 인증은 무효화)
      "already_active"             이미 인증 완료 후 구독 중 — 메일 다시 안 보냄
    호출부가 이 값으로 인증 메일을 보낼지 말지 정한다.

    categories: CATEGORIES 키 목록. None/빈 리스트/전부 모르는 키면 NULL로 저장되고
    "전체 수신"으로 취급한다(관심사를 안 좁힌 사람은 다 받는 게 기본값) — 알 수 없는
    키는 조용히 걸러낸다.
    """
    cats = ",".join(k for k in (categories or []) if k in CATEGORIES) or None
    with _conn() as c:
        row = c.execute("SELECT confirmed_at, unsubscribed_at FROM subscribers WHERE email=?",
                         (email,)).fetchone()
        now = datetime.now().isoformat(timespec="seconds")
        if row is None:
            c.execute("INSERT INTO subscribers(email, consented_at, source, categories) VALUES (?,?,?,?)",
                      (email, now, source, cats))
            return "pending_confirm"
        if row["unsubscribed_at"] is not None:
            c.execute("UPDATE subscribers SET consented_at=?, source=?, categories=?, "
                      "unsubscribed_at=NULL, confirmed_at=NULL WHERE email=?",
                      (now, source, cats, email))
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


def active_subscribers() -> list[str]:
    """실제 메일을 받는 사람 — 인증 완료 + 탈퇴 안 함."""
    with _conn() as c:
        return [r["email"] for r in c.execute(
            "SELECT email FROM subscribers WHERE confirmed_at IS NOT NULL "
            "AND unsubscribed_at IS NULL ORDER BY consented_at")]


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
