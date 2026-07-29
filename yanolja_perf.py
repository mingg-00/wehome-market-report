#!/usr/bin/env python3
"""
야놀자리서치 숙박업 실적 지표(ADR·OCC·RevPAR) 클라이언트.

datalab/trend/dashboard 페이지가 화면에 그리는 걸 프론트에서 그대로 호출하는
공개 JSON 엔드포인트다(인증·API키 불필요) — 로그인 게이트나 스크래핑이 아니라
그 페이지 자체가 쓰는 API를 우리도 똑같이 호출하는 것뿐이다.

이 지표가 값어치 있는 이유: 야놀자리서치는 이 수치를 NOL(야놀자 플랫폼) +
AirDNA + 산하정보기술 데이터를 블렌딩해서 만든다고 공개 방법론에 밝히고 있다.
AirDNA 원본은 ToS상 크롤링·전용이 금지돼 못 끌어오지만(build_site.py의
estimate.html 참고), 이미 2차 가공·공개 발행된 형태로는 그 데이터의 일부가
여기 들어 있다.

지역 단위는 시군구가 아니라 광역 10개 권역(전국·서울·경기·강원·충청·전라·경북·
경남·부산·제주)뿐이다 — localdata.py의 시군구 단위보다 훨씬 거칠다. 대구·대전·
인천·울산·세종은 대응 권역이 없어 SIDO_TO_REGION 에서 뺐다(억지로 인접 권역에
끼워맞추지 않는다).

발행 지연이 있어(대시보드의 최신 선택월이 항상 데이터를 갖고 있진 않다) 이번
달부터 거슬러 올라가며 첫 유효 월을 찾는다.
"""

from __future__ import annotations

from datetime import date, timedelta

import requests

BASE = "https://www.yanolja-research.com/datalab"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

REGIONS = ["전국", "서울", "경기", "강원", "충청", "전라", "경북", "경남", "부산", "제주"]
TYPE_SHARED = "공유숙박"  # getLodgingPerformanceLangContents 가 주는 TYPE 옵션 중 우리 리포트와 맞는 것

# localdata.py 의 시도명(예: "서울특별시") -> 이 API의 광역 권역명.
SIDO_TO_REGION = {
    "서울특별시": "서울", "경기도": "경기", "강원특별자치도": "강원",
    "충청남도": "충청", "충청북도": "충청",
    "전북특별자치도": "전라", "전남광주통합특별시": "전라",
    "경상북도": "경북", "경상남도": "경남",
    "부산광역시": "부산", "제주특별자치도": "제주",
}


def fetch_adr_occ_revpar(region: str, year_month: str, type_: str = TYPE_SHARED) -> dict | None:
    """avg_ADR/avg_OCC/avg_REVPAR를 float로 반환. 해당 월 데이터가 아직 없으면 None."""
    r = requests.get(f"{BASE}/getAdrOccRevpar", headers=UA, timeout=15,
                      params={"year": year_month, "region": region, "type": type_, "lang": "kr"})
    r.raise_for_status()
    info = r.json()["data"]["info"]
    if info["avg_ADR"] is None:
        return None
    return {"adr": float(info["avg_ADR"]), "occ": float(info["avg_OCC"]), "revpar": float(info["avg_REVPAR"])}


def latest_month(type_: str = TYPE_SHARED, lookback: int = 6) -> str | None:
    """이번 달부터 최대 lookback개월 거슬러 올라가며 데이터가 있는 첫 달(전국 기준)을 찾는다."""
    d = date.today().replace(day=1)
    for _ in range(lookback):
        if fetch_adr_occ_revpar("전국", d.strftime("%Y%m"), type_):
            return d.strftime("%Y%m")
        d = (d - timedelta(days=1)).replace(day=1)
    return None


def collect(type_: str = TYPE_SHARED) -> dict[str, dict]:
    """전국 포함 10개 권역의 최신월 ADR/OCC/RevPAR. 데이터 없는 권역은 결과에서 빠진다."""
    ym = latest_month(type_)
    if ym is None:
        return {}
    out = {}
    for region in REGIONS:
        stats = fetch_adr_occ_revpar(region, ym, type_)
        if stats:
            stats["ym"] = ym
            out[region] = stats
    return out


if __name__ == "__main__":
    data = collect()
    print(f"{len(data)}/{len(REGIONS)}개 권역 · {TYPE_SHARED}")
    for region, s in data.items():
        print(f"  {region:4} {s['ym']}  ADR {s['adr']:>9,.0f}원  OCC {s['occ']:>5.1f}%  "
              f"RevPAR {s['revpar']:>9,.0f}원")
