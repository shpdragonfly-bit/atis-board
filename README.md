# ATIS Board

인천(RKSI)을 비롯한 국내·해외 주요 공항의 ATIS를 문자로 모아 아이폰·아이패드에서 보는 개인용 웹앱입니다.

웹앱: https://shpdragonfly-bit.github.io/atis-board/

> **비공식 정보입니다. 운항에 사용하지 마세요.** 실제 운항에는 VHF ATIS, ACARS D-ATIS 등 공식 수단으로 확인하세요.

## 동작 방식

1. GitHub Actions가 약 10분마다 `scripts/collect.py`를 실행합니다 (GitHub 사정으로 10~20분 늦게 도는 경우가 흔합니다).
   공항마다 `tier`에 따라 읽는 주기가 다릅니다: 1 = 10분, 2 = 30분, 3 = 60분. 미국 공항(FAA)은 한 번의 호출로 모두 받습니다.
2. 결과를 **`data` 브랜치**의 `atis.json`과 공항별 기록 `history/{ICAO}.json`에 저장하고, 내용이 바뀌었을 때만 커밋합니다. (main에 커밋하면 Pages가 매번 다시 배포되므로 분리했습니다.)
3. GitHub Pages(`docs/`)의 웹앱이 그 JSON을 읽어 보여줍니다.

## 자료 출처

| 소스 | 공항 | 비고 |
|---|---|---|
| [atis.guru](https://atis.guru/) | 국내·일본·중국·동남아·유럽·대양주·캐나다 55곳 | ACARS로 수집된 D-ATIS. 항공기가 요청했을 때만 갱신되어 수 시간~수일 지난 정보일 수 있음 |
| [홍콩 민항처 ATIS](https://atis.cad.gov.hk/ATIS/ATISweb/atis.php) | VHHH | 공식 공개 페이지 |
| [FAA D-ATIS (datis.clowd.io)](https://datis.clowd.io/) | 미국 15곳 | 공식 FAA D-ATIS |
| [NOAA Aviation Weather Center](https://aviationweather.gov/data/api/) | 전 공항 METAR | 매 실행마다 25곳씩 묶어 조회 |

전체 목록은 `airports.json`에 있습니다.

- 발행 시각은 사이트의 수신 시각이 아니라 ATIS 본문에 적힌 `HHMMZ`로 계산합니다.
- 화면의 배지: 35분 이내 "최신", 90분 이내 "갱신 확인 필요", 그 이상 "오래된 참고 정보".
- 각 소스는 개인 용도로 낮은 빈도로만 읽습니다.

## 공항 추가·삭제

`airports.json`에 한 줄을 추가하면 됩니다. `source`는 `atisguru`, `hkcad`, `faa` 중 하나, `region`은 KR·JP·CN·SEA·AM·EU·OC 중 하나입니다. atis.guru 공항을 많이 늘릴 때는 `tier` 3을 쓰세요(개인이 운영하는 사이트라 부담을 줄이기 위해).

```json
{"icao": "RJBB", "iata": "KIX", "name": "간사이", "region": "JP", "source": "atisguru", "tier": 2}
```

## 웹앱 기능

- 원문 우선 표시, 해석은 "해석 보기"로 펼침
- 도착·출발이 따로 나오는 공항은 두 칸(없는 쪽은 빈 카드), 하나로 나오는 공항은 "통합 ATIS (도착·출발 공용)" 한 칸
- 90분 넘은 ATIS에는 경고와 전화 ATIS 버튼, 반대쪽 ATIS가 최신이면 그 기준 활주로를 "참고(추정)"로 표시
- 공항마다 최신 METAR 원문
- 즐겨찾기(☆) 공항이 위쪽 버튼으로 표시, "공항 ▾"에서 검색·지역별 전체 목록
- "오늘 비행": 출발 공항은 출발 ATIS, 목적지·교체는 도착 ATIS를 한 화면에

## 휴대폰 알림 (ntfy)

새 ATIS가 들어오면 휴대폰으로 알림을 받을 수 있습니다 (무료, 가입 없음).

1. 휴대폰에 **ntfy** 앱을 설치하고, 남이 추측할 수 없는 채널 이름(예: `atis-` 뒤에 무작위 글자)을 구독합니다.
2. 저장소 Settings → Secrets and variables → Actions → New repository secret: 이름 `NTFY_TOPIC`, 값은 그 채널 이름.
3. 알림 받을 공항과 종류는 `notify.json`에서 고칩니다. 예: `"RKSI": ["ARR", "DEP"]`.

90분 넘게 지난 ATIS는 늦게 들어와도 알리지 않습니다. 채널 이름을 아는 사람은 누구나 알림을 볼 수 있으니 공개하지 마세요.

## 오프라인

앱은 마지막으로 받은 자료를 기기에 저장해 두고, 인터넷이 없으면 그 자료를 보여줍니다(위쪽에 "오프라인" 표시). 위쪽의 "수집 n분 전"이 45분을 넘으면 빨간색으로 바뀌어 수집이 멈췄음을 알려줍니다.

## 캐나다

NAV CANADA는 공식 ATIS 문자를 웹(AeroView)에 공개하지만, 이용약관상 자료를 복사·재배포하거나 소프트웨어에 넣을 수 없어 수집하지 않습니다. 밴쿠버·토론토는 atis.guru 자료를 보여주고, 공식 페이지로 가는 버튼을 둡니다.

## 수동 실행

Actions 탭 → Collect ATIS → Run workflow.
