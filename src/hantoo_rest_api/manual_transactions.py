"""KIS Open API가 체결내역을 제공하지 않는 계좌(예: 퇴직연금 DC형)를 위해,
사용자가 직접 기록한 매매 내역을 관리한다.

transactions.yaml (레포 루트)에 아래 형식으로 기록한다:

transactions:
  - date: "2026-07-24"
    code: "459580"
    name: "KODEX CD금리액티브(합성)"
    side: "매도"
    quantity: 20
    price: 1073625
    note: "API로 조회되지 않아 수동 기록"
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TRANSACTIONS_PATH = Path(__file__).resolve().parents[2] / "transactions.yaml"


@dataclass(frozen=True)
class ManualTransaction:
    date: dt.date
    code: str
    name: str
    side: str  # "매수" / "매도"
    quantity: int
    price: float
    note: str = ""

    @property
    def amount(self) -> float:
        return self.quantity * self.price


def load_manual_transactions(
    path: Path = DEFAULT_TRANSACTIONS_PATH,
) -> list[ManualTransaction]:
    """transactions.yaml에서 수동 기록된 매매 내역을 읽는다. 파일이 없으면 빈 목록."""
    if not path.exists():
        return []

    data = yaml.safe_load(path.read_text()) or {}
    items = []
    for item in data.get("transactions", []):
        items.append(
            ManualTransaction(
                date=item["date"] if isinstance(item["date"], dt.date) else dt.date.fromisoformat(str(item["date"])),
                code=str(item["code"]),
                name=item.get("name", ""),
                side=item["side"],
                quantity=int(item["quantity"]),
                price=float(item.get("price", 0)),
                note=item.get("note", ""),
            )
        )
    items.sort(key=lambda t: t.date)
    return items


def per_stock_summary(transactions: list[ManualTransaction]) -> list[dict]:
    """종목별 매수/매도 합계를 계산한다."""
    by_code: dict[str, dict] = {}
    for t in transactions:
        s = by_code.setdefault(
            t.code,
            {"code": t.code, "name": t.name, "buy_amount": 0.0, "sell_amount": 0.0, "trade_count": 0},
        )
        if t.side == "매수":
            s["buy_amount"] += t.amount
        elif t.side == "매도":
            s["sell_amount"] += t.amount
        s["trade_count"] += 1
        s["name"] = s["name"] or t.name

    result = list(by_code.values())
    for s in result:
        s["net_amount"] = s["buy_amount"] - s["sell_amount"]
    return result
