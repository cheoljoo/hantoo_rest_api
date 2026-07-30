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
    """미국 10년물 국채금리가 최근 1년 고점을 돌파했는지 여부.

    김효진 박사의 논리: 금리 인상 "초입"에는 오히려 주가가 오르지만, 금리가
    "이전 고점을 넘어서는 순간" 밸류에이션이 압착되며 급락한다 — 즉 금리
    수준 자체보다 "전고점 돌파 여부"가 트리거라서, 절대 수치가 아니라
    최근 1년 고점 대비 위치로 판단한다.
    """

    current: float
    one_year_high: float

    @property
    def breakout(self) -> bool:
        """현재 금리가 최근 1년 고점 이상이면 True (전고점 돌파)."""
        return self.current >= self.one_year_high


def get_rate_signal() -> RateSignal:
    """yfinance `^TNX`(CBOE 10년물 국채수익률 지수, 실제 금리의 10배 값)로
    미국 10년물 국채수익률의 현재값과 최근 1년 고점을 조회한다.

    KIS Open API에는 미국 국채금리를 직접 조회하는 엔드포인트가 없어(국내
    채권/선물옵션 위주), 해외 지수와 마찬가지로 yfinance를 보조 데이터
    소스로 사용한다.
    """
    hist = yf.Ticker("^TNX").history(period="1y")
    closes = hist["Close"].dropna()
    return RateSignal(current=float(closes.iloc[-1]), one_year_high=float(closes.max()))


@dataclass(frozen=True)
class ChecklistItem:
    """체크리스트 항목 하나.

    Attributes:
        key: 코드에서 항목을 식별하기 위한 슬러그.
        label: 화면에 표시할 항목 설명.
        triggered: 이 신호가 "발동"했는지 여부 (automated=False면 의미 없음, 항상 False).
        detail: 판단 근거가 된 실제 수치/상태를 설명하는 문자열.
        automated: 데이터로 자동 판정 가능한 항목이면 True, 사람이 직접
            확인해야 하는 항목(현재는 "③ 부실 IPO")이면 False.
    """

    key: str
    label: str
    triggered: bool
    detail: str
    automated: bool = True


@dataclass(frozen=True)
class MacroChecklist:
    """`build_macro_checklist`의 결과. `automated=True`인 항목만 집계해 종합 판정을 낸다."""

    items: list[ChecklistItem]

    @property
    def triggered_count(self) -> int:
        """자동 판정 가능한 항목 중 실제로 발동한 개수."""
        return sum(1 for i in self.items if i.automated and i.triggered)

    @property
    def automated_count(self) -> int:
        """자동 판정 가능한 항목의 총 개수 (현재 구현 기준 2개: 금리, 글로벌 쏠림)."""
        return sum(1 for i in self.items if i.automated)

    @property
    def verdict(self) -> str:
        """발동된 자동 신호 개수에 따른 3단계 종합 판정.

        0개: 정상, 1개: 주의, 2개 이상: 경고. 임계값은 "삼박자가 다 맞아야
        확실한 경고"라는 원문 취지를 반영한 단순 규칙이며, 통계적으로
        검증된 임계값은 아니다.
        """
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
    """미리 조회해둔 스냅샷들을 조합해 삼박자 체크리스트를 만든다.

    이 함수 자체는 네트워크 호출을 하지 않는다(호출자가 각 스냅샷을 미리
    조회해 넘겨줌) — Streamlit 캐싱 경계(`@st.cache_data`)와 맞물려, 지수
    조회 자체는 각각 독립적으로 캐시되고 이 함수는 순수 계산만 담당하도록
    분리했다. `concentration_signal`을 함수 내부에서 지연 import하는 이유는
    `market.py`가 이미 이 모듈을 참조하지 않아 순환 import는 아니지만,
    `market`의 다른 무거운 의존성을 이 모듈 로드 시점에 강제하지 않기 위함이다.
    """
    from .market import concentration_signal

    us_concentration = concentration_signal(overseas)
    us_wobbly = "쏠림 심화" in us_concentration or "동반 하락" in us_concentration
    # 주의: 문자열에 "쏠림"만 포함되는지로 검사하면 "고른 흐름 (지수 간 쏠림
    # 없음)"도 오탐(false positive)으로 걸린다 — 반드시 "쏠림 심화"처럼
    # 구체적인 트리거 문구로 매칭할 것 (한 번 실제로 이 버그가 있었음).

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
