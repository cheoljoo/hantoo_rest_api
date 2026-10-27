"""`uv run hantoo-snapshot` — 모든 한투 계좌의 예수금 스냅샷을 오늘자로 기록한다 (cron용)."""

from __future__ import annotations

from .account import get_account_balance
from .account_activity import get_pension_deposit, is_pension_account
from .asset_status import get_asset_status
from .auth import get_access_token
from .config import KisConfig, load_configs
from .deposit_snapshots import record_snapshot


def snapshot_one(cfg: KisConfig, token: str) -> tuple[float, float, float]:
    """(현금, 유가증권평가, 총평가)를 조회한다. 계좌 유형별 규칙은 deposit_snapshots 문서 참고."""
    try:
        bal = get_account_balance(cfg, token)
    except RuntimeError:
        st = get_asset_status(cfg, token)  # 금현물/CMA
        return st.deposit, st.eval_amount, st.total_asset
    s = bal.summary
    if is_pension_account(cfg):
        cash = get_pension_deposit(cfg, token).deposit_total
        return cash, s.securities_eval_amount, s.securities_eval_amount + cash
    return s.next_2day_withdrawable, s.securities_eval_amount, s.total_eval_amount


def main() -> None:
    for cfg in load_configs():
        try:
            cash, sec, total = snapshot_one(cfg, get_access_token(cfg))
        except Exception as e:  # 한 계좌 실패가 나머지 기록을 막지 않게
            print(f"[skip] {cfg.display_label}: {e}")
            continue
        record_snapshot(cfg.account_no, cfg.display_label, cash, sec, total)
        print(f"[ok] {cfg.display_label} 현금 {cash:,.0f} 증권 {sec:,.0f} 총 {total:,.0f}")


if __name__ == "__main__":
    main()
