"""내 계좌의 매매빈도/분산도를 '장기·분산·저빈도 투자자가 수익률이 좋다'는
실증 연구 원칙(자본시장연구원 개인투자자 성과 분석 등)에 비추어 정량화한다.
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
    """체결 1건."""

    date: dt.date
    stock_code: str
    stock_name: str
    side: str  # "매수" / "매도"
    quantity: int
    amount: float


def _headers(cfg: KisConfig, access_token: str, tr_id: str) -> dict[str, str]:
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
    """KIS API가 간헐적으로 5xx를 반환하는 경우를 위한 재시도 래퍼."""
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
    """퇴직연금(IRP/DC) 계좌 전용 체결 내역 조회 (일반 계좌용 API는 이 계좌 유형을 지원하지 않음)."""
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
    """퇴직연금 계좌 예수금 스냅샷 (거래내역이 아닌 '현재 잔액')."""

    deposit_total: float  # 예수금총액
    next_day_settlement: float  # 익일정산금액
    next_2day_settlement: float  # 익2일정산금액


def get_pension_deposit(cfg: KisConfig, access_token: str) -> PensionDeposit:
    """퇴직연금 계좌의 현재 예수금을 조회한다. (입출금 이력이 아닌 스냅샷만 제공됨)"""
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
        next_day_settlement=float(o.get("nxdy_sttl_amt", 0) or 0),
        next_2day_settlement=float(o.get("nx2_day_sttl_amt", 0) or 0),
    )


def get_recent_executions(
    cfg: KisConfig, access_token: str, *, days: int = 90
) -> list[TradeExecution]:
    """최근 N일(최대 3개월 이내) 체결 내역을 조회한다. 퇴직연금 계좌는 전용 API로 자동 전환."""
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
    """기간 내 매매 활동 요약."""

    period_days: int
    trade_count: int
    buy_count: int
    sell_count: int
    unique_stocks: int
    trades_per_month: float


def summarize_trading_activity(
    executions: list[TradeExecution], period_days: int
) -> TradingActivitySummary:
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
    """보유 종목의 집중도를 계산한다.

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
    """퇴직연금 DC형 계좌인지 여부 (체결내역 API가 지원되지 않는 계좌 유형)."""
    return cfg.account_product_cd == PENSION_DC_ACCOUNT_PRODUCT_CD


def investment_style_signal(
    activity: TradingActivitySummary, hhi: float, *, data_reliable: bool = True
) -> str:
    """'분산 + 장기 + 저빈도' 원칙에 비추어 현재 계좌 운용 스타일을 간단히 진단한다."""
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
