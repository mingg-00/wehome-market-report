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


def test_add_new_subscriber():
    _fresh_db()
    assert sub.add("a@example.com") == "new"
    assert sub.active_subscribers() == ["a@example.com"]


def test_add_duplicate_returns_already_active():
    _fresh_db()
    sub.add("a@example.com")
    assert sub.add("a@example.com") == "already_active"
    assert len(sub.active_subscribers()) == 1, "중복 추가로 행이 늘면 안 된다"


def test_unsubscribe_removes_from_active_list():
    _fresh_db()
    sub.add("a@example.com")
    assert sub.unsubscribe("a@example.com") is True
    assert sub.active_subscribers() == []


def test_unsubscribe_nonexistent_email_returns_false():
    _fresh_db()
    assert sub.unsubscribe("nobody@example.com") is False


def test_unsubscribe_twice_second_call_returns_false():
    """이미 탈퇴한 사람을 또 탈퇴시키려 하면 아무 일도 안 일어나야(false) 한다."""
    _fresh_db()
    sub.add("a@example.com")
    sub.unsubscribe("a@example.com")
    assert sub.unsubscribe("a@example.com") is False


def test_resubscribe_after_unsubscribe():
    _fresh_db()
    sub.add("a@example.com")
    sub.unsubscribe("a@example.com")
    assert sub.add("a@example.com") == "resubscribed"
    assert sub.active_subscribers() == ["a@example.com"]


def test_stats_counts_correctly():
    _fresh_db()
    sub.add("a@example.com")
    sub.add("b@example.com")
    sub.unsubscribe("a@example.com")
    s = sub.stats()
    assert s == {"total": 2, "active": 1, "unsubscribed": 1}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
