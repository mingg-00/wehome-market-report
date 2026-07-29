#!/usr/bin/env python3
"""
구독 즉시 발송 — wehome-newsletter/send_newsletter.py 의 SMTP 발송 로직을
참고해 최소 버전으로 재구현했다(그대로 import는 안 함 — 그쪽은 이미지 CID
임베드·트래킹 픽셀 등 뉴스레터 전용 기능이 얽혀 있고, 우리는 요약 이메일 하나만
필요해서 가져다 쓰면 필요 없는 것까지 딸려온다. "재사용"의 취지는 SMTP 발송
방식·자격증명 읽는 패턴을 그대로 따르는 것으로 충분하다고 판단).

자격증명이 없으면(.env 미설정) 실제 발송 대신 무엇을 보냈을지 로그만 남긴다.
kakao_sms_sender.py 의 dry-run 패턴과 같은 이유 — 아직 승인 전 기능을 조용히
건너뛰는 대신, "이렇게 동작할 것"을 눈에 보이게 남겨서 나중에 자격증명만
채우면 그대로 작동하게 한다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
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


def is_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD)


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


def send_email(to_email: str, subject: str, html_body: str) -> dict:
    """
    반환: {"sent": bool, "dry_run": bool, "error": str|None}
    dry_run=True 면 실제로는 안 보냈다는 뜻 — 호출부가 이걸 보고 사용자에게
    "구독은 됐지만 메일은 아직 안 나갔다"를 명확히 알려야 한다(조용히 성공한 척 금지).
    """
    if not is_configured():
        print(f"  📭 [DRY-RUN] SMTP 미설정 — {to_email} 에게 '{subject}' 발송 시뮬레이션만 함")
        return {"sent": False, "dry_run": True, "error": None}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())
        print(f"  ✅ 발송 완료: {to_email}")
        return {"sent": True, "dry_run": False, "error": None}
    except Exception as e:
        print(f"  ❌ 발송 실패: {to_email} — {type(e).__name__}: {e}")
        return {"sent": False, "dry_run": False, "error": str(e)}


def report_url(ym: str) -> str:
    base = SITE_BASE_URL or "https://SITE-BASE-URL-미설정.example"
    return f"{base}/report/{ym}.html"


def unsubscribe_url(email: str) -> str:
    base = SITE_BASE_URL or "https://SITE-BASE-URL-미설정.example"
    return f"{base}/unsubscribe?email={email}&token={unsubscribe_token(email)}"


if __name__ == "__main__":
    print(f"SMTP 설정됨: {is_configured()}")
    html = render_issue_email("2026-07", 10894, 0.63, "마포구",
                               report_url("2026-07"), unsubscribe_url("test@example.com"))
    send_email("test@example.com", "[테스트] 2026-07 공유숙박 마켓리포트", html)
