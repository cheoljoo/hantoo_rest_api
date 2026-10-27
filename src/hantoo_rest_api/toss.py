"""토스증권 Open API (https://openapi.tossinvest.com) 보유 종목/체결(주문) 내역 조회.

공식 스펙(OpenAPI JSON, /openapi-docs/latest/openapi.json) 기준:
- 토큰: POST /oauth2/token (form: grant_type=client_credentials, client_id, client_secret), 24시간 유효.
- 계좌: GET /api/v1/accounts → accountSeq. 계좌 단위 호출은 `X-Tossinvest-Account: <accountSeq>` 헤더 필요.
- 보유: GET /api/v1/holdings, 주문 이력: GET /api/v1/orders?status=CLOSED (cursor 페이지네이션),
  매수가능금액: GET /api/v1/buying-power?currency=KRW|USD, 환율: GET /api/v1/exchange-rate.
- 응답은 `{"result": ...}` 래핑, 금액은 문자열이다.

`.env`: TOSS_ID, TOSS_SECRET (+ 선택 TOSS_ACCOUNT_LABEL). 허용 IP를 등록하지 않으면 403이 날 수 있다.
이 모듈은 조회(GET)만 사용하며 주문 API는 호출하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

BASE_URL = "https://openapi.tossinvest.com"
_TOKEN_CACHE = Path.home() / ".cache" / "hantoo_rest_api" / "toss_token.json"


@dataclass(frozen=True)
class TossConfig:
    client_id: str
    client_secret: str
    label: str = ""

    @property
    def display_label(self) -> str:
        return self.label or "토스증권"


def load_toss_config() -> TossConfig | None:
    """`.env`에 TOSS_ID/TOSS_SECRET이 있으면 설정을 돌려주고, 없으면 None."""
    load_dotenv(find_dotenv(usecwd=True))
    cid, sec = os.environ.get("TOSS_ID", "").strip(), os.environ.get("TOSS_SECRET", "").strip()
    if not cid or not sec:
        return None
    return TossConfig(cid, sec, os.environ.get("TOSS_ACCOUNT_LABEL", "").strip())


def get_toss_token(cfg: TossConfig) -> str:
    """액세스 토큰. 파일(0600)에 캐시해 재발급을 줄인다 (키는 client_id 해시)."""
    key = hashlib.sha256(cfg.client_id.encode()).hexdigest()[:16]
    try:
        c = json.loads(_TOKEN_CACHE.read_text())
        if c.get("key") == key and c.get("expires_at", 0) > time.time() + 300:
            return c["token"]
    except (OSError, ValueError):
        pass
    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cfg.client_id,
              "client_secret": cfg.client_secret},
        timeout=15,
    )
    resp.raise_for_status()
    j = resp.json()
    token = j["access_token"]
    try:
        _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE.write_text(json.dumps(
            {"key": key, "token": token, "expires_at": time.time() + int(j.get("expires_in", 3600))}))
        _TOKEN_CACHE.chmod(0o600)
    except OSError:
        pass
    return token


def _get(token: str, path: str, params: dict | None = None, account: int | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if account is not None:
        headers["X-Tossinvest-Account"] = str(account)
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=15)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        raise RuntimeError(f"토스 API 실패 {path}: HTTP {resp.status_code} {err.get('code', '')} {err.get('message', '')}")
    return data.get("result", data)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class TossHolding:
    symbol: str
    name: str
    country: str
    currency: str
    quantity: float
    last_price: float
    avg_price: float
    purchase_amount: float  # 거래통화
    eval_amount: float
    profit_loss: float
    profit_loss_rate: float  # 0.05 = 5%


@dataclass(frozen=True)
class TossOrder:
    order_id: str
    ordered_at: dt.datetime
    symbol: str
    side: str  # BUY/SELL
    order_type: str
    status: str  # FILLED/CANCELED ...
    quantity: float
    filled_quantity: float
    avg_fill_price: float
    filled_amount: float
    commission: float
    tax: float
    currency: str


@dataclass(frozen=True)
class TossAccount:
    account_no: str
    seq: int
    holdings: list[TossHolding]
    cash_krw: float
    cash_usd: float
    usd_krw: float  # 환율
    purchase_krw: float  # 원화 환산 매입금액
    eval_krw: float  # 원화 환산 평가금액

    @property
    def total_krw(self) -> float:
        return self.eval_krw + self.cash_krw + self.cash_usd * self.usd_krw


def get_toss_account(cfg: TossConfig, token: str) -> TossAccount:
    """첫 번째 계좌의 보유 종목·매수가능 현금·환율을 한 번에 조회한다."""
    accts = _get(token, "/api/v1/accounts")
    if not accts:
        raise RuntimeError("토스 계좌가 없습니다")
    a = accts[0]
    seq = int(a["accountSeq"])
    h = _get(token, "/api/v1/holdings", account=seq)
    fx = _f(_get(token, "/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"}).get("rate"))
    holdings = [
        TossHolding(
            symbol=i["symbol"], name=i.get("name", i["symbol"]), country=i.get("marketCountry", ""),
            currency=i.get("currency", ""), quantity=_f(i.get("quantity")),
            last_price=_f(i.get("lastPrice")), avg_price=_f(i.get("averagePurchasePrice")),
            purchase_amount=_f(i["marketValue"].get("purchaseAmount")),
            eval_amount=_f(i["marketValue"].get("amount")),
            profit_loss=_f(i["profitLoss"].get("amount")),
            profit_loss_rate=_f(i["profitLoss"].get("rate")),
        )
        for i in h.get("items", [])
    ]
    def krw(d: dict) -> float:
        return _f(d.get("krw")) + _f(d.get("usd")) * fx
    cash = {c: _f(_get(token, "/api/v1/buying-power", {"currency": c}, account=seq).get("cashBuyingPower"))
            for c in ("KRW", "USD")}
    return TossAccount(
        account_no=str(a.get("accountNo", "")), seq=seq, holdings=holdings,
        cash_krw=cash["KRW"], cash_usd=cash["USD"], usd_krw=fx,
        purchase_krw=krw(h.get("totalPurchaseAmount", {})),
        eval_krw=krw(h.get("marketValue", {}).get("amount", {})),
    )


def get_toss_orders(cfg: TossConfig, token: str, seq: int) -> list[TossOrder]:
    """종료된 주문(체결/취소) 전체. 체결된 것만 추리려면 status == 'FILLED'로 거른다."""
    out: list[TossOrder] = []
    cursor = None
    for _ in range(50):
        params = {"status": "CLOSED", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = _get(token, "/api/v1/orders", params, account=seq)
        for o in r.get("orders", []):
            ex = o.get("execution") or {}
            out.append(TossOrder(
                order_id=o["orderId"], ordered_at=dt.datetime.fromisoformat(o["orderedAt"]),
                symbol=o["symbol"], side=o["side"], order_type=o.get("orderType", ""),
                status=o.get("status", ""), quantity=_f(o.get("quantity")),
                filled_quantity=_f(ex.get("filledQuantity")),
                avg_fill_price=_f(ex.get("averageFilledPrice")), filled_amount=_f(ex.get("filledAmount")),
                commission=_f(ex.get("commission")), tax=_f(ex.get("tax")),
                currency=o.get("currency", ""),
            ))
        if not r.get("hasNext") or not r.get("nextCursor"):
            break
        cursor = r["nextCursor"]
    return out
