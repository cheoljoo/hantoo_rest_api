"""주변국(글로벌) 증시 지수 조회 — KIS Open API가 지원하지 않는 해외지수를 위한 보조 데이터 소스.

KIS 해외지수 API(FHKST03030100)는 실측 결과 다우30/나스닥100/S&P500 구성종목만
조회 가능해, 미국 외 국가의 지수는 Yahoo Finance(yfinance)로 조회한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf

# 경제 규모가 큰 주요국 지수. 미국은 market.py의 OVERSEAS_INDEX_CODES(다우/나스닥/S&P500)에서
# 이미 다루므로, 여기서는 '주변국'에 해당하는 국가 위주로 구성한다.
GLOBAL_INDEX_TICKERS: dict[str, str] = {
    "^GDAXI": "DAX(독일)",
    "^FTSE": "FTSE100(영국)",
    "^FCHI": "CAC40(프랑스)",
    "^N225": "니케이225(일본)",
    "^HSI": "항셍(홍콩)",
    "000001.SS": "상해종합(중국)",
    "^BSESN": "센섹스(인도)",
    "^TWII": "가권지수(대만)",
}


@dataclass(frozen=True)
class GlobalIndexSnapshot:
    """주변국 지수 하나의 스냅샷."""

    ticker: str
    name: str
    current: float
    change_rate: float  # 전일 대비율(%)


def get_global_index_snapshots(
    tickers: dict[str, str] | None = None,
) -> list[GlobalIndexSnapshot]:
    """주변국 지수들의 최신 등락률을 한 번에 조회한다."""
    ticker_map = tickers or GLOBAL_INDEX_TICKERS
    data = yf.download(
        list(ticker_map),
        period="5d",
        progress=False,
        group_by="ticker",
        threads=True,
    )

    snapshots: list[GlobalIndexSnapshot] = []
    for ticker, name in ticker_map.items():
        try:
            closes = data[ticker]["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if len(closes) < 2:
            continue
        prev, current = float(closes.iloc[-2]), float(closes.iloc[-1])
        change_rate = (current - prev) / prev * 100
        snapshots.append(
            GlobalIndexSnapshot(ticker=ticker, name=name, current=current, change_rate=change_rate)
        )
    return snapshots


def peripheral_negative_count(snapshots: list[GlobalIndexSnapshot]) -> tuple[int, int]:
    """마이너스로 전환한 주변국 수와 전체 조회된 국가 수를 반환한다."""
    negative = sum(1 for s in snapshots if s.change_rate < 0)
    return negative, len(snapshots)


def peripheral_signal(snapshots: list[GlobalIndexSnapshot]) -> str:
    """주변국 증시 다수가 마이너스로 돌아섰는지로 '글로벌 연끌' 완성 단계를 가늠한다."""
    negative, total = peripheral_negative_count(snapshots)
    if total == 0:
        return "판단 불가 (데이터 부족)"
    ratio = negative / total
    if ratio >= 0.7:
        return "⚠️ 글로벌 연끌 완성 단계 (주변국 대다수 하락 전환)"
    if ratio >= 0.4:
        return "쏠림 진행 중 (주변국 일부 하락 전환)"
    return "주변부 유동성 양호 (대다수 국가 견조)"
