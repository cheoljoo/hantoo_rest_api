"""KIS 계좌/시장 데이터를 보여주는 Streamlit 웹 대시보드.

`hantoo_rest_api` 라이브러리(루트의 `src/`)를 워크스페이스 의존성으로 사용해
계좌 조회, 시장 지표, 매매 스타일 진단, 캔들차트를 한 화면에 모아 보여준다.
systemd(`hantooweb.service`) + nginx 리버스 프록시로 `psncs.iptime.org/hantoo/`에
배포되어 있다. 캐시(`@st.cache_data`/`@st.cache_resource`)로 KIS API 호출
빈도를 낮추고, 각 섹션을 개별 `try/except`로 감싸 한 API가 실패해도 나머지
섹션은 정상 렌더링되도록 설계했다(예: 지수 API가 500 에러를 내도 보유종목
섹션은 계속 보인다).
"""

import sys
import tomllib
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from hantoo_rest_api.account import get_account_balance
from hantoo_rest_api.account_activity import (
    diversification_score,
    get_pension_deposit,
    get_recent_executions,
    investment_style_signal,
    is_pension_account,
    summarize_trading_activity,
)
from hantoo_rest_api.auth import get_access_token
from hantoo_rest_api.config import KisConfig, load_configs
from hantoo_rest_api.global_indices import (
    get_global_index_snapshots,
    peripheral_negative_count,
    peripheral_signal,
)
from hantoo_rest_api.macro_checklist import build_macro_checklist, get_rate_signal
from hantoo_rest_api.manual_transactions import (
    filter_by_account,
    load_manual_transactions,
    per_stock_summary,
)
from hantoo_rest_api.market import (
    concentration_signal,
    get_index_snapshots,
    get_net_flow_ranking,
    get_overseas_index_snapshots,
)
from hantoo_rest_api.portfolio_rebalance import (
    CONTRIBUTION_PER_ROUND,
    EXISTING_CASH_PARK,
    TARGET_ASSETS,
    PurchaseRecord,
    append_purchase,
    build_positions,
    compute_rebalance_plan,
    load_fund_nav,
    load_purchases,
    save_fund_nav,
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
    """비밀번호 게이트. 인증 성공 시 `st.session_state["authed"]`를 세워 다음
    렌더부터는 폼을 건너뛴다. 인증 전에는 항상 False를 반환해 `main()`이
    나머지 화면을 그리지 않고 조기 종료하도록 한다.
    """
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


# 아래 `_*` 함수들은 KIS/외부 API 호출을 감싼 캐시 레이어다. `@st.cache_resource`는
# 세션 간에 값 자체(토큰 문자열)를 공유해도 안전한 리소스에, `@st.cache_data`는
# 매번 새로 복사해도 되는 순수 데이터에 사용한다 — Streamlit 캐싱 규칙을 그대로 따름.
# TTL은 API 성격에 맞춰 다르게 뒀다: 토큰(6시간, 실제 만료는 auth.py가 별도 관리),
# 실시간성이 중요한 계좌잔고/예수금(60초), 나머지 시세성 데이터(5~30분).


@st.cache_resource(ttl=60 * 60 * 6)
def _access_token(cfg: KisConfig) -> str:
    """`get_access_token(cfg)`의 결과를 계좌(cfg)별로 세션 전체에서 공유 캐시한다.

    `get_access_token` 자체도 파일 캐싱을 하지만, 그와 별개로 Streamlit
    프로세스 안에서 매 위젯 상호작용(리런)마다 캐시 파일을 여는 오버헤드를
    없애기 위해 한 번 더 캐시한다. `KisConfig`는 frozen dataclass라 그
    자체를 캐시 키로 써도(값 기반 해시) 안전하다 — 계좌가 여러 개여도
    각 계좌(cfg)마다 독립된 캐시 항목을 갖는다.
    """
    return get_access_token(cfg)


@st.cache_data(ttl=60)
def _account_balance(cfg: KisConfig):
    """`get_account_balance` 캐시. 60초 TTL — 계좌 잔고는 실시간성이 가장
    중요한 데이터라 다른 시세성 데이터보다 짧게 캐시한다."""
    token = _access_token(cfg)
    return get_account_balance(cfg, token)


@st.cache_data(ttl=60 * 5)
def _candles(cfg: KisConfig, stock_code: str, days: int):
    """`get_daily_candles` 캐시. `(cfg, stock_code, days)` 조합별로 별도
    캐시되므로 캔들차트의 종목 선택 셀렉트박스를 바꿔도 이전에 조회한
    종목은 재요청하지 않는다."""
    token = _access_token(cfg)
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days)
    return get_daily_candles(cfg, token, stock_code, start_date=start_date, end_date=end_date)


@st.cache_data(ttl=60 * 5)
def _index_snapshots(cfg: KisConfig):
    """`get_index_snapshots`(코스피/코스닥) 캐시. 시장 전체 데이터라 계좌와
    무관하지만, 유효한 토큰이 필요해 대표 계좌(`cfg`, 보통 첫 번째 계좌)의
    자격증명을 빌려 쓴다."""
    token = _access_token(cfg)
    return get_index_snapshots(cfg, token)


@st.cache_data(ttl=60 * 5)
def _net_flow_ranking(cfg: KisConfig):
    """`get_net_flow_ranking`(외국인/기관 순매수 상위 종목) 캐시."""
    token = _access_token(cfg)
    return get_net_flow_ranking(cfg, token, top_n=10)


@st.cache_data(ttl=60 * 5)
def _overseas_index_snapshots(cfg: KisConfig):
    """`get_overseas_index_snapshots`(다우/나스닥/S&P500) 캐시."""
    token = _access_token(cfg)
    return get_overseas_index_snapshots(cfg, token)


@st.cache_data(ttl=60 * 10)
def _global_index_snapshots():
    """`get_global_index_snapshots`(yfinance 기반 주변국 8개국 지수) 캐시.

    KIS API를 쓰지 않는 유일한 캐시 함수라 `_access_token()`이 필요 없다.
    TTL을 다른 KIS 데이터보다 길게(10분) 잡은 이유는 yfinance 호출이
    상대적으로 느리고(8개 티커 병렬 다운로드), 주변국 지수는 분 단위로
    바뀌어도 대시보드 판단에 큰 영향이 없기 때문이다.
    """
    return get_global_index_snapshots()


@st.cache_data(ttl=60 * 30)
def _recent_executions(cfg: KisConfig, days: int):
    """`get_recent_executions` 캐시. 체결내역은 자주 바뀌지 않아 30분으로 길게 캐시."""
    token = _access_token(cfg)
    return get_recent_executions(cfg, token, days=days)


@st.cache_data(ttl=60)
def _pension_deposit(cfg: KisConfig):
    """`get_pension_deposit` 캐시. 퇴직연금 계좌는 일반 잔고조회의 예수금
    필드가 0으로 나와(`account_activity` 모듈 docstring 참고) 이 전용 API로
    보완한다. 예수금은 실시간성이 중요해 60초로 짧게 캐시."""
    token = _access_token(cfg)
    return get_pension_deposit(cfg, token)


@st.cache_data(ttl=60 * 30)
def _rate_signal():
    """`get_rate_signal`(미국 10년물 금리) 캐시. 하루에도 크게 안 바뀌는
    거시 지표라 30분으로 길게 캐시한다."""
    return get_rate_signal()


def render_market_overview(cfg: KisConfig):
    """"📈 시장 동향 (Macro)" 섹션 전체를 렌더링한다.

    시장 전체 데이터라 특정 계좌에 종속되지 않지만, KIS API 호출에는 유효한
    토큰이 필요하므로 `cfg`(보통 계좌 목록의 첫 번째, 대표 계좌)의 자격증명을
    빌려 쓴다 — 계좌를 여러 개 등록했어도 이 섹션은 계좌별로 반복 렌더링하지
    않고 한 번만 그린다.

    구성(위→아래): 코스피/코스닥 지수+시장폭 → 미국 3대 지수 쏠림 신호 →
    주변국 8개국 지수(글로벌 연끌 확인용) → 위 셋을 조합한 삼박자 약세장
    체크리스트 → 외국인/기관 순매수 랭킹(접이식) → 판단 기준 설명(접이식).
    각 하위 블록은 독립적인 `try/except`로 감싸, 예를 들어 미국 지수 API가
    실패해도 국내 지수·주변국 지수는 정상 표시되고, 체크리스트만 "계산 불가"로
    표시된다(`overseas`/`peripherals`가 None이면 체크리스트를 건너뜀).
    """
    st.subheader("📈 시장 동향 (Macro)")

    try:
        snapshots = _index_snapshots(cfg)
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
    overseas = None
    try:
        overseas = _overseas_index_snapshots(cfg)
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
    peripherals = None
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

    st.markdown("**✅ 삼박자 약세장 체크리스트 (금리 · 글로벌 쏠림 · 부실 IPO)**")
    if overseas is None or peripherals is None:
        st.info("위 지표 조회가 실패해 체크리스트를 계산할 수 없습니다.")
    else:
        try:
            rate = _rate_signal()
        except Exception as e:
            st.warning(f"미국 10년물 금리 조회 실패: {e}")
        else:
            checklist = build_macro_checklist(overseas, peripherals, rate)
            for item in checklist.items:
                mark = "🔴" if item.triggered else ("⚪" if not item.automated else "🟢")
                st.write(f"{mark} {item.label}  \n　　{item.detail}")
            st.markdown(f"### {checklist.verdict}")
            st.caption(
                "①②는 실시간 데이터로 자동 판정, ③은 대형 IPO 뉴스를 직접 확인해야 합니다. "
                "삼박자가 모두 충족되면 욕심을 버리고 3~5등분 기계적 분할매도를, 신호가 없으면 "
                "분할매수를 검토하는 것이 김효진 박사의 원칙입니다."
            )

    with st.expander("외국인/기관 순매수 상위 종목 (전체 시장)"):
        try:
            flows = _net_flow_ranking(cfg)
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


def render_holdings(cfg: KisConfig):
    """"보유 종목" 섹션(표 + 계좌 요약 지표)을 렌더링하고, 이후 렌더 함수들이
    재사용할 수 있도록 조회한 `AccountBalance`를 반환한다 (조회 실패 시 None).

    계좌를 여러 개 등록했을 때 한 계좌의 인증 실패/API 오류가 다른 계좌 탭까지
    끌고 내려가지 않도록 여기서 예외를 잡는다 — 잡지 않으면 `st.tabs` 안에서
    발생한 예외가 스크립트 전체 실행을 중단시켜 다른 탭도 같이 깨진다.
    """
    st.subheader("보유 종목")
    try:
        balance = _account_balance(cfg)
    except Exception as e:
        st.error(f"계좌 조회 실패 ({cfg.display_label}): {e}")
        return None
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
            key=f"holdings_table_{cfg.account_no}",
        )

    s = balance.summary
    col1, col2, col3 = st.columns(3)
    col1.metric("총평가금액", f"{s.total_eval_amount:,.0f}")
    col2.metric("평가손익합계", f"{s.total_eval_profit_loss:,.0f}")

    st.markdown("**💰 예수금 (D / D+1 / D+2)**")
    if is_pension_account(cfg):
        try:
            deposit = _pension_deposit(cfg)
        except Exception as e:
            col3.metric("현금성 자산 (CMA)", "조회 실패")
            st.warning(f"퇴직연금 예수금 조회 실패: {e}")
        else:
            # 퇴직연금(DC형) 계좌는 일반 잔고조회 API가 이 계좌의 현금(예수금)을
            # 아예 인식하지 못해(D/D+1이 항상 0) 그 현금은 별도로 "현금성 자산"으로
            # 표시하고, D/D+1/D+2는 일반 잔고조회 API가 주는 값을 그대로(가공 없이)
            # 보여준다 — D+2는 매매 결제로 추가 반영되는 금액이라 현금과는 성격이
            # 다른 별개의 값이라 합산하지 않는다(사용자 확인).
            col3.metric("현금성 자산 (CMA)", f"{deposit.deposit_total:,.0f}")
            d_col1, d_col2, d_col3 = st.columns(3)
            d_col1.metric("D (예수금총액)", f"{s.deposit_total:,.0f}")
            d_col2.metric("D+1 (익일 인출가능)", f"{s.next_day_withdrawable:,.0f}")
            d_col3.metric("D+2 (결제예정금액)", f"{s.next_2day_withdrawable:,.0f}")
            st.caption(
                "퇴직연금(DC형) 계좌의 현금은 일반 잔고조회 API에 안 잡혀 전용 "
                "예수금조회 API 값을 '현금성 자산'으로 표시합니다. D/D+1/D+2는 "
                "일반 잔고조회 API 값 그대로이며, D+2는 최근 매매가 결제되며 "
                "추가로 반영될 금액(현금성 자산과는 별개)입니다."
            )
    else:
        col3.metric("현금성 자산 (CMA)", f"{s.cma_eval_amount:,.0f}")
        st.caption(
            "국내 주식 결제가 T+2(매매일+2영업일)로 이뤄지는 것을 반영한 3단계 예수금입니다."
        )
        d_col1, d_col2, d_col3 = st.columns(3)
        d_col1.metric("D (예수금총액)", f"{s.deposit_total:,.0f}")
        d_col2.metric("D+1 (익일 인출가능)", f"{s.next_day_withdrawable:,.0f}")
        d_col3.metric("D+2 (최종 인출가능)", f"{s.next_2day_withdrawable:,.0f}")

    return balance


def render_investment_style(cfg: KisConfig, balance):
    """"🧭 내 투자 스타일 진단" + "💰 예수금 현황" 섹션을 렌더링한다.

    `is_pension_account(cfg)`로 계좌 유형을 판별해 두 갈래로 동작한다:
    퇴직연금 계좌면 체결내역 API 한계를 경고 문구로 알리고 예수금 스냅샷을
    보여주며, 일반 계좌면 매매빈도/분산도를 있는 그대로 진단한다
    (`account_activity` 모듈 docstring의 계좌 유형별 API 지원 범위 참고).
    """
    st.subheader("🧭 내 투자 스타일 진단 (분산도 · 매매빈도)")
    st.caption(
        "실증 연구(자본시장연구원 개인투자자 성과 분석 등)에서 상대적으로 좋은 성과를 보인 "
        "패턴은 '분산 + 장기 보유 + 저빈도 매매'입니다. 내 계좌를 이 기준으로 비교합니다."
    )

    hhi, top_weight_pct = diversification_score(balance.holdings)
    pension = is_pension_account(cfg)
    try:
        executions = _recent_executions(cfg, 90)
    except Exception as e:
        st.warning(f"최근 체결 내역 조회 실패: {e}")
        executions = []
    activity = summarize_trading_activity(executions, 90)

    col1, col2, col3 = st.columns(3)
    col1.metric("최근 90일 매매 건수", f"{activity.trade_count}건", f"매수 {activity.buy_count} / 매도 {activity.sell_count}")
    col2.metric("월평균 매매빈도", f"{activity.trades_per_month:.1f}건/월")
    col3.metric("최대 종목 비중", f"{top_weight_pct:.1f}%", f"보유 {len(balance.holdings)}종목, HHI {hhi:.2f}")

    st.markdown(f"→ **{investment_style_signal(activity, hhi, data_reliable=not pension)}**")
    if pension:
        st.caption(
            "⚠️ 퇴직연금(DC형) 계좌는 KIS Open API의 체결내역 조회 API(일반/퇴직연금/실현손익 전부)가 "
            "'조회할 내용이 없음'만 반환해, 실제 매매가 있었어도 위 매매 건수는 항상 0으로 나옵니다 — "
            "이 계좌 유형의 API 한계이며, 보유 종목·평가금액은 정상적으로 반영됩니다."
        )

    st.caption(
        "예수금(D/D+1/D+2)은 위 '보유 종목' 섹션에 계좌 유형에 맞는 API로 표시됩니다 "
        "(퇴직연금은 전용 예수금조회 API, 일반 계좌는 잔고조회 API의 D/D+1/D+2 필드) — "
        "이 값은 현재 잔액 스냅샷이며, 언제 얼마가 입출금됐는지의 이력(원장)은 어떤 계좌 "
        "유형이든 KIS Open API로 조회할 수 없어 HTS/MTS 앱에서 확인해야 합니다."
    )


TRANSACTIONS_PATH = Path(__file__).parent.parent / "transactions.yaml"


def render_manual_transactions(cfg: KisConfig):
    """"📝 매매 내역 (수동 기록)" 섹션 — `transactions.yaml`(git에는 커밋되지
    않는 개인 파일, 형식은 `transactions.yaml.example` 참고)에 사용자가 직접
    기록한 매매를, 이 계좌(`cfg.account_no`)에 해당하는 것만 골라 매수/매도
    시계열 막대그래프 + 종목별 매수/매도 비교 표 + 전체 원장(접이식)으로
    보여준다. `account_no`를 적지 않은 기록은 모든 계좌 탭에 공통 표시된다
    (`manual_transactions.filter_by_account` 참고). KIS API가 체결내역을
    제공하지 않는 계좌(퇴직연금 DC형 등)를 보완하기 위한 섹션이다.
    """
    st.subheader("📝 매매 내역 (수동 기록)")
    st.caption(
        "퇴직연금 등 KIS Open API가 체결내역을 제공하지 않는 계좌를 위해, "
        f"`{TRANSACTIONS_PATH.name}`에 직접 기록한 매매 내역입니다 "
        "(계좌를 구분하지 않고 적은 기록은 모든 계좌 탭에 함께 표시됩니다)."
    )
    all_transactions = load_manual_transactions(TRANSACTIONS_PATH)
    transactions = filter_by_account(all_transactions, cfg.account_no)
    if not transactions:
        st.info(f"{TRANSACTIONS_PATH.name}에 이 계좌로 기록된 매매 내역이 없습니다.")
        return

    fig = go.Figure()
    for side, color in [("매수", "#d62728"), ("매도", "#1f77b4")]:
        side_txs = [t for t in transactions if t.side == side]
        if not side_txs:
            continue
        fig.add_trace(
            go.Bar(
                x=[t.date for t in side_txs],
                y=[t.amount for t in side_txs],
                name=side,
                marker_color=color,
                text=[f"{t.name} {t.quantity}주" for t in side_txs],
            )
        )
    fig.update_layout(
        title="매매 시계열 (거래대금)",
        xaxis_title="날짜",
        yaxis_title="거래대금",
        height=350,
    )
    st.plotly_chart(fig, width="stretch", key=f"manual_tx_chart_{cfg.account_no}")

    st.markdown("**종목별 매매 비교**")
    st.dataframe(
        [
            {
                "종목명": s["name"],
                "종목코드": s["code"],
                "매수금액": s["buy_amount"],
                "매도금액": s["sell_amount"],
                "순매수금액": s["net_amount"],
                "매매횟수": s["trade_count"],
            }
            for s in per_stock_summary(transactions)
        ],
        width="stretch",
        hide_index=True,
        key=f"manual_tx_summary_{cfg.account_no}",
    )

    with st.expander("전체 매매 기록 원장"):
        st.dataframe(
            [
                {
                    "날짜": t.date,
                    "종목명": t.name,
                    "종목코드": t.code,
                    "구분": t.side,
                    "수량": t.quantity,
                    "단가": t.price,
                    "거래대금": t.amount,
                    "메모": t.note,
                }
                for t in transactions
            ],
            width="stretch",
            hide_index=True,
            key=f"manual_tx_ledger_{cfg.account_no}",
        )


WATCHLIST_PATH = Path(__file__).parent.parent / "watchlist.yaml"


def render_watchlist():
    """"관심종목" 섹션 — `watchlist.yaml`을 표시하고 목록을 반환한다(보유종목과
    합쳐 캔들차트 종목 선택지를 구성하는 데 재사용됨)."""
    st.subheader("관심종목")
    watchlist = load_watchlist(WATCHLIST_PATH)
    if not watchlist:
        st.info("watchlist.yaml에 등록된 관심종목이 없습니다.")
    return watchlist


def render_candles(cfg: KisConfig, codes: list[tuple[str, str]]):
    """"캔들 차트" 섹션 — `codes`(모든 계좌의 보유종목+관심종목 합친 (코드, 이름)
    목록) 중 하나를 셀렉트박스로 골라 plotly 캔들스틱으로 그린다.

    시세 조회 자체는 계좌와 무관하지만 유효한 토큰이 필요해 대표 계좌
    (`cfg`, 보통 첫 번째 계좌)의 자격증명을 빌려 쓴다. 조회 실패(KIS 서버
    5xx 등)를 여기서 잡아 경고만 띄우고 함수가 조기 반환하도록 해, 캔들
    데이터 하나 때문에 페이지 전체가 죽지 않게 한다.
    """
    st.subheader("캔들 차트")
    if not codes:
        st.info("표시할 종목이 없습니다.")
        return

    days = st.slider("조회 기간(일)", min_value=30, max_value=365, value=90, step=30)
    labels = [f"{name}({code})" for code, name in codes]
    selected = st.selectbox("종목 선택", labels)
    code, name = codes[labels.index(selected)]

    try:
        candles = _candles(cfg, code, days)
    except Exception as e:
        st.warning(f"{name}({code}) 캔들 조회 실패 (KIS 서버 일시 오류일 수 있습니다): {e}")
        return
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


def render_portfolio_rebalance(cfg: KisConfig):
    """"🎯 목표 포트폴리오 리밸런싱" 섹션.

    회차당 `CONTRIBUTION_PER_ROUND`(11,000,000원)를 목표 비중(TDF2050 20% /
    TDF2035 20% / 나스닥100 20% / S&P500 40% / 금현물 10%)에 맞춰 배분한다.
    ETF 3종은 KIS 시세를 그대로 쓰고, TDF 펀드 2종은 KIS API가 펀드 시세를
    지원하지 않아 사용자가 직접 입력한 기준가(`fund_nav.csv`)를 쓴다. 이
    섹션은 계좌 탭과 무관한 개인 목표 포트폴리오 계획이라 특정 계좌에 종속되지
    않고(`cfg`는 시세 조회용 토큰만 빌려 씀) 페이지에 한 번만 렌더링된다.
    """
    st.subheader("🎯 목표 포트폴리오 리밸런싱")
    st.caption(
        f"회차당 {CONTRIBUTION_PER_ROUND:,.0f}원 투자, 목표 비중: "
        + " · ".join(f"{a.name} {a.weight_pct:.0f}%" for a in TARGET_ASSETS)
        + ". 이미 산 만큼을 반영해 다음 회차 매수 금액을 계산합니다."
    )

    live_prices: dict[str, float] = {}
    for asset in TARGET_ASSETS + [EXISTING_CASH_PARK]:
        if not asset.code:
            continue
        try:
            candles = _candles(cfg, asset.code, 5)
            if candles:
                live_prices[asset.key] = candles[-1].close
        except Exception as e:
            st.warning(f"{asset.name} 시세 조회 실패: {e}")

    fund_nav = load_fund_nav()
    with st.expander("📌 펀드 기준가(NAV) 직접 입력 — KIS API가 펀드 시세를 지원하지 않음"):
        st.caption("한투 앱이나 펀드 사이트에서 확인한 오늘 기준가를 입력하세요.")
        for asset in TARGET_ASSETS:
            if asset.asset_type != "fund":
                continue
            existing_nav, existing_date = fund_nav.get(asset.key, (0.0, dt.date.today()))
            nc1, nc2, nc3 = st.columns([2, 1, 1])
            nc1.write(f"**{asset.name}**  \n마지막 입력: {existing_date} = {existing_nav:,.2f}")
            new_nav = nc2.number_input(
                "기준가", min_value=0.0, value=float(existing_nav), key=f"nav_input_{asset.key}"
            )
            if nc3.button("저장", key=f"nav_save_{asset.key}"):
                save_fund_nav(asset.key, new_nav, dt.date.today())
                st.rerun()

    current_prices = dict(live_prices)
    for key, (nav, _as_of) in fund_nav.items():
        current_prices[key] = nav

    purchases = load_purchases()
    positions = build_positions(purchases, current_prices)

    with st.form("portfolio_purchase_form"):
        st.markdown("**📝 매수 기록 입력**")
        all_assets = TARGET_ASSETS + [EXISTING_CASH_PARK]
        asset_options = {a.name: a.key for a in all_assets}
        selected_name = st.selectbox("종목/펀드", list(asset_options.keys()))
        p_date = st.date_input("매수일", value=dt.date.today())
        p_qty = st.number_input("수량/좌수 (펀드는 몰라도 0으로 두고 금액만 기록 가능)", min_value=0.0, value=0.0, step=1.0)
        p_price = st.number_input("매수 단가(원, 펀드는 기준가)", min_value=0.0, value=0.0, step=1.0)
        p_amount = st.number_input("매수 금액(원)", min_value=0.0, value=0.0, step=10000.0)
        p_note = st.text_input("메모", value="")
        submitted = st.form_submit_button("기록 저장")
        if submitted:
            if p_amount <= 0:
                st.error("매수 금액을 입력하세요.")
            else:
                append_purchase(
                    PurchaseRecord(
                        date=p_date,
                        asset_key=asset_options[selected_name],
                        quantity=p_qty,
                        price=p_price,
                        amount=p_amount,
                        note=p_note,
                    )
                )
                st.success("저장했습니다.")
                st.rerun()

    st.markdown("**📊 매입 대비 현재 수익률**")
    total_invested = sum(p.invested_amount for p in positions)
    total_current = sum(p.current_value or 0 for p in positions)
    rows = [
        {
            "자산": p.asset.name,
            "보유수량": p.quantity or None,
            "매입총액": p.invested_amount,
            "현재가/기준가": p.current_price,
            "평가금액": p.current_value,
            "손익": p.profit_loss,
            "수익률(%)": p.profit_loss_rate,
        }
        for p in positions
    ]
    rows.append(
        {
            "자산": "합계",
            "보유수량": None,
            "매입총액": total_invested,
            "현재가/기준가": None,
            "평가금액": total_current,
            "손익": (total_current - total_invested) if total_invested else None,
            "수익률(%)": ((total_current - total_invested) / total_invested * 100) if total_invested else None,
        }
    )
    st.dataframe(rows, width="stretch", hide_index=True, key="portfolio_return_table")

    st.markdown(f"**🛒 다음 회차 매수 계획 (총 {CONTRIBUTION_PER_ROUND:,.0f}원)**")
    plan = compute_rebalance_plan(positions)
    plan_rows = [
        {
            "자산": item.asset.name,
            "목표비중": f"{item.asset.weight_pct:.0f}%",
            "현재평가금액": item.current_value,
            "목표금액": item.target_value,
            "매수필요금액": item.buy_amount,
            "단가": item.price,
            "매수수량": item.buy_quantity,
            "실제매수금액": item.actual_cost,
            "잔액": item.leftover,
        }
        for item in plan
    ]
    st.dataframe(plan_rows, width="stretch", hide_index=True, key="portfolio_plan_table")
    st.caption(
        "펀드는 정수 좌수 개념이 없어 '매수수량'이 비어 있습니다 — 계산된 '매수필요금액'만큼 "
        "증권사 앱에서 '금액 매수'로 신청하면 됩니다. ACE KRX금현물은 실제 KRX 금현물시장(별도 "
        "계좌 필요)의 대리 자산으로 쓴 상장 ETF입니다."
    )


def render_account_section(cfg: KisConfig):
    """계좌 하나에 대한 "보유 종목 + 투자 스타일 진단 + 수동 매매기록"을
    묶어서 렌더링하고, 캔들차트 종목 선택지 구성에 쓸 `AccountBalance`를
    반환한다 (계좌 조회 자체가 실패했으면 None — 이후 두 섹션은 건너뛴다).
    """
    balance = render_holdings(cfg)
    if balance is None:
        return None
    render_investment_style(cfg, balance)
    render_manual_transactions(cfg)
    return balance


def main():
    """페이지 설정 → 비밀번호 게이트 → 각 섹션을 순서대로 렌더링하는 앱 엔트리포인트.

    렌더링 순서(시장 동향 → 계좌 섹션 → 관심종목 → 캔들차트)는 "계좌 확인 전에
    먼저 매크로 상황을 보여준다"는 의도로 배치했다. 계좌가 여러 개면(`.env`에
    `KIS_APP_KEY_2` 등으로 추가 등록, `config.load_configs` 참고) 계좌별로
    `st.tabs`를 그려 각 탭에서 독립적으로 보유종목/투자스타일/수동매매기록을
    보여주고, 계좌가 1개뿐이면 탭 없이 바로 보여준다. 시장 동향/캔들차트는
    계좌에 종속되지 않는 공용 섹션이라 탭 밖에서 한 번만 그린다(캔들차트의
    종목 선택지는 모든 계좌의 보유종목 + 관심종목을 합친 목록). 마지막
    "ℹ️ 관리 정보" 접이식 패널은 이 화면과 무관한 저장소/설정 정보라 항상
    맨 아래 둔다.
    """
    st.set_page_config(page_title="hantoo", page_icon=str(ICON_PATH), layout="wide")
    if not check_password():
        return

    now = dt.datetime.now()
    st.title("한국투자증권 계좌 대시보드")
    st.caption(f"기준일 {now.date()} · 생성 {now.strftime('%Y-%m-%dT%H:%M:%S')}")
    st.caption(
        f"Owner: {OWNER_NAME} <{OWNER_EMAIL}> · [GitHub ↗]({GITHUB_URL})"
    )

    configs = load_configs()
    primary_cfg = configs[0]

    render_market_overview(primary_cfg)

    all_holdings = []
    if len(configs) == 1:
        balance = render_account_section(configs[0])
        if balance is not None:
            all_holdings.extend(balance.holdings)
    else:
        tabs = st.tabs([c.display_label for c in configs])
        for tab, cfg in zip(tabs, configs):
            with tab:
                balance = render_account_section(cfg)
                if balance is not None:
                    all_holdings.extend(balance.holdings)

    watchlist = render_watchlist()

    code_to_name: dict[str, str] = {}
    for h in all_holdings:
        code_to_name.setdefault(h.code, h.name)
    for w in watchlist:
        code_to_name.setdefault(w.code, w.name or w.code)
    render_candles(primary_cfg, list(code_to_name.items()))

    render_portfolio_rebalance(primary_cfg)

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
