#!/usr/bin/env python3
"""구독자 저장 자체 점검. `python test_subscribers.py` 로 실행. 임시 DB 파일 사용, 실행 후 삭제."""

import os
import tempfile

import subscribers as sub


def _fresh_db():
    """매 테스트가 독립된 DB를 쓰도록 DB_PATH를 임시 파일로 바꿔치기."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # sqlite가 새로 만들게
    sub.DB_PATH = path
    return path


def test_add_new_subscriber_is_pending_not_active():
    """더블 옵트인 — add() 직후엔 아직 인증 전이라 active_subscribers()에 안 잡혀야 한다."""
    _fresh_db()
    assert sub.add("a@example.com") == "pending_confirm"
    assert sub.active_subscribers() == []


def test_confirm_moves_pending_to_active():
    _fresh_db()
    sub.add("a@example.com")
    assert sub.confirm("a@example.com") is True
    assert sub.active_subscribers() == ["a@example.com"]


def test_confirm_twice_second_call_returns_false():
    _fresh_db()
    sub.add("a@example.com")
    sub.confirm("a@example.com")
    assert sub.confirm("a@example.com") is False, "이미 인증된 걸 또 인증 처리하면 안 된다"


def test_confirm_nonexistent_email_returns_false():
    _fresh_db()
    assert sub.confirm("nobody@example.com") is False


def test_add_while_still_pending_returns_pending_again():
    """인증 메일을 못 받았거나 잃어버렸을 때 재요청 — 중복 행 없이 다시 pending을 돌려줘야 한다."""
    _fresh_db()
    sub.add("a@example.com")
    assert sub.add("a@example.com") == "pending_confirm"
    assert sub.stats()["total"] == 1, "재요청으로 행이 늘면 안 된다"


def test_add_after_confirmed_returns_already_active():
    _fresh_db()
    sub.add("a@example.com")
    sub.confirm("a@example.com")
    assert sub.add("a@example.com") == "already_active"
    assert len(sub.active_subscribers()) == 1, "중복 추가로 행이 늘면 안 된다"


def test_unsubscribe_removes_confirmed_subscriber_from_active_list():
    _fresh_db()
    sub.add("a@example.com")
    sub.confirm("a@example.com")
    assert sub.unsubscribe("a@example.com") is True
    assert sub.active_subscribers() == []


def test_unsubscribe_pending_subscriber_who_never_confirmed():
    """인증 전에도 수신거부는 돼야 한다(더 이상 인증 메일도 안 받고 싶을 수 있음)."""
    _fresh_db()
    sub.add("a@example.com")
    assert sub.unsubscribe("a@example.com") is True


def test_unsubscribe_nonexistent_email_returns_false():
    _fresh_db()
    assert sub.unsubscribe("nobody@example.com") is False


def test_unsubscribe_twice_second_call_returns_false():
    """이미 탈퇴한 사람을 또 탈퇴시키려 하면 아무 일도 안 일어나야(false) 한다."""
    _fresh_db()
    sub.add("a@example.com")
    sub.confirm("a@example.com")
    sub.unsubscribe("a@example.com")
    assert sub.unsubscribe("a@example.com") is False


def test_resubscribe_after_unsubscribe_requires_confirm_again():
    """탈퇴 전 인증 여부와 무관하게, 재신청은 다시 인증을 거쳐야 한다 — 더블 옵트인 취지상
    본인 의사를 매번 다시 확인하는 게 맞다."""
    _fresh_db()
    sub.add("a@example.com")
    sub.confirm("a@example.com")
    sub.unsubscribe("a@example.com")
    assert sub.add("a@example.com") == "resubscribe_pending_confirm"
    assert sub.active_subscribers() == []
    assert sub.confirm("a@example.com") is True
    assert sub.active_subscribers() == ["a@example.com"]


def test_stats_counts_correctly():
    _fresh_db()
    sub.add("a@example.com")           # pending
    sub.add("b@example.com")
    sub.confirm("b@example.com")       # active
    sub.add("c@example.com")
    sub.confirm("c@example.com")
    sub.unsubscribe("c@example.com")   # unsubscribed
    s = sub.stats()
    assert s == {"total": 3, "active": 1, "pending_confirm": 1, "unsubscribed": 1}


def test_add_stores_selected_categories():
    import sqlite3
    _fresh_db()
    sub.add("a@example.com", categories=["policy", "market"])
    c = sqlite3.connect(sub.DB_PATH)
    row = c.execute("SELECT categories FROM subscribers WHERE email=?", ("a@example.com",)).fetchone()
    assert row[0] == "policy,market"


def test_add_filters_out_unknown_category_keys():
    import sqlite3
    _fresh_db()
    sub.add("a@example.com", categories=["policy", "not-a-real-category"])
    c = sqlite3.connect(sub.DB_PATH)
    row = c.execute("SELECT categories FROM subscribers WHERE email=?", ("a@example.com",)).fetchone()
    assert row[0] == "policy"


def test_add_without_categories_stores_null_meaning_all():
    import sqlite3
    _fresh_db()
    sub.add("a@example.com")
    c = sqlite3.connect(sub.DB_PATH)
    row = c.execute("SELECT categories FROM subscribers WHERE email=?", ("a@example.com",)).fetchone()
    assert row[0] is None


def test_migration_grandfathers_pre_existing_rows_as_confirmed():
    """더블 옵트인 도입 전 스키마(confirmed_at 컬럼 없음)로 이미 존재하던 행은, 마이그레이션
    시 전부 인증된 걸로 백필돼야 한다 — 안 그러면 이미 구독 중이던 사람들이 갑자기
    메일을 못 받게 된다(실제 배포 데이터를 상정한 회귀 가드)."""
    import sqlite3
    path = _fresh_db()
    # 구버전 스키마로 직접 행을 만든다(confirmed_at 컬럼 자체가 없던 시절 흉내).
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE subscribers (email TEXT PRIMARY KEY, consented_at TEXT NOT NULL, "
              "source TEXT NOT NULL, unsubscribed_at TEXT)")
    c.execute("INSERT INTO subscribers(email, consented_at, source) VALUES (?,?,?)",
              ("old@example.com", "2026-01-01T00:00:00", "landing_page"))
    c.commit()
    c.close()

    assert sub.active_subscribers() == ["old@example.com"], "마이그레이션 전 가입자가 그대로 활성 상태여야 한다"


def test_pending_lists_unconfirmed_only():
    _fresh_db()
    sub.add("a@example.com")
    sub.add("b@example.com")
    sub.confirm("b@example.com")
    emails = [r["email"] for r in sub.pending()]
    assert emails == ["a@example.com"]


def test_delete_removes_row():
    _fresh_db()
    sub.add("a@example.com")
    assert sub.delete("a@example.com") is True
    assert sub.pending() == []


def test_delete_nonexistent_returns_false():
    _fresh_db()
    assert sub.delete("nobody@example.com") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
