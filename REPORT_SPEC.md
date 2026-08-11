# 리포트 목차 + 지표별 데이터 출처 매핑

7/30 계획 체크포인트("리포트 목차 + 지표별 데이터 출처 매핑 완료")의 산출물.
코드(`sources.py`, `localdata.py`, `build_site.py`)에 흩어진 걸 한 장으로 모은 것 —
실제 소스오브트루스는 코드고, 이 문서는 코드가 바뀌면 같이 갱신해야 하는 요약본이다.

## 사이트 목차

```
index.html            대시보드 (매 실행 시 최신 스냅샷)
├─ KPI 4종             영업중·폐업률·서울비중·상위3구집중도
├─ 등록 추이            24개월 월별 신규등록
├─ 서울 자치구 순위      TOP 15
├─ 포화 신호            밀도 상위 구의 최근/직전 6개월 신규등록 증감률
├─ 카테고리 비교         5종 로그스케일
├─ 이번 달 계류 법안      국회 입법예고 추적
├─ 규제·정책 동향        문체부·정책브리핑 자동 수집
└─ 데이터 신뢰도         교차검증 노트

reports.html           월간 리포트 아카이브 (호별 카드)
report/{ym}.html       호별 상세 — KPI 3종 + 구별TOP10표 + 카테고리표 + 인바운드차트 + 전월대비
```

## 지표 → 데이터 출처 매핑

| 지표 | 계산 방식 | 원천 | 갱신주기 | 코드 |
|---|---|---|---|---|
| 영업중 / 폐업 / 휴업 | 관리번호+주소 dedup 후 영업상태명 분류 | `file.localdata.go.kr` 원본 CSV, 직접 다운로드 | **매일 자동갱신, D-2 기준** (data.go.kr 카탈로그 확인) | `localdata.py::aggregate` |
| 서울/부산 구별 밀도 | 도로명주소 파싱(시도/시군구) 후 active만 카운트 | 위와 동일 | 위와 동일 | `localdata.py::district_rank` |
| 월별 신규등록(24개월) | 인허가일자 기준 월 그룹핑, 현재 상태 무관 | 위와 동일 | 위와 동일 | `localdata.py::recent_months` |
| **포화 신호** (차별 지표) | 구별 최근 6개월 신규등록 vs 직전 6개월, 증감률 | 위와 동일(구별+월별 교차 집계) | 위와 동일 | `localdata.py::saturation_signal` |
| 5종 카테고리 비교 | 카테고리별 active 합계 | 위와 동일, 5개 슬러그 병렬 수집 | 위와 동일 | `localdata.py::CATEGORIES` |
| 폐업률 | closed / total | 위와 동일 | 위와 동일 | `CategoryStats.closure_rate` |
| MoM 증감 | 이번 스냅샷 vs 직전 스냅샷 | 자체 축적(`history/*.json`) | 우리 실행 주기(매일 05:00, launchd) | `build_site.py::mom_delta` |
| 계류 법안(관광진흥법) | billName 부분일치 검색 | 국회 입법예고(`pal.assembly.go.kr`) | 매 실행 시 실시간 조회 | `regulation.py::fetch_assembly_bills` |
| 규제·정책 뉴스 | 키워드 필터(`CORE_KEYWORDS`) | 문체부 보도자료·입법예고, 정책브리핑 | 매 실행 시 실시간 조회 | `regulation.py::collect` |
| 인바운드 관광객 | K-STAY 큐레이션 그대로 사용 | KTO 데이터랩 + 법무부 통계연보 (k-stay가 손으로 큐레이션 — 자동 크롤링 대상 아님) | k-stay 갱신 주기에 종속 | `kstay.py::fetch_inbound` |
| 교차검증 노트 | localdata active vs 세이프스테이 operating, 격차율 | 한국관광공사 세이프스테이(`safestay.visitkorea.or.kr`) | 매 실행 시 실시간 조회 | `build_site.py::gather` |

## 경쟁사 대비 지표 포지셔닝

| | k-stay.ai | AirDNA | 이 리포트 |
|---|---|---|---|
| 등록 통계 원천 | 동일(file.localdata.go.kr), API 경유 | 자체 스크래핑(Airbnb/Vrbo 캘린더) | **동일 원천, 직접 수집** — API 의존 없음 |
| 구 단위 밀도 | 서울·부산만 | 없음(시/카운티급) | 서울·부산 + **전국 확장 가능**(원본이 전국이라) |
| 성장/포화 지표 | 없음(밀도 스냅샷만) | Market Score(계절성·투자성 등 5축, 유료) | **포화 신호**(구별 6개월 증감률, 무료·공개) |
| 판정 로직 공개 | 비공개 | 비공개 | **공개**(active/closed/pause 분류 기준이 코드에 그대로) |
| 규제 추적 | 없음 | 없음 | **국회 계류법안 자동 추적** |

## 미착수

- **예상 수익 지표(원/박 실거래 기반)** — 별도 문서 [`REVENUE_METRIC_SPEC.md`](REVENUE_METRIC_SPEC.md) 참고. ADR·점유율 실거래 데이터(위홈 예약 시스템)가 없어 막힘 — `WEHOME_INTERN_API_KEY`는 발급됐으나 호출할 엔드포인트 스펙이 없음.

완료된 것(이월 아님):
- 지역 검색 → 즉시 맞춤 리포트 — `estimate.html`(SPA 조회 도구), `area/*.html`(정적 68개 시군구 페이지)
- 예상 수익의 정성적 절충안 — `build_site.py::compute_regions`의 `verdict()`(밀도×증감 조합 → "포화 주의"/"성장 기회" 등 라벨, 원 단위 금액 없음), `estimate.html`·`area/*.html`의 `verdictCard`로 노출
