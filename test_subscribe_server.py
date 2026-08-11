#!/usr/bin/env python3
"""구독 API 자체 점검(Flask test_client, 실제 서버 기동 없음). 임시 DB 사용."""

import json
import os
import tempfile
from pathlib import Path

import requests

import email_sender
import subscribe_server as srv
import subscribers as sub


# 로컬 .env에 진짜 자격증명이 채워진 뒤 이 스위트가 가짜 주소(a@example.com)로 실제
# 메일을 보내려던 걸 실측 중 발견했다 — 자격증명을 지워서 send_email이 스스로 dry-run
# 으로 빠지게 한다. 처음엔 send_email 자체를 가짜 함수로 갈아치웠는데, 그러면 복원할
# 지점이 없어서 같은 pytest 세션의 test_email_sender.py까지 그 가짜를 물고 돌아
# 거기 Resend 테스트 2개가 항상 dry_run=True로 깨졌다(8/6 pytest 도입하며 발견).
# 공유 모듈의 함수를 되돌릴 수 없게 바꾸지 말 것 — 값만 바꾸면 저쪽에서 자기가 쓸
# 키를 직접 세팅했다 되돌리는 기존 패턴과 충돌하지 않는다.
email_sender.RESEND_API_KEY = ""
# 같은 이유로 SITE_BASE_URL도 비운다 — 로컬 .env엔 실제 Vercel 주소가 채워져 있어서
# 그냥 두면 _latest_issue()가 테스트 중에 진짜 HTTP 요청을 날린다.
email_sender.SITE_BASE_URL = ""


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


def test_admin_send_digest_requires_token():
    _fresh_db()
    assert _client().post("/admin/send-digest").status_code == 403
    assert _client().post("/admin/send-digest?token=wrong").status_code == 403


def test_admin_send_digest_only_targets_market_category(tmp_path=None):
    """로그인 없이 기존 구독 체크박스로 처리하기로 한 결정의 핵심 — "market"(시장
    통계·리포트) 카테고리를 고른 사람만 다이제스트를 받아야 한다."""
    _fresh_db()
    original_secret = srv.UNSUB_SECRET
    original_latest = srv.LATEST_ISSUE
    srv.UNSUB_SECRET = "test-secret"
    fixture = Path(tempfile.mkstemp(suffix=".json")[1])
    fixture.write_text(json.dumps({"ym": "2026-08", "active": 100, "seoul_share": 0.5,
                                    "top_district": "마포구"}), encoding="utf-8")
    srv.LATEST_ISSUE = fixture
    try:
        c = _client()
        for email, cats in [("a@example.com", ["market"]), ("b@example.com", ["news"])]:
            c.post("/subscribe", json={"email": email, "consent": True, "categories": cats})
            token = email_sender.confirm_token(email)
            c.get(f"/confirm?email={email}&token={token}")

        r = c.post("/admin/send-digest?token=test-secret")
        body = r.get_json()
        assert r.status_code == 200
        assert body["attempted"] == 1, "market이 아닌 news만 고른 b@example.com은 빠져야 한다"
        assert body["issue_ym"] == "2026-08"
    finally:
        srv.UNSUB_SECRET = original_secret
        srv.LATEST_ISSUE = original_latest
        fixture.unlink()


def test_admin_send_digest_skips_subscriber_not_due_yet():
    """가입 시 monthly를 고른 사람은 방금 보냈으면 다음 날 다시 호출해도 또 안
    받아야 한다 — due_for_digest()가 실제로 걸러지는지 API 레벨에서 확인."""
    _fresh_db()
    original_secret = srv.UNSUB_SECRET
    original_latest = srv.LATEST_ISSUE
    srv.UNSUB_SECRET = "test-secret"
    fixture = Path(tempfile.mkstemp(suffix=".json")[1])
    fixture.write_text(json.dumps({"ym": "2026-08", "active": 100, "seoul_share": 0.5,
                                    "top_district": "마포구"}), encoding="utf-8")
    srv.LATEST_ISSUE = fixture
    try:
        c = _client()
        c.post("/subscribe", json={"email": "a@example.com", "consent": True,
                                    "categories": ["market"], "frequency": "monthly"})
        token = email_sender.confirm_token("a@example.com")
        c.get(f"/confirm?email=a@example.com&token={token}")

        r1 = c.post("/admin/send-digest?token=test-secret")
        assert r1.get_json()["attempted"] == 1
        sub.mark_sent(["a@example.com"])  # dry-run이라 서버가 직접 못 채우니 대신 채운다

        r2 = c.post("/admin/send-digest?token=test-secret")
        assert r2.get_json()["attempted"] == 0, "방금 보낸 monthly 구독자가 바로 다시 대상이면 안 된다"
    finally:
        srv.UNSUB_SECRET = original_secret
        srv.LATEST_ISSUE = original_latest
        fixture.unlink()


def test_admin_send_digest_no_issue_yet_returns_zero():
    _fresh_db()
    original_secret = srv.UNSUB_SECRET
    original_latest = srv.LATEST_ISSUE
    srv.UNSUB_SECRET = "test-secret"
    srv.LATEST_ISSUE = Path("/nonexistent/latest_issue.json")
    try:
        r = _client().post("/admin/send-digest?token=test-secret")
        body = r.get_json()
        assert body == {"sent": 0, "attempted": 0, "error": "아직 발행된 리포트가 없습니다."}
    finally:
        srv.UNSUB_SECRET = original_secret
        srv.LATEST_ISSUE = original_latest


def test_subscribe_stores_chosen_frequency():
    import sqlite3
    _fresh_db()
    _client().post("/subscribe", json={"email": "a@example.com", "consent": True,
                                        "frequency": "biweekly"})
    c = sqlite3.connect(sub.DB_PATH)
    row = c.execute("SELECT frequency FROM subscribers WHERE email=?", ("a@example.com",)).fetchone()
    assert row[0] == "biweekly"


def test_latest_issue_fetches_from_deployed_site():
    """서버(Railway)엔 site/가 없다 — 배포된 사이트에서 가져와야 한다. 로컬 파일만
    보던 탓에 프로덕션에서 인증 완료 메일이 안 나가던 게 이 테스트의 존재 이유."""
    original = email_sender.SITE_BASE_URL
    original_get = requests.get
    email_sender.SITE_BASE_URL = "https://example.com"

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"ym": "2026-08", "active": 1, "seoul_share": 0.5,
                                "top_district": "마포구"}

    urls = []
    requests.get = lambda url, timeout: (urls.append(url), FakeResp())[1]
    try:
        assert srv._latest_issue()["ym"] == "2026-08"
        assert urls == ["https://example.com/latest_issue.json"]
    finally:
        email_sender.SITE_BASE_URL = original
        requests.get = original_get


def test_latest_issue_falls_back_when_fetch_fails():
    """가져오기가 실패해도 /confirm은 이미 인증을 끝낸 뒤다 — 예외가 밖으로 새면
    사용자는 인증이 실패한 줄 안다."""
    original = email_sender.SITE_BASE_URL
    original_get = requests.get
    original_path = srv.LATEST_ISSUE
    email_sender.SITE_BASE_URL = "https://example.com"

    def boom(url, timeout):
        raise requests.ConnectionError("network down")

    requests.get = boom
    srv.LATEST_ISSUE = Path("/nonexistent/latest_issue.json")
    try:
        assert srv._latest_issue() is None
    finally:
        email_sender.SITE_BASE_URL = original
        requests.get = original_get
        srv.LATEST_ISSUE = original_path


def test_subscribers_stats_requires_token():
    """이 엔드포인트는 실배포로 인터넷에 그대로 열려 있다 — 토큰 없이 구독자 수가
    새면 안 된다."""
    _fresh_db()
    assert _client().get("/subscribers").status_code == 403
    assert _client().get("/subscribers?token=wrong").status_code == 403


def test_subscribers_stats_rejects_when_secret_unset():
    """UNSUB_SECRET이 비어 있을 때 빈 토큰이 빈 비밀값과 '일치'해서 통과해버리는 게
    제일 흔한 사고다 — 미설정은 무조건 거부여야 한다."""
    _fresh_db()
    original = srv.UNSUB_SECRET
    srv.UNSUB_SECRET = ""
    try:
        assert _client().get("/subscribers?token=").status_code == 403
    finally:
        srv.UNSUB_SECRET = original


def test_subscribers_stats_with_valid_token_returns_counts():
    _fresh_db()
    original = srv.UNSUB_SECRET
    srv.UNSUB_SECRET = "test-secret"
    try:
        c = _client()
        c.post("/subscribe", json={"email": "a@example.com", "consent": True})
        r = c.get("/subscribers?token=test-secret")
        assert r.status_code == 200
        assert r.get_json()["pending_confirm"] == 1
    finally:
        srv.UNSUB_SECRET = original


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
