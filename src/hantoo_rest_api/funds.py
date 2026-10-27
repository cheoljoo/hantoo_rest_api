"""KIS Open API가 주지 않는 펀드(퇴직연금 DC 펀드/원리금보장상품 등) 보유 현황을 위한 수동 기록.

레포 루트의 `funds.yaml`(개인 금액이 들어 있어 git 제외, 형식은 `funds.yaml.example`)에
사용자가 직접 적는다. `account_no`로 해당 계좌 탭에만 표시한다.

funds:
  - account_no: "12345678"
    name: "OO TDF2050"
    purchase_amount: 1000000
    eval_amount: 1050000
    as_of: "2026-10-27"
    note: ""
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_FUNDS_PATH = Path(__file__).resolve().parents[2] / "funds.yaml"


@dataclass(frozen=True)
class ManualFund:
    account_no: str
    name: str
    purchase_amount: float
    eval_amount: float
    as_of: dt.date | None = None
    note: str = ""

    @property
    def profit_loss(self) -> float:
        return self.eval_amount - self.purchase_amount

    @property
    def profit_loss_rate(self) -> float:
        return self.profit_loss / self.purchase_amount * 100 if self.purchase_amount else 0.0


def load_manual_funds(path: Path = DEFAULT_FUNDS_PATH) -> list[ManualFund]:
    """`funds.yaml`을 읽는다. 파일이 없으면 빈 목록."""
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[ManualFund] = []
    for item in raw.get("funds") or []:
        as_of = item.get("as_of")
        if isinstance(as_of, str):
            as_of = dt.date.fromisoformat(as_of)
        out.append(
            ManualFund(
                account_no=str(item.get("account_no", "")),
                name=str(item["name"]),
                purchase_amount=float(item.get("purchase_amount") or 0),
                eval_amount=float(item.get("eval_amount") or 0),
                as_of=as_of,
                note=str(item.get("note", "")),
            )
        )
    return out
