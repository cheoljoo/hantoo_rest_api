"""예수금 일일 스냅샷 + 입출금/배당/이자 추정.

한투 Open API에는 입출금·배당·이자 내역을 주는 API가 없어서(키움과 다름), 계좌별 현금을
매일 한 줄씩 `deposit_snapshots.csv`(레포 루트, git 제외)에 쌓고, 연속한 두 스냅샷 사이
현금 변화에서 그 기간의 체결 순액(매도-매수)을 뺀 나머지를 "입출금·배당·이자 추정"으로
본다. 수수료/세금은 반영하지 않아 몇십~몇백 원 잔차가 생길 수 있다.

기록하는 현금(`cash`)은 매매가 즉시 반영되는 값이어야 체결과 같은 날 비교가 된다:
- 일반 계좌: D+2 예수금(`prvs_rcdl_excc_amt`, 가수도정산금액) — 위탁계좌에서 삼성전자 매수 당일
  250,000 → 41,494로 바로 줄어드는 것을 확인함
- 퇴직연금 DC: 퇴직연금 예수금조회의 예수금총액 (결제 시점 반영 방식은 미확인)
- 금현물: 자산현황 API의 예수금 (체결 API가 없어 추정 불가, 변화량만 기록)

스냅샷을 놓친 날이 있으면 그 구간은 여러 날이 합쳐진 변화로 계산된다.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "deposit_snapshots.csv"
_FIELDS = ["date", "account_no", "label", "cash", "securities_eval", "total_eval", "recorded_at"]


@dataclass(frozen=True)
class DepositSnapshot:
    date: dt.date
    account_no: str
    label: str
    cash: float
    securities_eval: float
    total_eval: float
    recorded_at: str


def load_snapshots(path: Path = SNAPSHOT_PATH) -> list[DepositSnapshot]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [
            DepositSnapshot(
                date=dt.date.fromisoformat(r["date"]),
                account_no=r["account_no"],
                label=r["label"],
                cash=float(r["cash"]),
                securities_eval=float(r["securities_eval"] or 0),
                total_eval=float(r["total_eval"] or 0),
                recorded_at=r["recorded_at"],
            )
            for r in csv.DictReader(f)
        ]


def record_snapshot(
    account_no: str,
    label: str,
    cash: float,
    securities_eval: float = 0.0,
    total_eval: float = 0.0,
    *,
    path: Path = SNAPSHOT_PATH,
    today: dt.date | None = None,
) -> None:
    """오늘자 (계좌별) 스냅샷을 기록한다. 같은 날 이미 있으면 최신 값으로 덮어쓴다."""
    today = today or dt.date.today()
    rows = [
        s for s in load_snapshots(path)
        if not (s.date == today and s.account_no == account_no)
    ]
    rows.append(
        DepositSnapshot(
            today, account_no, label, cash, securities_eval, total_eval,
            dt.datetime.now().isoformat(timespec="seconds"),
        )
    )
    rows.sort(key=lambda s: (s.account_no, s.date))
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for s in rows:
            w.writerow(
                {
                    "date": s.date.isoformat(), "account_no": s.account_no, "label": s.label,
                    "cash": s.cash, "securities_eval": s.securities_eval,
                    "total_eval": s.total_eval, "recorded_at": s.recorded_at,
                }
            )
    tmp.replace(path)


@dataclass(frozen=True)
class CashFlowEstimate:
    """연속한 두 스냅샷 구간 하나의 현금 변화 분해."""

    start: dt.date  # 이전 스냅샷 일자 (이 날 이후 ~ end 까지의 변화)
    end: dt.date
    cash_before: float
    cash_after: float
    change: float
    trade_net: float | None  # 구간 체결 순액(매도-매수). 체결 데이터가 없으면 None
    residual: float | None  # change - trade_net = 입출금·배당·이자 추정


def infer_cash_flows(
    snapshots: list[DepositSnapshot], executions: list | None
) -> list[CashFlowEstimate]:
    """한 계좌의 스냅샷(일자순)과 체결 목록(`.date/.side/.amount`)으로 구간별 추정을 만든다."""
    snaps = sorted(snapshots, key=lambda s: s.date)
    out: list[CashFlowEstimate] = []
    for a, b in zip(snaps, snaps[1:]):
        net = None
        if executions is not None:
            net = sum(
                (e.amount if e.side == "매도" else -e.amount)
                for e in executions
                if a.date < e.date <= b.date
            )
        change = b.cash - a.cash
        out.append(
            CashFlowEstimate(
                a.date, b.date, a.cash, b.cash, change, net,
                None if net is None else change - net,
            )
        )
    return out
