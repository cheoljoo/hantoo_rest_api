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

## 웹 대시보드

`web/app.py`(Streamlit)에서 계좌/시장 데이터를 한 화면에 모아 보여줍니다.

```bash
cd web
uv run streamlit run app.py
```

비밀번호는 `web/.streamlit/secrets.toml`(git에는 커밋되지 않음, 최초 실행 시
기본값으로 자동 생성)에서 관리합니다.

## 모듈 구성

- `config.py`: `.env` 기반 설정 로딩 (`KisConfig`, `load_config`)
- `auth.py`: 접근토큰(access token) 발급 및 파일 캐싱 (`get_access_token`)
- `account.py`: 계좌 잔고 조회 (`get_account_balance`) — 보유 종목별 매입가/평가금액/평가손익
- `account_activity.py`: 매매 체결내역/예수금 조회 및 매매빈도·분산도 진단
  (`get_recent_executions`, `get_pension_deposit`, `investment_style_signal`)
- `manual_transactions.py`: KIS API가 체결내역을 제공하지 않는 계좌를 위한
  수동 매매 기록 관리 (`transactions.yaml`)
- `watchlist.py`: `watchlist.yaml` 관심종목 로딩 (`load_watchlist`)
- `price.py`: 종목별 기간별 캔들(OHLCV) 조회 (`get_daily_candles`, `get_candles_for_codes`)
- `market.py`: 코스피/코스닥 지수, 외국인/기관 매매동향, 미국 3대 지수 쏠림 신호
- `global_indices.py`: 주변국 8개국 지수 조회 (yfinance 기반 보조 데이터 소스)
- `macro_checklist.py`: 금리/글로벌쏠림/부실IPO 삼박자 약세장 체크리스트

## 구현하면서 겪은 문제들 (트러블슈팅)

- **계좌 유형에 따라 체결내역 API 지원 범위가 다름**: 실제 계좌(퇴직연금 DC형,
  계좌상품코드 "55")로 테스트해보니, 일반 계좌용 체결내역 API
  (`inquire-daily-ccld`)는 `APBK1744`("퇴직연금계좌는 해당 서비스가 불가합니다")
  오류를 낸다. 퇴직연금 전용 API(`pension/inquire-daily-ccld`)로 바꿔도
  `CCLD_NCCS_DVSN`을 체결/미체결/전체 어느 값으로 바꿔도 항상 빈 배열만 온다.
  실현손익 조회, 체결기준잔고 조회까지 추가로 테스트했지만 전부 과거 매매
  이력을 주지 않았다 — **DC형 퇴직연금 계좌는 KIS Open API로 과거 매매 이력을
  전혀 복원할 수 없다**는 결론을 내렸다 (자세한 내용은
  `account_activity.py` 모듈 docstring 참고). 이 때문에
  "매매 0건"을 그대로 "장기 보유"로 표시하면 실제로는 매매가 있었는데도
  잘못된 진단을 내리게 되어, `data_reliable` 플래그로 이 둘을 구분했다.
- **KIS 문서와 실제 동작이 다른 경우가 있음**: `pension/inquire-deposit`은
  공식 문서에 "55번 계좌(DC가입자계좌)는 이용 불가"라고 적혀 있지만, 실제로는
  정상 응답을 반환했다. 반대로 다른 퇴직연금 API들은 문서에 경고가 없어도
  실제로는 빈 값만 왔다. **계좌 유형별 API 가용 여부는 문서만으로 판단하지
  말고 실측(직접 호출)으로 확인해야 한다.**
- **해외지수 API의 "조용한 실패"**: `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice`
  (해외지수 일별시세)는 다우30/나스닥100/S&P500 구성종목이 아닌 지수 코드
  (니케이/항셍/유럽/인도 등)를 넣어도 `rt_cd="0"`(성공)을 반환하면서 필드가
  전부 0인 빈 값을 준다. 에러가 아니라서 여러 종목코드로 직접 호출해보기
  전까지는 문서만으로 알아채기 어려웠다. 그래서 미국 외 국가 지수는 KIS API
  대신 yfinance(`global_indices.py`)로 조회하도록 우회했다.
- **KIS 서버의 간헐적 5xx / 초당거래건수초과**: 실제 운영 중 캔들 조회, 지수
  조회, 예수금 조회 등 여러 API에서 간헐적으로 500 Internal Server Error나
  "초당 거래건수를 초과하였습니다" 오류가 발생하는 것을 확인했다. 모든 조회
  함수에 재시도(최대 2~3회, 선형 백오프) 로직을 추가했고, 웹 대시보드에서는
  섹션별로 `try/except`를 둬 한 API 실패가 전체 페이지를 죽이지 않도록 했다.
- **문자열 매칭으로 신호를 판단할 때의 함정**: 초기 구현에서
  `"쏠림" in concentration_signal(...)`처럼 부분 문자열로 신호를 검사했더니,
  "고른 흐름 (지수 간 **쏠림** 없음)"이라는 정상 상태 문구에도 "쏠림"이
  포함되어 있어 오탐(false positive)이 발생했다. 신호 문자열은 반드시
  "쏠림 심화"처럼 구체적인 트리거 문구로 매칭해야 한다(`macro_checklist.py`
  참고).
- **`.gitignore` 패턴이 하위 디렉토리에서 안 먹히는 함정**: 슬래시가 포함된
  gitignore 패턴(`.streamlit/secrets.toml`)은 그 파일이 있는 디렉토리 기준으로
  앵커링되어, `web/.streamlit/secrets.toml`처럼 하위 디렉토리의 파일을
  매칭하지 못한다. `**/.streamlit/secrets.toml`로 바꿔야 모든 깊이에서
  매칭된다. 커밋 전에 `git add -n <path>`로 실제 스테이징될 파일을 dry-run
  확인하는 습관이 이 실수(민감 파일 커밋)를 막아준다.
- **uv workspace에서 앱과 라이브러리를 함께 관리할 때**: `web/`을
  `uv init --app`으로 만들면 루트 `pyproject.toml`에
  `[tool.uv.workspace] members = ["web"]`가 추가되고, `web/pyproject.toml`의
  `[tool.uv.sources]`에서 `hantoo-rest-api = { workspace = true }`로 루트
  라이브러리를 그대로 의존성으로 쓸 수 있다. 다만 workspace는 venv를 루트에
  하나만 두므로, 루트에서 `uv run`만 하면 `web/`의 의존성(streamlit 등)이
  설치되지 않는다 — `uv sync --package hantoo-web`으로 해당 멤버의 의존성까지
  같은 venv에 동기화해야 한다.

## 나중에 고려해야 할 것

- **수동 매매 기록(`transactions.yaml`)의 신뢰성**: 사람이 직접 입력하므로
  누락/오타 위험이 있다. 장기적으로는 KIS HTS/MTS 앱에서 CSV로 내보낸
  거래내역을 파싱해 `transactions.yaml`을 자동 생성하는 스크립트를 만들면
  정확도를 높일 수 있다.
- **금리/신용융자 데이터 자동화**: 현재 "부실 대형 IPO" 신호는 자동화하지
  못했고(자동 조회 가능한 공개 데이터 소스를 아직 찾지 못함), 국내 신용거래
  융자(B2) 금리 신호도 구현하지 못했다. 필요하면 관련 공개 데이터 API를
  더 조사해볼 것.
- **주변국 지수의 실시간성**: `global_indices.py`는 yfinance의 일별 종가
  기준이라 장중 실시간 등락률과는 다를 수 있다. 실시간성이 중요해지면
  KIS의 해외주식 시세 API(개별 종목 단위)로 대체 지수를 근사하거나, 유료
  실시간 데이터 소스 도입을 검토할 것.
- **`macro_checklist.py`의 임계값들**: 저빈도(월 4회 이하)/분산(HHI 0.35
  이하)/주변국 하락 비율(40%/70%) 등은 모두 통계적으로 검증된 값이 아니라
  실용적인 경험적 기준이다. 실제 시장 데이터가 더 쌓이면 백테스트로 재검토할 것.
- **API 재시도 로직의 락 없는 동시성**: `auth.py`의 토큰 캐시는 여러 프로세스가
  동시에 캐시 만료 시점에 재발급을 시도하면 레이스 컨디션이 생길 수 있다.
  지금까지는 문제된 적 없지만, 다중 프로세스/워커 환경으로 확장하면 파일 락
  도입을 고려할 것.
- **테스트 자동화**: 현재는 `streamlit.testing.v1.AppTest`로 수동 회귀
  테스트만 하고 있다. pytest 기반 자동화된 테스트 스위트(특히 API 응답
  파싱 로직에 대한 단위 테스트, mock 기반)를 추가하면 리팩터링 안정성이
  높아질 것이다.

License: Apache License 2.0
