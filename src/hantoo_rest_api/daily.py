"""`uv run hantoo-daily [--no-ai] [--date YYYY-MM-DD]` — 매일 새벽 2시(cron) 일일 리포트 생성.

1) 모든 계좌 수집  2) 지표 수집  3) 통계 + AI 분석  4) `data/reports.db`에 날짜별 저장.
웹 "모드 2"는 이 DB만 읽는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time

from dotenv import find_dotenv, load_dotenv

from . import daily_db
from .daily_accounts import collect_accounts
from .daily_analysis import analyze, compute_stats
from .daily_prices import FX_SYMBOL, latest_closes, update_prices
from .indicators import collect_indicators


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true", help="AI 분석 없이 수집·통계만 저장")
    ap.add_argument("--scan-trades", action="store_true", help="체결 이력에서 과거 보유 종목까지 복원 (기본: 일요일만)")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args()

    t0 = time.time()
    print(f"[{args.date}] 지표 수집...")
    indicators = collect_indicators()
    bad = [i.id for i in indicators if i.error]
    print(f"  {len(indicators) - len(bad)}/{len(indicators)} 성공" + (f", 실패: {bad}" if bad else ""))
    fx = next((i.value for i in indicators if i.id == "usdkrw" and i.value), None)
    if not fx:
        raise SystemExit("원/달러 환율을 얻지 못해 중단합니다")
    print(f"계좌 수집... (당일 환율 {fx:,.2f})")
    from .manual_ledger import load_ledger
    mirae_us = sorted({r.ticker for r in load_ledger() if r.currency == "달러" and r.ticker})
    accounts = [a.to_dict() for a in collect_accounts(fx, latest_closes(mirae_us))]
    for a in accounts:
        print(f"  {'[err]' if a['error'] else '[ok] '} {a['label']} {a['total']:,.0f} {a['error']}")
    scan = args.scan_trades or dt.date.fromisoformat(args.date).weekday() == 6
    n, rows = update_prices(accounts, args.date, scan_trades=scan)
    print(f"종목 시세 갱신: {n}종목, {rows}행 (체결이력 스캔 {'함' if scan else '안 함'})")

    stats = compute_stats(accounts, daily_db.previous_run(args.date))
    stats["fx_usdkrw"] = fx
    if args.no_ai:
        prev = daily_db.load_run(args.date)  # 같은 날짜의 기존 AI 분석은 지우지 않는다
        r = prev["run"] if prev else {}
        analysis = r.get("analysis") or "(--no-ai: AI 분석 생략)"
        engine, phase = r.get("engine") or "none", r.get("phase")
        name, reason = r.get("phase_name") or "", r.get("phase_reason") or ""
    else:
        print("AI 분석...")
        analysis, engine, phase, name, reason = analyze(args.date, accounts, indicators, stats)
        print(f"  엔진 {engine}, 매크로 단계 {phase} {name}")
    daily_db.save_run(args.date, dt.datetime.now().isoformat(timespec="seconds"), accounts, indicators,
                      stats, analysis, engine, phase, name, reason)
    print(f"저장 완료 {daily_db.DB_PATH} ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    main()
