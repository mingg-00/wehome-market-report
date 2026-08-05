#!/usr/bin/env python3
"""이메일 발송 모듈 자체 점검. 실제 SMTP 연결 없음(SMTP 미설정이면 자동 dry-run)."""

import email_sender as es


def test_unsubscribe_token_is_deterministic_and_verifiable():
    t1 = es.unsubscribe_token("a@example.com")
    t2 = es.unsubscribe_token("a@example.com")
    assert t1 == t2, "같은 이메일은 항상 같은 토큰이어야 링크가 재사용 가능하다"
    assert es.verify_unsubscribe_token("a@example.com", t1) is True


def test_unsubscribe_token_differs_per_email():
    assert es.unsubscribe_token("a@example.com") != es.unsubscribe_token("b@example.com")


def test_verify_rejects_wrong_token():
    assert es.verify_unsubscribe_token("a@example.com", "wrong-token") is False


def test_verify_rejects_token_for_different_email():
    """b의 토큰으로 a를 수신거부시키는 걸 막아야 한다 — 링크 위조 방지의 핵심."""
    token_for_b = es.unsubscribe_token("b@example.com")
    assert es.verify_unsubscribe_token("a@example.com", token_for_b) is False


def test_dry_run_when_smtp_not_configured():
    """이 테스트 환경엔 RESEND_API_KEY가 없다고 가정 — 실제 발송 없이 dry-run으로 빠져야 한다."""
    if es.is_configured():
        print("  (RESEND_API_KEY가 실제로 설정돼 있어 이 테스트는 건너뜀 — 실발송 방지)")
        return
    result = es.send_email("test@example.com", "제목", "<p>본문</p>")
    assert result == {"sent": False, "dry_run": True, "error": None}


def test_confirm_token_is_deterministic_and_verifiable():
    t1 = es.confirm_token("a@example.com")
    t2 = es.confirm_token("a@example.com")
    assert t1 == t2
    assert es.verify_confirm_token("a@example.com", t1) is True


def test_confirm_token_differs_from_unsubscribe_token():
    """같은 비밀값을 재사용하니, 접두어 없이 email만 서명하면 두 토큰이 같아져서
    인증 링크로 수신거부를 트리거하는(또는 그 반대) 재생 공격이 가능해진다."""
    assert es.confirm_token("a@example.com") != es.unsubscribe_token("a@example.com")


def test_verify_confirm_token_rejects_wrong_token():
    assert es.verify_confirm_token("a@example.com", "wrong-token") is False


def test_verify_confirm_token_rejects_unsubscribe_token_for_same_email():
    """수신거부 링크를 그대로 인증 링크에 넣어도 통과하면 안 된다."""
    assert es.verify_confirm_token("a@example.com", es.unsubscribe_token("a@example.com")) is False


def test_send_email_success_via_resend(monkeypatch=None):
    """실제 네트워크 없이 requests.post만 바꿔치기 — Resend 성공 응답을 흉내낸다.
    email_sender.send_email이 API 실패를 조용히 삼키지 않고 sent/dry_run/error를
    정확히 채우는지가 핵심(subscribe_server.py가 이 값으로 사용자에게 발송 여부를
    보여준다)."""
    import requests

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

    calls = []
    original_post = requests.post
    original_key = es.RESEND_API_KEY
    requests.post = lambda url, headers, json, timeout: (calls.append((url, headers, json)), FakeResp())[1]
    es.RESEND_API_KEY = "re_test_fake_key"
    try:
        result = es.send_email("a@example.com", "제목", "<p>본문</p>")
    finally:
        requests.post = original_post
        es.RESEND_API_KEY = original_key

    assert result == {"sent": True, "dry_run": False, "error": None}
    url, headers, payload = calls[0]
    assert url == "https://api.resend.com/emails"
    assert headers["Authorization"] == "Bearer re_test_fake_key"
    assert payload["to"] == ["a@example.com"]


def test_send_email_surfaces_resend_error_body():
    """Resend가 403(도메인 미인증 등)을 주면 dry_run이 아니라 명시적 실패로 잡아야
    한다 — dry_run=True로 잘못 보고하면 호출부가 '개발 환경이라 안 나갔다'고
    안내해서, 실제로는 시도했다 실패했다는 걸 사용자가 알 길이 없어진다."""
    import requests

    class FakeErrorResp:
        status_code = 403
        text = '{"statusCode":403,"message":"domain not verified"}'

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    original_post = requests.post
    original_key = es.RESEND_API_KEY
    requests.post = lambda url, headers, json, timeout: FakeErrorResp()
    es.RESEND_API_KEY = "re_test_fake_key"
    try:
        result = es.send_email("a@example.com", "제목", "<p>본문</p>")
    finally:
        requests.post = original_post
        es.RESEND_API_KEY = original_key

    assert result["sent"] is False
    assert result["dry_run"] is False
    assert "domain not verified" in result["error"]


def test_render_confirm_email_contains_confirm_link():
    html = es.render_confirm_email("https://example.com/confirm?email=a@x.com&token=abc")
    assert "https://example.com/confirm?email=a@x.com&token=abc" in html


def test_render_issue_email_contains_key_fields():
    html = es.render_issue_email("2026-07", 10894, 0.63, "마포구",
                                  "https://example.com/report/2026-07.html",
                                  "https://example.com/unsubscribe?email=a@x.com&token=abc")
    assert "10,894" in html
    assert "63%" in html
    assert "마포구" in html
    assert "https://example.com/report/2026-07.html" in html
    assert "수신거부" in html


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("\n통과.")
