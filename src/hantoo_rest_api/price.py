"""캔들차트(OHLCV)용 기간별 시세 조회."""

from __future__ import annotations

import datetime as dt
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
    """종목 하나의 기간별 캔들(OHLCV) 데이터를 오래된 순으로 반환한다.

    stock_code: 6자리 종목코드 (예: "005930")
    period_div: "D"(일봉) / "W"(주봉) / "M"(월봉) / "Y"(년봉)
    adjusted_price: True면 수정주가(액면분할/배당 등 반영) 기준으로 조회
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

    resp = requests.get(
        f"{cfg.base_url}{_INQUIRE_DAILY_CHARTPRICE_PATH}",
        headers=headers,
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(
            f"{stock_code} 캔들 조회 실패: {data.get('msg_cd')} {data.get('msg1')}"
        )

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
    """여러 종목코드에 대해 한 번에 캔들 데이터를 조회한다. (보유종목 + 관심종목 조합에 사용)"""
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
