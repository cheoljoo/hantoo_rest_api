"""시장 전체 방향성(코스피/코스닥 지수, 등락 종목수, 외국인/기관 매매동향, 미국 3대 지수 쏠림) 조회.

이 모듈은 웹 대시보드의 "📈 시장 동향 (Macro)" 섹션의 데이터 소스다. 한 가지 중요한
제약: `OVERSEAS_INDEX_CODES` 아래 주석대로, KIS의 해외지수 API는 다우30/나스닥100/
S&P500 구성종목 외의 해외 지수(니케이/항셍/유럽/인도 등)를 조회하면 rt_cd는
성공(0)이지만 필드가 전부 0인 빈 값을 반환한다 — 에러가 아니라 "조용한 실패"라서
실측(여러 종목코드로 직접 호출)해보기 전까지는 API 문서만으로 알아채기 어려웠다.
그래서 미국 외 국가 지수는 이 모듈이 아니라 `global_indices.py`(yfinance 기반)에서
별도로 처리한다.
"""

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
    """GET 요청 후 응답 JSON을 반환하되, 실패 시 최대 `retries`회 재시도한다.

    "실패"의 범위는 두 가지다: (1) HTTP 예외/타임아웃 등 `requests.RequestException`,
    JSON 파싱 실패, (2) HTTP 상태코드는 200이지만 KIS 응답 바디의 `rt_cd`가 "0"이
    아닌 업무 오류(예: 초당 거래건수 초과). 실제 운영 중 이 두 유형 모두 KIS 서버
    쪽에서 간헐적으로 발생하는 것을 확인했다. 매 재시도 사이 `0.5 * 시도횟수`초만큼
    선형 백오프한다. 최종 실패 시 마지막 오류를 포함한 `RuntimeError`를 던진다.
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(0.5 * attempt)
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            continue
        if data.get("rt_cd") != "0":
            last_error = RuntimeError(f"{data.get('msg_cd')} {data.get('msg1')}")
            continue
        return data
    raise RuntimeError(f"API 호출 실패 ({url}): {last_error}")


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
    """`/uapi/domestic-stock/v1/quotations/inquire-index-price`(tr_id FHPUP02100000)로
    지수 하나(코스피 0001 / 코스닥 1001 등)의 현재가와 상승/하락/보합/상한/하한
    종목수를 조회한다. `FID_COND_MRKT_DIV_CODE="U"`(업종)로 고정하고 `index_code`를
    `FID_INPUT_ISCD`에 넘긴다. 등락 종목수는 `IndexSnapshot.breadth_signal`에서
    시장 폭(breadth) 판단에, 등락률과 함께 `trend_signal`에서 종합 신호 산출에 쓰인다.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
    }
    data = _get_with_retry(
        f"{cfg.base_url}{_INDEX_PRICE_PATH}",
        _headers(cfg, access_token, _INDEX_PRICE_TR_ID),
        params,
    )

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
    """`index_codes`(기본: 코스피/코스닥)를 순서대로 `get_index_snapshot`으로 조회한다."""
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
    """`/uapi/domestic-stock/v1/quotations/foreign-institution-total`(tr_id
    FHPTJ04400000, HTS [0440] 화면에 대응)으로 외국인+기관 합산 순매수 상위
    종목 랭킹을 조회한다.

    이 API는 "증권사 직원이 장중에 집계/입력한 자료를 단순 누계한 수치"라서
    실시간 정밀 데이터가 아니라 하루 중 몇 차례(외국인 09:30/11:20/13:20/14:30,
    기관종합 10:00/11:20/13:20/14:30)만 갱신되며 ±10분 정도 오차가 날 수
    있다(KIS 공식 안내) — 대시보드에도 이 사실을 캡션으로 명시해 사용자가
    실시간 데이터로 오해하지 않도록 했다.

    Args:
        market_code: "0000" 전체, "0001" 코스피, "1001" 코스닥.
        top_n: 반환할 상위 종목 수 (API 응답을 그대로 자르는 방식이라, API가
            더 적은 수만 반환하면 top_n보다 적게 나올 수 있다).
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "V",
        "FID_COND_SCR_DIV_CODE": "16449",
        "FID_INPUT_ISCD": market_code,
        "FID_DIV_CLS_CODE": "0",  # 수량정열
        "FID_RANK_SORT_CLS_CODE": "0",  # 순매수상위
        "FID_ETC_CLS_CODE": "0",  # 전체
    }
    data = _get_with_retry(
        f"{cfg.base_url}{_FOREIGN_INSTITUTION_TOTAL_PATH}",
        _headers(cfg, access_token, _FOREIGN_INSTITUTION_TOTAL_TR_ID),
        params,
    )

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
    """`/uapi/overseas-price/v1/quotations/inquire-daily-chartprice`(tr_id
    FHKST03030100, `FID_COND_MRKT_DIV_CODE="N"` 해외지수)로 미국 주요 지수
    하나의 현재 스냅샷을 조회한다. 이 엔드포인트는 일/주/월/년봉 기간별 시세
    조회용이라 `start_date`/`end_date` 구간이 필요하지만, 여기서는 "가장 최근
    거래일의 스냅샷"만 필요하므로 최근 10일 구간으로 요청해 `output1`(가장
    최근 시점의 현재가/전일대비 요약)만 사용하고 `output2`(일별 상세 배열)는
    버린다. `index_code`는 반드시 `OVERSEAS_INDEX_CODES`에 있는 코드여야
    의미 있는 데이터가 나온다 (모듈 docstring 참고).
    """
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=10)
    params = {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
    }
    data = _get_with_retry(
        f"{cfg.base_url}{_OVERSEAS_INDEX_CHARTPRICE_PATH}",
        _headers(cfg, access_token, _OVERSEAS_INDEX_CHARTPRICE_TR_ID),
        params,
    )

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
