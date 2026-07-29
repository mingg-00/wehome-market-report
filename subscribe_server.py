#!/usr/bin/env python3
"""
구독 폼 수신 + 즉시발송 서버.

  python subscribe_server.py     # http://localhost:5055

8/4 에는 subscribers.json 파일 하나로 저장만 했다. 오늘(8/5) 세 가지를 채운다:
  1. 저장을 SQLite로(subscribers.py) — 동시 요청에 안전, 탈퇴/재구독 상태 관리.
  2. 구독 즉시 요약 리포트 이메일 발송(email_sender.py) — SMTP 자격증명 없으면
     dry-run으로 빠지고 그 사실을 응답에 그대로 표시한다(조용히 성공한 척 안 함).
  3. /unsubscribe — 이메일 본문의 수신거부 링크가 실제로 동작하게.

여전히 안 하는 것: 이메일 인증(더블 옵트인). 체크박스 동의 시점에 즉시 저장하는
현재 방식으로도 개인정보 최소수집 원칙은 지켜진다(이메일 하나만, 목적 명시,
탈퇴 가능) — 인증 메일 왕복까지 넣는 건 이번 체크포인트("구독→수신 실제
테스트") 대비 과한 추가라 안 했다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Flask, jsonify, request

import email_sender
import subscribers as sub

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LATEST_ISSUE = Path(__file__).parent / "site" / "latest_issue.json"

app = Flask(__name__)


def _latest_issue() -> dict | None:
    if not LATEST_ISSUE.exists():
        return None
    return json.loads(LATEST_ISSUE.read_text(encoding="utf-8"))


@app.after_request
def _cors(resp):
    # 랜딩을 file:// 로 열어 테스트하는 동안 origin이 "null"이라 와일드카드 허용.
    # 실배포 시엔 실제 도메인으로 좁혀야 한다(8/6 항목).
    resp.headers["Access-Control-Allow-Origin"] = "*"
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

    if not EMAIL_RE.match(email):
        return jsonify(error="이메일 형식이 올바르지 않습니다."), 400
    if not consent:
        return jsonify(error="수신 동의가 필요합니다."), 400

    status = sub.add(email)
    if status == "already_active":
        return jsonify(ok=True, status=status, note="이미 구독 중인 이메일입니다."), 200

    mail_result = {"sent": False, "dry_run": True, "error": "최신 리포트 정보 없음(build_site.py 먼저 실행)"}
    issue = _latest_issue()
    if issue:
        html = email_sender.render_issue_email(
            issue["ym"], issue["active"], issue["seoul_share"], issue["top_district"],
            email_sender.report_url(issue["ym"]), email_sender.unsubscribe_url(email))
        mail_result = email_sender.send_email(
            email, f"[구독 완료] {issue['ym']} 공유숙박 마켓리포트", html)

    return jsonify(ok=True, status=status, mail=mail_result), 201


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    email = (request.args.get("email") or "").strip().lower()
    token = request.args.get("token") or ""
    if not email_sender.verify_unsubscribe_token(email, token):
        return "유효하지 않은 수신거부 링크입니다.", 400
    ok = sub.unsubscribe(email)
    return ("수신거부가 완료되었습니다. 그동안 구독해주셔서 감사합니다." if ok
            else "이미 수신거부되었거나 존재하지 않는 이메일입니다.")


@app.route("/subscribers", methods=["GET"])
def list_subscribers():
    """로컬 확인용 집계치만 반환(개별 이메일 노출 안 함). 실배포 전 인증 추가 검토."""
    return jsonify(sub.stats())


if __name__ == "__main__":
    print(f"DB: {sub.DB_PATH}")
    print(f"SMTP 설정됨: {email_sender.is_configured()}")
    app.run(port=5055, debug=False)
