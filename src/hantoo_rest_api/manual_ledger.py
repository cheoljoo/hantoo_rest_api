"""API가 없는 증권사(미래에셋 등)를 위한 수동 입력 원장.

레포 루트의 `mirae.csv`(개인 금액이 들어 있어 git 제외, 형식은 `mirae.csv.example`)에
구글시트 ALL 탭의 행을 그대로 붙여 넣어 쓴다. 탭(TSV)·쉼표(CSV) 구분 모두 읽고,
금액은 `₩1,116,122` / `-₩679,200` / `1116122` 형식을 모두 받는다.

시트의 한 행은 "산 날짜 시점의 잔고/매매 1건"이다. 같은 종목의 행을 모두 더하면
현재 보유 수량·장부금액이 된다(매도는 수량·금액이 음수). 현재가는 없으므로
평가손익은 계산하지 않고 장부금액(산총합)까지만 보여 준다.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parents[2] / "mirae.csv"


@dataclass(frozen=True)
class LedgerRow:
    broker: str
    kind: str  # 종류: CMA/IMA/현물/IRP/일반
    account: str
    ticker: str
    category: str
    currency: str  # 화폐
    name: str
    price: float  # 산가격 (원화 환산)
    quantity: float
    date: str  # YYYY.MM.DD 그대로
    total: float  # 산총합 (원)
    usd_cost: float = 0.0  # USD매입가 (선택: 달러 종목의 실제 달러 매입단가)


@dataclass(frozen=True)
class LedgerPosition:
    """같은 (종류, 종목)의 행을 합친 현재 보유."""

    kind: str
    ticker: str
    name: str
    currency: str
    quantity: float
    total: float
    avg_usd: float = 0.0  # 달러 매입 평균단가 (행의 USD매입가를 수량 가중평균)

    @property
    def avg_price(self) -> float:
        return self.total / self.quantity if self.quantity else 0.0


def _num(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    neg = "-" in s or (s.startswith("(") and s.endswith(")"))
    digits = re.sub(r"[^\d.]", "", s)
    if not digits:
        return 0.0
    v = float(digits)
    return -v if neg else v


def load_ledger(path: Path = DEFAULT_LEDGER_PATH) -> list[LedgerRow]:
    """원장 파일을 읽는다. 파일이 없으면 빈 목록."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text.strip() else ""
    delim = "\t" if "\t" in first else ","
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=delim):
        if not (r.get("종목") or "").strip():
            continue
        rows.append(
            LedgerRow(
                broker=(r.get("증권사") or "").strip(),
                kind=(r.get("종류") or "").strip(),
                account=(r.get("통장") or "").strip(),
                ticker=(r.get("TICKER") or "").strip(),
                category=(r.get("CATEGORY") or "").strip(),
                currency=(r.get("화폐") or "").strip(),
                name=r["종목"].strip(),
                price=_num(r.get("산가격", "")),
                quantity=_num(r.get("산수량", "")),
                date=(r.get("산날짜") or "").strip(),
                total=_num(r.get("산총합", "")),
                usd_cost=_num(r.get("USD매입가", "")),
            )
        )
    return rows


def aggregate(rows: list[LedgerRow]) -> list[LedgerPosition]:
    """(종류, 종목)별로 수량·장부금액을 합산한다. 수량이 0이 된 종목은 제외."""
    acc: dict[tuple[str, str], list] = {}
    for r in rows:
        key = (r.kind, r.name)
        cur = acc.setdefault(key, [r.ticker, r.currency, 0.0, 0.0, 0.0, 0.0])
        cur[2] += r.quantity
        cur[3] += r.total
        if r.usd_cost and r.quantity > 0:
            cur[4] += r.usd_cost * r.quantity
            cur[5] += r.quantity
    return [
        LedgerPosition(kind=k, ticker=v[0], name=n, currency=v[1], quantity=v[2], total=v[3],
                       avg_usd=v[4] / v[5] if v[5] else 0.0)
        for (k, n), v in acc.items()
        if abs(v[2]) > 1e-9
    ]
