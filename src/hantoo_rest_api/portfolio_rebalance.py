"""목표 포트폴리오(TDF 펀드 + ETF + 금)를 분할매수하며 리밸런싱하는 계산 로직.

한 번에 11,000,000원씩 투자하며, 목표 비중은 다음과 같다 (합계 110% —
정규화된 100% 비중이 아니라, 회차당 투자금 단위를 "20% = 2,000,000원"으로
고정해 각 자산에 배분할 비율을 그대로 퍼센트로 쓴 것):

- 신한마음편한TDF2050 A-e: 20%
- 신한마음편한TDF2035 A-e: 20%
- KODEX 미국나스닥100: 20%
- KODEX 미국S&P500: 40%
- ACE KRX금현물(KRX 금현물 가격을 추종하는 ETF, 종목코드 411060): 10%

**금 관련 참고**: 실제 KRX 금현물시장(금 실물 스팟 거래)은 별도의 "금현물계좌"가
있어야 거래 가능하고 일반 위탁계좌/KIS Open API로는 접근할 수 없다. 그래서
KRX 금현물 가격을 그대로 추종하는 상장 ETF(ACE KRX금현물, 411060)를 대리
자산으로 사용한다 — 실제 금현물 가격과 완전히 같지는 않지만(운용보수 등 미세한
괴리 존재) 매우 근접하게 움직인다.

**펀드(TDF) 가격 관련 참고**: TDF는 거래소에 상장되지 않은 일반 개방형 펀드라
KIS Open API로 시세 조회가 불가능하다(KIS는 국내주식/ETF/채권/선물옵션만 다루고
펀드 기준가는 다루지 않음). 그래서 기준가(NAV)는 `fund_nav.csv`에 사용자가
직접 입력해서 관리한다. 펀드는 보통 "금액 매수"(예: 100만원어치) 방식이라
정수 좌수 개념이 ETF와 다르다.

CD금리액티브(기존 보유 현금성 자산)는 이 5종 리밸런싱 대상에는 포함하지
않고(`weight_pct=0`), 참고용 정보로만 다룬다.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PURCHASES_PATH = Path(__file__).resolve().parents[2] / "portfolio_purchases.csv"
DEFAULT_FUND_NAV_PATH = Path(__file__).resolve().parents[2] / "fund_nav.csv"

CONTRIBUTION_PER_ROUND = 11_000_000.0


@dataclass(frozen=True)
class TargetAsset:
    """리밸런싱 대상(또는 참고 대상) 자산 하나."""

    key: str
    name: str
    asset_type: str  # "etf" 또는 "fund"
    code: str | None  # KRX 종목코드 (ETF만 해당, 펀드는 None)
    weight_pct: float  # 예: 20.0 (=20%). 0.0이면 리밸런싱 대상에서 제외(참고용).


TARGET_ASSETS: list[TargetAsset] = [
    TargetAsset("tdf2050", "신한마음편한TDF2050 A-e", "fund", None, 20.0),
    TargetAsset("tdf2035", "신한마음편한TDF2035 A-e", "fund", None, 20.0),
    TargetAsset("nasdaq100", "KODEX 미국나스닥100", "etf", "379810", 20.0),
    TargetAsset("sp500", "KODEX 미국S&P500", "etf", "379800", 40.0),
    TargetAsset("gold", "ACE KRX금현물", "etf", "411060", 10.0),
]

TOTAL_WEIGHT_PCT = sum(a.weight_pct for a in TARGET_ASSETS)  # 110.0

# 기존에 보유 중인 현금성 자산. 리밸런싱 계산에는 포함하지 않고 수익률 비교표에만 쓴다.
EXISTING_CASH_PARK = TargetAsset("cd_active", "KODEX CD금리액티브", "etf", "459580", 0.0)

ALL_ASSETS: list[TargetAsset] = TARGET_ASSETS + [EXISTING_CASH_PARK]
ASSETS_BY_KEY: dict[str, TargetAsset] = {a.key: a for a in ALL_ASSETS}


@dataclass(frozen=True)
class PurchaseRecord:
    """실제로 매수한 기록 1건 (`portfolio_purchases.csv`의 한 행)."""

    date: dt.date
    asset_key: str
    quantity: float  # ETF: 매수 수량(주). 펀드: 좌수(모르면 0으로 두고 금액만 기록해도 됨)
    price: float  # 매수 단가 (ETF 주가 또는 펀드 기준가)
    amount: float  # 실제 매수 금액(원)
    note: str = ""


def load_purchases(path: Path = DEFAULT_PURCHASES_PATH) -> list[PurchaseRecord]:
    """`portfolio_purchases.csv`에서 매수 기록을 읽어 날짜 오름차순으로 반환한다.

    이 파일은 실제 매수 금액을 담고 있어 `.gitignore`에 등록해 커밋되지 않는다
    (형식 예시는 `portfolio_purchases.csv.example` 참고). 파일이 없으면(아직
    한 번도 안 산 경우) 빈 목록을 반환한다.
    """
    if not path.exists():
        return []
    records = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append(
                PurchaseRecord(
                    date=dt.date.fromisoformat(row["date"]),
                    asset_key=row["asset_key"],
                    quantity=float(row.get("quantity") or 0),
                    price=float(row.get("price") or 0),
                    amount=float(row["amount"]),
                    note=row.get("note", ""),
                )
            )
    records.sort(key=lambda r: r.date)
    return records


def append_purchase(record: PurchaseRecord, path: Path = DEFAULT_PURCHASES_PATH) -> None:
    """매수 기록 1건을 CSV 끝에 추가한다 (파일이 없으면 헤더와 함께 새로 만든다)."""
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "asset_key", "quantity", "price", "amount", "note"])
        writer.writerow(
            [
                record.date.isoformat(),
                record.asset_key,
                record.quantity,
                record.price,
                record.amount,
                record.note,
            ]
        )


def load_fund_nav(path: Path = DEFAULT_FUND_NAV_PATH) -> dict[str, tuple[float, dt.date]]:
    """펀드별 최신 기준가(NAV)를 `fund_nav.csv`에서 읽는다.

    KIS Open API가 펀드 시세를 제공하지 않아, 사용자가 앱/펀드 사이트에서 확인한
    기준가를 직접 입력해 관리하는 파일이다. 반환값은 `{asset_key: (nav, 기준일)}`.
    """
    if not path.exists():
        return {}
    result: dict[str, tuple[float, dt.date]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["asset_key"]] = (float(row["nav"]), dt.date.fromisoformat(row["as_of"]))
    return result


def save_fund_nav(
    asset_key: str, nav: float, as_of: dt.date, path: Path = DEFAULT_FUND_NAV_PATH
) -> None:
    """펀드 기준가를 갱신한다 (같은 asset_key가 있으면 덮어쓰고, 없으면 추가)."""
    existing = load_fund_nav(path)
    existing[asset_key] = (nav, as_of)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asset_key", "nav", "as_of"])
        for key, (n, d) in existing.items():
            writer.writerow([key, n, d.isoformat()])


@dataclass(frozen=True)
class AssetPosition:
    """자산 하나의 누적 매수 현황과 현재 평가금액."""

    asset: TargetAsset
    quantity: float
    invested_amount: float
    current_price: float | None
    current_value: float | None

    @property
    def profit_loss(self) -> float | None:
        if self.current_value is None:
            return None
        return self.current_value - self.invested_amount

    @property
    def profit_loss_rate(self) -> float | None:
        if self.current_value is None or self.invested_amount <= 0:
            return None
        return self.profit_loss / self.invested_amount * 100


def build_positions(
    purchases: list[PurchaseRecord], current_prices: dict[str, float]
) -> list[AssetPosition]:
    """자산별 누적 매수량/매입금액과, 현재가(또는 펀드 기준가) 기준 평가금액을 계산한다."""
    positions = []
    for asset in ALL_ASSETS:
        asset_purchases = [p for p in purchases if p.asset_key == asset.key]
        quantity = sum(p.quantity for p in asset_purchases)
        invested = sum(p.amount for p in asset_purchases)
        current_price = current_prices.get(asset.key)
        current_value = quantity * current_price if current_price is not None else None
        positions.append(
            AssetPosition(
                asset=asset,
                quantity=quantity,
                invested_amount=invested,
                current_price=current_price,
                current_value=current_value,
            )
        )
    return positions


@dataclass(frozen=True)
class RebalanceItem:
    """리밸런싱 계획에서 자산 하나에 대한 매수 계획."""

    asset: TargetAsset
    current_value: float
    target_value: float
    buy_amount: float
    price: float | None
    buy_quantity: int | None  # ETF만 정수 주식수, 펀드는 None(금액만 투자)
    actual_cost: float
    leftover: float


def compute_rebalance_plan(
    positions: list[AssetPosition], *, contribution: float = CONTRIBUTION_PER_ROUND
) -> list[RebalanceItem]:
    """다음 회차 투자금(`contribution`)을 목표 비중에 맞춰 5개 자산에 배분한다.

    이미 보유한 자산의 현재 평가금액을 반영해 "목표 비중 × 미래(현재+투자금)
    총자산" 만큼을 목표금액으로 잡는 표준적인 '매도 없는 리밸런싱' 방식이다.
    이미 목표 비중을 초과한 자산은 이번 회차에 추가 매수액이 음수로 계산되는데,
    그 경우 0으로 clamp하고(팔지 않음), 부족한 자산들에는 투자금을 비례
    배분해서 `contribution` 전액이 항상 소진되도록 한다. ETF는 소수 주식을 살
    수 없으므로 정수 주식수로 내림(`//`) 처리하고 남는 잔액(`leftover`)을 함께
    보여준다 — 펀드는 금액 단위로 매수하므로 잔액 개념이 없다(항상 0).
    """
    by_key = {p.asset.key: p for p in positions}
    target_positions = [by_key[a.key] for a in TARGET_ASSETS]

    total_current = sum(p.current_value or 0 for p in target_positions)
    total_future = total_current + contribution

    raw_buys: dict[str, float] = {}
    for p in target_positions:
        target_value = total_future * (p.asset.weight_pct / TOTAL_WEIGHT_PCT)
        raw_buys[p.asset.key] = max(0.0, target_value - (p.current_value or 0))

    raw_total = sum(raw_buys.values())
    scale = contribution / raw_total if raw_total > 0 else 0.0

    items = []
    for p in target_positions:
        buy_amount = raw_buys[p.asset.key] * scale
        target_value = total_future * (p.asset.weight_pct / TOTAL_WEIGHT_PCT)
        if p.asset.asset_type == "etf" and p.current_price:
            qty = int(buy_amount // p.current_price)
            actual_cost = qty * p.current_price
            leftover = buy_amount - actual_cost
        else:
            qty = None
            actual_cost = buy_amount
            leftover = 0.0
        items.append(
            RebalanceItem(
                asset=p.asset,
                current_value=p.current_value or 0,
                target_value=target_value,
                buy_amount=buy_amount,
                price=p.current_price,
                buy_quantity=qty,
                actual_cost=actual_cost,
                leftover=leftover,
            )
        )
    return items
