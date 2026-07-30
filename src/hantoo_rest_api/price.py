"""캔들차트(OHLCV)용 기간별 시세 조회."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Literal

import requests

from .config import KisConfig

_INQUIRE_DAILY_CHARTPRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

PeriodDiv = Literal["D", "W", "M", "Y"]  # 일/주/월/년


@dataclass(frozen=True)
class Candle:
    """캔들차트 한 봉(1일/1주/1월/1년)에 대한 OHLCV 데이터."""

    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int


def get_daily_candles(
    cfg: KisConfig,
    access_token: str,
    stock_code: str,
    *,
    start_date: dt.date,
    end_date: dt.date,
    period_div: PeriodDiv = "D",
    adjusted_price: bool = True,
) -> list[Candle]:
    """`/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`(tr_id
    FHKST03010100)로 종목 하나의 기간별 캔들(OHLCV)을 조회해 날짜 오름차순으로
    반환한다. 응답의 `output2`가 캔들 배열이고, `stck_bsop_date`(영업일자)가
    없는 행은 걸러낸다(장 운영일이 아닌 날 등 빈 데이터 방어).

    Args:
        stock_code: 6자리 종목코드 (예: "005930"). ETF도 동일한 방식으로 조회된다.
        start_date, end_date: 조회 기간 (양 끝 포함).
        period_div: "D"(일봉) / "W"(주봉) / "M"(월봉) / "Y"(년봉).
        adjusted_price: True면 수정주가(액면분할/배당 등 반영) 기준으로 조회.

    KIS 서버가 간헐적으로 5xx(특히 500 Internal Server Error)나 "초당 거래건수
    초과" 오류를 반환하는 것이 실제 운영 중 여러 번 관찰되어, 최대 3회까지
    지수적으로 대기하며 재시도한다. 3회 모두 실패하면 마지막 오류 내용을 담아
    `RuntimeError`를 던진다 — 호출자(웹 대시보드 등)는 이 예외를 잡아 해당
    종목만 건너뛰고 나머지 화면은 정상 렌더링하도록 처리해야 한다.

    Raises:
        RuntimeError: 3회 재시도 후에도 요청이 실패한 경우.
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": cfg.app_key,
        "appsecret": cfg.app_secret,
        "tr_id": "FHKST03010100",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": period_div,
        "FID_ORG_ADJ_PRC": "0" if adjusted_price else "1",
    }

    last_error: Exception | None = None
    data: dict | None = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(0.5 * attempt)
        try:
            resp = requests.get(
                f"{cfg.base_url}{_INQUIRE_DAILY_CHARTPRICE_PATH}",
                headers=headers,
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            continue
        if data.get("rt_cd") != "0":
            last_error = RuntimeError(f"{data.get('msg_cd')} {data.get('msg1')}")
            data = None
            continue
        break

    if data is None:
        raise RuntimeError(f"{stock_code} 캔들 조회 실패 (재시도 3회 실패): {last_error}")

    candles = [
        Candle(
            date=dt.datetime.strptime(item["stck_bsop_date"], "%Y%m%d").date(),
            open=float(item["stck_oprc"]),
            high=float(item["stck_hgpr"]),
            low=float(item["stck_lwpr"]),
            close=float(item["stck_clpr"]),
            volume=int(item["acml_vol"]),
        )
        for item in data.get("output2", [])
        if item.get("stck_bsop_date")
    ]
    candles.sort(key=lambda c: c.date)
    return candles


def get_candles_for_codes(
    cfg: KisConfig,
    access_token: str,
    stock_codes: list[str],
    *,
    start_date: dt.date,
    end_date: dt.date,
    period_div: PeriodDiv = "D",
) -> dict[str, list[Candle]]:
    """여러 종목코드에 대해 `get_daily_candles`를 하나씩 호출해 코드→캔들 딕셔너리로 모은다.

    보유종목 + 관심종목을 합친 코드 목록에 사용하도록 설계했다. 종목별로 순차 호출하므로
    종목 수가 많아지면 그만큼 시간이 걸린다(현재는 배치/병렬 조회 API를 쓰지 않음) —
    종목이 아주 많아지면 병렬화나 배치 API 검토가 필요할 수 있다.
    """
    return {
        code: get_daily_candles(
            cfg,
            access_token,
            code,
            start_date=start_date,
            end_date=end_date,
            period_div=period_div,
        )
        for code in stock_codes
    }
