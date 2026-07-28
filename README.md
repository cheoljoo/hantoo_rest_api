# hantoo_rest_api

한국투자증권(한투) REST API를 Python으로 사용하기 위한 프로젝트입니다.

계좌의 보유 종목/매입가/평가손익 등을 조회하고, 보유종목과 관심종목에 대해
캔들차트(OHLCV)를 그릴 수 있는 데이터를 가져옵니다.

## 준비

의존성 관리는 [uv](https://docs.astral.sh/uv/)를 사용합니다.

```bash
uv sync
cp .env.example .env
```

`.env`에 [한투 Open API 포털](https://apiportal.koreainvestment.com)에서 발급받은
앱키/시크릿과 계좌번호를 채워 넣습니다.

- `KIS_ACCOUNT_NO`: 계좌번호 10자리 중 앞 8자리
- `KIS_ACCOUNT_PRODUCT_CD`: 계좌번호 뒤 2자리 (보통 `01`)
- `KIS_IS_VIRTUAL`: 모의투자 계좌면 `true`, 실전투자 계좌면 `false`

관심종목은 `watchlist.yaml`에 종목코드를 등록해서 관리합니다.

## 실행

```bash
uv run hantoo-rest-api
```

계좌 보유 종목/평가손익, 계좌 요약, 관심종목 목록, 그리고 보유종목+관심종목에 대한
최근 일봉 데이터를 출력합니다.

## 모듈 구성

- `config.py`: `.env` 기반 설정 로딩
- `auth.py`: 접근토큰(access token) 발급 및 파일 캐싱
- `account.py`: 계좌 잔고 조회 (`get_account_balance`) — 보유 종목별 매입가/평가금액/평가손익
- `watchlist.py`: `watchlist.yaml` 관심종목 로딩 (`load_watchlist`)
- `price.py`: 종목별 기간별 캔들(OHLCV) 조회 (`get_daily_candles`, `get_candles_for_codes`)

License: Apache License 2.0
