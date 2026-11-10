"""계좌 탭별 배경색 — 탭 버튼과 본문 패널을 같은 색조로 칠해 계좌를 한눈에 구분한다.

각 탭 본문 맨 앞에서 `tab_marker(i)`를 호출하면 그 패널에만 i번째 색이 적용된다
(`:has()` 선택자 사용). 반투명 색이라 라이트/다크 테마 모두에서 읽힌다.
"""

from __future__ import annotations

import streamlit as st

_COLORS = [
    "66,133,244",   # 파랑
    "52,168,83",    # 초록
    "251,140,0",    # 주황
    "171,71,188",   # 보라
    "0,172,193",    # 청록
    "229,57,53",    # 빨강
    "158,157,36",   # 올리브
    "236,64,122",   # 분홍
    "121,85,72",    # 갈색
    "96,125,139",   # 청회색
    "124,179,66",   # 연두
    "3,155,229",    # 하늘
]


def inject_tab_css() -> None:
    """Streamlit 버전별로 탭 DOM이 달라(1.60: `data-testid="stTab"`/`stTabPanel`, 이전: baseweb) 두 가지 선택자를 모두 쓴다."""
    rules = []
    for i, c in enumerate(_COLORS):
        n = i + 1
        tabs = [
            f'[data-testid="stTabs"] [role="tablist"] > [data-testid="stTab"]:nth-child({n})',
            f'[data-testid="stTabs"] [data-testid="stTab"]:nth-of-type({n})',
            f'div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-of-type({n})',
        ]
        panels = [f'[data-testid="stTabPanel"]:has(.tab-bg-{i})', f'div[data-baseweb="tab-panel"]:has(.tab-bg-{i})']
        rules.append(
            f'{",".join(panels)}{{background:rgba({c},0.12) !important;border-radius:10px;'
            f"padding:1rem 1.2rem;border:1px solid rgba({c},0.45);}}"
        )
        # 탭 제목: 평소엔 옅게, 선택된 탭은 진하게 + 아래 굵은 선
        rules.append(
            f'{",".join(tabs)}{{background:rgba({c},0.30) !important;border-radius:8px 8px 0 0;'
            f"margin-right:3px;padding:0.45rem 0.9rem;}}"
        )
        sel = [f'{t}[aria-selected="true"]' for t in tabs]
        rules.append(
            f'{",".join(sel)}{{background:rgba({c},0.80) !important;box-shadow:inset 0 -4px 0 rgb({c});}}'
        )
        rules.append(f'{",".join(t + "[aria-selected=true] *" for t in tabs)}{{font-weight:700 !important;}}')
    st.markdown(f"<style>{''.join(rules)}</style>", unsafe_allow_html=True)


def tab_marker(i: int) -> None:
    st.markdown(f'<span class="tab-bg-{i % len(_COLORS)}"></span>', unsafe_allow_html=True)


def pct_delta(pl, purchase) -> str | None:
    """평가손익의 매입금액 대비 % — st.metric의 delta로 넘기면 +는 초록, -는 빨강으로 칠해진다."""
    if not purchase or pl is None:
        return None
    return f"{pl / purchase * 100:+.2f}%"


def _pl_color(v) -> str:
    """부호별 글자색(Streamlit delta와 같은 초록/빨강). 숫자·`-1,234원`·`-$1.00` 문자열 모두 처리."""
    neg = pos = False
    if isinstance(v, (int, float)):
        neg, pos = v < 0, v > 0
    elif isinstance(v, str):
        t = v.strip()
        neg = t.startswith("-")
        pos = bool(t) and not neg and any(ch.isdigit() for ch in t) and t.lstrip("+$₩").lstrip()[:1] not in ("0", "")  \
            and any(ch in "123456789" for ch in t)
    return "color:#ff2b2b;font-weight:600" if neg else "color:#09ab3b;font-weight:600" if pos else ""


def styled_pl(rows: list[dict], cols: list[str]):
    """손익 관련 열(`cols`)을 부호별 색으로 칠한 표. 손익률(%) 숫자 열은 `+1.23%` 형식으로 보여 준다."""
    import pandas as pd

    df = pd.DataFrame(rows)
    use = [c for c in cols if c in df.columns]
    sty = df.style.map(_pl_color, subset=use) if use else df.style
    pct = {c: "{:+.2f}%" for c in use if "(%)" in c and pd.api.types.is_numeric_dtype(df[c])}
    return sty.format(pct, na_rep="") if pct else sty
