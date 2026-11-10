"""보유·과거 보유 종목(주식/ETF)의 일봉 종가를 DB에 쌓는다 (일일 리포트 차트용).

- 종목 목록: 오늘 보유 + DB에 남은 과거 보유 + (일요일/`--scan-trades`) 증권사 체결 이력의 종목.
- 시세 출처: Yahoo Finance. 국내 6자리 코드는 `.KS`(없으면 `.KQ`), 미국은 티커 그대로.
- 증분 갱신: DB에 있는 마지막 날짜 이후(여유 7일)만 받고, 처음 보는 종목은 5년치를 받는다.
- 환산용으로 `KRW=X`(원/달러)도 같이 쌓는다.
"""

from __future__ import annotations

import datetime as dt
import re

from . import daily_db

FX_SYMBOL = "KRW=X"
_KR = re.compile(r"^[0-9A-Z]{6}$")


def yf_symbol(code: str, currency: str) -> str | None:
    code = code.strip()
    if not code:
        return None
    if currency == "KRW":
        return f"{code}.KS" if _KR.match(code) and any(c.isdigit() for c in code) else None
    return code.replace(".", "-")


def latest_closes(symbols: list[str]) -> dict[str, float]:
    """미국 티커 최신 종가 (미래에셋 수동 원장 재평가용)."""
    import yfinance as yf

    if not symbols:
        return {}
    df = yf.download(symbols, period="7d", interval="1d", progress=False, auto_adjust=False, group_by="ticker")
    out = {}
    for s in symbols:
        try:
            col = (df[s]["Close"] if len(symbols) > 1 else df["Close"]).dropna()
            if len(col):
                out[s] = float(col.iloc[-1])
        except Exception:
            pass
    return out


def _past_trade_securities(accounts_cfg_scan: bool) -> list[dict]:
    """증권사 체결 이력에 나온 종목 (이미 정리해 판 종목 포함). 느려서 주 1회만."""
    if not accounts_cfg_scan:
        return []
    from .account_activity import get_full_executions
    from .auth import get_access_token
    from .config import load_configs
    from .kiwoom import get_kiwoom_history, load_kiwoom_configs
    from .toss import get_toss_orders, get_toss_token, get_toss_account, load_toss_config

    out: list[dict] = []
    for cfg in load_configs():
        try:
            for e in get_full_executions(cfg, get_access_token(cfg)):
                out.append({"name": e.stock_name, "code": e.stock_code, "currency": "KRW"})
        except Exception:
            pass
    for k in load_kiwoom_configs():
        try:
            for r in get_kiwoom_history(k):
                if r.is_trade and r.code:
                    out.append({"name": r.name, "code": r.code, "currency": "KRW"})
        except Exception:
            pass
    toss = load_toss_config()
    if toss:
        try:
            tok = get_toss_token(toss)
            seq = get_toss_account(toss, tok).seq
            for o in get_toss_orders(toss, tok, seq):
                if o.status == "FILLED":
                    out.append({"name": o.symbol, "code": o.symbol, "currency": o.currency})
        except Exception:
            pass
    return out


def update_prices(accounts: list[dict], date: str, scan_trades: bool = False) -> tuple[int, int]:
    """(종목 수, 새로 저장한 시세 행 수). 실패한 종목은 건너뛴다."""
    import yfinance as yf

    held = {}
    for a in accounts:
        for p in a["positions"]:
            if p["kind"] == "주식" and p["code"] and p["quantity"] > 0:
                sym = yf_symbol(p["code"], "USD" if p["currency"] == "USD" else "KRW")
                if sym:
                    held[sym] = {"symbol": sym, "name": p["name"], "code": p["code"], "currency": p["currency"], "held": 1}
    universe = dict(held)
    extra = daily_db.past_position_securities() + _past_trade_securities(scan_trades)
    for e in extra:
        sym = yf_symbol(e["code"], "USD" if e["currency"] == "USD" else "KRW")
        if sym and sym not in universe:
            universe[sym] = {"symbol": sym, "name": e["name"], "code": e["code"], "currency": e["currency"], "held": 0}
    daily_db.upsert_securities(list(universe.values()), date)
    # 직전 실행에서 보유였다가 지금은 아닌 종목도 universe에 들어오므로(held=0) 별도 처리 불필요

    symbols = list(universe) + [FX_SYMBOL]
    last = daily_db.last_price_dates()
    new = [s for s in symbols if s not in last]
    old = [s for s in symbols if s in last]
    rows: list[tuple[str, str, float]] = []

    def pull(syms: list[str], **kw):
        if not syms:
            return
        try:
            df = yf.download(syms, interval="1d", progress=False, auto_adjust=False, group_by="ticker", **kw)
        except Exception:
            return
        for s in syms:
            try:
                col = (df[s]["Close"] if len(syms) > 1 else df["Close"]).dropna()
            except Exception:
                continue
            rows.extend((s, d.strftime("%Y-%m-%d"), float(v)) for d, v in col.items())

    pull(new, period="5y")
    if old:
        start = min(dt.date.fromisoformat(last[s]) for s in old) - dt.timedelta(days=7)
        pull(old, start=start.isoformat())
    # 국내 .KS에 데이터가 없는 종목은 코스닥(.KQ)으로 재시도
    got = {r[0] for r in rows} | set(last)
    retry = [s for s in universe if s.endswith(".KS") and s not in got]
    if retry:
        before = len(rows)
        pull([s.replace(".KS", ".KQ") for s in retry], period="5y")
        for s in retry:
            kq = s.replace(".KS", ".KQ")
            if any(r[0] == kq for r in rows):
                daily_db.upsert_securities([{**universe[s], "symbol": kq}], date)
                rows = [(kq if r[0] == kq else r[0], r[1], r[2]) for r in rows]
    daily_db.upsert_prices(rows)
    return len(universe), len(rows)
