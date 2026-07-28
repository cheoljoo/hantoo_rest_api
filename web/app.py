import sys
import tomllib
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from hantoo_rest_api.account import get_account_balance
from hantoo_rest_api.auth import get_access_token
from hantoo_rest_api.config import load_config
from hantoo_rest_api.price import get_daily_candles
from hantoo_rest_api.watchlist import load_watchlist

import datetime as dt

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"
DEFAULT_PASSWORD = "2222"
ICON_PATH = Path(__file__).parent / "assets" / "kis_icon.png"


def load_password() -> str:
    """이 저장소는 public이라 비밀번호를 소스에 두지 않고
    web/.streamlit/secrets.toml(git에 커밋되지 않음)에 둔다. clone 직후처럼
    이 파일이 아직 없으면 최초 접속 시 기본값으로 자동 생성한다."""
    if not SECRETS_PATH.exists():
        SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRETS_PATH.write_text(f'password = "{DEFAULT_PASSWORD}"\n')
        print(
            f"[app] {SECRETS_PATH}가 없어 기본 비밀번호로 새로 만듦 — 필요하면 직접 수정하세요.",
            file=sys.stderr,
            flush=True,
        )
    secrets = tomllib.loads(SECRETS_PATH.read_text())
    return secrets.get("password", DEFAULT_PASSWORD)


PASSWORD = load_password()


def check_password() -> bool:
    if st.session_state.get("authed"):
        return True
    st.title("hantoo 접근")
    with st.form("password_form"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("확인")
    if submitted:
        if pw == PASSWORD:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False


@st.cache_resource(ttl=60 * 60 * 6)
def _access_token():
    cfg = load_config()
    return cfg, get_access_token(cfg)


@st.cache_data(ttl=60)
def _account_balance():
    cfg, token = _access_token()
    return get_account_balance(cfg, token)


@st.cache_data(ttl=60 * 5)
def _candles(stock_code: str, days: int):
    cfg, token = _access_token()
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days)
    return get_daily_candles(cfg, token, stock_code, start_date=start_date, end_date=end_date)


def render_holdings():
    st.subheader("보유 종목")
    balance = _account_balance()
    if not balance.holdings:
        st.info("보유 중인 종목이 없습니다.")
    else:
        st.dataframe(
            [
                {
                    "종목명": h.name,
                    "종목코드": h.code,
                    "수량": h.quantity,
                    "매입가": h.avg_purchase_price,
                    "현재가": h.current_price,
                    "평가금액": h.eval_amount,
                    "평가손익": h.eval_profit_loss,
                    "손익률(%)": h.eval_profit_loss_rate,
                }
                for h in balance.holdings
            ],
            width="stretch",
            hide_index=True,
        )

    s = balance.summary
    col1, col2, col3 = st.columns(3)
    col1.metric("예수금총액", f"{s.deposit_total:,.0f}")
    col2.metric("총평가금액", f"{s.total_eval_amount:,.0f}")
    col3.metric("평가손익합계", f"{s.total_eval_profit_loss:,.0f}")

    return balance


WATCHLIST_PATH = Path(__file__).parent.parent / "watchlist.yaml"


def render_watchlist():
    st.subheader("관심종목")
    watchlist = load_watchlist(WATCHLIST_PATH)
    if not watchlist:
        st.info("watchlist.yaml에 등록된 관심종목이 없습니다.")
    return watchlist


def render_candles(codes: list[tuple[str, str]]):
    st.subheader("캔들 차트")
    if not codes:
        st.info("표시할 종목이 없습니다.")
        return

    days = st.slider("조회 기간(일)", min_value=30, max_value=365, value=90, step=30)
    labels = [f"{name}({code})" for code, name in codes]
    selected = st.selectbox("종목 선택", labels)
    code, name = codes[labels.index(selected)]

    candles = _candles(code, days)
    if not candles:
        st.warning(f"{name}({code}) 캔들 데이터가 없습니다.")
        return

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=[c.date for c in candles],
                open=[c.open for c in candles],
                high=[c.high for c in candles],
                low=[c.low for c in candles],
                close=[c.close for c in candles],
                name=name,
            )
        ]
    )
    fig.update_layout(
        title=f"{name}({code})",
        xaxis_title="날짜",
        yaxis_title="가격",
        xaxis_rangeslider_visible=False,
        height=500,
    )
    st.plotly_chart(fig, width="stretch")


def main():
    st.set_page_config(page_title="hantoo", page_icon=str(ICON_PATH), layout="wide")
    if not check_password():
        return

    st.title("한국투자증권 계좌 대시보드")

    balance = render_holdings()
    watchlist = render_watchlist()

    code_to_name: dict[str, str] = {}
    for h in balance.holdings:
        code_to_name.setdefault(h.code, h.name)
    for w in watchlist:
        code_to_name.setdefault(w.code, w.name or w.code)
    render_candles(list(code_to_name.items()))


if __name__ == "__main__":
    main()
