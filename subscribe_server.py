#!/usr/bin/env python3
"""
구독 폼 수신 + 즉시발송 서버.

  python subscribe_server.py     # http://localhost:5055

8/4 에는 subscribers.json 파일 하나로 저장만 했다. 8/5에 세 가지를 채웠다:
  1. 저장을 SQLite로(subscribers.py) — 동시 요청에 안전, 탈퇴/재구독 상태 관리.
  2. 구독 즉시 요약 리포트 이메일 발송(email_sender.py) — SMTP 자격증명 없으면
     dry-run으로 빠지고 그 사실을 응답에 그대로 표시한다(조용히 성공한 척 안 함).
  3. /unsubscribe — 이메일 본문의 수신거부 링크가 실제로 동작하게.

더블 옵트인(8/5 추가): /subscribe는 이제 최신호를 바로 안 보낸다 — 인증 메일만
보낸다. 실제 최신호 요약은 /confirm(이메일 인증 링크 클릭)이 성공했을 때 나간다.
검증 안 된 이메일 주소에 곧바로 리포트를 뿌리지 않는다는 게 핵심 — subscribers.py
의 confirmed_at이 없으면 active_subscribers()에도 안 잡힌다.

상시 배포용(8/5 추가): PORT는 Railway/Render가 실행 시점에 주입하는 env var를
그대로 쓴다(못 미리 하드코딩 — 플랫폼이 매번 다른 포트를 배정한다). CORS는
CORS_ALLOWED_ORIGIN이 있을 때만 좁힌다 — SITE_BASE_URL로 자동 대체하지 않는다,
그러면 로컬에서 랜딩을 file://로 열어 테스트할 때(origin이 "null") 곧바로
CORS가 막혀버린다(SITE_BASE_URL은 이미 로컬에도 채워져 있는 값이라). 실제
배포 호스트(Railway/Render)의 환경변수에만 CORS_ALLOWED_ORIGIN을 명시적으로
설정한다 — DEPLOY.md에 절차 정리해뒀다.
"""

from __future__ import annotations

import hmac
import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

import email_sender
import subscribers as sub

load_dotenv()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LATEST_ISSUE = Path(__file__).parent / "site" / "latest_issue.json"
ALLOWED_ORIGIN = os.getenv("CORS_ALLOWED_ORIGIN", "").rstrip("/") or "*"
UNSUB_SECRET = os.getenv("UNSUB_SECRET", "").strip()

app = Flask(__name__)


def _latest_issue() -> dict | None:
    """빌드 산출물(site/)은 Vercel에만 있고 이 서버(Railway)엔 없다 — .gitignore에
    걸려 저장소에 안 들어가기 때문이다. 로컬 파일만 보면 프로덕션에선 항상 None이라
    /confirm이 인증 완료 메일을 조용히 안 보낸다(8/6 실측 발견 — 인증은 성공하는데
    최신호 요약만 안 나가서 눈에 안 띄었다). 배포된 사이트에서 가져오고, 실패하거나
    SITE_BASE_URL이 없는 로컬 개발에선 파일로 폴백한다. 여기서 못 가져와도 인증
    자체는 이미 끝난 뒤라 예외를 밖으로 던지지 않는다."""
    base = email_sender.SITE_BASE_URL
    if base:
        try:
            r = requests.get(f"{base}/latest_issue.json", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  ⚠️  latest_issue.json 조회 실패({base}) — {type(e).__name__}: {e}")
    if LATEST_ISSUE.exists():
        return json.loads(LATEST_ISSUE.read_text(encoding="utf-8"))
    return None


def _result_page(message: str, ok: bool = True) -> str:
    """/confirm·/unsubscribe가 지금까지 스타일 없는 순수 문자열만 돌려줘서, 이메일
    링크를 눌렀을 때 브랜드 없는 흰 화면만 보였다(실행계획 08-05 항목에 남겨둔 갭).
    사이트 본체(build_site.py)의 CSS 전체를 끌어오는 대신, 팔레트 변수만 그대로
    복제해 이 서버 혼자서도 완결된 페이지를 낼 수 있게 한다."""
    home = email_sender.SITE_BASE_URL or "#"
    icon = "✓" if ok else "✕"
    color = "var(--mint)" if ok else "#e5484d"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>공유숙박 마켓리포트</title><style>
:root{{--navy:#1B2A4A;--mint:#00C2A8;--bg:#fff;--fg:#161b22;--muted:#5b6472;--line:#e6e9ee;--card:#f7f9fb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0c1017;--fg:#e8edf4;--muted:#93a1b5;--line:#232c3a;--card:#141b26}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,BlinkMacSystemFont,"Pretendard",sans-serif;display:flex;
min-height:100vh;align-items:center;justify-content:center;padding:24px}}
.card{{max-width:420px;text-align:center;background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:40px 32px}}
.icon{{width:56px;height:56px;border-radius:50%;background:{color};color:#fff;
font-size:28px;line-height:56px;margin:0 auto 20px}}
p{{font-size:15px;line-height:1.6;color:var(--fg)}}
a{{display:inline-block;margin-top:20px;color:var(--mint);text-decoration:none;font-weight:600}}
</style></head><body><div class="card"><div class="icon">{icon}</div>
<p>{message}</p><a href="{home}">← 공유숙박 마켓리포트로 돌아가기</a></div></body></html>"""


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/subscribe", methods=["POST", "OPTIONS"])
def subscribe():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    consent = bool(data.get("consent"))
    categories = data.get("categories") or []
    frequency = data.get("frequency")

    if not EMAIL_RE.match(email):
        return jsonify(error="이메일 형식이 올바르지 않습니다."), 400
    if not consent:
        return jsonify(error="수신 동의가 필요합니다."), 400
    if not isinstance(categories, list):
        return jsonify(error="categories는 목록 형식이어야 합니다."), 400

    status = sub.add(email, categories=categories, frequency=frequency)
    if status == "already_active":
        return jsonify(ok=True, status=status, note="이미 구독 중인 이메일입니다."), 200

    # pending_confirm / resubscribe_pending_confirm — 인증 메일만 보낸다. 최신호
    # 요약은 검증된 주소에만 보내야 하니 /confirm이 성공했을 때로 미룬다.
    html = email_sender.render_confirm_email(email_sender.confirm_url(email))
    mail_result = email_sender.send_email(email, "[구독 인증] 공유숙박 마켓리포트", html)

    return jsonify(ok=True, status=status, mail=mail_result), 201


@app.route("/confirm", methods=["GET"])
def confirm():
    email = (request.args.get("email") or "").strip().lower()
    token = request.args.get("token") or ""
    if not email_sender.verify_confirm_token(email, token):
        return _result_page("유효하지 않은 인증 링크입니다.", ok=False), 400
    if not sub.confirm(email):
        return _result_page("이미 인증되었거나 존재하지 않는 이메일입니다.", ok=False)

    issue = _latest_issue()
    if issue:
        html = email_sender.render_issue_email(
            issue["ym"], issue["active"], issue["seoul_share"], issue["top_district"],
            email_sender.report_url(issue["ym"]), email_sender.unsubscribe_url(email))
        email_sender.send_email(email, f"[구독 완료] {issue['ym']} 공유숙박 마켓리포트", html)
    return _result_page("이메일 인증이 완료됐습니다.<br>매달 발행 즉시 리포트를 보내드립니다.")


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    email = (request.args.get("email") or "").strip().lower()
    token = request.args.get("token") or ""
    if not email_sender.verify_unsubscribe_token(email, token):
        return _result_page("유효하지 않은 수신거부 링크입니다.", ok=False), 400
    ok = sub.unsubscribe(email)
    return _result_page(
        "수신거부가 완료되었습니다.<br>그동안 구독해주셔서 감사합니다." if ok
        else "이미 수신거부되었거나 존재하지 않는 이메일입니다.", ok=ok)


def _require_admin_token() -> bool:
    token = request.args.get("token", "")
    return bool(UNSUB_SECRET) and hmac.compare_digest(token, UNSUB_SECRET)


@app.route("/subscribers", methods=["GET"])
def list_subscribers():
    """집계치만 반환(개별 이메일 노출 안 함). 8/6 실배포로 이게 인터넷에 그대로
    열렸다 — 구독자 수는 대외비라 토큰을 건다. 새 env var를 늘리는 대신 이미 모든
    환경에 채워져 있는 UNSUB_SECRET을 재사용한다. 비어 있으면 무조건 거부한다 —
    미설정을 '인증 없음'으로 흘려보내면 배포 환경에서 조용히 무방비가 된다."""
    if not _require_admin_token():
        return jsonify(error="unauthorized"), 403
    return jsonify(sub.stats())


@app.route("/admin/pending", methods=["GET"])
def admin_pending():
    """미인증 상태로 남은 이메일 목록 — 지우기 전에 실제 방문자 가입 시도인지
    테스트 흔적인지 구분하려고 8/6에 추가. 관리자 전용, 같은 토큰으로 보호."""
    if not _require_admin_token():
        return jsonify(error="unauthorized"), 403
    return jsonify(sub.pending())


@app.route("/admin/subscribers/<email>", methods=["DELETE"])
def admin_delete_subscriber(email):
    """행을 완전히 지운다 — 관리자가 테스트로 만든 미인증 행 정리용."""
    if not _require_admin_token():
        return jsonify(error="unauthorized"), 403
    return jsonify(deleted=sub.delete(email.strip().lower()))


@app.route("/admin/send-digest", methods=["POST"])
def admin_send_digest():
    """"market"(시장 통계·리포트) 구독자 중, 각자 가입 때 고른 주기(매주/2주마다/매달 —
    subscribers.FREQUENCIES)가 찬 사람에게만 최신호 요약을 보낸다. digest.sh가 launchd로
    매일 이 엔드포인트를 호출한다(publish.sh의 월간 빌드·배포와 같은 패턴, 자세한 건
    DEPLOY.md) — 사람마다 "찬" 날짜가 다르니 트리거 자체는 매주가 아니라 매일 돌아야
    하고, 실제로 보낼지는 sub.due_for_digest()가 매번 판단한다. 로그인 시스템을 새로
    만드는 대신 기존 이중 확인 구독을 그대로 쓰기로 한 결정 — subscribers.db가 이
    서버(Railway)에만 있어서, 발송도 여기서 일어나야 한다(로컬 launchd는 트리거만
    하고 실제 발송은 못 한다).

    등록 데이터가 매번 안 바뀌어도(원본이 월 단위 갱신) 최신 스냅샷을 다시 보내는 건
    의도한 동작이다 — "다이제스트"가 매번 새 숫자를 담보하진 않는다, 그건 뉴스레터에
    흔한 패턴이다. 발송에 성공한 사람만 sub.mark_sent()로 기록해야 다음 실행에서
    다시 대상으로 안 잡힌다(dry-run이나 실패는 기록 안 함 — 안 그러면 그 사람은
    영영 못 받는다)."""
    if not _require_admin_token():
        return jsonify(error="unauthorized"), 403

    issue = _latest_issue()
    if not issue:
        return jsonify(sent=0, attempted=0, error="아직 발행된 리포트가 없습니다."), 200

    emails = sub.due_for_digest(category="market")
    sent = []
    for email in emails:
        html = email_sender.render_issue_email(
            issue["ym"], issue["active"], issue["seoul_share"], issue["top_district"],
            email_sender.report_url(issue["ym"]), email_sender.unsubscribe_url(email))
        result = email_sender.send_email(email, f"[통계 요약] {issue['ym']} 공유숙박 마켓리포트", html)
        if result["sent"]:
            sent.append(email)
    sub.mark_sent(sent)
    return jsonify(sent=len(sent), attempted=len(emails), issue_ym=issue["ym"])


if __name__ == "__main__":
    print(f"DB: {sub.DB_PATH}")
    print(f"SMTP 설정됨: {email_sender.is_configured()}")
    # 로컬 개발은 5055 고정, Railway/Render 등 실배포 호스트는 PORT를 실행 시점에
    # 주입한다 — 이 스크립트 자체는 `python subscribe_server.py`로 로컬 확인용이고,
    # 실배포는 Procfile의 gunicorn이 이 PORT를 그대로 물려받아 쓴다.
    app.run(port=int(os.getenv("PORT", "5055")), debug=False)
