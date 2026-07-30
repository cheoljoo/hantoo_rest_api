"""내 계좌의 매매빈도/분산도를 '장기·분산·저빈도 투자자가 수익률이 좋다'는
실증 연구 원칙(자본시장연구원 개인투자자 성과 분석 등)에 비추어 정량화한다.

## 계좌 유형별 KIS Open API 지원 범위 (실측 결과 정리)

체결내역(거래내역) 조회는 계좌 유형에 따라 지원 범위가 다르다는 것을 실제 계좌로
5개 이상의 엔드포인트를 테스트해서 확인했다:

- **일반 위탁계좌**: `_DAILY_CCLD_PATH`(tr_id TTTC0081R/VTTC0081R, 최근 3개월 이내)로
  체결내역이 정상 조회된다.
- **퇴직연금 DC형 계좌(상품코드 "55")**: 위 API를 호출하면 `APBK1744`
  ("퇴직연금계좌는 해당 서비스가 불가합니다") 오류가 나서, 퇴직연금 전용
  `_PENSION_DAILY_CCLD_PATH`(tr_id TTTC2201R)로 자동 전환한다(`get_recent_executions`
  참고). **그런데 이 전용 API조차 실제 매매(예: 173주→193주 등 수량 변동)가
  있었음에도 항상 빈 배열만 반환한다** — `CCLD_NCCS_DVSN`을 "01"(체결)/"02"(미체결)/
  "%%"(전체) 어느 값으로 바꿔도 결과는 같았고, 별도로 실현손익 조회
  (`TTTC8494R`)와 체결기준잔고(`TTTC2202R`)까지 테스트해봤지만 전부 "현재 잔고
  스냅샷"만 주거나 "조회할 내용이 없습니다"만 반환했다. 즉 **DC형 계좌는 KIS
  Open API로 과거 매매 이력을 복원할 방법이 없는 것으로 결론 내렸다.**
  (`docs/bear_market_signals_kim_hyojin.md`과 별개로, 이 결론 자체가 이 프로젝트의
  중요한 트러블슈팅 기록이다.)
- 이 한계 때문에 `investment_style_signal`은 `data_reliable=False`일 때 "매매
  0건"을 "장기 보유"로 오해하지 않도록 별도 경고 문구를 반환하고, 실제 매매는
  `manual_transactions.py`(사용자가 직접 기록하는 `transactions.yaml`)로 보완한다.
- 예수금(현재 잔액) 조회(`get_pension_deposit`, tr_id TTTC0506R)는 DC형 계좌에서도
  정상 동작한다 — 공식 문서에는 "55번 계좌는 이용 불가"라고 적혀 있었지만 실제로는
  정상 응답이 왔다. **문서상 경고와 실제 동작이 다를 수 있으니, 계좌 유형별 API
  가용 여부는 문서보다 실측으로 확인하는 것이 안전하다.**
- **DC형 계좌는 일반 잔고조회(`inquire-balance`)의 예수금 필드가 전부 0으로 나온다.**
  실측 결과 이 계좌는 실제로 예수금이 있었는데도(예: 48,611원) `inquire-balance`
  응답의 `dnca_tot_amt`/`nxdy_excc_amt`/`prvs_rcdl_excc_amt`/`cma_evlu_amt`가
  모두 0이었고, 대신 `get_pension_deposit`(퇴직연금 전용 예수금조회)로 조회하면
  정확한 값이 나왔다. 그래서 웹 대시보드는 `is_pension_account(cfg)`가 True면
  일반 잔고조회 대신 이 함수를 써서 예수금을 보여준다.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

import requests

from .account import StockHolding
from .config import KisConfig

_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
_PENSION_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-daily-ccld"
_PENSION_DEPOSIT_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-deposit"
_PENSION_ACCOUNT_ERROR_CODE = "APBK1744"  # "퇴직연금계좌는 해당 서비스가 불가합니다"

# 퇴직연금 DC형(상품코드 55) 계좌는 실측 결과 일반/퇴직연금용 체결내역 API가 모두
# "조회할 내용이 없습니다"류의 빈 응답만 반환한다 (실제 매매가 있었음에도). 즉 이
# 계좌 유형은 KIS Open API로 매매 체결내역 자체를 제공하지 않는 것으로 보인다.
PENSION_DC_ACCOUNT_PRODUCT_CD = "55"


@dataclass(frozen=True)
class TradeExecution:
    """체결 1건. 일반/퇴직연금 체결내역 API 응답을 공통 형태로 정규화한 것."""

    date: dt.date
    stock_code: str
    stock_name: str
    side: str  # "매수" / "매도"
    quantity: int
    amount: float


def _headers(cfg: KisConfig, access_token: str, tr_id: str) -> dict[str, str]:
    """KIS REST API 공통 요청 헤더. `tr_id`(거래ID)만 API마다 다르게 넘겨준다."""
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": cfg.app_key,
        "appsecret": cfg.app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def _get_with_retry(
    url: str, headers: dict[str, str], params: dict[str, str], *, retries: int = 2
) -> dict:
    """GET 요청 후 JSON을 반환하되, HTTP 예외/타임아웃을 최대 `retries`회
    재시도한다(선형 백오프: `0.5 * 시도횟수`초). `rt_cd` 업무 오류는 여기서
    재시도하지 않고 그대로 반환한다 — 호출자가 `msg_cd`를 보고
    (예: `_PENSION_ACCOUNT_ERROR_CODE`) 계좌 유형별 분기 처리를 해야 하기
    때문에, 이 함수가 그 정보를 먹어버리면 안 된다 (`market.py`의 동명 함수는
    범용 조회용이라 업무 오류도 재시도하는 것과의 차이점).
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(0.5 * attempt)
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"API 호출 실패 ({url}): {last_error}")


def _parse_execution(item: dict, default_date: dt.date) -> TradeExecution | None:
    """체결내역 API 응답의 원소 하나를 `TradeExecution`으로 변환한다.

    `tot_ccld_qty`(총체결수량)가 0인 행(미체결 주문 등)은 실제 체결이 아니므로
    None을 반환해 걸러낸다. `ord_dt`(주문일자) 필드가 없는 응답(일부 퇴직연금
    API 응답 형태)에 대비해 `default_date`로 대체한다.
    """
    qty = int(item.get("tot_ccld_qty", 0) or 0)
    if qty <= 0:
        return None
    ord_dt = item.get("ord_dt")
    return TradeExecution(
        date=dt.datetime.strptime(ord_dt, "%Y%m%d").date() if ord_dt else default_date,
        stock_code=item.get("pdno", ""),
        stock_name=item.get("prdt_name", ""),
        side=item.get("sll_buy_dvsn_cd_name", ""),
        quantity=qty,
        amount=float(item.get("tot_ccld_amt", 0) or 0),
    )


def _get_pension_executions(cfg: KisConfig, access_token: str) -> list[TradeExecution]:
    """퇴직연금(DC형) 계좌 전용 체결 내역 조회.

    실측 결과 이 API는 `rt_cd="0"`(성공)을 반환하지만 `output`이 항상 빈
    배열이었다 — 즉 호출은 성공하는데 실제 데이터가 없다. 이 함수 자체는
    "정상적으로 빈 목록을 반환"하는 것으로 동작하므로, 이 결과를 "실제 매매
    없음"으로 오인하지 않도록 호출자(`investment_style_signal`)에서
    `data_reliable=False`로 명시적으로 표시해야 한다. 모듈 docstring의 계좌
    유형별 정리 참고.
    """
    params = {
        "CANO": cfg.account_no,
        "ACNT_PRDT_CD": cfg.account_product_cd,
        "USER_DVSN_CD": "%%",
        "SLL_BUY_DVSN_CD": "00",
        "CCLD_NCCS_DVSN": "01",  # 체결
        "INQR_DVSN_3": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    data = _get_with_retry(
        f"{cfg.base_url}{_PENSION_DAILY_CCLD_PATH}",
        _headers(cfg, access_token, "TTTC2201R"),
        params,
    )

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"퇴직연금 체결 내역 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    today = dt.date.today()
    return [
        e
        for item in data.get("output", [])
        if (e := _parse_execution(item, today)) is not None
    ]


@dataclass(frozen=True)
class PensionDeposit:
    """퇴직연금 계좌 예수금 스냅샷 (거래내역이 아닌 '현재 잔액').

    실측 결과, 이 계좌 유형은 일반 잔고조회(`inquire-balance`)의 예수금
    관련 필드(`dnca_tot_amt`/`nxdy_excc_amt`/`prvs_rcdl_excc_amt`/
    `cma_evlu_amt`)가 전부 0으로 나온다 — 대신 이 퇴직연금 전용 API로
    조회해야 실제 값(D/D+1)이 보인다. `next_day_settlement`/
    `next_2day_settlement`는 D+1/D+2 시점의 "총 인출가능금액"이 아니라
    그 날 추가로 정산되어 들어올 예정 금액(델타)이라 일반 계좌의
    D/D+1/D+2 총액과 성격이 다르다는 점에 주의할 것.
    """

    deposit_total: float  # 예수금총액, D (dnca_tota)
    next_day_withdrawable: float  # 익일 인출가능금액, D+1 (nxdy_excc_amt)
    next_day_settlement: float  # 익일 정산예정금액 (nxdy_sttl_amt)
    next_2day_settlement: float  # 익2일 정산예정금액 (nx2_day_sttl_amt)


def get_pension_deposit(cfg: KisConfig, access_token: str) -> PensionDeposit:
    """`/uapi/domestic-stock/v1/trading/pension/inquire-deposit`(tr_id
    TTTC0506R)로 퇴직연금 계좌의 현재 예수금 스냅샷을 조회한다.

    KIS 공식 문서에는 이 API가 "55번 계좌(DC가입자계좌)는 이용 불가"라고
    적혀 있지만, 실제 이 계좌(상품코드 55)로 호출해보면 정상적으로 값을
    반환한다 — 문서와 실제 동작이 어긋나는 사례이니, 이 API가 향후 정말
    막히거나 응답 필드가 바뀌더라도 "원래 안 되는 API였다"고 오판하지 말고
    실제 응답을 다시 확인할 것. 언제 얼마가 입금/출금됐는지의 이력은
    제공하지 않고, 조회 시점의 잔액만 준다.
    """
    params = {
        "CANO": cfg.account_no,
        "ACNT_PRDT_CD": cfg.account_product_cd,
        "ACCA_DVSN_CD": "00",
    }
    data = _get_with_retry(
        f"{cfg.base_url}{_PENSION_DEPOSIT_PATH}",
        _headers(cfg, access_token, "TTTC0506R"),
        params,
    )

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"퇴직연금 예수금 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    o = data.get("output", {})
    return PensionDeposit(
        deposit_total=float(o.get("dnca_tota", 0) or 0),
        next_day_withdrawable=float(o.get("nxdy_excc_amt", 0) or 0),
        next_day_settlement=float(o.get("nxdy_sttl_amt", 0) or 0),
        next_2day_settlement=float(o.get("nx2_day_sttl_amt", 0) or 0),
    )


def get_recent_executions(
    cfg: KisConfig, access_token: str, *, days: int = 90
) -> list[TradeExecution]:
    """`/uapi/domestic-stock/v1/trading/inquire-daily-ccld`(tr_id TTTC0081R
    실전 / VTTC0081R 모의)로 최근 N일(최대 90일 = 3개월 이내) 체결 내역을
    조회한다. `CCLD_DVSN="01"`(체결만)로 미체결/취소 주문은 제외한다.

    응답이 `rt_cd != "0"`이면서 `msg_cd`가 `_PENSION_ACCOUNT_ERROR_CODE`
    ("퇴직연금계좌는 해당 서비스가 불가합니다")이면, 이 계좌가 퇴직연금
    계좌라는 뜻이므로 `_get_pension_executions`로 자동 전환한다 — 호출자는
    계좌 유형을 미리 알 필요 없이 이 함수 하나만 부르면 된다. 다만 모듈
    docstring에서 설명한 대로 퇴직연금 계좌는 결국 빈 목록이 반환되니,
    호출자가 `is_pension_account(cfg)`로 이 사실을 사용자에게 알려주는 것이
    좋다.

    Raises:
        RuntimeError: 퇴직연금 계좌 오류가 아닌 다른 사유로 조회에 실패한 경우.
    """
    tr_id = "VTTC0081R" if cfg.is_virtual else "TTTC0081R"
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=min(days, 90))

    params = {
        "CANO": cfg.account_no,
        "ACNT_PRDT_CD": cfg.account_product_cd,
        "INQR_STRT_DT": start_date.strftime("%Y%m%d"),
        "INQR_END_DT": end_date.strftime("%Y%m%d"),
        "SLL_BUY_DVSN_CD": "00",
        "PDNO": "",
        "CCLD_DVSN": "01",  # 체결만
        "INQR_DVSN": "00",
        "INQR_DVSN_3": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
        "EXCG_ID_DVSN_CD": "KRX",
    }

    data = _get_with_retry(
        f"{cfg.base_url}{_DAILY_CCLD_PATH}",
        _headers(cfg, access_token, tr_id),
        params,
    )

    if data.get("rt_cd") != "0":
        if data.get("msg_cd") == _PENSION_ACCOUNT_ERROR_CODE:
            return _get_pension_executions(cfg, access_token)
        raise RuntimeError(f"체결 내역 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    today = dt.date.today()
    return [
        e
        for item in data.get("output1", [])
        if (e := _parse_execution(item, today)) is not None
    ]


@dataclass(frozen=True)
class TradingActivitySummary:
    """기간 내 매매 활동 요약. `investment_style_signal`의 입력으로 쓰인다."""

    period_days: int
    trade_count: int
    buy_count: int
    sell_count: int
    unique_stocks: int
    trades_per_month: float


def summarize_trading_activity(
    executions: list[TradeExecution], period_days: int
) -> TradingActivitySummary:
    """체결 목록을 기간(`period_days`) 기준 통계로 집계한다.

    `trades_per_month`는 "월 4회 이하면 저빈도"라는 `investment_style_signal`의
    판단 기준에 쓰이는 정규화된 지표로, `period_days`가 90이 아니어도(예:
    30일만 조회) 월 단위로 비교 가능하도록 `len(executions) / (period_days/30)`
    으로 계산한다.
    """
    buy_count = sum(1 for e in executions if e.side == "매수")
    sell_count = sum(1 for e in executions if e.side == "매도")
    months = max(period_days / 30.0, 1e-9)
    return TradingActivitySummary(
        period_days=period_days,
        trade_count=len(executions),
        buy_count=buy_count,
        sell_count=sell_count,
        unique_stocks=len({e.stock_code for e in executions}),
        trades_per_month=len(executions) / months,
    )


def diversification_score(holdings: list[StockHolding]) -> tuple[float, float]:
    """평가금액 비중으로 허핀달-허쉬만지수(HHI)와 최대 비중 종목의 비중을 계산한다.

    HHI = Σ(비중²)이며 1종목에 100% 집중이면 1.0, N종목에 균등 분산이면
    1/N로 낮아진다. `investment_style_signal`에서는 HHI ≤ 0.35(대략 3종목
    이상 고르게 분산된 수준)를 "분산됨"의 임계값으로 쓴다. 보유 종목이 없거나
    평가금액 합계가 0 이하이면 (0.0, 0.0)을 반환한다.

    Returns:
        (허핀달-허쉬만지수 0~1(1이면 한 종목에 100% 집중), 최대 비중 종목의 비중(%))
    """
    total = sum(h.eval_amount for h in holdings)
    if total <= 0 or not holdings:
        return 0.0, 0.0
    weights = [h.eval_amount / total for h in holdings]
    hhi = sum(w**2 for w in weights)
    top_weight_pct = max(weights) * 100
    return hhi, top_weight_pct


def is_pension_account(cfg: KisConfig) -> bool:
    """계좌상품코드로 퇴직연금 DC형 계좌인지 판별한다 (체결내역 API 미지원 계좌 유형).

    이 판별은 상품코드 문자열 비교만으로 이뤄지는 휴리스틱이다 — KIS가 공식
    문서로 상품코드 체계를 공개하지 않아, 실제 계좌(상품코드 "55")로 여러
    API를 테스트해서 얻은 경험적 결론을 코드화한 것이다. 다른 특수 계좌
    유형(개인형 IRP 등)이 다른 코드를 쓴다면 이 함수로 걸러지지 않을 수 있다.
    """
    return cfg.account_product_cd == PENSION_DC_ACCOUNT_PRODUCT_CD


def investment_style_signal(
    activity: TradingActivitySummary, hhi: float, *, data_reliable: bool = True
) -> str:
    """'분산 + 장기 + 저빈도' 원칙에 비추어 현재 계좌 운용 스타일을 간단히 진단한다.

    분류 기준: 월평균 매매 4회 이하를 저빈도, HHI 0.35 이하를 분산으로 본다
    (둘 다 정교한 통계적 최적값이 아니라 실용적인 경험적 임계값).

    Args:
        activity: `summarize_trading_activity`의 결과.
        hhi: `diversification_score`가 계산한 허핀달-허쉬만지수.
        data_reliable: False면 `activity.trade_count == 0`을 "매매 없음"이
            아니라 "이 계좌 유형은 체결내역 API를 지원하지 않아 판단 불가"로
            해석해 다른 문구를 반환한다 (`account_activity` 모듈 docstring의
            퇴직연금 계좌 한계 참고). 호출자는 `is_pension_account(cfg)`의
            반전값을 넘기면 된다.
    """
    low_frequency = activity.trades_per_month <= 4
    diversified = hhi <= 0.35  # 대략 3종목 이상 고르게 분산된 수준

    if activity.trade_count == 0:
        if not data_reliable:
            return (
                "⚠️ 매매 0건으로 보이지만, 이 계좌 유형은 KIS Open API가 체결내역을 "
                "제공하지 않아 실제 매매 여부를 판단할 수 없습니다 (데이터 아님, API 한계)"
            )
        return "최근 매매 없음 — 보유 종목 유지 중 (장기 보유 성향)"
    if low_frequency and diversified:
        return "✅ 저빈도 + 분산 — 연구에서 상대적으로 좋은 성과를 보인 패턴에 부합"
    if not low_frequency and not diversified:
        return "⚠️ 고빈도 + 집중 — 연구에서 저조한 성과를 보인 패턴과 유사, 매매빈도/집중도 점검 권장"
    if not low_frequency:
        return "매매빈도가 다소 높음 — 거래비용 누적에 유의"
    return "보유 종목이 특정 종목에 다소 집중되어 있음 — 분산도 점검 권장"
