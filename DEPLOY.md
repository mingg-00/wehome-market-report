# subscribe_server.py 상시 배포

지금은 로컬 맥북 + ngrok — 맥북이 잠들거나 ngrok 터널이 새로 열리면(무료 플랜은 URL이
매번 바뀜) 구독 폼이 통째로 죽는다. 이 문서는 그 의존을 없애는 절차다.

빌드 산출물(`site/`)은 이미 Vercel에 있다 — 여기서 배포하는 건 `subscribe_server.py`
(구독 폼 수신 + 이메일 인증/발송) **하나뿐**이다.

## 전제

- 이 저장소가 GitHub(또는 GitLab)에 올라가 있어야 한다 — Railway/Render 둘 다 git
  저장소를 연결해 자동 배포하는 게 기본 흐름이다. 아직 원격(remote)이 없다면
  (`git remote -v`가 비어 있으면) 먼저 GitHub에 올려야 한다.
- Railway 또는 Render 계정 — 둘 다 무료 티어로 시작 가능.

## 어느 쪽이든 공통으로 필요한 것

- **Build**: `pip install -r requirements.txt`
- **Start**: `gunicorn subscribe_server:app --bind 0.0.0.0:$PORT` (Procfile에 이미 있음 —
  Railway는 Procfile을 자동 인식, Render는 대시보드에 Start Command로 직접 입력)
- **환경변수** (.env 내용을 그대로 옮기되 아래 한 줄만 추가):
  ```
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_NAME
  SITE_BASE_URL=https://wehome-market-report.vercel.app
  UNSUB_SECRET=<실제 배포 전 반드시 무작위 값으로>
  CORS_ALLOWED_ORIGIN=https://wehome-market-report.vercel.app   # 새로 추가 — 없으면 "*" 허용(로컬 개발 기본값) 그대로 나감
  ```
- **영구 저장소(중요)**: `subscribers.db`는 SQLite 파일이다. Railway/Render의 기본
  파일시스템은 재배포·재시작 시 초기화될 수 있다 — **persistent volume/disk**를
  반드시 붙여야 구독자 데이터가 안 날아간다(두 플랫폼 다 유료 플랜부터 지원하는 경우가
  많으니 가입 시 확인 필요). 볼륨을 마운트했으면 `subscribers.py`의 `DB_PATH`를 그
  마운트 경로로 바꿔야 한다(현재는 스크립트와 같은 디렉터리 고정).

## Railway

1. railway.app 가입 → New Project → Deploy from GitHub repo → 이 저장소 선택.
2. Variables 탭에 위 환경변수 입력.
3. Volume 추가(Settings → Volumes) → 마운트 경로 지정(예: `/data`) →
   `subscribers.py`의 `DB_PATH = Path(__file__).parent / "subscribers.db"`를
   `DB_PATH = Path("/data/subscribers.db")`로 바꾸는 커밋 필요.
4. 배포되면 나오는 `https://<프로젝트>.up.railway.app`을 `SUBSCRIBE_ENDPOINT`에
   `/subscribe`를 붙여 `.env`(build_site.py가 읽는 쪽)에 반영 → `build_site.py` 재실행.

## Render

1. render.com 가입 → New → Web Service → 이 저장소 연결.
2. Build Command: `pip install -r requirements.txt` / Start Command:
   `gunicorn subscribe_server:app --bind 0.0.0.0:$PORT`.
3. Environment 탭에 위 환경변수 입력.
4. Disks 탭에서 persistent disk 추가(마운트 경로 예: `/data`) → Railway와 같은 이유로
   `DB_PATH`를 그 경로로 바꾸는 커밋 필요.
5. 배포 URL을 `SUBSCRIBE_ENDPOINT`에 반영 → `build_site.py` 재실행.

## 배포 후 확인

```bash
curl -X POST https://<배포주소>/subscribe -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","consent":true}'
```
`pending_confirm` 상태와 함께 인증 메일이 실제로 오는지 확인(SMTP 자격증명이
채워져 있어야 dry-run이 아니라 진짜 발송된다).
