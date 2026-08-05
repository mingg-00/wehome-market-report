#!/usr/bin/env python3
"""구독 API 자체 점검(Flask test_client, 실제 서버 기동 없음). 임시 DB·SMTP dry-run 사용."""

import json
import os
import tempfile

import email_sender
import subscribe_server as srv
import subscribers as sub


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    sub.DB_PATH = path


def _client():
    srv.app.testing = True
    return srv.app.test_client()


def test_subscribe_new_email_returns_pending_confirm_and_sends_confirm_mail():
    """더블 옵트인 — 구독 직후엔 아직 인증 전이라 최신호가 아니라 인증 메일이 나가야 한다."""
    _fresh_db()
    c = _client()
    r = c.post("/subscribe", json={"email": "a@example.com", "consent": True})
    body = r.get_json()
    assert r.status_code == 201
    assert body["status"] == "pending_confirm"
    assert body["mail"]["dry_run"] is True, "테스트 환경엔 SMTP 미설정이라 dry-run이어야 한다"
    assert sub.active_subscribers() == [], "인증 전이라 아직 active면 안 된다"


def test_subscribe_missing_consent_rejected():
    _fresh_db()
    r = _client().post("/subscribe", json={"email": "a@example.com", "consent": False})
    assert r.status_code == 400


def test_subscribe_bad_email_rejected():
    _fresh_db()
    r = _client().post("/subscribe", json={"email": "not-an-email", "consent": True})
    assert r.status_code == 400


def test_confirm_with_valid_token_activates_subscriber():
    _fresh_db()
    c = _client()
    c.post("/subscribe", json={"email": "a@example.com", "consent": True})
    token = email_sender.confirm_token("a@example.com")
    r = c.get(f"/confirm?email=a@example.com&token={token}")
    assert "인증이 완료" in r.get_data(as_text=True)
    assert sub.active_subscribers() == ["a@example.com"]


def test_confirm_with_wrong_token_rejected():
    _fresh_db()
    c = _client()
    c.post("/subscribe", json={"email": "a@example.com", "consent": True})
    r = c.get("/confirm?email=a@example.com&token=wrong")
    assert r.status_code == 400
    assert sub.active_subscribers() == []


def test_confirm_twice_second_call_reports_already_confirmed():
    _fresh_db()
    c = _client()
    c.post("/subscribe", json={"email": "a@example.com", "consent": True})
    token = email_sender.confirm_token("a@example.com")
    c.get(f"/confirm?email=a@example.com&token={token}")
    r = c.get(f"/confirm?email=a@example.com&token={token}")
    assert "이미 인증" in r.get_data(as_text=True)


def test_subscribe_stores_selected_categories():
    import sqlite3
    _fresh_db()
    _client().post("/subscribe", json={"email": "a@example.com", "consent": True,
                                        "categories": ["policy", "news"]})
    c = sqlite3.connect(sub.DB_PATH)
    row = c.execute("SELECT categories FROM subscribers WHERE email=?", ("a@example.com",)).fetchone()
    assert row[0] == "policy,news"


def test_subscribe_categories_not_a_list_rejected():
    _fresh_db()
    r = _client().post("/subscribe", json={"email": "a@example.com", "consent": True,
                                            "categories": "policy"})
    assert r.status_code == 400


def test_subscribe_already_active_skips_mail():
    _fresh_db()
    c = _client()
    c.post("/subscribe", json={"email": "a@example.com", "consent": True})
    token = email_sender.confirm_token("a@example.com")
    c.get(f"/confirm?email=a@example.com&token={token}")
    r = c.post("/subscribe", json={"email": "a@example.com", "consent": True})
    body = r.get_json()
    assert body["status"] == "already_active"
    assert "mail" not in body, "이미 활성 구독자한텐 메일을 다시 안 보낸다"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
