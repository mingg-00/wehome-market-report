#!/usr/bin/env python3
"""
구독 즉시 발송 — Resend HTTPS API로 보낸다.

원래는 smtplib(Gmail SMTP)로 짰는데, 8/6 Railway 배포 후 실제로 발송이 전부
"[Errno 101] Network is unreachable"로 죽는 걸 발견했다 — Railway가 아웃바운드
SMTP(25/465/587)를 통째로 막아놔서다(Railway 자체 커뮤니티도 이 경우 HTTPS API
기반 발송 서비스로 바꾸라고 권장). SMTP는 코드를 아무리 고쳐도 그 호스트에서는
안 되는 문제라 프로토콜 자체를 HTTPS API로 바꿨다 — Resend는 일반 POST 요청이라
막힐 이유가 없고, 로컬에서도 Gmail 앱 비밀번호·2단계 인증 없이 그대로 된다.

자격증명이 없으면(.env 미설정) 실제 발송 대신 무엇을 보냈을지 로그만 남긴다.
kakao_sms_sender.py 의 dry-run 패턴과 같은 이유 — 아직 승인 전 기능을 조용히
건너뛰는 대신, "이렇게 동작할 것"을 눈에 보이게 남겨서 나중에 자격증명만
채우면 그대로 작동하게 한다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
# 발신 도메인을 아직 인증 안 했으면(wehome.me DNS 레코드 필요) Resend의 공유
# 테스트 도메인을 쓴다 — 단, 이 도메인은 Resend 가입 계정 본인 메일로만 보낼 수
# 있다는 제약이 있다. 실제 임의 구독자에게 보내려면 도메인 인증이 필요하다.
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev").strip()
FROM_NAME = os.getenv("SMTP_FROM_NAME", "위홈 공유숙박 마켓리포트")
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "").rstrip("/")
UNSUB_SECRET = os.getenv("UNSUB_SECRET", "").strip()

_MISSING_SECRET_WARNED = False


def _unsub_secret() -> str:
    """
    .env 에 UNSUB_SECRET 이 없으면 로컬 개발용 고정값을 쓴다 — 배포 전에는
    누가 수신거부 링크를 위조해도 실피해가 없어서 괜찮지만, 8/6 실배포 전에는
    반드시 .env 에 실제 비밀값을 넣어야 한다(그래야 링크 위조로 남을 임의
    구독취소시키는 걸 막는다).
    """
    global _MISSING_SECRET_WARNED
    if UNSUB_SECRET:
        return UNSUB_SECRET
    if not _MISSING_SECRET_WARNED:
        # stdout에 찍으면 `TOKEN=$(python3 ...)` 같은 캡처에 이 경고문이 토큰과 섞여
        # 들어간다(실제로 이 버그로 unsubscribe 토큰이 깨지는 걸 겪었다) — 로그는 stderr로.
        print("  ⚠️ UNSUB_SECRET 미설정 — 로컬 개발용 고정값 사용 중. 배포 전 .env에 실제 값 설정 필수.",
              file=sys.stderr)
        _MISSING_SECRET_WARNED = True
    return "dev-only-insecure-secret"


def unsubscribe_token(email: str) -> str:
    return hmac.new(_unsub_secret().encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:24]


def verify_unsubscribe_token(email: str, token: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(email), token)


def confirm_token(email: str) -> str:
    """
    더블 옵트인 인증 링크용 — unsubscribe_token과 같은 비밀값을 재사용하되, 서명
    대상 문자열 앞에 "confirm:" 접두어를 붙인다. 접두어 없이 email만 서명하면
    confirm_token(email) == unsubscribe_token(email)이 돼서(둘 다 email 하나만
    HMAC), 인증 링크를 수신거부 링크로 재생(replay)할 수 있게 된다.
    """
    return hmac.new(_unsub_secret().encode(), f"confirm:{email.lower()}".encode(),
                     hashlib.sha256).hexdigest()[:24]


def verify_confirm_token(email: str, token: str) -> bool:
    return hmac.compare_digest(confirm_token(email), token)


def is_configured() -> bool:
    return bool(RESEND_API_KEY)


def render_issue_email(ym: str, active: int, seoul_share: float, top_district: str,
                        report_url: str, unsubscribe_url: str) -> str:
    """
    이메일 클라이언트는 외부 CSS·flex/grid를 거의 지원 안 해서 사이트 CSS를 못 쓴다.
    전부 인라인 style, 레이아웃은 table 안 쓰고 block div로 최대한 단순하게 —
    Gmail/Outlook 양쪽에서 깨지지 않는 최소 공통분모.
    """
    return f"""<div style="font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:520px;margin:0 auto;padding:32px 20px;color:#161b22">
<div style="color:#00A88F;font-weight:800;font-size:11px;letter-spacing:.1em;text-transform:uppercase">SHARED STAY MARKET REPORT</div>
<h1 style="font-size:22px;margin:10px 0 4px">{ym} 공유숙박 마켓리포트</h1>
<p style="color:#5b6472;font-size:14px;margin:0 0 20px">구독해주셔서 감사합니다. 이번 달 핵심 수치를 먼저 보내드립니다.</p>
<div style="background:#f7f9fb;border:1px solid #e6e9ee;border-radius:12px;padding:18px 20px;margin-bottom:20px">
<div style="font-size:13px;color:#5b6472">외도민업 영업중</div>
<div style="font-size:28px;font-weight:800;margin:2px 0 12px">{active:,}곳</div>
<div style="font-size:13px;color:#5b6472">서울 비중 {seoul_share:.0%} · 최다 지역 {top_district}</div>
</div>
<a href="{report_url}" style="display:inline-block;background:#1B2A4A;color:#fff;text-decoration:none;
font-weight:700;font-size:14px;padding:12px 24px;border-radius:10px">전체 리포트 보기 →</a>
<p style="font-size:12px;color:#93a1b5;margin-top:32px;line-height:1.7">
행정안전부 원본 데이터 직접 수집·집계 · 위홈 마켓리포트<br>
더 이상 받고 싶지 않으신가요? <a href="{unsubscribe_url}" style="color:#93a1b5">수신거부</a></p>
</div>"""


def render_confirm_email(confirm_url: str) -> str:
    """더블 옵트인 인증 메일 — render_issue_email과 같은 인라인 스타일 규칙(외부 CSS
    못 쓰는 이메일 클라이언트 대응)을 따른다. 아직 구독이 활성화된 게 아니라는 걸
    분명히 하려고 "구독 완료"가 아니라 "한 단계 남았습니다"로 문구를 잡는다."""
    return f"""<div style="font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:520px;margin:0 auto;padding:32px 20px;color:#161b22">
<div style="color:#00A88F;font-weight:800;font-size:11px;letter-spacing:.1em;text-transform:uppercase">SHARED STAY MARKET REPORT</div>
<h1 style="font-size:22px;margin:10px 0 4px">구독 전 한 단계만 더</h1>
<p style="color:#5b6472;font-size:14px;margin:0 0 24px">아래 버튼을 눌러 이메일 주소를 인증하면 구독이 시작됩니다.
본인이 신청하지 않았다면 이 메일을 무시하셔도 됩니다 — 인증 전까지는 어떤 메일도 발송되지 않습니다.</p>
<a href="{confirm_url}" style="display:inline-block;background:#1B2A4A;color:#fff;text-decoration:none;
font-weight:700;font-size:14px;padding:12px 24px;border-radius:10px">구독 인증하기 →</a>
<p style="font-size:12px;color:#93a1b5;margin-top:32px;line-height:1.7">
위홈 마켓리포트</p>
</div>"""


def send_email(to_email: str, subject: str, html_body: str) -> dict:
    """
    반환: {"sent": bool, "dry_run": bool, "error": str|None}
    dry_run=True 면 실제로는 안 보냈다는 뜻 — 호출부가 이걸 보고 사용자에게
    "구독은 됐지만 메일은 아직 안 나갔다"를 명확히 알려야 한다(조용히 성공한 척 금지).
    """
    if not is_configured():
        print(f"  📭 [DRY-RUN] RESEND_API_KEY 미설정 — {to_email} 에게 '{subject}' 발송 시뮬레이션만 함")
        return {"sent": False, "dry_run": True, "error": None}

    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": f"{FROM_NAME} <{EMAIL_FROM}>", "to": [to_email],
                  "subject": subject, "html": html_body},
            timeout=15,
        )
        r.raise_for_status()
        print(f"  ✅ 발송 완료: {to_email}")
        return {"sent": True, "dry_run": False, "error": None}
    except Exception as e:
        detail = e.response.text if isinstance(e, requests.HTTPError) else str(e)
        print(f"  ❌ 발송 실패: {to_email} — {type(e).__name__}: {detail}")
        return {"sent": False, "dry_run": False, "error": detail}


def report_url(ym: str) -> str:
    base = SITE_BASE_URL or "https://SITE-BASE-URL-미설정.example"
    return f"{base}/report/{ym}.html"


def unsubscribe_url(email: str) -> str:
    base = SITE_BASE_URL or "https://SITE-BASE-URL-미설정.example"
    return f"{base}/unsubscribe?email={email}&token={unsubscribe_token(email)}"


def confirm_url(email: str) -> str:
    base = SITE_BASE_URL or "https://SITE-BASE-URL-미설정.example"
    return f"{base}/confirm?email={email}&token={confirm_token(email)}"


if __name__ == "__main__":
    print(f"메일 발송 설정됨(Resend): {is_configured()}")
    assert confirm_token("a@example.com") != unsubscribe_token("a@example.com"), \
        "인증 토큰과 수신거부 토큰이 같으면 링크 재생(replay)이 가능해진다"
    assert verify_confirm_token("a@example.com", confirm_token("a@example.com"))
    assert not verify_confirm_token("a@example.com", "wrong-token")
    html = render_issue_email("2026-07", 10894, 0.63, "마포구",
                               report_url("2026-07"), unsubscribe_url("test@example.com"))
    send_email("test@example.com", "[테스트] 2026-07 공유숙박 마켓리포트", html)
    send_email("test@example.com", "[테스트] 구독 인증", render_confirm_email(confirm_url("test@example.com")))
