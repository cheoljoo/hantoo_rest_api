"""시장 전체 방향성(코스피/코스닥 지수, 등락 종목수, 외국인/기관 매매동향) 조회."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .config import KisConfig

_INDEX_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_PRICE_TR_ID = "FHPUP02100000"

_FOREIGN_INSTITUTION_TOTAL_PATH = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
_FOREIGN_INSTITUTION_TOTAL_TR_ID = "FHPTJ04400000"

INDEX_CODES: dict[str, str] = {"0001": "코스피", "1001": "코스닥"}


def _headers(cfg: KisConfig, access_token: str, tr_id: str) -> dict[str, str]:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": cfg.app_key,
        "appsecret": cfg.app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


@dataclass(frozen=True)
class IndexSnapshot:
    """코스피/코스닥 등 업종 지수의 현재가 및 등락 종목수 스냅샷."""

    code: str
    name: str
    current: float
    change: float  # 전일 대비
    change_rate: float  # 전일 대비율(%)
    advancing: int  # 상승 종목 수
    declining: int  # 하락 종목 수
    unchanged: int  # 보합 종목 수
    upper_limit: int  # 상한 종목 수
    lower_limit: int  # 하한 종목 수

    @property
    def breadth_signal(self) -> str:
        """등락 종목수 비율로 본 시장 폭(breadth) 신호."""
        if self.advancing > self.declining * 1.2:
            return "상승 우세"
        if self.declining > self.advancing * 1.2:
            return "하락 우세"
        return "혼조"

    @property
    def trend_signal(self) -> str:
        """지수 등락률과 시장 폭을 함께 본 종합 방향성 신호."""
        if self.change_rate > 0 and self.advancing >= self.declining:
            return "상승 추세"
        if self.change_rate < 0 and self.declining >= self.advancing:
            return "하락 추세"
        return "혼조"


def get_index_snapshot(cfg: KisConfig, access_token: str, index_code: str) -> IndexSnapshot:
    """지수 하나(코스피 0001 / 코스닥 1001 등)의 현재 스냅샷을 조회한다."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
    }
    resp = requests.get(
        f"{cfg.base_url}{_INDEX_PRICE_PATH}",
        headers=_headers(cfg, access_token, _INDEX_PRICE_TR_ID),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"{index_code} 지수 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    o = data.get("output", {})
    return IndexSnapshot(
        code=index_code,
        name=INDEX_CODES.get(index_code, index_code),
        current=float(o.get("bstp_nmix_prpr", 0)),
        change=float(o.get("bstp_nmix_prdy_vrss", 0)),
        change_rate=float(o.get("bstp_nmix_prdy_ctrt", 0)),
        advancing=int(o.get("ascn_issu_cnt", 0)),
        declining=int(o.get("down_issu_cnt", 0)),
        unchanged=int(o.get("stnr_issu_cnt", 0)),
        upper_limit=int(o.get("uplm_issu_cnt", 0)),
        lower_limit=int(o.get("lslm_issu_cnt", 0)),
    )


def get_index_snapshots(
    cfg: KisConfig, access_token: str, index_codes: list[str] | None = None
) -> list[IndexSnapshot]:
    """여러 지수(기본: 코스피/코스닥)의 스냅샷을 한 번에 조회한다."""
    codes = index_codes or list(INDEX_CODES)
    return [get_index_snapshot(cfg, access_token, code) for code in codes]


@dataclass(frozen=True)
class NetFlowItem:
    """특정 종목에 대한 외국인/기관 순매수(량) 상위 랭킹 한 건."""

    code: str
    name: str
    current_price: float
    change_rate: float
    foreign_net_qty: int
    institution_net_qty: int


def get_net_flow_ranking(
    cfg: KisConfig,
    access_token: str,
    *,
    market_code: str = "0000",
    top_n: int = 10,
) -> list[NetFlowItem]:
    """외국인+기관 합산 순매수 상위 종목 랭킹을 조회한다.

    market_code: "0000" 전체, "0001" 코스피, "1001" 코스닥
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "V",
        "FID_COND_SCR_DIV_CODE": "16449",
        "FID_INPUT_ISCD": market_code,
        "FID_DIV_CLS_CODE": "0",  # 수량정열
        "FID_RANK_SORT_CLS_CODE": "0",  # 순매수상위
        "FID_ETC_CLS_CODE": "0",  # 전체
    }
    resp = requests.get(
        f"{cfg.base_url}{_FOREIGN_INSTITUTION_TOTAL_PATH}",
        headers=_headers(cfg, access_token, _FOREIGN_INSTITUTION_TOTAL_TR_ID),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"외국인/기관 매매동향 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    items = [
        NetFlowItem(
            code=item["mksc_shrn_iscd"],
            name=item["hts_kor_isnm"],
            current_price=float(item.get("stck_prpr", 0)),
            change_rate=float(item.get("prdy_ctrt", 0)),
            foreign_net_qty=int(item.get("frgn_ntby_qty", 0)),
            institution_net_qty=int(item.get("orgn_ntby_qty", 0)),
        )
        for item in data.get("output", [])
    ]
    return items[:top_n]
