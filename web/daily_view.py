"""모드 2 — 매일 새벽 2시(`hantoo-daily`)에 저장한 일일 리포트 보기.

순서: 날짜 선택 → 매크로 위치 + 지표 → 계좌 탭 → 계좌 분석(통계 + AI 분석).
API를 호출하지 않고 `data/reports.db`만 읽으므로 즉시 뜬다.
"""

from __future__ import annotations

import importlib

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from hantoo_rest_api import daily_db
from hantoo_rest_api.daily_analysis import MACRO_PHASES
from hantoo_rest_api.indicators import ALERT_PCT, STALE_DAYS
from ui_style import inject_tab_css, pct_delta, styled_pl, tab_marker

_MACRO_ORDER = ["성장", "물가", "금리", "신용·위험", "유동성", "환율·달러", "원자재", "주식시장"]
_MACRO_HELP = {
    "성장": "GDP·고용·생산·소비 — 경기 모멘텀",
    "물가": "CPI·PCE·기대인플레 — 긴축/완화 압력",
    "금리": "기준금리·국채 수익률·장단기차 — 할인율과 경기 선행",
    "신용·위험": "VIX·MOVE·하이일드 스프레드 — 시장 스트레스",
    "유동성": "M2·연준 자산·역레포·가상자산 — 돈의 양",
    "환율·달러": "원/달러·DXY — 외국인 수급과 해외자산 평가",
    "원자재": "유가·금·구리 — 인플레/수요 신호",
    "주식시장": "주요 지수·업종·대표 종목 — 위험선호",
}


def _won(v) -> str:
    return "-" if v is None else f"{v:,.0f}원"


def _price(p: dict, fx: float) -> str:
    """가격 표시: 미국 주식은 `$284.78 (≈386,224원)`, 국내는 `188,700원` (3자리마다 쉼표)."""
    if not p["price"]:
        return "-"
    if p["currency"] == "USD":
        return f"${p['price']:,.2f} (≈{p['price'] * fx:,.0f}원)"
    return f"{p['price']:,.0f}원"


def _avg(p: dict, fx: float) -> str:
    if not p["avg_price"]:
        return "-"
    if p["currency"] == "USD":
        return f"${p['avg_price']:,.2f} (≈{p['avg_price'] * fx:,.0f}원)"
    return f"{p['avg_price']:,.0f}원"


_PHASE_DESC = {
    1: "바닥을 지나 경기가 되살아나는 구간. 금리 인하 효과가 나타나고 선행지표(주가·신용스프레드)가 먼저 개선된다. 실물지표는 아직 약하다.",
    2: "성장률·고용·소비가 함께 좋아지는 구간. 물가는 안정적이고 금리는 낮거나 완만하게 오른다. 위험자산(주식)에 가장 우호적이다.",
    3: "성장이 정점에 가까워지며 과열 신호(물가·임금·자산가격 상승)가 나온다. 중앙은행이 긴축으로 돌아서기 시작한다.",
    4: "금리가 높은 수준에서 성장이 꺾이는 구간. 장단기 금리 역전, 기업 이익 둔화, 변동성 확대가 흔하다.",
    5: "생산·소비·고용이 줄어드는 침체 구간. 실업률이 오르고 물가는 내려가며 중앙은행이 완화로 돌아선다.",
    6: "악화 속도가 멈추고 정책 완화·유동성 공급이 효과를 내기 시작하는 구간. 주가는 실물보다 먼저 저점을 만든다.",
}
_PHASE_COLORS = {1: "#4aa36c", 2: "#2b9d8f", 3: "#d9b02b", 4: "#d9822b", 5: "#c0392b", 6: "#7b5ea7"}


def _cycle_svg(phase: int, past: list[tuple[str, int]]) -> str:
    """경기 순환 곡선(6구간). y = 100 + 62·cos(2π(x-30)/460): 왼쪽 끝=바닥, 가운데=정점. ●=오늘, ○=최근 판단."""
    import math

    def y(x: float) -> float:
        return 100 + 62 * math.cos(2 * math.pi * (x - 30) / 460)

    seg = 460 / 6
    parts = ['<svg viewBox="0 0 520 232" width="100%" style="max-width:640px" role="img" aria-label="매크로 주기 곡선">',
             '<line x1="30" y1="100" x2="490" y2="100" stroke="currentColor" stroke-dasharray="4 4" opacity=".35"/>',
             '<text x="492" y="104" font-size="10" fill="currentColor" opacity=".6">평균</text>',
             '<text x="30" y="22" font-size="10" fill="currentColor" opacity=".6">▲ 과열·정점</text>',
             '<text x="30" y="198" font-size="10" fill="currentColor" opacity=".6">▼ 침체·저점</text>']
    for k in range(1, 7):
        x0 = 30 + seg * (k - 1)
        pts = " ".join(f"{x0 + seg * t / 16:.1f},{y(x0 + seg * t / 16):.1f}" for t in range(17))
        cur = k == phase
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{_PHASE_COLORS[k]}" stroke-width="{9 if cur else 5}" '
            f'stroke-linecap="round" opacity="{1 if cur else 0.6}"/>')
        parts.append(
            f'<text x="{x0 + seg / 2:.0f}" y="222" text-anchor="middle" font-size="11" fill="currentColor" '
            f'font-weight="{700 if cur else 400}">{k}. {MACRO_PHASES[k]}</text>')
    for i, (d, k) in enumerate(past):
        x = 30 + seg * (k - 0.5) + (i % 3 - 1) * 5
        parts.append(f'<circle cx="{x:.1f}" cy="{y(x):.1f}" r="5" fill="none" stroke="currentColor" opacity=".45">'
                     f'<title>{d} {MACRO_PHASES[k]}</title></circle>')
    x = 30 + seg * (phase - 0.5)
    arrow = "↗ 상승 국면" if phase <= 3 else "↘ 하락 국면"
    ty = y(x) + (30 if phase in (3, 4) else -14)
    parts.append(f'<circle cx="{x:.1f}" cy="{y(x):.1f}" r="9" fill="{_PHASE_COLORS[phase]}" stroke="#fff" stroke-width="3"/>')
    parts.append(f'<text x="{x:.0f}" y="{ty:.0f}" text-anchor="middle" font-size="12" font-weight="700" '
                 f'fill="currentColor">현재: {MACRO_PHASES[phase]} {arrow}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _macro_basis(analysis: str) -> str:
    """분석 본문의 '1. 매크로 위치' 섹션(다음 ## 전까지) — 근거 상세."""
    import re

    m = re.search(r"^##\s*(?:1\.\s*)?매크로 위치[^\n]*\n(.*?)(?=^##\s|\Z)", analysis, flags=re.S | re.M)
    return m.group(1).strip() if m else ""


def _render_phase(run: dict) -> None:
    import html

    phase = run["phase"]
    if not phase:
        st.info("이 날짜에는 AI 매크로 판단이 없습니다 (AI 분석 생략/실패).")
        return
    st.markdown("**🧭 매크로 주기와 현재 위치**")
    st.caption("곡선은 경기 순환을 단순화한 모형입니다. ●는 이 날짜에 판단한 위치, ○는 최근 판단(마우스를 올리면 날짜).")
    st.markdown(_cycle_svg(phase, daily_db.phase_history(run["date"])), unsafe_allow_html=True)
    st.markdown(
        f"<details open><summary><b>위치 판단 근거</b> <span style='opacity:.6'>({run['date']} · 🤖 {html.escape(run['engine'])})"
        f"</span></summary><p>{html.escape(run['phase_reason'])}</p></details>", unsafe_allow_html=True)
    basis = _macro_basis(run["analysis"])
    if basis:
        with st.expander("축별 지표 근거 (AI 분석 1절)"):
            st.markdown(basis)
    rows = []
    for k in range(1, 7):
        mark = " <b style='color:" + _PHASE_COLORS[k] + "'>← 현재</b>" if k == phase else ""
        weight = "700" if k == phase else "400"
        rows.append(
            f"<div style='margin:.35rem 0;opacity:{1 if k == phase else .7}'><span style='font-weight:{weight};"
            f"border-left:4px solid {_PHASE_COLORS[k]};padding-left:.5rem'>{k}. {MACRO_PHASES[k]}</span>{mark}<br>"
            f"<span style='font-size:.9em'>{_PHASE_DESC[k]}</span></div>")
    st.markdown("".join(rows), unsafe_allow_html=True)


def _signal(i: dict, report_date: str) -> str:
    """신호 열: 이모지 + 이유 (🚨 급변: 전 관측 대비 변화율, 🕰️ 오래됨: 기준일 경과일)."""
    import datetime as dt

    parts = []
    if "🚨" in i["flag"] and i["change_pct"] is not None:
        parts.append(f"🚨 전 관측 대비 {i['change_pct']:+.1f}% 급변 (기준 ±{ALERT_PCT:g}%)"
                     + (f", 직전 {i['prev']:,.2f} → {i['value']:,.2f}" if i["prev"] is not None else ""))
    if "🕰️" in i["flag"]:
        try:
            days = (dt.date.fromisoformat(report_date) - dt.date.fromisoformat(i["as_of"])).days
            parts.append(f"🕰️ 기준일 {i['as_of']} ({days}일 전, {STALE_DAYS}일 초과) — 월·분기 발표 지표")
        except ValueError:
            parts.append("🕰️ 오래된 값")
    return " / ".join(parts)


def _render_indicators(inds: list[dict], report_date: str) -> None:
    inds = [i for i in inds if not i["error"]]
    alerts = [i for i in inds if i["flag"]]
    c1, c2, c3 = st.columns(3)
    c1.metric("수집된 지표", f"{len(inds)}개")
    c2.metric("🚨 급변/🕰️ 오래됨", f"{len(alerts)}개")
    only = c3.toggle("경고·급변만 보기", value=False, key="ind_alert_only")
    if only:
        inds = alerts
    tabs = st.tabs([f"{m}" for m in _MACRO_ORDER])
    for k, (t, m) in enumerate(zip(tabs, _MACRO_ORDER)):
        with t:
            tab_marker(k)
            st.caption(_MACRO_HELP[m])
            rows = [i for i in inds if i["macro"] == m]
            if not rows:
                st.write("표시할 지표가 없습니다.")
                continue
            st.dataframe(
                [{"구분": i["grp"], "지표": i["name"], "값": i["value"], "단위": i["unit"],
                  "변화(%)": None if i["change_pct"] is None else round(i["change_pct"], 2),
                  "1주": i["trend"], "신호·이유": _signal(i, report_date), "기준일": i["as_of"]} for i in rows],
                width="stretch", hide_index=True, key=f"ind_{m}",
            )


def _render_account(label: str, acct: dict, positions: list[dict], idx: int, fx: float) -> None:
    tab_marker(idx)
    if acct["error"]:
        st.error(f"조회 실패: {acct['error']}")
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("총평가", _won(acct["total"]))
    c2.metric("평가손익", _won(acct["pl"]), pct_delta(acct["pl"], acct["purchase"]))
    c3.metric("매입금액", _won(acct["purchase"]))
    c4.metric("예수금·현금", _won(acct["cash"]))
    c5.metric("채권 (매수금액)", _won(acct["bond"]))
    if acct["note"]:
        st.caption(acct["note"])
    if not positions:
        st.info("보유 항목이 없습니다.")
        return
    st.dataframe(
        styled_pl([{"구분": p["kind"], "종목": p["name"], "코드": p["code"], "수량": f"{p['quantity']:,.4g}" if p["quantity"] < 1 else f"{p['quantity']:,.0f}",
          "평균단가": _avg(p, fx), "현재가": _price(p, fx), "매입(원)": f"{p['purchase']:,.0f}원",
          "평가(원)": f"{p['eval']:,.0f}원", "손익(원)": f"{p['pl']:,.0f}원", "손익률": f"{p['pl_rate']:+.2f}%"}
         for p in sorted(positions, key=lambda p: -p["eval"])],
                  ["손익(원)", "손익률"]),
        width="stretch", hide_index=True, key=f"pos_{label}",
    )
    if any(p["currency"] == "USD" and p["price"] for p in positions):
        st.caption(f"미국 주식은 달러 시세 × 당일 원/달러 {fx:,.2f}원으로 환산한 값입니다 (손익은 달러 기준 매입가 대비).")
    kinds: dict[str, float] = {}
    for p in positions:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + p["eval"]
    fig = px.pie(names=list(kinds), values=list(kinds.values()), hole=0.45, title="자산군 비중")
    fig.update_layout(height=300, margin=dict(t=40, b=0, l=0, r=0))
    st.plotly_chart(fig, width="stretch", key=f"pie_{label}")


def _render_analysis(date: str, data: dict) -> None:
    s = data["stats"]
    st.subheader("📊 계좌 통계")
    c1, c2, c3, c4 = st.columns(4)
    delta = f"{s['change']:+,.0f}원 ({s['change_pct']:+.2f}%) vs {s['prev_date']}" if "change" in s else None
    c1.metric("총자산", _won(s["total"]), delta)
    c2.metric("평가손익 (미래에셋 제외 매입 대비)", _won(s["pl"]), f"{s['pl_rate']:+.2f}%")
    c3.metric("현금 비중", f"{s['cash_ratio']:.1f}%")
    c4.metric("달러 자산 비중", f"{s['usd_ratio']:.1f}%")
    c1, c2, c3 = st.columns(3)
    c1.metric("보유 종목 수", f"{s['holding_count']}")
    c2.metric("집중도 (HHI)", f"{s['hhi']:.3f}", help="종목 비중 제곱합. 0.1 미만 분산, 0.25 이상 집중")
    top = s["top_holdings"][0] if s["top_holdings"] else None
    c3.metric("최대 종목 비중", f"{top['weight']:.1f}%" if top else "-", top["name"] if top else None)

    left, right = st.columns(2)
    kinds = {k: v for k, v in s["by_kind"].items() if v > 0}
    fig = px.pie(names=list(kinds), values=list(kinds.values()), hole=0.45, title="자산군별 비중")
    fig.update_layout(height=320, margin=dict(t=40, b=0, l=0, r=0))
    left.plotly_chart(fig, width="stretch", key="an_kind")
    brokers = s["by_broker"]
    fig = px.pie(names=list(brokers), values=list(brokers.values()), hole=0.45, title="증권사별 비중")
    fig.update_layout(height=320, margin=dict(t=40, b=0, l=0, r=0))
    right.plotly_chart(fig, width="stretch", key="an_broker")

    st.markdown("**상위 보유 종목**")
    st.dataframe(
        [{"종목": t["name"], "평가(원)": f'{t["eval"]:,.0f}원', "비중(%)": round(t["weight"], 2)} for t in s["top_holdings"]],
        width="stretch", hide_index=True, key="an_top",
    )
    hist = daily_db.total_history()
    dates = sorted({h["date"] for h in hist})
    if len(dates) >= 2:
        tot = {d: sum(h["total"] for h in hist if h["date"] == d) for d in dates}
        fig = go.Figure(go.Scatter(x=dates, y=[tot[d] for d in dates], mode="lines+markers"))
        fig.update_layout(title="총자산 추이 (저장된 날짜별)", height=300, yaxis_tickformat=",.0f", yaxis_ticksuffix="원", margin=dict(t=40, b=0, l=0, r=0))
        st.plotly_chart(fig, width="stretch", key="an_hist")
    else:
        st.caption("총자산 추이는 2일치 이상 쌓이면 그려집니다.")
    if s.get("errors"):
        st.warning("조회 실패 계좌: " + ", ".join(f"{k} ({v[:60]})" for k, v in s["errors"].items()))

    st.subheader("🧠 AI 분석 · 뉴스 · 조언")
    run = data["run"]
    st.caption(f"생성 {run['created_at']} · 엔진 {run['engine']}")
    st.markdown(run["analysis"])
    st.caption("AI가 작성한 참고용 분석이며 투자 권유가 아닙니다. 뉴스·수치는 원문 확인이 필요합니다.")


def render_daily_report() -> None:
    # 서버가 오래 떠 있으면 `daily_db`가 예전 버전으로 import 캐시된 채 남아 새 함수가 없다고 나온다 — 다시 읽는다.
    importlib.reload(daily_db)
    dates = daily_db.list_dates()
    if not dates:
        st.info("저장된 일일 리포트가 없습니다. `uv run hantoo-daily`를 한 번 실행하거나 새벽 2시 cron을 기다려 주세요.")
        return
    date = st.selectbox("📅 리포트 날짜", dates, index=0, key="daily_date")
    data = daily_db.load_run(date)
    if not data:
        st.error("선택한 날짜의 리포트를 읽지 못했습니다.")
        return
    inject_tab_css()

    st.header("🧭 매크로 위치")
    _render_phase(data["run"])
    st.header("📈 지표")
    _render_indicators(data["indicators"], date)

    fx = data["stats"].get("fx_usdkrw") or next(
        (i["value"] for i in data["indicators"] if i["id"] == "usdkrw" and i["value"]), 0.0)
    st.header("💼 내 계좌")
    accounts = data["accounts"]
    pos_by = {}
    for p in data["positions"]:
        pos_by.setdefault(p["account"], []).append(p)
    tabs = st.tabs([a["label"] for a in accounts])
    for i, (tab, a) in enumerate(zip(tabs, accounts)):
        with tab:
            _render_account(a["label"], a, pos_by.get(a["label"], []), i, fx)

    st.header("🔍 내 계좌 분석")
    _render_analysis(date, data)

    st.header("📉 보유 / 과거 보유 종목 차트")
    _render_charts(date, data["positions"], fx)


_RANGES = {"1개월": 31, "3개월": 92, "6개월": 183, "1년": 366, "3년": 1100, "5년": 1830, "전체": 99999}


def _render_charts(date: str, positions: list[dict], fx_today: float) -> None:
    secs = daily_db.list_securities()
    secs = [s for s in secs if s["n"]]
    if not secs:
        st.info("저장된 종목 시세가 없습니다. `uv run hantoo-daily`가 한 번 돌면 채워집니다.")
        return
    label = {f"{'🟢 보유' if s['held'] else '⚪ 과거'} · {s['name']} ({s['code']})": s for s in secs}
    c1, c2, c3 = st.columns([3, 1, 1])
    sel = c1.selectbox("종목 선택 (한 번에 1개)", list(label), key="chart_sec")
    rng = c2.selectbox("기간", list(_RANGES), index=3, key="chart_rng")
    sec = label[sel]
    usd = sec["currency"] == "USD"
    view = c3.radio("가격 단위", ["$ 달러", "원 환산"], horizontal=True, key="chart_cur") if usd else "원"
    series = daily_db.load_prices(sec["symbol"])
    if not series:
        st.warning("시세 데이터가 없습니다.")
        return
    cutoff = (__import__("datetime").date.fromisoformat(series[-1][0])
              - __import__("datetime").timedelta(days=_RANGES[rng])).isoformat()
    series = [(d, v) for d, v in series if d >= cutoff]
    fxmap = dict(daily_db.load_prices("KRW=X")) if usd else {}
    krw = usd and view.startswith("원")
    if krw:  # 그날 환율로 환산 (그날 환율이 없으면 가장 가까운 이전 값)
        fx_dates = sorted(fxmap)
        last = fx_dates[0] if fx_dates else None
        conv, j = [], 0
        for d, v in series:
            while j < len(fx_dates) and fx_dates[j] <= d:
                last = fx_dates[j]
                j += 1
            conv.append((d, v * fxmap[last] if last else v))
        series = conv
    xs, ys = [d for d, _ in series], [v for _, v in series]
    unit = "원" if (krw or not usd) else "$"
    fmt = (lambda v: f"{v:,.0f}원") if unit == "원" else (lambda v: f"${v:,.2f}")
    m1, m2, m3 = st.columns(3)
    m1.metric("최근 종가", fmt(ys[-1]) + (f" (≈{ys[-1] * fx_today:,.0f}원)" if unit == "$" and fx_today else ""),
              f"{(ys[-1] / ys[-2] - 1) * 100:+.2f}% 전일 대비" if len(ys) > 1 else None)
    m2.metric(f"{rng} 변화", f"{(ys[-1] / ys[0] - 1) * 100:+.1f}%")
    m3.metric(f"{rng} 고가 / 저가", f"{fmt(max(ys))} / {fmt(min(ys))}")
    fig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines", name=sec["name"],
                               hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>"))
    # 20·60일 이동평균
    for w, dash in ((20, "dot"), (60, "dash")):
        if len(ys) > w:
            ma = [sum(ys[max(0, i - w + 1):i + 1]) / min(w, i + 1) for i in range(len(ys))]
            fig.add_trace(go.Scatter(x=xs, y=ma, mode="lines", name=f"{w}일 평균", line=dict(width=1, dash=dash)))
    mine = [p for p in positions if p["code"] == sec["code"] and p["quantity"] > 0 and p["avg_price"]]
    if mine:
        qty = sum(p["quantity"] for p in mine)
        avg = sum(p["avg_price"] * p["quantity"] for p in mine) / qty
        if krw:
            avg *= fx_today
        fig.add_hline(y=avg, line_dash="dash", line_color="orange",
                      annotation_text=f"내 평균단가 {fmt(avg)}", annotation_position="top left")
    fig.update_layout(height=420, margin=dict(t=30, b=10, l=10, r=10), yaxis_title=unit,
                      yaxis_tickformat=",.2f" if unit == "$" else ",.0f")
    st.plotly_chart(fig, width="stretch", key="chart_main")
    held_txt = "현재 보유 중" if sec["held"] else "과거 보유(현재 0주)"
    st.caption(f"{held_txt} · 시세 {len(series)}일치 · 출처 Yahoo Finance(DB에 일일 증분 저장) · "
               f"{'달러 종목: 그날 원/달러로 환산' if krw else ''}")
