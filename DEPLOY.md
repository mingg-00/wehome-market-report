# subscribe_server.py 상시 배포

~~지금은 로컬 맥북 + ngrok~~ → **8/6부로 Railway에 배포 완료**
(`wehome-market-report-production.up.railway.app`). 이 문서는 그 절차와, 다시
배포하거나(Render 등 다른 호스트로) 재현할 때 필요한 걸 정리한 것이다.

빌드 산출물(`site/`)은 Vercel에 있다 — 여기서 배포하는 건 `subscribe_server.py`
(구독 폼 수신 + 이메일 인증/발송) **하나뿐**이다.

## 이메일 발송: Resend API (Gmail SMTP 아님)

원래 Gmail SMTP로 짰다가, 실제 배포 후 발송이 전부 `[Errno 101] Network is
unreachable`로 죽는 걸 발견했다 — **Railway가 아웃바운드 SMTP(25/465/587번 포트)를
막아놔서**다(Railway 커뮤니티도 이 경우 HTTPS API 기반 발송 서비스로 바꾸라고
권장). 그래서 `email_sender.py`는 이제 Resend(https://resend.com)의 HTTPS API로
보낸다 — 일반 POST 요청이라 막힐 이유가 없고, 로컬에서도 Gmail 앱 비밀번호·2단계
인증 없이 그대로 된다. `RESEND_API_KEY` 발급은 resend.com 가입 후 API Keys 메뉴.

도메인(`wehome.me`)을 Resend에서 인증하기 전까지는 `EMAIL_FROM=onboarding@resend.dev`
공유 테스트 도메인만 쓸 수 있고, **Resend 가입 계정 본인 메일로만 실제 도착한다** —
임의 구독자에게 보내려면 도메인 인증(DNS 레코드 추가)이 필요하다.

## 전제

- 이 저장소가 GitHub에 있어야 한다(이미 `mingg-00/wehome-market-report`로 완료).
- Railway 또는 Render 계정.

## 공통으로 필요한 것

- **Build**: `pip install -r requirements.txt`
- **Start**: `gunicorn subscribe_server:app --bind 0.0.0.0:$PORT` (Procfile에 있음)
- **환경변수**:
  ```
  RESEND_API_KEY=<resend.com에서 발급>
  EMAIL_FROM=onboarding@resend.dev   # 도메인 인증 전까지
  SMTP_FROM_NAME=위홈 공유숙박 마켓리포트
  SITE_BASE_URL=https://wehome-market-report.vercel.app
  UNSUB_SECRET=<python3 -c "import secrets; print(secrets.token_hex(32))">
  CORS_ALLOWED_ORIGIN=https://wehome-market-report.vercel.app
  ```
- **영구 저장소(중요)**: `subscribers.db`는 SQLite 파일이라 볼륨 없이 배포하면
  재배포·재시작마다 초기화된다. **persistent volume/disk 필수.**
  - Railway는 볼륨을 서비스에 붙이면 `RAILWAY_VOLUME_MOUNT_PATH`를 자동으로
    심어준다 — `subscribers.py`가 이미 그걸 자동 감지하므로 `SUBSCRIBERS_DB_PATH`를
    따로 안 넣어도 된다.
  - Render 등 이 env var가 없는 호스트는 `SUBSCRIBERS_DB_PATH=/data/subscribers.db`
    처럼 수동으로 넣어야 한다.

## Railway (완료된 절차 — 재현 시 참고)

1. railway.app 가입(GitHub OAuth) → New Project → Deploy from GitHub repo.
2. **주의**: Variables 탭에서 값을 입력·붙여넣기만 하면 바로 반영되는 게 아니다 —
   화면 하단에 뜨는 **"Apply N changes" → "Deploy" 버튼을 눌러야** 실제로 적용된다.
3. 볼륨은 서비스 Settings 안에 없다 — 프로젝트 캔버스에서 **Cmd+K → "volume"** 검색
   (또는 캔버스 우클릭 → New → Volume) → 서비스에 연결 → 마운트 경로 지정.
4. Settings → Networking → **Generate Domain**을 눌러야 외부에서 접근 가능한
   `https://xxxx.up.railway.app` 주소가 생긴다(기본은 "Unexposed service").
5. 배포 URL을 `SUBSCRIBE_ENDPOINT`에 `/subscribe`를 붙여 `.env`(build_site.py가
   읽는 쪽)에 반영 → `build_site.py` 재실행 → Vercel 재배포.

## 통계 다이제스트 — 구독자가 주기 선택 (8/7 추가, 8/11 주기 선택 추가)

로그인 계정 시스템 대신 기존 구독 체크박스를 재사용한다 — "시장 통계·리포트"
체크박스(`subscribers.CATEGORIES`의 `"market"`)를 켠 사람에게 최신호 요약을 보낸다.
발송 주기는 가입 시 `subscribers.FREQUENCIES`(매주/2주마다/매달) 중 하나를 직접
고른다. `subscribers.db`가 Railway에만 있어서 발송도 거기서 일어나야 하고, 로컬
launchd는 트리거만 한다:

1. `digest.sh`가 `POST {SUBSCRIBE_ENDPOINT 도메인}/admin/send-digest?token=$UNSUB_SECRET`
   를 호출 — Railway의 `/admin/send-digest`가 실제 발송을 수행. 사람마다 주기가
   달라 "찬" 날짜도 제각각이라, 실제로 누구에게 보낼지는 `sub.due_for_digest()`가
   매번 판단한다(각자의 `last_sent_at` + 고른 주기 기준).
2. `~/Library/LaunchAgents/me.wehome.marketreport.digest.plist`가 매일 08:00에
   위 스크립트를 실행(publish.sh/월간 배포와 같은 launchd 패턴, 트리거만 매일 돌고
   실제 발송 여부는 서버가 사람별로 가른다).
3. 등록 원본 데이터는 월 단위로만 갱신되므로, 그 사이엔 최신 스냅샷을 그대로 다시
   보내는 게 정상 동작이다(뉴스레터에 흔한 패턴).

수동 트리거 확인:
```bash
bash digest.sh
```

## Render (대안, 미사용)

1. render.com 가입 → New → Web Service → 저장소 연결.
2. Build Command: `pip install -r requirements.txt` / Start Command:
   `gunicorn subscribe_server:app --bind 0.0.0.0:$PORT`.
3. Environment 탭에 위 환경변수 입력(+ `SUBSCRIBERS_DB_PATH` 수동 지정).
4. Disks 탭에서 persistent disk 추가.
5. 배포 URL을 `SUBSCRIBE_ENDPOINT`에 반영 → `build_site.py` 재실행.

## /subscribers (집계 조회)

인터넷에 열려 있는 엔드포인트라 토큰이 필요하다. 새 env var를 늘리지 않고
`UNSUB_SECRET`을 그대로 재사용한다:

```bash
curl "https://<배포주소>/subscribers?token=$UNSUB_SECRET"
```

`UNSUB_SECRET`이 비어 있으면 무조건 403 — 미설정이 곧 무방비가 되지 않게.

## 배포 후 확인

```bash
curl -X POST https://<배포주소>/subscribe -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","consent":true}'
```
`pending_confirm` 상태 확인. `mail.dry_run`이 `false`면 Resend로 실제 발송 시도된
것 — `mail.error`가 있으면 도메인 인증 전이라 본인 메일이 아닌 주소로 보내려 한
경우일 수 있다(위 "이메일 발송" 절 참고).
