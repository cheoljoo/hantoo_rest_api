import sys
import tomllib
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from hantoo_rest_api.account import get_account_balance
from hantoo_rest_api.auth import get_access_token
from hantoo_rest_api.config import load_config
from hantoo_rest_api.global_indices import (
    get_global_index_snapshots,
    peripheral_negative_count,
    peripheral_signal,
)
from hantoo_rest_api.market import (
    concentration_signal,
    get_index_snapshots,
    get_net_flow_ranking,
    get_overseas_index_snapshots,
)
from hantoo_rest_api.price import get_daily_candles
from hantoo_rest_api.watchlist import load_watchlist

import datetime as dt

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"
DEFAULT_PASSWORD = "2222"
ICON_PATH = Path(__file__).parent / "assets" / "kis_icon.png"
GITHUB_URL = "https://github.com/cheoljoo/hantoo_rest_api"
OWNER_NAME = "이철주"
OWNER_EMAIL = "cheoljoo@gmail.com"


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


@st.cache_data(ttl=60 * 5)
def _index_snapshots():
    cfg, token = _access_token()
    return get_index_snapshots(cfg, token)


@st.cache_data(ttl=60 * 5)
def _net_flow_ranking():
    cfg, token = _access_token()
    return get_net_flow_ranking(cfg, token, top_n=10)


@st.cache_data(ttl=60 * 5)
def _overseas_index_snapshots():
    cfg, token = _access_token()
    return get_overseas_index_snapshots(cfg, token)


@st.cache_data(ttl=60 * 10)
def _global_index_snapshots():
    return get_global_index_snapshots()


def render_market_overview():
    st.subheader("📈 시장 동향 (Macro)")

    try:
        snapshots = _index_snapshots()
    except Exception as e:
        st.warning(f"국내 지수 조회 실패 (KIS 서버 일시 오류일 수 있습니다): {e}")
    else:
        cols = st.columns(len(snapshots))
        for col, s in zip(cols, snapshots):
            col.metric(
                s.name,
                f"{s.current:,.2f}",
                f"{s.change:+,.2f} ({s.change_rate:+.2f}%)",
            )
            col.caption(
                f"상승 {s.advancing} · 하락 {s.declining} · 보합 {s.unchanged}  →  "
                f"**{s.trend_signal}** (시장폭: {s.breadth_signal})"
            )

    st.markdown("**🌐 글로벌 쏠림(연끌) 신호 — 미국 3대 지수**")
    try:
        overseas = _overseas_index_snapshots()
    except Exception as e:
        st.warning(f"미국 지수 조회 실패 (KIS 서버 일시 오류일 수 있습니다): {e}")
    else:
        ov_cols = st.columns(len(overseas))
        for col, s in zip(ov_cols, overseas):
            col.metric(s.name, f"{s.current:,.2f}", f"{s.change:+,.2f} ({s.change_rate:+.2f}%)")
        st.caption(
            f"→ **{concentration_signal(overseas)}**  \n"
            "(닷컴버블 막판처럼 나스닥만 급등하고 다우/S&P500이 부진하면 시장 자금이 "
            "소진되고 있다는 경고 신호로 해석 — 자세한 배경은 아래 '시장 방향성 판단 기준' 참고)"
        )

    st.markdown("**🌏 주변국 증시 현황 (글로벌 연끌 확인용)**")
    try:
        peripherals = _global_index_snapshots()
    except Exception as e:
        st.warning(f"주변국 지수 조회 실패: {e}")
    else:
        neg_count, total = peripheral_negative_count(peripherals)
        p_cols = st.columns(4)
        for i, s in enumerate(peripherals):
            col = p_cols[i % 4]
            col.metric(s.name, f"{s.current:,.2f}", f"{s.change_rate:+.2f}%")
        st.caption(
            f"→ 미국 제외 {total}개국 중 **{neg_count}개국 하락 전환** — "
            f"**{peripheral_signal(peripherals)}**  \n"
            "(주변국 다수가 동시에 마이너스로 돌아서면 자금이 대장주로만 쏠리는 "
            "'글로벌 연끌'이 완성 단계에 가까워졌다는 신호)  \n"
            "데이터 출처: Yahoo Finance(yfinance) — KIS Open API는 미국 3대 지수 외 해외지수를 지원하지 않음"
        )

    with st.expander("외국인/기관 순매수 상위 종목 (전체 시장)"):
        try:
            flows = _net_flow_ranking()
        except Exception as e:
            st.warning(f"외국인/기관 매매동향 조회 실패: {e}")
            flows = []
        if not flows:
            st.info("조회된 데이터가 없습니다.")
        else:
            st.dataframe(
                [
                    {
                        "종목명": f.name,
                        "종목코드": f.code,
                        "현재가": f.current_price,
                        "등락률(%)": f.change_rate,
                        "외국인 순매수(주)": f.foreign_net_qty,
                        "기관 순매수(주)": f.institution_net_qty,
                    }
                    for f in flows
                ],
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "장중 집계 기준(증권사 직원 수기 입력, ±10분 오차 가능) — 외국인·기관이 활발히 순매수하는 "
                "종목이 많을수록 매수 우위 심리로 해석할 수 있습니다."
            )

    with st.expander("📖 시장 방향성 판단 기준 (김효진 박사 정리)"):
        st.markdown(
            """
대세 상승장이 끝나고 약세장(지수 30~50% 하락)으로 전환될 때 나타나는 대표 신호 3가지:

1. **주도주 역전**: 경쟁사가 핵심 기술 격차를 줄이고 점유율을 빼앗아 이익이 훼손되는 경우 (가장 거리가 먼 시나리오)
2. **글로벌 연끌(쏠림)**: 대장주(나스닥)만 급등하고 다우/S&P500·유럽·주변국 증시에서 자금이 빠져나갈 때 — 위 "글로벌 쏠림 신호"가 이 부분을 확인하는 지표
3. **금리 인상 누적 (가장 결정적)**: 금리가 이전 고점을 돌파할 때 밸류에이션이 압착되며 급락 — 현재는 금리 인상 초입이 아니라 동결/인하 논의 국면이라 근거 부족

전체 배경과 실전 체크리스트(삼박자 신호, IPO 품질, 신용융자 금리 등)는
[`docs/bear_market_signals_kim_hyojin.md`](https://github.com/cheoljoo/hantoo_rest_api/blob/main/docs/bear_market_signals_kim_hyojin.md) 참고.
            """
        )


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

    now = dt.datetime.now()
    st.title("한국투자증권 계좌 대시보드")
    st.caption(f"기준일 {now.date()} · 생성 {now.strftime('%Y-%m-%dT%H:%M:%S')}")
    st.caption(
        f"Owner: {OWNER_NAME} <{OWNER_EMAIL}> · [GitHub ↗]({GITHUB_URL})"
    )

    render_market_overview()

    balance = render_holdings()
    watchlist = render_watchlist()

    code_to_name: dict[str, str] = {}
    for h in balance.holdings:
        code_to_name.setdefault(h.code, h.name)
    for w in watchlist:
        code_to_name.setdefault(w.code, w.name or w.code)
    render_candles(list(code_to_name.items()))

    with st.expander("ℹ️ 관리 정보"):
        st.markdown(
            f"""
- **GitHub 저장소**: [{GITHUB_URL}]({GITHUB_URL})
- **비밀번호 설정 파일**: `web/.streamlit/secrets.toml` (git에는 커밋되지 않음, 서버에서 직접 수정)
- **Owner**: {OWNER_NAME} <{OWNER_EMAIL}>
            """
        )


if __name__ == "__main__":
    main()
