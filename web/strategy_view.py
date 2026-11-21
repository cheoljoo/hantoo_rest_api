"""모드 4 — 투자 전략: 서대리TV · 서준식 교수 · 김영익 교수의 방식으로 내 현금을 어떻게 나눌지.

데이터는 일일 리포트 DB(`data/reports.db`)의 가장 최근 실행(보유 종목·현금·지표)을 그대로 쓴다. 시세(SPY·QQQ·환율)만
Yahoo Finance 에서 새로 받는다(1시간 캐시). 전문가가 숫자로 밝히지 않은 부분(분할 횟수, 구간별 비중)은 화면에 '가정'이라고 적고
슬라이더로 바꿀 수 있게 했다. 투자 권유가 아니라 각 전문가 규칙을 내 계좌에 대입해 본 시나리오다.
"""

from __future__ import annotations

import datetime as dt
import importlib
import re

import pandas as pd
import plotly.express as px
import streamlit as st

from hantoo_rest_api import daily_db
from strategy_data import EXPERTS, YIELD_GAP_TIERS

GROUPS = ["일반", "ISA", "연금"]          # 즉시 쓸 수 있는 돈 → 세제 계좌 순
_BOND = re.compile(r"채|CD금리|KOFR|머니마켓|초단기|파킹|단기통안|SOFR")
_SHORT = re.compile(r"초단기|CD금리|KOFR|머니마켓|파킹|단기|SOFR")
_FOREIGN = re.compile(r"미국|나스닥|S&P|해외|글로벌|차이나|중국|일본|인도|베트남")
DC_RISK_CAP = 0.7                         # 퇴직연금(DC) 위험자산 한도


def _won(v: float) -> str:
    v = float(v)
    return f"{v / 1e8:,.2f}억" if abs(v) >= 1e8 else f"{v / 1e4:,.0f}만"


def _group(label: str) -> str:
    if "ISA" in label:
        return "ISA"
    return "연금" if ("연금저축" in label or "DC" in label) else "일반"


def _classify(p: dict) -> tuple[str, str, bool]:
    """(자산군, 만기구분 '단'/'장'/'', 외화노출 여부). 자산군: 현금·채권·금·주식·기타."""
    name, kind = p["name"], p["kind"]
    usd = p["currency"] == "USD"
    if kind == "현금":
        return "현금", "", usd
    if kind == "금":
        return "금", "", False
    if kind == "채권":
        return "채권", "", False
    if kind == "주식":
        if _BOND.search(name):
            return "채권", ("단" if _SHORT.search(name) else "장"), usd or bool(_FOREIGN.search(name) and "(H)" not in name)
        return "주식", "", usd or bool(_FOREIGN.search(name) and "(H)" not in name)
    return "기타", "", False


@st.cache_data(ttl=600, show_spinner=False)
def _state() -> dict | None:
    importlib.reload(daily_db)
    dates = daily_db.list_dates()
    if not dates:
        return None
    data = daily_db.load_run(dates[0])
    rows = []
    for p in data["positions"]:
        cls, term, foreign = _classify(p)
        rows.append({"계좌": p["account"], "그룹": _group(p["account"]), "종목": p["name"], "자산군": cls, "만기": term,
                     "외화": foreign, "통화": p["currency"], "평가": float(p["eval"] or 0), "손익률": p["pl_rate"]})
    df = pd.DataFrame(rows)
    ind = {i["id"]: i["value"] for i in data["indicators"] if i["value"] is not None}
    return {"date": dates[0], "df": df, "ind": ind, "total": float(df["평가"].sum())}


@st.cache_data(ttl=3600, show_spinner=False)
def _px(sym: str, period: str) -> list[float]:
    try:
        import yfinance as yf
        df = yf.download(sym, period=period, interval="1d", progress=False, auto_adjust=False)
        s = df["Close"]
        s = s.iloc[:, 0] if hasattr(s, "columns") else s
        return [float(x) for x in s.dropna()]
    except Exception:
        return []


def _cash_by(df: pd.DataFrame) -> dict[str, float]:
    c = df[df["자산군"] == "현금"].groupby("그룹")["평가"].sum()
    return {g: float(c.get(g, 0.0)) for g in GROUPS}


def _dc_room(df: pd.DataFrame) -> float:
    """퇴직연금(DC)에서 주식(위험자산)을 더 살 수 있는 한도 — 총액의 70% − 현재 주식."""
    dc = df[df["계좌"].str.contains("DC")]
    return max(0.0, DC_RISK_CAP * float(dc["평가"].sum()) - float(dc[dc["자산군"] == "주식"]["평가"].sum()))


def _take(amount: float, order: list[str], avail: dict[str, float]) -> tuple[dict[str, float], float]:
    """order 순서로 avail 에서 amount 를 뽑는다(avail 을 줄인다). (그룹별 뽑은 금액, 모자란 금액)."""
    got = {g: 0.0 for g in GROUPS}
    left = amount
    for g in order:
        x = min(max(avail[g], 0.0), left)
        got[g], avail[g], left = x, avail[g] - x, left - x
    return got, left


def _next_25th(today: dt.date, k: int) -> dt.date:
    m = today.month - 1 + k + (0 if today.day <= 25 else 1)
    return dt.date(today.year + m // 12, m % 12 + 1, 25)


def _header(key: str) -> None:
    ex = EXPERTS[key]
    st.subheader(f"{ex['icon']} {ex['name']} — {ex['tagline']}")
    st.info(ex["summary"])
    with st.expander("핵심 원칙 · 근거 · 한계"):
        for t, b in ex["principles"]:
            st.markdown(f"- **{t}** — {b}")
        st.markdown("**⚠️ 이 페이지의 한계·가정**")
        for c in ex["caveats"]:
            st.markdown(f"- {c}")
        st.markdown("**출처**")
        for t, u in ex["sources"]:
            st.markdown(f"- [{t}]({u})")


def _overview(S: dict, tag: str) -> dict:
    df, T = S["df"], S["total"]
    cb = _cash_by(df)
    st.markdown(f"##### 💼 내 돈 현황 ({S['date']} 일일 리포트 기준)")
    c = st.columns(4)
    c[0].metric("총자산", _won(T))
    c[1].metric("현금 합계", _won(sum(cb.values())), f"{sum(cb.values()) / T:.0%}", delta_color="off")
    c[2].metric("즉시 쓸 수 있는 현금(일반계좌)", _won(cb["일반"]), f"{cb['일반'] / T:.1%}", delta_color="off")
    c[3].metric("절세계좌 안 현금(ISA·연금·DC)", _won(cb["ISA"] + cb["연금"]), f"{(cb['ISA'] + cb['연금']) / T:.0%}", delta_color="off")
    st.caption("연금저축·DC 안의 현금은 인출할 수 없어 '즉시 현금'이 아니고 그 계좌 안에서만 투자할 수 있습니다. "
               f"DC는 위험자산(주식 ETF) 한도 {DC_RISK_CAP:.0%} — 지금 주식을 더 살 수 있는 여유는 약 {_won(_dc_room(df))}입니다.")
    mix = df.groupby("자산군")["평가"].sum().reindex(["주식", "채권", "금", "현금", "기타"]).fillna(0)
    fx = float(df[df["외화"]]["평가"].sum())
    a, b = st.columns([1, 1])
    with a:
        fig = px.pie(values=mix.values, names=mix.index, hole=0.45, title="자산군 비중(채권 ETF는 채권으로 분류)")
        fig.update_layout(height=260, margin=dict(t=40, b=0, l=0, r=0))
        st.plotly_chart(fig, width="stretch", key=f"st_pie_{tag}")
    with b:
        t = pd.DataFrame({"자산군": mix.index, "금액": [_won(v) for v in mix.values], "비중": [f"{v / T:.1%}" for v in mix.values]})
        st.dataframe(t, hide_index=True, width="stretch")
        st.caption(f"외화 노출(달러 자산 + 환노출 해외 ETF) {_won(fx)} · {fx / T:.1%}")
    return {"T": T, "df": df, "cb": cb, "mix": mix, "fx": fx}


def _group_table(split: dict[str, dict[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"항목": k, **{g: (_won(v[g]) if v[g] else "-") for g in GROUPS}, "합계": _won(sum(v.values()))}
                         for k, v in split.items()])


# ───────────────────────── 서대리 ─────────────────────────


def _seodaeri_market() -> dict | None:
    spy, qqq, fx = _px("SPY", "2y"), _px("QQQ", "2y"), _px("KRW=X", "10y")
    if not (spy and qqq and fx):
        return None
    return {"SPY": (spy[-1], max(spy), 0.20), "QQQ": (qqq[-1], max(qqq), 0.15), "fx": fx[-1], "fx_avg": sum(fx) / len(fx)}


def render_seodaeri(S: dict) -> None:
    _header("seodaeri")
    o = _overview(S, "sd")
    T, cb = o["T"], o["cb"]
    st.markdown("##### 🎛️ 입력 (서대리 기본값, 바꿔 볼 수 있음)")
    c = st.columns(5)
    cost = c[0].number_input("월 생활비(만원)", 50, 2000, 250, 10, key="sd_cost", help="서대리는 부부 월 250만 원 기준")
    months = c[1].number_input("비상금 개월 수", 3, 24, 12, 1, key="sd_em")
    cash_pct = c[2].slider("추매 실탄(현금성) 비중 %", 5, 30, 14, key="sd_cash")
    n = c[3].slider("월적립 분할(개월)", 3, 24, 12, key="sd_n")
    spx = c[4].slider("S&P500 : 나스닥 비중 %", 30, 80, 55, key="sd_spx", help="서대리 6월 기준 S&P500 39% : 나스닥 32%")

    emergency = cost * 1e4 * months
    avail = dict(cb)
    em_take = min(emergency, avail["일반"])
    avail["일반"] -= em_take
    reserve = T * cash_pct / 100
    res_take, res_short = _take(reserve, ["연금", "일반", "ISA"], avail)
    dc_cap = _dc_room(o["df"])
    nonDC_pension = float(o["df"][(o["df"]["그룹"] == "연금") & (~o["df"]["계좌"].str.contains("DC")) & (o["df"]["자산군"] == "현금")]["평가"].sum())
    pension_eq_cap = dc_cap + nonDC_pension
    spare = dict(avail)
    over = max(0.0, spare["연금"] - pension_eq_cap)
    spare["연금"] -= over

    split = {
        "비상금(보관: 일반계좌 현금·CMA)": {"일반": em_take, "ISA": 0.0, "연금": 0.0},
        f"추매 실탄 {cash_pct}% (달러 RP·파킹 ETF)": res_take,
        "월적립 재원(S&P500·나스닥 ETF)": spare,
        "DC 위험자산 한도 초과분(안전자산으로 보관)": {"일반": 0.0, "ISA": 0.0, "연금": over},
    }
    st.markdown("##### 1️⃣ 돈 나누기 — 현재 현금을 어디에 얼마나")
    st.dataframe(_group_table(split), hide_index=True, width="stretch")
    if emergency > em_take:
        st.warning(f"비상금 {_won(emergency)}에 일반계좌 현금이 {_won(emergency - em_take)} 모자랍니다 — 비상금부터 채우세요(월적립 재원은 그만큼 줄어듭니다).")
    if res_short > 0:
        st.warning(f"추매 실탄 목표에 {_won(res_short)} 모자랍니다 — 월적립 재원이 없고, 월 소득으로 실탄부터 채우는 단계입니다.")
    total_spare = sum(spare.values())
    st.caption(f"월적립 재원 합계 {_won(total_spare)} = 총자산의 {total_spare / T:.1%}. 서대리는 월 소득을 따로 적립하므로, 이 재원은 '이미 쌓인 현금'을 {n}개월에 나눠 넣는 계획입니다.")

    today = dt.date.today()
    rows = []
    for k in range(n):
        d = _next_25th(today, k)
        m = {g: spare[g] / n for g in GROUPS}
        tot = sum(m.values())
        rows.append({"차수": f"{k + 1}차", "날짜(월급날)": d.isoformat(), "일반": _won(m["일반"]), "ISA": _won(m["ISA"]), "연금·DC": _won(m["연금"]),
                     "합계": _won(tot), "S&P500": _won(tot * spx / 100), "나스닥": _won(tot * (100 - spx) / 100)})
    st.markdown(f"##### 2️⃣ 몇 차로, 언제 — 월적립 {n}차 (매월 25일 전후, 주가·환율과 무관하게 시장가)")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    M = _seodaeri_market()
    st.markdown("##### 3️⃣ 무엇을 보고 — 추가 매수(추매) 기준가")
    if not M:
        st.warning("Yahoo Finance 에서 SPY·QQQ·환율을 받지 못했습니다. 잠시 후 다시 열어 보세요.")
        return
    t_rows, hit = [], []
    for s in ("SPY", "QQQ"):
        last, high, dd = M[s]
        base = high * (1 - dd)
        d1 = base / last - 1
        s1 = "🔴 도달" if last <= base else ("🟡 근접" if d1 > -0.05 else "🟢 멀음")
        s2 = "🔴 도달" if last <= base * 0.9 else "-"
        hit.append((s, last <= base, last <= base * 0.9))
        t_rows.append({"ETF": s, "현재가": f"{last:,.2f}", "2년 고점": f"{high:,.2f}", "고점 대비": f"{last / high - 1:+.1%}",
                       "기준 낙폭": f"-{dd:.0%}", "1차 기준가": f"{base:,.2f}", "기준가까지 필요한 등락": f"{d1:+.1%}", "1차 상태": s1,
                       "2차 기준가(가정, -10% 더)": f"{base * 0.9:,.2f}", "2차 상태": s2})
    st.dataframe(pd.DataFrame(t_rows), hide_index=True, width="stretch")
    half = reserve / 2
    st.markdown(f"- **추매 1차**: SPY·QQQ 중 하나라도 1차 기준가 이하이면 25일에 실탄의 50%({_won(half)})로 해당 ETF 추가 매수.\n"
                f"- **추매 2차(가정)**: 기준가에서 10% 더 내려가면 나머지 50%({_won(half)}).\n"
                "- 기준가는 고점이 갱신되면 같이 올라가므로 매달 25일에 다시 계산합니다. 규칙 기반 추매는 서대리도 2022년이 마지막이었습니다.")
    fxd = M["fx"] / M["fx_avg"] - 1
    fx_ok = M["fx"] < M["fx_avg"]
    st.markdown(f"- **환율**: 원/달러 {M['fx']:,.0f}원, 10년 평균 {M['fx_avg']:,.0f}원 ({fxd:+.1%}) → "
                + ("**평균보다 낮음 — 비상금 일부까지 달러로 추가 환전 대상**" if fx_ok else "평균보다 높아 추가 환전 없음(월적립 때 자연스럽게 환전)"))

    st.markdown("##### ✅ 지금 해야 할 일")
    nxt = _next_25th(today, 0)
    any_hit = any(h[1] for h in hit)
    st.success(f"{nxt:%Y-%m-%d} 25일: 월적립 {_won(total_spare / n)}"
               + (" + **추매 1차 발동 조건 충족**" if any_hit else " (추매 조건 미충족, 월적립만)")
               + f". 이후 {n - 1}번 같은 금액을 반복하고, 매달 25일에 기준가를 다시 확인하세요.")
    st.caption("※ 투자 권유가 아니라 서대리 규칙을 내 계좌에 대입한 시나리오입니다. 이미 가진 주식은 팔지 않는다는 것도 서대리 규칙입니다.")


# ───────────────────────── 서준식 ─────────────────────────


def render_seojunsik(S: dict) -> None:
    _header("seojunsik")
    o = _overview(S, "sj")
    T, df, cb, mix = o["T"], o["df"], o["cb"], o["mix"]
    st.markdown("##### 🎛️ 입력 (직접 확인해서 넣으세요)")
    c = st.columns(5)
    per = c[0].number_input("코스피 PER(배)", 3.0, 40.0, 11.0, 0.5, key="sj_per", help="수집하지 않는 값입니다. 기본값 11은 임의값 — 같은 출처로 매번 확인하세요.")
    y3 = c[1].number_input("국고채 3년(%)", 0.0, 10.0, 3.74, 0.05, key="sj_y3", help="2026-11-18 마감 3.736%")
    y3p = c[2].number_input("한 달 전 국고채 3년(%)", 0.0, 10.0, 3.74, 0.05, key="sj_y3p")
    n = c[3].slider("분할 횟수(매월 1회, 가정)", 2, 8, 4, key="sj_n")
    cash_t = c[4].slider("즉시 현금 목표 %", 5, 20, 10, key="sj_cash")

    gap = 100 / per - y3
    w = next(wt for th, wt, _ in YIELD_GAP_TIERS if gap >= th)
    adj = -0.05 if y3 - y3p >= 0.25 else (0.05 if y3p - y3 >= 0.25 else 0.0)
    w = min(0.8, max(0.2, w + adj))
    tier_name = next(nm for th, wt, nm in YIELD_GAP_TIERS if gap >= th)
    st.markdown(f"**Yield Gap** = 이익수익률 {100 / per:.2f}% − 국고채3년 {y3:.2f}% = **{gap:+.2f}%p** → 구간 `{tier_name}` → "
                f"주식:채권 = **{w:.0%} : {1 - w:.0%}**" + (f" (금리 변화 {y3 - y3p:+.2f}%p 반영 {adj:+.0%}p)" if adj else ""))
    st.dataframe(pd.DataFrame([{"Yield Gap": nm, "주식 비중(주식+채권 중)": f"{wt:.0%}", "현재": "◀" if nm == tier_name else ""}
                               for _, wt, nm in YIELD_GAP_TIERS]), hide_index=True, width="stretch")
    st.caption("교수는 '벌어지면 주식↑, 좁아지면 주식↓', '국고채 3년 금리↑ → 주식↓'의 방향만 밝혔습니다. 구간 숫자는 가정입니다. "
               "PER을 바꿔 보면서 민감도를 확인하세요(PER 8이면 갭이 크게, 16이면 작게 나옵니다).")

    core = 1 - cash_t / 100 - 0.10
    tgt = {"주식": core * w, "채권": core * (1 - w), "금·대체": 0.10, "현금": cash_t / 100}
    cur = {"주식": mix["주식"] / T, "채권": mix["채권"] / T, "금·대체": (mix["금"] + mix["기타"]) / T, "현금": mix["현금"] / T}
    tab = pd.DataFrame([{"자산군": k, "현재": f"{cur[k]:.1%}", "목표": f"{tgt[k]:.1%}", "차이": f"{(tgt[k] - cur[k]):+.1%}",
                         "금액 차이": ("+" if tgt[k] >= cur[k] else "-") + _won(abs(tgt[k] - cur[k]) * T)} for k in tgt])
    st.markdown("##### 1️⃣ 돈 나누기 — 목표 vs 현재 (주식+채권 80% · 현금 10% · 금·대체 10%)")
    st.dataframe(tab, hide_index=True, width="stretch")
    fxs = o["fx"] / T
    st.markdown(f"- **외화자산 10% 점검**: 현재 {fxs:.1%} → " + ("충족" if fxs >= 0.10 else f"**{(0.10 - fxs) * T / 1e4:,.0f}만 원 부족** — 새로 사는 주식·채권 중 이만큼은 달러 표시 자산으로"))
    imm = cb["일반"] / T
    st.markdown(f"- **즉시 현금 {cash_t}% 점검**: 일반계좌 현금 {imm:.1%} ({_won(cb['일반'])}) → " + ("충족" if imm >= cash_t / 100 else
                f"**{_won(cash_t / 100 * T - cb['일반'])} 모자람** — 연금·DC 안 현금은 꺼낼 수 없어 즉시 현금이 아닙니다. 일반계좌 현금은 이 목적으로 남겨 두세요."))

    deploy = max(0.0, sum(cb.values()) - cash_t / 100 * T)
    gaps = {k: max(0.0, (tgt[k] - cur[k]) * T) for k in ("채권", "주식", "금·대체")}
    gsum = sum(gaps.values()) or 1.0
    scale = min(1.0, deploy / gsum)
    plan = {k: v * scale for k, v in gaps.items()}
    st.markdown(f"##### 2️⃣ 몇 차로, 언제 — 현금 중 즉시 현금 목표를 뺀 {_won(deploy)}을 {n}차에 나눠 투입")
    avail = dict(cb)
    avail["일반"] = max(0.0, avail["일반"] - cash_t / 100 * T)      # 즉시 현금은 건드리지 않는다
    bond_g, _ = _take(plan["채권"], ["연금", "ISA", "일반"], avail)
    cap = _dc_room(df) + float(df[(df["그룹"] == "연금") & (~df["계좌"].str.contains("DC")) & (df["자산군"] == "현금")]["평가"].sum())
    stock_pension_avail = {**avail, "연금": min(avail["연금"], cap)}
    stk_g, stk_left = _take(plan["주식"], ["ISA", "연금", "일반"], stock_pension_avail)
    for g in GROUPS:
        avail[g] -= stk_g[g]
    avail["연금"] = min(avail["연금"], max(0.0, cap - stk_g["연금"]))   # 금 ETF 도 DC 위험자산 한도를 같이 쓴다
    gold_g, gold_left = _take(plan["금·대체"], ["ISA", "연금", "일반"], avail)
    st.dataframe(_group_table({"채권(국채·우량 회사채·채권 ETF)": bond_g, "주식(배당·채권형 주식 50% / 성장 50%)": stk_g, "금": gold_g}),
                 hide_index=True, width="stretch")
    if stk_left > 0:
        st.warning(f"주식 {_won(stk_left)}는 넣을 계좌가 없어 보류됩니다(DC 위험자산 한도 70% 또는 일반계좌 즉시 현금 보존).")
    if gold_left > 0:
        st.warning(f"금·대체 {_won(gold_left)}는 넣을 계좌가 없어 보류됩니다(일반계좌 현금은 즉시 현금으로 남겨 두는 중). 금 ETF(연금저축·ISA)나 금현물은 새 돈이 생길 때 채우세요.")
    st.caption("DC 안 현금은 원리금보장 상품·채권(ETF)으로만 채권 몫을 살 수 있고, 주식·금 ETF는 한도(70%) 안에서만 살 수 있습니다.")

    today = dt.date.today()
    rows = []
    b_split = [0.5, 0.5] + [0.0] * (n - 2)           # 채권: 금리가 높은 지금 앞 2차에 확정
    for k in range(n):
        d = dt.date(today.year + (today.month + k) // 12, (today.month + k) % 12 + 1, 1)
        rows.append({"차수": f"{k + 1}차", "시기": f"{d:%Y-%m} 첫 주", "채권": _won(plan["채권"] * b_split[k]), "주식": _won(plan["주식"] / n),
                     "금": _won(plan["금·대체"] / n), "합계": _won(plan["채권"] * b_split[k] + (plan["주식"] + plan["금·대체"]) / n),
                     "실행 전 확인": "Yield Gap ≥ 현재 구간 하한, 국고채 3년 급등 없음" if k else "지금(채권 먼저 — 금리 높을 때 확정)"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.markdown("##### 3️⃣ 무엇을 보고 — 매 차수 전에 보는 것")
    st.markdown(
        f"1. **Yield Gap** {gap:+.2f}%p — 구간이 한 단계 내려가면(주식 비중 ↓) 남은 차수의 주식 금액을 줄이고, 올라가면 늘립니다.\n"
        f"2. **국고채 3년** {y3:.2f}% — 한 달에 0.25%p 이상 오르면 주식 비중을 5%p 낮추고, 내리면 높입니다(가정).\n"
        "3. **종목 매수 기준(채권형 주식)**: 10년 연속 배당, 6년간 배당수익률이 정기예금의 2~3배 이상, 경기 영향 작음, 시가배당률 5%+ 이면 후보.\n"
        "4. **매도 신호**: 보유 종목 시가배당률이 매수 때 5% → 2%로 내려오면 비중을 줄입니다.\n"
        "5. **쏠림**: 삼성전자·SK하이닉스처럼 한두 종목에 몰리지 않게 — 업종·종목별 상한을 정해 두세요.")
    st.caption("※ 투자 권유가 아니라 서준식 교수의 공개된 원칙을 내 계좌에 대입한 시나리오입니다. 분할 횟수·구간 비중은 가정입니다.")


# ───────────────────────── 김영익 ─────────────────────────


def render_kimyoungik(S: dict) -> None:
    _header("kimyoungik")
    o = _overview(S, "ky")
    T, df, cb = o["T"], o["df"], o["cb"]
    ind = S["ind"]
    st.markdown("##### 🎛️ 입력")
    c = st.columns(5)
    phase = c[0].radio("국면", ["하락기(교수 판단)", "상승기"], key="ky_phase")
    cli = c[1].radio("선행지수 순환변동치(한국)", ["하락 중", "바닥 확인", "상승 중"], key="ky_cli",
                     help="통계청이 매월 말 발표. 수집하지 않으므로 직접 선택 — 교수 판단은 7월 정점 후 하락 중")
    gold = c[2].slider("금 비중 %", 0, 15, 7, key="ky_gold")
    n = c[3].slider("분할 횟수(가정)", 2, 6, 3, key="ky_n")
    fair = c[4].number_input("적정 코스피(교수 추정)", 3000, 9000, 5318, 50, key="ky_fair", help="11월 일평균 수출 기준 약 5,318 (2026-11-11 영상·기사)")
    down = phase.startswith("하락")
    tgt_stock = 0.20 if down else 0.60
    tgt_alt = 0.20
    tgt_cash = 1 - tgt_stock - tgt_alt
    mixc = df.groupby("자산군")["평가"].sum()
    long_b = float(df[(df["자산군"] == "채권") & (df["만기"] == "장")]["평가"].sum())
    short_b = float(mixc.get("채권", 0)) - long_b
    cur = {"주식": float(mixc.get("주식", 0)), "현금성(현금+단기채)": float(mixc.get("현금", 0)) + short_b,
           "장기채·금·대체": long_b + float(mixc.get("금", 0)) + float(mixc.get("기타", 0))}
    tgt = {"주식": tgt_stock, "현금성(현금+단기채)": tgt_cash, "장기채·금·대체": tgt_alt}
    lt = st.checkbox("한투 위탁계좌 채권(서울도시철도 등)을 장기채로 분류", value=False, key="ky_longbond",
                     help="개별 채권의 잔존 만기는 수집하지 않습니다. 기본은 단기로 봅니다.")
    if lt:
        k = float(df[(df["자산군"] == "채권") & (df["만기"] == "")]["평가"].sum())
        cur["현금성(현금+단기채)"] -= k
        cur["장기채·금·대체"] += k
    tab = pd.DataFrame([{"묶음": k, "현재": f"{cur[k] / T:.1%}", "목표": f"{tgt[k]:.0%}", "차이(금액)": ("+" if tgt[k] * T >= cur[k] else "-") + _won(abs(tgt[k] * T - cur[k]))}
                        for k in tgt])
    st.markdown(f"##### 1️⃣ 돈 나누기 — {'하락기: 주식 20 · 현금성 60 · 장기채·금 20' if down else '상승기: 주식 60 · 현금성 20 · 장기채·금 20'}")
    st.dataframe(tab, hide_index=True, width="stretch")
    st.caption(f"장기채·금·대체 {tgt_alt:.0%} 안에서 금 {gold}% / 장기채 {tgt_alt * 100 - gold:.0f}% 로 나눕니다.")

    over = cur["주식"] - tgt_stock * T
    st.markdown("##### 2️⃣ 몇 차로, 언제 — ① 주식 줄이기(반등 때마다) ② 현금 굴리기(단기채 사다리) ③ 재진입(신호 3단계)")
    kospi = ind.get("kospi")
    if over > 0:
        rows = []
        for i in range(n):
            up = (3, 6, 10, 14, 18, 22)[i]
            rows.append({"차수": f"{i + 1}차", "시점(가정)": f"코스피 현재 대비 +{up}% 반등" + (f" ≈ {kospi * (1 + up / 100):,.0f}" if kospi else ""),
                         "줄일 금액": _won(over / n)})
        st.markdown(f"**① 주식 비중 {cur['주식'] / T:.1%} → 목표 {tgt_stock:.0%}: {_won(over)} 줄이기** — 교수는 '반등이 나올 때마다 비중을 줄여라'고 했습니다.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        eq = df[df["자산군"] == "주식"].copy()
        eq["우선순위"] = eq["그룹"].map({"연금": 1, "ISA": 2, "일반": 3})
        eq = eq.sort_values(["우선순위", "평가"], ascending=[True, False]).head(12)
        st.dataframe(pd.DataFrame({"계좌": eq["계좌"], "종목": eq["종목"], "평가": [_won(v) for v in eq["평가"]],
                                   "손익률": [f"{v:+.1f}%" if pd.notna(v) else "-" for v in eq["손익률"]], "그룹": eq["그룹"]}),
                     hide_index=True, width="stretch")
        st.caption("매도 순서 참고: 연금·ISA 안에서 파는 것은 양도세가 없고, 일반계좌 해외주식은 연 250만 원 초과 차익에 22%가 붙습니다(손실 종목은 상계 가능). "
                   "개별 종목 선택은 직접 판단하세요.")
    else:
        st.markdown(f"**① 주식 비중이 이미 목표 이하입니다**({cur['주식'] / T:.1%} ≤ {tgt_stock:.0%}).")

    cash_like_target = tgt_cash * T
    cm_keep = 0.10 * T                                    # 즉시 꺼낼 CMA·MMF
    ladder = max(0.0, min(cur["현금성(현금+단기채)"], cash_like_target) - cm_keep)
    st.markdown(f"**② 현금성 {_won(cash_like_target)} 중 CMA·MMF {_won(cm_keep)}(총자산 10%)은 즉시 꺼낼 수 있게 두고, 나머지 {_won(ladder)}을 만기 6·9·12개월 단기채로 3분할**")
    base = _next_25th(dt.date.today(), 0)
    lad_rows = []
    for i, mth in enumerate((6, 9, 12)):
        lad_rows.append({"차수": f"{i + 1}차", "만기": f"{mth}개월(≈{(base.replace(day=1) + pd.DateOffset(months=mth)).strftime('%Y-%m')})", "금액": _won(ladder / 3),
                         "용도": "내년 상반기 주식 재진입 실탄" if mth <= 9 else "재진입이 늦어질 때 대비"})
    st.dataframe(pd.DataFrame(lad_rows), hide_index=True, width="stretch")
    st.caption("현금의 대부분이 퇴직연금(DC) 안에 있으면 사다리는 DC 안에서 원리금보장·단기채(ETF)로 만들고, 즉시 꺼낼 CMA·MMF 몫은 일반계좌에서 맞춰야 합니다.")
    long_gap = max(0.0, tgt_alt * T - cur["장기채·금·대체"])
    st.markdown(f"**장기채·금 보충 {_won(long_gap)}** (금 {gold}% = {_won(gold / 100 * T)}, 나머지는 10·30년 국채·장기채 ETF). 미국 10년물이 {ind.get('us10y', float('nan')):.2f}%로 높아 "
                f"{n}회에 나눠 매달 {_won(long_gap / n)}씩 담는 것을 가정합니다.")

    st.markdown("##### 3️⃣ 무엇을 보고 — 재진입(주식 20% → 60%) 신호")
    hy, us10 = ind.get("hy_spread"), ind.get("us10y")
    s1 = cli in ("바닥 확인", "상승 중")
    s2 = bool(hy is not None and us10 is not None and hy < 5.2 and us10 < 5.0)
    s3 = bool(kospi and kospi <= fair)
    sig = pd.DataFrame([
        {"신호": "① 선행지수 바닥 확인(필수)", "현재": cli, "충족": "✅" if s1 else "❌"},
        {"신호": "② 신용·금리 진정: 하이일드 스프레드 < 5.2%p 그리고 미국 10년 < 5%",
         "현재": f"스프레드 {hy if hy is not None else '-'}%p · 10년물 {us10 if us10 is not None else '-'}%", "충족": "✅" if s2 else ("⚠️ 10년물이 5% 이상" if (hy is not None and hy < 5.2) else "❌")},
        {"신호": f"③ 코스피 ≤ 적정 {fair:,}", "현재": f"{kospi:,.0f}" if kospi else "-", "충족": "✅" if s3 else "❌"},
    ])
    st.dataframe(sig, hide_index=True, width="stretch")
    reenter = (0.60 - 0.20) * T / 3
    ok = [s1, s1 and s2, s1 and s2 and s3]
    rows = [{"차수": f"{i + 1}차", "조건": c_, "금액": _won(reenter), "지금": "🟢 실행" if ok[i] else "⏳ 대기"}
            for i, c_ in enumerate(["① 선행지수 바닥 확인", "① + ② 신용·금리 진정", "① + ② + ③ 적정 지수 이하"])]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"재진입 3차 합계 {_won(reenter * 3)}(총자산의 40%p)는 ②의 단기채 만기 자금과 CMA에서 나옵니다. "
               "교수 판단은 '코스피는 1년 박스권, 저점이 한 단계 더 낮아질 수 있음, 내년 상반기 기회'입니다.")

    st.markdown("##### ✅ 지금 해야 할 일")
    if down:
        st.success(f"하락기: 주식 목표 20%. {'반등 때마다 ' + _won(over / n) + '씩 ' + str(n) + '번에 나눠 줄이고, ' if over > 0 else ''}"
                   f"쉬는 현금은 6·9·12개월 단기채로, 재진입은 선행지수 바닥 확인 후 3단계.")
    else:
        st.success("상승기: 주식 목표 60%. 재진입 3단계 표의 금액을 신호가 나오는 대로 넣으세요.")
    st.caption("※ 투자 권유가 아니라 김영익 교수의 공개된 판단과 원칙을 내 계좌에 대입한 시나리오입니다.")


# ───────────────────────── 진입점 ─────────────────────────


def render_strategies() -> None:
    S = _state()
    if not S:
        st.info("저장된 일일 리포트가 없습니다. `uv run hantoo-daily`를 한 번 실행해 주세요.")
        return
    st.caption("지금 가진 현금을 세 전문가의 방식으로 어떻게 나누고(얼마씩), 몇 차로, 어떤 지표를 보고 넣을지 계산합니다. "
               "일일 리포트의 최신 보유 종목·지표를 쓰며, 투자 권유가 아닙니다.")
    t1, t2, t3, t4 = st.tabs(["📺 서대리TV", "🧮 서준식 교수", "📉 김영익 교수", "⚖️ 한눈에 비교"])
    with t1:
        render_seodaeri(S)
    with t2:
        render_seojunsik(S)
    with t3:
        render_kimyoungik(S)
    with t4:
        st.markdown(
            "| | 서대리TV | 서준식 교수 | 김영익 교수 |\n|---|---|---|---|\n"
            "| 핵심 | 월적립 + 기준가 추매 | 주식·채권 80%, 금리·Yield Gap으로 비중 조절 | 선행지수로 국면 판단 |\n"
            "| 지금 판단 | 월적립 계속, SPY·QQQ 기준가 확인 | PER·금리 입력에 따라 | 하락기: 주식 20%, 현금·단기채 |\n"
            "| 현금 | 총자산 14% 달러 RP·파킹 + 비상금 1년 | 즉시 현금 10% | 현금성 60% (6~12개월 단기채) |\n"
            "| 분할 | 월 1회(25일) 영구 반복 | 매월 1회 n차(가정) | 반등 시 매도 · 신호 3단계 재진입 |\n"
            "| 보는 것 | SPY -20%·QQQ -15%, 환율 10년 평균 | 1/PER − 국고채3년, 3년 금리, 시가배당률 | 선행지수, 신용스프레드, 10년물, 적정 코스피 |\n"
            "| 팔 때 | 팔지 않음 | 시가배당률 5%→2% | 반등마다 비중 축소 |")
        st.caption("세 방식은 서로 반대 방향을 가리킬 수 있습니다(서대리: 계속 산다, 김영익: 줄이고 기다린다). 하나를 고르거나, "
                   "계좌별로 역할을 나누세요(예: 연금저축은 서대리식 적립, DC·일반은 김영익식 방어).")
