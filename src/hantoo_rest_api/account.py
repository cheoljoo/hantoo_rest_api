"""계좌 잔고 조회 (보유 종목, 매입가, 평가손익 등)."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .config import KisConfig

_INQUIRE_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"


@dataclass(frozen=True)
class StockHolding:
    """보유 중인 종목 하나에 대한 정보."""

    code: str  # 종목코드 (pdno)
    name: str  # 종목명 (prdt_name)
    quantity: int  # 보유수량 (hldg_qty)
    avg_purchase_price: float  # 매입평균가격 (pchs_avg_pric)
    purchase_amount: float  # 매입금액 (pchs_amt)
    current_price: float  # 현재가 (prpr)
    eval_amount: float  # 평가금액 (evlu_amt)
    eval_profit_loss: float  # 평가손익금액 (evlu_pfls_amt)
    eval_profit_loss_rate: float  # 평가손익율(%) (evlu_pfls_rt)


@dataclass(frozen=True)
class AccountSummary:
    """계좌 전체 요약 정보.

    예수금은 국내 주식 결제가 T+2(매매일+2영업일)로 이뤄지는 것을 반영해
    D(당일)/D+1(익일)/D+2(익익일, 최종 인출가능금액) 3단계로 나온다. 일반
    위탁계좌·퇴직연금(DC형) 계좌 모두 `inquire-balance` 응답에 동일한 필드
    구조로 이 3단계와 CMA평가금액(현금성 자산)을 포함하는 것을 실측으로
    확인했다 — 계좌 유형과 무관하게 항상 채워진다.
    """

    deposit_total: float  # 예수금총금액, D (dnca_tot_amt)
    next_day_withdrawable: float  # 익일 인출가능금액, D+1 (nxdy_excc_amt)
    next_2day_withdrawable: float  # 가수도정산금액(최종 인출가능금액), D+2 (prvs_rcdl_excc_amt)
    cma_eval_amount: float  # CMA평가금액 — 현금성 자산 (cma_evlu_amt)
    securities_eval_amount: float  # 유가증권평가금액 (scts_evlu_amt)
    total_eval_amount: float  # 총평가금액 (tot_evlu_amt)
    total_purchase_amount: float  # 매입금액합계금액 (pchs_amt_smtl_amt)
    total_eval_profit_loss: float  # 평가손익합계금액 (evlu_pfls_smtl_amt)


@dataclass(frozen=True)
class AccountBalance:
    """`get_account_balance` 반환값 — 보유 종목 목록과 계좌 요약을 함께 담는다."""

    holdings: list[StockHolding]
    summary: AccountSummary


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


def get_account_balance(cfg: KisConfig, access_token: str) -> AccountBalance:
    """`/uapi/domestic-stock/v1/trading/inquire-balance`로 계좌 잔고를 조회한다.

    실전투자는 tr_id `TTTC8434R`, 모의투자는 `VTTC8434R`을 사용한다(엔드포인트는
    동일하고 tr_id로 실전/모의를 구분하는 것이 KIS API의 일반적인 패턴이다).
    응답의 `output1`은 종목별 보유 내역 배열, `output2`는 계좌 요약(첫 번째 원소만
    사용) 배열이다. `output1`에서 `hldg_qty`(보유수량)가 0인 항목(과거에 전량
    매도했지만 이력상 남아있는 행 등)은 걸러내고 실제 보유 중인 종목만 반환한다.

    이 API는 퇴직연금(DC형) 계좌에서도 정상 동작한다 — 체결내역/실현손익 조회
    API와 달리 "현재 잔고 스냅샷"은 계좌 유형에 상관없이 제공되는 것으로 보인다
    (`account_activity.py`의 계좌 유형별 API 지원 여부 정리 참고).

    Raises:
        RuntimeError: KIS API가 rt_cd != "0"으로 실패를 응답한 경우.
    """
    tr_id = "VTTC8434R" if cfg.is_virtual else "TTTC8434R"

    params = {
        "CANO": cfg.account_no,
        "ACNT_PRDT_CD": cfg.account_product_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    resp = requests.get(
        f"{cfg.base_url}{_INQUIRE_BALANCE_PATH}",
        headers=_headers(cfg, access_token, tr_id),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"잔고 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    holdings = [
        StockHolding(
            code=item["pdno"],
            name=item["prdt_name"],
            quantity=int(item["hldg_qty"]),
            avg_purchase_price=float(item["pchs_avg_pric"]),
            purchase_amount=float(item["pchs_amt"]),
            current_price=float(item["prpr"]),
            eval_amount=float(item["evlu_amt"]),
            eval_profit_loss=float(item["evlu_pfls_amt"]),
            eval_profit_loss_rate=float(item["evlu_pfls_rt"]),
        )
        for item in data.get("output1", [])
        if int(item.get("hldg_qty", 0)) > 0
    ]

    summary_items = data.get("output2", [])
    summary_item = summary_items[0] if summary_items else {}
    summary = AccountSummary(
        deposit_total=float(summary_item.get("dnca_tot_amt", 0)),
        next_day_withdrawable=float(summary_item.get("nxdy_excc_amt", 0)),
        next_2day_withdrawable=float(summary_item.get("prvs_rcdl_excc_amt", 0)),
        cma_eval_amount=float(summary_item.get("cma_evlu_amt", 0)),
        securities_eval_amount=float(summary_item.get("scts_evlu_amt", 0)),
        total_eval_amount=float(summary_item.get("tot_evlu_amt", 0)),
        total_purchase_amount=float(summary_item.get("pchs_amt_smtl_amt", 0)),
        total_eval_profit_loss=float(summary_item.get("evlu_pfls_smtl_amt", 0)),
    )

    return AccountBalance(holdings=holdings, summary=summary)
