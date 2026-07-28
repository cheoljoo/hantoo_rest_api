"""시장 전체 방향성(코스피/코스닥 지수, 등락 종목수, 외국인/기관 매매동향, 미국 3대 지수 쏠림) 조회."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

import requests

from .config import KisConfig

_INDEX_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_PRICE_TR_ID = "FHPUP02100000"

_FOREIGN_INSTITUTION_TOTAL_PATH = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
_FOREIGN_INSTITUTION_TOTAL_TR_ID = "FHPTJ04400000"

_OVERSEAS_INDEX_CHARTPRICE_PATH = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
_OVERSEAS_INDEX_CHARTPRICE_TR_ID = "FHKST03030100"

INDEX_CODES: dict[str, str] = {"0001": "코스피", "1001": "코스닥"}

# KIS 해외지수 API(FHKST03030100)는 문서상 다우30/나스닥100/S&P500 구성종목만 조회 가능하다.
# (다른 국가 지수 코드는 실측 결과 빈 값이 반환되어 이 API로는 조회되지 않았다.)
OVERSEAS_INDEX_CODES: dict[str, str] = {
    ".DJI": "다우존스",
    "COMP": "나스닥종합",
    "SPX": "S&P500",
}


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


@dataclass(frozen=True)
class OverseasIndexSnapshot:
    """미국 주요 지수(다우/나스닥종합/S&P500) 스냅샷."""

    code: str
    name: str
    current: float
    change: float
    change_rate: float


def get_overseas_index_snapshot(
    cfg: KisConfig, access_token: str, index_code: str
) -> OverseasIndexSnapshot:
    """미국 주요 지수 하나의 현재 스냅샷을 조회한다."""
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=10)
    params = {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
    }
    resp = requests.get(
        f"{cfg.base_url}{_OVERSEAS_INDEX_CHARTPRICE_PATH}",
        headers=_headers(cfg, access_token, _OVERSEAS_INDEX_CHARTPRICE_TR_ID),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"{index_code} 해외지수 조회 실패: {data.get('msg_cd')} {data.get('msg1')}")

    o = data.get("output1", {})
    return OverseasIndexSnapshot(
        code=index_code,
        name=OVERSEAS_INDEX_CODES.get(index_code, index_code),
        current=float(o.get("ovrs_nmix_prpr", 0)),
        change=float(o.get("ovrs_nmix_prdy_vrss", 0)),
        change_rate=float(o.get("prdy_ctrt", 0)),
    )


def get_overseas_index_snapshots(
    cfg: KisConfig, access_token: str, index_codes: list[str] | None = None
) -> list[OverseasIndexSnapshot]:
    """미국 주요 지수 여러 개를 순차 조회한다.

    (초당 거래건수 제한 때문에 각 호출 사이에 짧게 대기한다.)
    """
    codes = index_codes or list(OVERSEAS_INDEX_CODES)
    snapshots = []
    for i, code in enumerate(codes):
        if i > 0:
            time.sleep(0.3)
        snapshots.append(get_overseas_index_snapshot(cfg, access_token, code))
    return snapshots


def concentration_signal(snapshots: list[OverseasIndexSnapshot]) -> str:
    """나스닥(대장주 지수)만 급등하고 다우/S&P500은 부진한 '쏠림(연끌)' 여부를 판단한다.

    닷컴버블 막판 3개월처럼 나스닥만 오르고 다른 우량주 지수(다우/S&P500)가
    뒤처지거나 하락하면 시장 자금이 소진되고 있다는 경고 신호로 본다.
    """
    by_code = {s.code: s for s in snapshots}
    nasdaq = by_code.get("COMP")
    others = [s for s in snapshots if s.code != "COMP"]
    if nasdaq is None or not others:
        return "판단 불가 (데이터 부족)"

    others_avg_rate = sum(s.change_rate for s in others) / len(others)
    gap = nasdaq.change_rate - others_avg_rate

    if gap >= 1.0 and others_avg_rate <= 0:
        return "⚠️ 쏠림 심화 (나스닥만 급등, 다우/S&P500 부진 — 닷컴버블형 경고 신호)"
    if gap >= 1.0:
        return "쏠림 진행 중 (나스닥 상대적 강세)"
    if all(s.change_rate < 0 for s in snapshots):
        return "동반 하락 (대장주 포함 전체 약세)"
    return "고른 흐름 (지수 간 쏠림 없음)"
