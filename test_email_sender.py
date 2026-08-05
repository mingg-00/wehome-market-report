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
    """이 테스트 환경엔 SMTP_USER/PASSWORD가 없다고 가정 — 실제 발송 없이 dry-run으로 빠져야 한다."""
    if es.is_configured():
        print("  (SMTP가 실제로 설정돼 있어 이 테스트는 건너뜀 — 실발송 방지)")
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
