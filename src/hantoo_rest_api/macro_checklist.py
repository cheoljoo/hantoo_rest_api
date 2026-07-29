"""김효진 박사의 '삼박자 신호(금리·글로벌 쏠림·부실 IPO) + 기계적 분할매도' 체크리스트를
자동으로 판단 가능한 부분만 데이터로 확인한다.

자세한 배경: docs/bear_market_signals_kim_hyojin.md
"""

from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf

from .global_indices import GlobalIndexSnapshot, peripheral_negative_count
from .market import OverseasIndexSnapshot


@dataclass(frozen=True)
class RateSignal:
    """미국 10년물 국채금리가 최근 1년 고점을 돌파했는지 여부."""

    current: float
    one_year_high: float

    @property
    def breakout(self) -> bool:
        return self.current >= self.one_year_high


def get_rate_signal() -> RateSignal:
    """미국 10년물 국채수익률(^TNX)의 현재값과 최근 1년 고점을 조회한다."""
    hist = yf.Ticker("^TNX").history(period="1y")
    closes = hist["Close"].dropna()
    return RateSignal(current=float(closes.iloc[-1]), one_year_high=float(closes.max()))


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    label: str
    triggered: bool
    detail: str
    automated: bool = True


@dataclass(frozen=True)
class MacroChecklist:
    items: list[ChecklistItem]

    @property
    def triggered_count(self) -> int:
        return sum(1 for i in self.items if i.automated and i.triggered)

    @property
    def automated_count(self) -> int:
        return sum(1 for i in self.items if i.automated)

    @property
    def verdict(self) -> str:
        n, total = self.triggered_count, self.automated_count
        if n == 0:
            return "🟢 정상 국면 — 계획된 분할매수 검토 가능"
        if n == 1:
            return "🟡 주의 — 신규 매수 비중을 줄이고 신호 재확인 권장"
        return "🔴 삼박자 경고 — 신규 매수 보류, 기계적 분할매도(3~5등분) 검토 시점"


def build_macro_checklist(
    overseas: list[OverseasIndexSnapshot],
    peripherals: list[GlobalIndexSnapshot],
    rate: RateSignal,
) -> MacroChecklist:
    from .market import concentration_signal

    us_concentration = concentration_signal(overseas)
    us_wobbly = "쏠림 심화" in us_concentration or "동반 하락" in us_concentration

    neg, total = peripheral_negative_count(peripherals)
    peripheral_drain = total > 0 and (neg / total) >= 0.7

    items = [
        ChecklistItem(
            key="rate_breakout",
            label="① 금리: 미국 10년물 국채금리가 최근 1년 고점 돌파",
            triggered=rate.breakout,
            detail=f"현재 {rate.current:.2f}% / 1년 고점 {rate.one_year_high:.2f}%",
        ),
        ChecklistItem(
            key="global_concentration",
            label="② 글로벌 쏠림: 나스닥만 급등 + 다우/S&P500·주변국 부진",
            triggered=us_wobbly or peripheral_drain,
            detail=(
                f"미국 3대 지수: {us_concentration} / "
                f"주변국 {total}개국 중 {neg}개국 하락 전환"
            ),
        ),
        ChecklistItem(
            key="bad_ipo",
            label="③ 부실 대형 IPO의 대규모 물량 출회",
            triggered=False,
            detail="자동 조회 데이터 소스 없음 — 대형 적자기업 IPO 뉴스는 직접 확인 필요",
            automated=False,
        ),
    ]
    return MacroChecklist(items=items)
