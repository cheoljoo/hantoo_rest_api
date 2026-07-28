import datetime as dt

from .account import get_account_balance
from .auth import get_access_token
from .config import load_config
from .price import get_candles_for_codes
from .watchlist import load_watchlist


def _print_holdings(balance) -> None:
    print("\n=== 보유 종목 ===")
    if not balance.holdings:
        print("보유 중인 종목이 없습니다.")
    for h in balance.holdings:
        print(
            f"{h.name}({h.code}) | 수량 {h.quantity:,}주 | "
            f"매입가 {h.avg_purchase_price:,.0f} | 현재가 {h.current_price:,.0f} | "
            f"평가금액 {h.eval_amount:,.0f} | 평가손익 {h.eval_profit_loss:,.0f} "
            f"({h.eval_profit_loss_rate:.2f}%)"
        )

    s = balance.summary
    print("\n=== 계좌 요약 ===")
    print(f"예수금총액        : {s.deposit_total:,.0f}")
    print(f"유가증권평가금액  : {s.securities_eval_amount:,.0f}")
    print(f"총평가금액        : {s.total_eval_amount:,.0f}")
    print(f"매입금액합계      : {s.total_purchase_amount:,.0f}")
    print(f"평가손익합계      : {s.total_eval_profit_loss:,.0f}")


def main() -> None:
    cfg = load_config()
    access_token = get_access_token(cfg)

    balance = get_account_balance(cfg, access_token)
    _print_holdings(balance)

    watchlist = load_watchlist()
    print("\n=== 관심종목 ===")
    if not watchlist:
        print("watchlist.yaml에 등록된 관심종목이 없습니다.")
    for item in watchlist:
        print(f"{item.name}({item.code})")

    # 보유종목 + 관심종목을 합쳐 최근 캔들(일봉) 데이터가 정상 조회되는지 확인
    codes = list(
        dict.fromkeys(
            [h.code for h in balance.holdings] + [w.code for w in watchlist]
        )
    )
    if codes:
        end_date = dt.date.today()
        start_date = end_date - dt.timedelta(days=14)
        candles_by_code = get_candles_for_codes(
            cfg, access_token, codes, start_date=start_date, end_date=end_date
        )

        print("\n=== 최근 일봉(캔들) 확인 ===")
        for code, candles in candles_by_code.items():
            if not candles:
                print(f"{code}: 조회된 캔들 데이터가 없습니다.")
                continue
            last = candles[-1]
            print(
                f"{code} 최근({last.date}) 시가 {last.open:,.0f} / 고가 {last.high:,.0f} / "
                f"저가 {last.low:,.0f} / 종가 {last.close:,.0f} / 거래량 {last.volume:,}"
            )
