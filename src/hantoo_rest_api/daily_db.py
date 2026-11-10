"""일일 리포트 저장소 (SQLite, 레포 루트 `data/reports.db` — 개인 금융정보라 git 제외).

날짜(YYYY-MM-DD)별로 한 번의 실행 결과를 통째로 보관한다. 같은 날짜를 다시 돌리면 덮어쓴다.

- runs: 날짜별 요약(총자산, 매크로 단계, 분석 마크다운, 통계 JSON)
- accounts: 날짜 × 계좌 합계
- positions: 날짜 × 계좌 × 보유 항목
- indicators: 날짜 × 지표 값
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "reports.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  date TEXT PRIMARY KEY, created_at TEXT, total REAL, phase INTEGER, phase_name TEXT,
  phase_reason TEXT, analysis TEXT, engine TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  date TEXT, label TEXT, broker TEXT, total REAL, purchase REAL, pl REAL, cash REAL,
  bond REAL, note TEXT, error TEXT, PRIMARY KEY (date, label)
);
CREATE TABLE IF NOT EXISTS positions (
  date TEXT, account TEXT, kind TEXT, name TEXT, code TEXT, currency TEXT, quantity REAL,
  avg_price REAL, price REAL, purchase REAL, eval REAL, pl REAL, pl_rate REAL
);
CREATE INDEX IF NOT EXISTS pos_date ON positions(date, account);
CREATE TABLE IF NOT EXISTS securities (
  symbol TEXT PRIMARY KEY, name TEXT, code TEXT, currency TEXT, held INTEGER, first_seen TEXT,
  last_seen TEXT
);
CREATE TABLE IF NOT EXISTS prices (
  symbol TEXT, date TEXT, close REAL, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS indicators (
  date TEXT, id TEXT, name TEXT, grp TEXT, macro TEXT, unit TEXT, value REAL, prev REAL,
  change_pct REAL, trend TEXT, as_of TEXT, flag TEXT, error TEXT, PRIMARY KEY (date, id)
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def save_run(date: str, created_at: str, accounts: list[dict], indicators: list, stats: dict,
             analysis: str, engine: str, phase: int | None, phase_name: str, phase_reason: str,
             path: Path = DB_PATH) -> None:
    con = connect(path)
    with con:
        for t in ("runs", "accounts", "positions", "indicators"):
            con.execute(f"DELETE FROM {t} WHERE date=?", (date,))
        con.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
            (date, created_at, stats.get("total", 0), phase, phase_name, phase_reason, analysis, engine,
             json.dumps(stats, ensure_ascii=False)),
        )
        for a in accounts:
            con.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (date, a["label"], a["broker"], a["total"], a["purchase"], a["pl"], a["cash"],
                         a["bond"], a["note"], a["error"]))
            for p in a["positions"]:
                con.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (date, a["label"], p["kind"], p["name"], p["code"], p["currency"], p["quantity"],
                             p["avg_price"], p["price"], p["purchase"], p["eval"], p["pl"], p["pl_rate"]))
        for i in indicators:
            con.execute("INSERT INTO indicators VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (date, i.id, i.name, i.group, i.macro, i.unit, i.value, i.prev, i.change_pct,
                         i.trend, i.as_of, i.flag, i.error))
    con.close()


def list_dates(path: Path = DB_PATH) -> list[str]:
    if not path.exists():
        return []
    con = connect(path)
    rows = [r["date"] for r in con.execute("SELECT date FROM runs ORDER BY date DESC")]
    con.close()
    return rows


def load_run(date: str, path: Path = DB_PATH) -> dict | None:
    con = connect(path)
    run = con.execute("SELECT * FROM runs WHERE date=?", (date,)).fetchone()
    if not run:
        con.close()
        return None
    out = {
        "run": dict(run),
        "stats": json.loads(run["stats"] or "{}"),
        "accounts": [dict(r) for r in con.execute("SELECT * FROM accounts WHERE date=? ORDER BY rowid", (date,))],
        "positions": [dict(r) for r in con.execute("SELECT * FROM positions WHERE date=?", (date,))],
        "indicators": [dict(r) for r in con.execute("SELECT * FROM indicators WHERE date=?", (date,))],
    }
    con.close()
    return out


def total_history(path: Path = DB_PATH) -> list[dict]:
    """날짜별 총자산·계좌별 합계 (추이 차트용)."""
    if not path.exists():
        return []
    con = connect(path)
    rows = [dict(r) for r in con.execute("SELECT date, label, total FROM accounts ORDER BY date")]
    con.close()
    return rows


def previous_run(date: str, path: Path = DB_PATH) -> dict | None:
    """`date` 직전 실행의 요약(총자산·계좌별 합계). 전일 대비 계산용."""
    if not path.exists():
        return None
    con = connect(path)
    run = con.execute("SELECT date, total FROM runs WHERE date<? ORDER BY date DESC LIMIT 1", (date,)).fetchone()
    if not run:
        con.close()
        return None
    accts = {r["label"]: r["total"] for r in con.execute("SELECT label,total FROM accounts WHERE date=?", (run["date"],))}
    con.close()
    return {"date": run["date"], "total": run["total"], "accounts": accts}


# ── 종목·시세 (차트용). 보유/과거 보유 종목의 일봉 종가를 필요한 만큼만 증분 갱신한다 ──


def upsert_securities(rows: list[dict], date: str, path: Path = DB_PATH) -> None:
    """rows: symbol, name, code, currency, held(0/1). 한번이라도 held였던 종목은 계속 남는다."""
    con = connect(path)
    with con:
        for r in rows:
            con.execute(
                "INSERT INTO securities(symbol,name,code,currency,held,first_seen,last_seen) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, held=excluded.held, "
                "last_seen=CASE WHEN excluded.held=1 THEN excluded.last_seen ELSE securities.last_seen END",
                (r["symbol"], r["name"], r["code"], r["currency"], r["held"], date, date),
            )
    con.close()


def last_price_dates(path: Path = DB_PATH) -> dict[str, str]:
    con = connect(path)
    out = {r["symbol"]: r["d"] for r in con.execute("SELECT symbol, MAX(date) d FROM prices GROUP BY symbol")}
    con.close()
    return out


def upsert_prices(rows: list[tuple[str, str, float]], path: Path = DB_PATH) -> None:
    con = connect(path)
    with con:
        con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)", rows)
    con.close()


def list_securities(path: Path = DB_PATH) -> list[dict]:
    if not path.exists():
        return []
    con = connect(path)
    rows = [dict(r) for r in con.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM prices p WHERE p.symbol=s.symbol) n FROM securities s "
        "ORDER BY held DESC, name")]
    con.close()
    return rows


def load_prices(symbol: str, path: Path = DB_PATH) -> list[tuple[str, float]]:
    con = connect(path)
    rows = [(r["date"], r["close"]) for r in con.execute(
        "SELECT date, close FROM prices WHERE symbol=? ORDER BY date", (symbol,))]
    con.close()
    return rows


def known_securities(path: Path = DB_PATH) -> set[str]:
    con = connect(path)
    out = {r["symbol"] for r in con.execute("SELECT symbol FROM securities")}
    con.close()
    return out


def past_position_securities(path: Path = DB_PATH) -> list[dict]:
    """지금까지 저장된 모든 날짜의 보유 종목(주식/ETF) — 과거에 보유했던 종목 복원용."""
    if not path.exists():
        return []
    con = connect(path)
    rows = [dict(r) for r in con.execute(
        "SELECT DISTINCT name, code, currency FROM positions WHERE kind='주식' AND code!=''")]
    con.close()
    return rows


def phase_history(before: str, limit: int = 10, path: Path = DB_PATH) -> list[tuple[str, int]]:
    """`before` 이전 날짜들의 (날짜, 매크로 단계). 사이클 그림의 ○(최근 판단)용, 오래된 순."""
    if not path.exists():
        return []
    con = connect(path)
    rows = [(r["date"], r["phase"]) for r in con.execute(
        "SELECT date, phase FROM runs WHERE date<? AND phase IS NOT NULL ORDER BY date DESC LIMIT ?", (before, limit))]
    con.close()
    return rows[::-1]
