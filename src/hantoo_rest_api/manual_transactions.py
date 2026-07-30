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
    """`transactions.yaml`의 항목 하나를 나타내는 사용자 직접 기록 매매 1건."""

    date: dt.date
    code: str
    name: str
    side: str  # "매수" / "매도"
    quantity: int
    price: float
    note: str = ""

    @property
    def amount(self) -> float:
        """거래대금 = 수량 × 단가. 수수료/세금은 반영하지 않은 단순 계산이다."""
        return self.quantity * self.price


def load_manual_transactions(
    path: Path = DEFAULT_TRANSACTIONS_PATH,
) -> list[ManualTransaction]:
    """`transactions.yaml`에서 수동 기록된 매매 내역을 읽어 날짜 오름차순으로 반환한다.

    파일이 없으면(아직 아무것도 기록하지 않은 최초 상태) 빈 목록을 반환한다 —
    이 파일은 실제 거래 금액을 담고 있어 `.gitignore`에 등록해 커밋되지 않도록
    했으므로(공개 저장소에서 개인 금융정보 노출 방지), 새로 clone한 환경에는
    당연히 파일이 없을 수 있다. 형식 예시는 `transactions.yaml.example` 참고.
    `date`는 YAML이 이미 date 객체로 파싱해주는 경우와 문자열로 남는 경우
    (따옴표로 감싼 경우 등)를 모두 지원하기 위해 타입을 분기해서 처리한다.
    """
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
    """종목코드별로 매수금액/매도금액/순매수금액/매매횟수를 합산한다.

    dict 리스트를 반환하는 이유는 이 함수의 결과가 Streamlit의 `st.dataframe`에
    바로 넘겨지기 좋은 형태이기 때문이다(dataclass보다 컬럼명을 자유롭게 조정하기
    쉬움). 종목명은 여러 건 중 처음 발견된 비어있지 않은 값을 사용한다.
    """
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
