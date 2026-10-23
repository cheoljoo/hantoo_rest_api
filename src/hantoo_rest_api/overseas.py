"""해외주식 잔고/외화 예수금/체결내역 조회.

국내주식용 `account.get_account_balance`는 해외주식과 외화 잔고를 보여주지
않으므로 별도 API를 쓴다.

- 체결기준현재잔고(`inquire-present-balance`, CTRP6504R): 해외 보유종목(output1),
  통화별 외화 예수금(output2), 원화 환산 합계(output3)를 한 번에 준다.
- 주문체결내역(`inquire-ccnl`, TTTS3035R): 해외주식 체결 내역.

필드 이름은 KIS 공식 저장소(koreainvestment/open-trading-api)의 예제를 따랐다.
보유 종목/체결이 있는 계좌로는 아직 실측하지 못했고(현재 계좌는 외화 예수금만
있음), 종목 필드는 값이 없을 때를 대비해 `.get`으로 읽는다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

from .account import _headers
from .config import KisConfig

_PRESENT_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
_CCNL_PATH = "/uapi/overseas-stock/v1/trading/inquire-ccnl"


@dataclass(frozen=True)
class OverseasHolding:
    """해외 보유 종목 하나. 금액은 해당 종목의 거래통화 기준."""

    code: str  # pdno
    name: str  # prdt_name
    market: str  # tr_mket_name
    currency: str  # buy_crcy_cd
    quantity: float  # cblc_qty13
    avg_price: float  # avg_unpr3
    current_price: float  # ovrs_now_pric1
    purchase_amount: float  # frcr_pchs_amt
    eval_amount: float  # frcr_evlu_amt2
    eval_profit_loss: float  # evlu_pfls_amt2
    eval_profit_loss_rate: float  # evlu_pfls_rt1


@dataclass(frozen=True)
class ForeignCash:
    """통화별 외화 예수금."""

    currency: str  # crcy_cd
    deposit: float  # frcr_dncl_amt_2
    withdrawable: float  # frcr_drwg_psbl_amt_1


@dataclass(frozen=True)
class OverseasSummary:
    """원화 환산 합계 (output3)."""

    total_deposit_krw: float  # tot_dncl_amt
    total_asset_krw: float  # tot_asst_amt
    withdrawable_krw: float  # wdrw_psbl_tot_amt
    purchase_amount_krw: float  # pchs_amt_smtl_amt
    eval_amount_krw: float  # evlu_amt_smtl_amt
    eval_profit_loss_krw: float  # tot_evlu_pfls_amt


@dataclass(frozen=True)
class OverseasBalance:
    holdings: list[OverseasHolding]
    cash: list[ForeignCash]
    summary: OverseasSummary


@dataclass(frozen=True)
class OverseasExecution:
    date: dt.date
    code: str
    name: str
    side: str  # 매도/매수
    quantity: float
    price: float
    amount: float
    currency: str


def _f(item: dict, key: str) -> float:
    try:
        return float(item.get(key) or 0)
    except ValueError:
        return 0.0


def get_overseas_balance(cfg: KisConfig, access_token: str) -> OverseasBalance:
    """해외주식 체결기준현재잔고를 조회한다 (외화 기준, 전 국가/시장).

    Raises:
        RuntimeError: KIS API가 rt_cd != "0"으로 실패를 응답한 경우.
    """
    tr_id = "VTRP6504R" if cfg.is_virtual else "CTRP6504R"
    params = {
        "CANO": cfg.account_no,
        "ACNT_PRDT_CD": cfg.account_product_cd,
        "WCRC_FRCR_DVSN_CD": "02",  # 외화
        "NATN_CD": "000",  # 전체 국가
        "TR_MKET_CD": "00",  # 전체 시장
        "INQR_DVSN_CD": "00",  # 일반+미니스탁
    }
    resp = requests.get(
        f"{cfg.base_url}{_PRESENT_BALANCE_PATH}",
        headers=_headers(cfg, access_token, tr_id),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"해외 잔고 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    holdings = [
        OverseasHolding(
            code=i.get("pdno", ""),
            name=i.get("prdt_name", "") or i.get("pdno", ""),
            market=i.get("tr_mket_name", ""),
            currency=i.get("buy_crcy_cd", ""),
            quantity=_f(i, "cblc_qty13"),
            avg_price=_f(i, "avg_unpr3"),
            current_price=_f(i, "ovrs_now_pric1"),
            purchase_amount=_f(i, "frcr_pchs_amt"),
            eval_amount=_f(i, "frcr_evlu_amt2"),
            eval_profit_loss=_f(i, "evlu_pfls_amt2"),
            eval_profit_loss_rate=_f(i, "evlu_pfls_rt1"),
        )
        for i in data.get("output1") or []
        if _f(i, "cblc_qty13") > 0
    ]
    cash = [
        ForeignCash(
            currency=i.get("crcy_cd", ""),
            deposit=_f(i, "frcr_dncl_amt_2"),
            withdrawable=_f(i, "frcr_drwg_psbl_amt_1"),
        )
        for i in data.get("output2") or []
        if _f(i, "frcr_dncl_amt_2") or _f(i, "frcr_drwg_psbl_amt_1")
    ]
    o3 = data.get("output3") or {}
    if isinstance(o3, list):
        o3 = o3[0] if o3 else {}
    summary = OverseasSummary(
        total_deposit_krw=_f(o3, "tot_dncl_amt"),
        total_asset_krw=_f(o3, "tot_asst_amt"),
        withdrawable_krw=_f(o3, "wdrw_psbl_tot_amt"),
        purchase_amount_krw=_f(o3, "pchs_amt_smtl_amt"),
        eval_amount_krw=_f(o3, "evlu_amt_smtl_amt"),
        eval_profit_loss_krw=_f(o3, "tot_evlu_pfls_amt"),
    )
    return OverseasBalance(holdings=holdings, cash=cash, summary=summary)


def get_overseas_executions(
    cfg: KisConfig, access_token: str, days: int = 90
) -> list[OverseasExecution]:
    """최근 `days`일간 해외주식 체결 내역(체결수량 > 0)을 조회한다.

    한 번에 반환되는 건수 제한이 있어 연속조회(tr_cont)를 따라 최대 10페이지까지 읽는다.
    """
    tr_id = "VTTS3035R" if cfg.is_virtual else "TTTS3035R"
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    fk, nk, tr_cont = "", "", ""
    result: list[OverseasExecution] = []
    for _ in range(10):
        params = {
            "CANO": cfg.account_no,
            "ACNT_PRDT_CD": cfg.account_product_cd,
            "PDNO": "" if cfg.is_virtual else "%",
            "ORD_STRT_DT": start.strftime("%Y%m%d"),
            "ORD_END_DT": end.strftime("%Y%m%d"),
            "SLL_BUY_DVSN": "00",
            "CCLD_NCCS_DVSN": "00" if cfg.is_virtual else "01",  # 체결만
            "OVRS_EXCG_CD": "" if cfg.is_virtual else "%",
            "SORT_SQN": "DS",
            "ORD_DT": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "CTX_AREA_NK200": nk,
            "CTX_AREA_FK200": fk,
        }
        headers = _headers(cfg, access_token, tr_id)
        headers["tr_cont"] = tr_cont
        resp = requests.get(
            f"{cfg.base_url}{_CCNL_PATH}", headers=headers, params=params, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"해외 체결내역 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")
        for i in data.get("output") or []:
            qty = _f(i, "ft_ccld_qty")
            if qty <= 0:
                continue
            result.append(
                OverseasExecution(
                    date=dt.datetime.strptime(i["ord_dt"], "%Y%m%d").date(),
                    code=i.get("pdno", ""),
                    name=i.get("prdt_name", ""),
                    side=i.get("sll_buy_dvsn_cd_name", ""),
                    quantity=qty,
                    price=_f(i, "ft_ccld_unpr3"),
                    amount=_f(i, "ft_ccld_amt3"),
                    currency=i.get("tr_crcy_cd", ""),
                )
            )
        tr_cont = resp.headers.get("tr_cont", "")
        if tr_cont not in ("M", "F"):
            break
        fk, nk, tr_cont = data.get("ctx_area_fk200", ""), data.get("ctx_area_nk200", ""), "N"
    return result
