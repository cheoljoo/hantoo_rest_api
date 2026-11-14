"""일일 리포트용: 모든 계좌(한투·키움·토스·미래에셋 수동)를 같은 모양의 dict로 모은다.

웹 모드 1(실시간)이 화면에 그리는 값과 같은 출처(API)에서 가져오되, Streamlit 없이
cron에서 돌 수 있도록 순수 함수로 만들었다. 계좌 하나가 실패해도 `error`만 채워 두고
나머지는 계속 진행한다.

총평가 = 주식 평가 + 현금 + 채권 매수금액 (+ 해외주식 평가 원화 환산). 웹의
"시트 ↔ API 대조" 표와 같은 정의다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .account import get_account_balance
from .account_activity import get_pension_deposit, is_pension_account
from .asset_status import get_asset_status, get_gold_price
from .auth import get_access_token
from .bond import get_bond_balance
from .config import KisConfig, load_configs
from .funds import load_manual_funds
from .kiwoom import get_kiwoom_balance, load_kiwoom_configs
from .manual_ledger import aggregate, load_ledger
from .overseas import get_overseas_balance
from .toss import get_toss_account, get_toss_token, load_toss_config


@dataclass
class Position:
    kind: str  # 주식/채권/펀드/금/현금/기타
    name: str
    code: str = ""
    currency: str = "KRW"
    quantity: float = 0.0
    avg_price: float = 0.0
    price: float = 0.0
    purchase: float = 0.0  # 원화
    eval: float = 0.0  # 원화
    pl: float = 0.0
    pl_rate: float = 0.0  # %


@dataclass
class AccountSnap:
    label: str
    broker: str
    total: float = 0.0
    purchase: float = 0.0
    pl: float = 0.0
    cash: float = 0.0
    bond: float = 0.0
    note: str = ""
    error: str = ""
    positions: list[Position] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _kis(cfg: KisConfig, fx: float) -> AccountSnap:
    snap = AccountSnap(label=cfg.display_label, broker="한투")
    token = get_access_token(cfg)
    try:
        bal = get_account_balance(cfg, token)
    except RuntimeError:
        bal = None
    if bal is not None:
        s = bal.summary
        cash = get_pension_deposit(cfg, token).deposit_total if is_pension_account(cfg) else s.next_2day_withdrawable
        snap.cash = cash
        snap.purchase = s.total_purchase_amount
        snap.pl = s.total_eval_profit_loss
        sec = s.securities_eval_amount
        for h in bal.holdings:
            snap.positions.append(Position(
                kind="주식", name=h.name, code=h.code, quantity=h.quantity,
                avg_price=h.avg_purchase_price, price=h.current_price, purchase=h.purchase_amount,
                eval=h.eval_amount, pl=h.eval_profit_loss, pl_rate=h.eval_profit_loss_rate))
    else:  # 금현물/CMA
        st = get_asset_status(cfg, token)
        sec, snap.cash = st.eval_amount, st.deposit
        snap.purchase, snap.pl = st.purchase_amount, st.eval_profit_loss
        for c in st.categories:
            if c.name == "예수금+CMA":
                continue
            kind = "금" if c.name == "금현물" else "기타"
            name = c.name
            qty = price = 0.0
            if kind == "금" and c.eval_amount > 0:
                try:
                    price = get_gold_price(cfg, token)
                    qty = round(c.eval_amount / price)
                    name = "금현물 (수량 추정)"
                except Exception:
                    pass
            snap.positions.append(Position(
                kind=kind, name=name, quantity=qty, price=price, purchase=c.purchase_amount,
                avg_price=c.purchase_amount / qty if qty else 0.0, eval=c.eval_amount,
                pl=c.eval_profit_loss,
                pl_rate=c.eval_profit_loss / c.purchase_amount * 100 if c.purchase_amount else 0.0))
    # 채권
    try:
        bonds = get_bond_balance(cfg, token)
    except Exception:
        bonds = []
    for b in bonds:
        snap.bond += b.buy_amount
        snap.positions.append(Position(kind="채권", name=b.name, code=b.code, quantity=b.quantity,
                                       purchase=b.buy_amount, eval=b.buy_amount))
    # 해외주식 (보유 종목 평가만 — 예수금은 위 현금에 이미 포함)
    try:
        ovs = get_overseas_balance(cfg, token)
    except Exception:
        ovs = None
    if ovs and ovs.holdings:
        # 증권사가 계산한 원화 환산은 환율이 제각각이라 쓰지 않고, 해외 보유는 외화 시세 × 당일 환율로 다시 계산한다.
        ovs_eval = ovs_purchase = 0.0
        for h in ovs.holdings:
            r = fx if h.currency == "USD" else (
                ovs.summary.eval_amount_krw / sum(x.eval_amount for x in ovs.holdings))
            ev, pu = h.quantity * h.current_price * r, h.quantity * h.avg_price * r
            ovs_eval += ev
            ovs_purchase += pu
            snap.positions.append(Position(
                kind="주식", name=h.name, code=h.code, currency=h.currency, quantity=h.quantity,
                avg_price=h.avg_price, price=h.current_price, purchase=pu, eval=ev, pl=ev - pu,
                pl_rate=h.eval_profit_loss_rate))
        sec += ovs_eval
        snap.purchase += ovs_purchase
        snap.pl += ovs_eval - ovs_purchase
    # 수동 기록 펀드
    for f in load_manual_funds():
        if f.account_no == cfg.account_no:
            sec += f.eval_amount
            snap.purchase += f.purchase_amount
            snap.pl += f.profit_loss
            snap.positions.append(Position(kind="펀드", name=f.name, purchase=f.purchase_amount,
                                           eval=f.eval_amount, pl=f.profit_loss, pl_rate=f.profit_loss_rate))
    snap.total = sec + snap.cash + snap.bond
    if snap.cash:
        snap.positions.append(Position(kind="현금", name="예수금·현금", purchase=snap.cash, eval=snap.cash))
    return snap


def _kiwoom(cfg) -> AccountSnap:
    snap = AccountSnap(label=f"키움 {cfg.display_label}", broker="키움")
    bal = get_kiwoom_balance(cfg)
    s = bal.summary
    snap.cash, snap.purchase, snap.pl = s.deposit, s.purchase_amount, s.eval_profit_loss
    snap.total = s.eval_amount + s.deposit
    for h in bal.holdings:
        snap.positions.append(Position(
            kind="주식", name=h.name, code=h.code, quantity=h.quantity, avg_price=h.avg_price,
            price=h.current_price, purchase=h.purchase_amount, eval=h.eval_amount,
            pl=h.eval_profit_loss, pl_rate=h.profit_rate))
    if snap.cash:
        snap.positions.append(Position(kind="현금", name="예수금", purchase=snap.cash, eval=snap.cash))
    return snap


def _toss(cfg, fx: float) -> AccountSnap:
    snap = AccountSnap(label=cfg.display_label, broker="토스")
    acct = get_toss_account(cfg, get_toss_token(cfg))
    snap.cash = acct.cash_krw + acct.cash_usd * fx
    snap.note = f"USD는 당일 환율 {fx:,.1f}원으로 환산 (토스 환율 {acct.usd_krw:,.1f}원은 사용하지 않음)"
    for h in acct.holdings:
        r = fx if h.currency == "USD" else 1.0
        snap.positions.append(Position(
            kind="주식", name=h.name, code=h.symbol, currency=h.currency, quantity=h.quantity,
            avg_price=h.avg_price, price=h.last_price, purchase=h.purchase_amount * r,
            eval=h.eval_amount * r, pl=h.profit_loss * r, pl_rate=h.profit_loss_rate * 100))
    snap.purchase = sum(p.purchase for p in snap.positions)
    snap.pl = sum(p.pl for p in snap.positions)
    snap.total = sum(p.eval for p in snap.positions) + snap.cash
    if snap.cash:
        snap.positions.append(Position(kind="현금", name="예수금·현금", purchase=snap.cash, eval=snap.cash))
    return snap


def _mirae(fx: float, us_prices: dict[str, float]) -> AccountSnap | None:
    rows = load_ledger()
    if not rows:
        return None
    snap = AccountSnap(label="미래에셋 (수동)", broker="미래에셋",
                       note=f"수동 입력 장부금액 기준 (기준일 {max(r.date for r in rows)}). 미국 주식(티커 있는 달러 종목)만 시세×당일 환율로 재평가")
    for p in aggregate(rows):
        cash = any(k in p.name for k in ("예수금", "RP", "CMA"))
        kind = "현금" if cash else "기타" if p.kind in ("IMA", "IRP") else "금" if p.kind == "현물" else "주식"
        usd = p.currency == "달러"
        # 티커가 있으면 시세, 티커 없는 달러 상품(RP 등)은 달러 매입단가를 현재가로 보고 당일 환율만 반영한다
        price = (us_prices.get(p.ticker, 0.0) if p.ticker else p.avg_usd) if usd else 0.0
        ev = p.quantity * price * fx if price else p.total  # 미국 주식은 시세 × 당일 환율로 재평가
        # 달러 매입단가가 있으면 매입금액도 달러 매입가 × 당일 환율 (다른 계좌의 미국 주식과 같은 방식)
        cost = p.quantity * p.avg_usd * fx if price and p.avg_usd else p.total
        avg = p.avg_usd if price and p.avg_usd else p.avg_price
        snap.positions.append(Position(
            kind=kind, name=p.name, code=p.ticker, currency="USD" if usd else "KRW",
            quantity=p.quantity, avg_price=avg, price=price, purchase=cost, eval=ev,
            pl=ev - cost if price else 0.0, pl_rate=(ev / cost - 1) * 100 if price and cost else 0.0))
        snap.total += ev
        snap.purchase += cost
        snap.pl += ev - cost if price else 0.0
        if cash:
            snap.cash += p.total
    return snap


CASH_LIKE_KEYWORDS = ("CD금리",)  # 사용자 기준 현금 취급 상품 (예: KODEX CD금리액티브)


def _cash_like(snap: AccountSnap) -> None:
    """현금으로 취급하는 ETF를 '현금' 항목으로 옮기고 예수금·현금에 더한다 (총평가는 이미 포함돼 있어 그대로)."""
    for p in snap.positions:
        if p.kind == "주식" and any(k in p.name for k in CASH_LIKE_KEYWORDS):
            p.kind = "현금"
            snap.cash += p.eval


def collect_accounts(fx: float, us_prices: dict[str, float] | None = None) -> list[AccountSnap]:
    """`fx`: 당일 원/달러. 모든 계좌의 미국 주식을 같은 환율로 원화 환산한다."""
    out: list[AccountSnap] = []
    jobs = [(c.display_label, "한투", lambda c=c: _kis(c, fx)) for c in load_configs()]
    jobs += [(f"키움 {c.display_label}", "키움", lambda c=c: _kiwoom(c)) for c in load_kiwoom_configs()]
    toss = load_toss_config()
    if toss:
        jobs.append((toss.display_label, "토스", lambda: _toss(toss, fx)))
    for label, broker, fn in jobs:
        try:
            out.append(fn())
        except Exception as e:
            out.append(AccountSnap(label=label, broker=broker, error=str(e)[:300]))
    mirae = _mirae(fx, us_prices or {})
    if mirae:
        out.append(mirae)
    for snap in out:
        _cash_like(snap)
    return out
