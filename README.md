# ATIS Board

인천(RKSI)을 비롯한 국내·해외 주요 공항의 ATIS를 문자로 모아 아이폰·아이패드에서 보는 개인용 웹앱입니다.

웹앱: https://shpdragonfly-bit.github.io/atis-board/

> **비공식 정보입니다. 운항에 사용하지 마세요.** 실제 운항에는 VHF ATIS, ACARS D-ATIS 등 공식 수단으로 확인하세요.

## 동작 방식

1. GitHub Actions가 약 10분마다 `scripts/collect.py`를 실행합니다 (GitHub 사정으로 10~20분 늦게 도는 경우가 흔합니다).
2. 결과를 `data/atis.json`과 공항별 기록 `data/history/{ICAO}.json`에 저장하고, 내용이 바뀌었을 때만 커밋합니다.
3. GitHub Pages(`docs/`)의 웹앱이 그 JSON을 읽어 보여줍니다.

## 자료 출처

| 소스 | 공항 | 비고 |
|---|---|---|
| [atis.guru](https://atis.guru/) | RKSI, RKSS, RKPC, RKPK, RJAA, RJTT, EGLL | ACARS로 수집된 D-ATIS. 항공기가 요청했을 때만 갱신되어 수 시간~수일 지난 정보일 수 있음 |
| [홍콩 민항처 ATIS](https://atis.cad.gov.hk/ATIS/ATISweb/atis.php) | VHHH | 공식 공개 페이지 |
| [FAA D-ATIS (datis.clowd.io)](https://datis.clowd.io/) | KLAX, KSFO, KJFK | 공식 FAA D-ATIS |

- 발행 시각은 사이트의 수신 시각이 아니라 ATIS 본문에 적힌 `HHMMZ`로 계산합니다.
- 화면의 배지: 35분 이내 "최신", 90분 이내 "갱신 확인 필요", 그 이상 "오래된 참고 정보".
- 각 소스는 개인 용도로 낮은 빈도로만 읽습니다.

## 공항 추가·삭제

`airports.json`에 한 줄을 추가하면 됩니다. `source`는 `atisguru`, `hkcad`, `faa` 중 하나입니다.

```json
{"icao": "RJBB", "name": "간사이", "region": "JP", "source": "atisguru"}
```

## 수동 실행

Actions 탭 → Collect ATIS → Run workflow.
