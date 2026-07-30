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
    """주변국 지수 하나의 스냅샷 (yfinance 기준)."""

    ticker: str  # yfinance 티커 (예: "^N225")
    name: str  # 표시용 이름 (예: "니케이225(일본)")
    current: float  # 최근 종가
    change_rate: float  # 전일 대비율(%). 직전 종가 대비 계산 (장중 실시간 등락률이 아님)


def get_global_index_snapshots(
    tickers: dict[str, str] | None = None,
) -> list[GlobalIndexSnapshot]:
    """`yf.download`로 여러 티커의 최근 5거래일 종가를 한 번에 받아와, 각 티커의
    "최근 종가 vs 직전 종가" 등락률을 계산한다.

    5일치를 받는 이유는 공휴일 등으로 최근 1~2일 데이터가 비어있을 수 있어
    최소 2개의 유효한 종가를 확보하기 위한 여유분이다. `threads=True`로 티커별
    요청을 병렬화해 8개국을 순차 조회할 때보다 응답을 앞당긴다. 특정 티커의
    데이터가 없거나(`KeyError`) 유효 종가가 2개 미만이면 그 티커는 결과에서
    조용히 제외한다(전체 조회 실패로 처리하지 않음) — 일부 국가장이 휴장이어도
    나머지 국가는 정상 표시되도록 하기 위함이다.
    """
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
    """(마이너스로 전환한 국가 수, 조회에 성공한 전체 국가 수)를 반환한다."""
    negative = sum(1 for s in snapshots if s.change_rate < 0)
    return negative, len(snapshots)


def peripheral_signal(snapshots: list[GlobalIndexSnapshot]) -> str:
    """주변국 증시 중 몇 %가 마이너스로 전환했는지로 '글로벌 연끌(쏠림)' 완성 단계를 가늠한다.

    김효진 박사가 설명한 논리: 버블 막판에는 대장주(나스닥 등)만 오르고 주변부
    자산(다른 우량주, 주변국 증시)에서 자금이 빠져나간다 — 즉 주변국이 얼마나
    동반 하락하는지가 '연끌이 얼마나 진행됐는지'의 대리 지표가 된다. 임계값
    70%/40%는 정교한 통계적 근거가 아니라 "대다수/일부"를 구분하기 위한 실용적
    기준값이며, 실제 사용 데이터가 쌓이면 조정이 필요할 수 있다.
    """
    negative, total = peripheral_negative_count(snapshots)
    if total == 0:
        return "판단 불가 (데이터 부족)"
    ratio = negative / total
    if ratio >= 0.7:
        return "⚠️ 글로벌 연끌 완성 단계 (주변국 대다수 하락 전환)"
    if ratio >= 0.4:
        return "쏠림 진행 중 (주변국 일부 하락 전환)"
    return "주변부 유동성 양호 (대다수 국가 견조)"
