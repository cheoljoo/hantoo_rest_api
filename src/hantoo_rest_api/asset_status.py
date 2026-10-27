"""투자계좌자산현황조회 (`inquire-account-balance`, CTRP6548R).

금현물·CMA 계좌는 일반 잔고조회(`inquire-balance`)가 거절하지만(APBK1620 /
APAC0489), 이 API는 금현물 계좌에서 실측으로 정상 응답했다(종목 단위가 아니라
자산군별 합계). HTS [0891] 계좌 자산비중 화면과 같은 구성이라 종목명/수량은
없고 매입/평가/손익 금액만 알 수 있다. 금현물 시세·수량이 필요하면 이 API로는
불가능하다.

output1은 자산군 20행 고정 배열이다. 행 순서는 HTS 0891 화면 기준으로 추정한
것이다(금현물 계좌에서 10번째 행에만 값이 있는 것으로 "금현물" 행을 확인).
CMA 계좌(상품코드 21)는 호출은 성공하지만 전부 0이 나와 실제로 비어 있는지
API가 이 계좌를 못 보는 것인지는 구분하지 못했다.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .account import _headers
from .config import KisConfig

_PATH = "/uapi/domestic-stock/v1/trading/inquire-account-balance"

# HTS 0891 기준 추정 순서 (0-based). 17=예수금+CMA, 19=합계.
_CATEGORY_NAMES = [
    "주식", "펀드/MMW", "IMA", "채권", "ELS/DLS", "WRAP", "신탁/퇴직연금",
    "RP/발행어음", "해외주식", "해외채권", "금현물", "CD/CP", "단기사채",
    "타사상품", "외화단기사채", "외화 ELS/DLS", "외화", "예수금+CMA",
    "청약자금", "합계",
]


@dataclass(frozen=True)
class AssetCategory:
    name: str
    purchase_amount: float
    eval_amount: float
    eval_profit_loss: float
    weight_pct: float


@dataclass(frozen=True)
class AssetStatus:
    categories: list[AssetCategory]  # 값이 있는 자산군만 (합계 행 제외)
    purchase_amount: float  # pchs_amt_smtl
    eval_amount: float  # evlu_amt_smtl
    eval_profit_loss: float  # evlu_pfls_amt_smtl
    deposit: float  # dncl_amt
    cma_eval_amount: float  # cma_evlu_amt
    total_asset: float  # tot_asst_amt


def get_asset_status(cfg: KisConfig, access_token: str) -> AssetStatus:
    """계좌 자산군별 매입/평가/손익과 총자산을 조회한다.

    Raises:
        RuntimeError: rt_cd != "0" (예: 금현물 아닌 일부 계좌 유형, 모의투자 미지원).
    """
    resp = requests.get(
        f"{cfg.base_url}{_PATH}",
        headers=_headers(cfg, access_token, "CTRP6548R"),
        params={
            "CANO": cfg.account_no,
            "ACNT_PRDT_CD": cfg.account_product_cd,
            "INQR_DVSN_1": "",
            "BSPR_BF_DT_APLY_YN": "",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"자산현황 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    def f(d: dict, k: str) -> float:
        try:
            return float(d.get(k) or 0)
        except ValueError:
            return 0.0

    rows = data.get("output1") or []
    categories = [
        AssetCategory(
            name=_CATEGORY_NAMES[i] if i < len(_CATEGORY_NAMES) else f"항목{i}",
            purchase_amount=f(r, "pchs_amt"),
            eval_amount=f(r, "evlu_amt"),
            eval_profit_loss=f(r, "evlu_pfls_amt"),
            weight_pct=f(r, "whol_weit_rt"),
        )
        for i, r in enumerate(rows)
        if i != len(_CATEGORY_NAMES) - 1 and (f(r, "evlu_amt") or f(r, "pchs_amt"))
    ]
    o2 = data.get("output2") or {}
    if isinstance(o2, list):
        o2 = o2[0] if o2 else {}
    return AssetStatus(
        categories=categories,
        purchase_amount=f(o2, "pchs_amt_smtl"),
        eval_amount=f(o2, "evlu_amt_smtl"),
        eval_profit_loss=f(o2, "evlu_pfls_amt_smtl"),
        deposit=f(o2, "dncl_amt"),
        cma_eval_amount=f(o2, "cma_evlu_amt"),
        total_asset=f(o2, "tot_asst_amt"),
    )


_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
GOLD_1KG_CODE = "M04020000"  # KRX 금 99.99_1kg (원/g)


def get_gold_price(cfg: KisConfig, access_token: str) -> float:
    """KRX 금시장 금 99.99_1kg 현재가(원/g). 금현물 보유 수량 추정에 쓴다.

    잔고 API가 없어 수량은 `평가금액 / 현재가`로 추정할 수밖에 없다 (평가 시점이
    달라 수 g 단위 오차가 있을 수 있으므로 반올림해서 쓴다).
    """
    resp = requests.get(
        f"{cfg.base_url}{_PRICE_PATH}",
        headers=_headers(cfg, access_token, "FHKST01010100"),
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": GOLD_1KG_CODE},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"금 시세 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")
    return float(data["output"]["stck_prpr"])
