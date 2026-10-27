"""장내/장외 채권 보유 잔고 조회 (`domestic-bond/inquire-balance`, CTSC8407R).

국내주식 잔고조회(`inquire-balance`)에는 채권이 잡히지 않는다. 이 API는 매수 건(로트)
단위로 보유 채권을 돌려주므로 매수일·매수단가·매수수익률·만기까지 알 수 있다 — 지금
보유 중인 채권의 "구매내역" 역할을 한다. 이미 상환/매도한 채권은 나오지 않는다.
채권 체결내역(`inquire-daily-ccld`, CTSC8013R)은 위탁계좌/ISA에서 1년 범위로
조회해도 "조회할 내용이 없습니다"만 돌아와 쓰지 않는다.

금액 단위: `buy_unpr`은 액면 10,000원당 매수가, `buy_amt`는 총 매수금액(원)이다.
DC/달러 계좌처럼 채권이 없으면 rt_cd=0 + "조회할 내용이 없습니다"(KIOK0560)이다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

from .account import _headers
from .config import KisConfig

_PATH = "/uapi/domestic-bond/v1/trading/inquire-balance"


@dataclass(frozen=True)
class BondHolding:
    """채권 매수 로트 하나."""

    code: str  # pdno (ISIN)
    name: str  # prdt_name
    buy_date: dt.date  # buy_dt
    lot: int  # buy_sqno (같은 날 매수 순번)
    quantity: float  # cblc_qty
    buy_price: float  # buy_unpr (액면 10,000원당)
    buy_yield: float  # buy_erng_rt (%)
    buy_amount: float  # buy_amt (원)
    maturity: dt.date | None  # exdt


def _f(item: dict, key: str) -> float:
    try:
        return float(item.get(key) or 0)
    except ValueError:
        return 0.0


def _d(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s, "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def get_bond_balance(cfg: KisConfig, access_token: str) -> list[BondHolding]:
    """보유 채권을 매수 로트 단위로 조회한다 (연속조회 최대 10페이지).

    Raises:
        RuntimeError: rt_cd != "0"인 경우. 보유 채권이 없는 것(KIOK0560)은 빈 목록이다.
    """
    if cfg.is_virtual:
        return []
    fk, nk, tr_cont = "", "", ""
    result: list[BondHolding] = []
    for _ in range(10):
        params = {
            "CANO": cfg.account_no,
            "ACNT_PRDT_CD": cfg.account_product_cd,
            "INQR_CNDT": "00",
            "PDNO": "",
            "BUY_DT": "",
            "CTX_AREA_FK200": fk,
            "CTX_AREA_NK200": nk,
        }
        headers = _headers(cfg, access_token, "CTSC8407R")
        headers["tr_cont"] = tr_cont
        resp = requests.get(f"{cfg.base_url}{_PATH}", headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"채권 잔고 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")
        for i in data.get("output") or []:
            qty = _f(i, "cblc_qty")
            if qty <= 0:
                continue
            result.append(
                BondHolding(
                    code=i.get("pdno", ""),
                    name=i.get("prdt_name", "") or i.get("pdno", ""),
                    buy_date=_d(i.get("buy_dt", "")) or dt.date.min,
                    lot=int(_f(i, "buy_sqno")),
                    quantity=qty,
                    buy_price=_f(i, "buy_unpr"),
                    buy_yield=_f(i, "buy_erng_rt"),
                    buy_amount=_f(i, "buy_amt"),
                    maturity=_d(i.get("exdt", "")),
                )
            )
        tr_cont = resp.headers.get("tr_cont", "")
        if tr_cont not in ("M", "F"):
            break
        fk, nk, tr_cont = data.get("ctx_area_fk200", ""), data.get("ctx_area_nk200", ""), "N"
    return result
