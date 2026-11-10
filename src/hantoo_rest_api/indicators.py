"""매일 수집하는 금융·거시 지표 (Yahoo Finance + FRED + 한국은행 ECOS).

각 지표는 `macro`(성장/물가/금리/신용·위험/환율·달러/원자재/주식시장/유동성/가상자산)
분류를 달고 있어 일일 리포트에서 "이 지표가 거시의 어느 축에 속하는지"를 보여 준다.
한 지표가 실패해도 나머지는 계속 수집한다 (실패 사유는 `error`에 남김).
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

import requests

# (id, 이름, 그룹, macro 분류, 출처, 심볼/시리즈, 단위, 변환)
# 변환: "" 그대로 | "yoy" 전년 동기 대비 % (월간 지수)
_SPECS: list[tuple] = [
    # ── 주가지수 ──
    ("kospi", "KOSPI", "주가지수", "주식시장", "yf", "^KS11", "pt", ""),
    ("kosdaq", "KOSDAQ", "주가지수", "주식시장", "yf", "^KQ11", "pt", ""),
    ("sp500", "S&P 500", "주가지수", "주식시장", "yf", "^GSPC", "pt", ""),
    ("nasdaq", "NASDAQ 종합", "주가지수", "주식시장", "yf", "^IXIC", "pt", ""),
    ("ndx", "NASDAQ 100", "주가지수", "주식시장", "yf", "^NDX", "pt", ""),
    ("dow", "다우존스", "주가지수", "주식시장", "yf", "^DJI", "pt", ""),
    ("russell2000", "러셀 2000 (소형주)", "주가지수", "주식시장", "yf", "^RUT", "pt", ""),
    ("nikkei", "닛케이 225", "주가지수", "주식시장", "yf", "^N225", "pt", ""),
    ("hsi", "항셍", "주가지수", "주식시장", "yf", "^HSI", "pt", ""),
    ("csi300", "CSI 300", "주가지수", "주식시장", "yf", "000300.SS", "pt", ""),
    ("stoxx50", "EuroStoxx 50", "주가지수", "주식시장", "yf", "^STOXX50E", "pt", ""),
    ("dax", "DAX", "주가지수", "주식시장", "yf", "^GDAXI", "pt", ""),
    # ── 업종·ETF·대표 종목 ──
    ("soxx", "반도체 ETF (SOXX)", "업종·ETF", "주식시장", "yf", "SOXX", "USD", ""),
    ("smh", "반도체 ETF (SMH)", "업종·ETF", "주식시장", "yf", "SMH", "USD", ""),
    ("ewy", "한국 ETF (EWY)", "업종·ETF", "주식시장", "yf", "EWY", "USD", ""),
    ("xlk", "미국 기술주 (XLK)", "업종·ETF", "주식시장", "yf", "XLK", "USD", ""),
    ("xle", "미국 에너지 (XLE)", "업종·ETF", "주식시장", "yf", "XLE", "USD", ""),
    ("xlf", "미국 금융 (XLF)", "업종·ETF", "주식시장", "yf", "XLF", "USD", ""),
    ("samsung", "삼성전자", "대표 종목", "주식시장", "yf", "005930.KS", "원", ""),
    ("hynix", "SK하이닉스", "대표 종목", "주식시장", "yf", "000660.KS", "원", ""),
    ("googl", "알파벳 A", "대표 종목", "주식시장", "yf", "GOOGL", "USD", ""),
    ("nvda", "엔비디아", "대표 종목", "주식시장", "yf", "NVDA", "USD", ""),
    # ── 변동성·위험 ──
    ("vix", "VIX (공포지수)", "변동성", "신용·위험", "yf", "^VIX", "pt", ""),
    ("move", "MOVE (채권 변동성)", "변동성", "신용·위험", "yf", "^MOVE", "pt", ""),
    ("hy_spread", "하이일드 스프레드", "신용", "신용·위험", "fred", "BAMLH0A0HYM2", "%p", ""),
    ("hyg", "하이일드 채권 ETF (HYG)", "신용", "신용·위험", "yf", "HYG", "USD", ""),
    ("nfci", "시카고연은 금융여건지수", "신용", "신용·위험", "fred", "NFCI", "pt", ""),
    # ── 금리 ──
    ("fedfunds", "미국 기준금리 (실효)", "금리", "금리", "fred", "FEDFUNDS", "%", ""),
    ("us2y", "미국 국채 2년", "금리", "금리", "fred", "DGS2", "%", ""),
    ("us10y", "미국 국채 10년", "금리", "금리", "fred", "DGS10", "%", ""),
    ("us30y", "미국 국채 30년", "금리", "금리", "fred", "DGS30", "%", ""),
    ("t10y2y", "장단기 금리차 (10Y-2Y)", "금리", "금리", "fred", "T10Y2Y", "%p", ""),
    ("t10y3m", "장단기 금리차 (10Y-3M)", "금리", "금리", "fred", "T10Y3M", "%p", ""),
    ("real10y", "미국 10년 실질금리", "금리", "금리", "fred", "DFII10", "%", ""),
    ("breakeven10y", "10년 기대인플레이션", "금리", "물가", "fred", "T10YIE", "%", ""),
    ("jp10y", "일본 국채 10년", "금리", "금리", "fred", "IRLTLT01JPM156N", "%", ""),
    ("de10y", "독일 국채 10년", "금리", "금리", "fred", "IRLTLT01DEM156N", "%", ""),
    ("kr_base", "한국 기준금리", "금리", "금리", "ecos", "722Y001:0101000:M", "%", ""),
    ("tlt", "미국 장기채 ETF (TLT)", "금리", "금리", "yf", "TLT", "USD", ""),
    # ── 성장·고용 ──
    ("gdp", "미국 실질 GDP 성장률 (분기, 연율)", "성장", "성장", "fred", "A191RL1Q225SBEA", "%", ""),
    ("unrate", "미국 실업률", "고용", "성장", "fred", "UNRATE", "%", ""),
    ("payems", "미국 비농업 고용 (전년비)", "고용", "성장", "fred", "PAYEMS", "%", "yoy"),
    ("icsa", "미국 신규 실업수당 청구", "고용", "성장", "fred", "ICSA", "건", ""),
    ("indpro", "미국 산업생산 (전년비)", "성장", "성장", "fred", "INDPRO", "%", "yoy"),
    ("retail", "미국 소매판매 (전년비)", "성장", "성장", "fred", "RSAFS", "%", "yoy"),
    ("umcsent", "미시간대 소비자심리", "성장", "성장", "fred", "UMCSENT", "pt", ""),
    ("houst", "미국 주택착공", "성장", "성장", "fred", "HOUST", "천 호", ""),
    # ── 물가 ──
    ("cpi", "미국 CPI (전년비)", "물가", "물가", "fred", "CPIAUCSL", "%", "yoy"),
    ("core_cpi", "미국 근원 CPI (전년비)", "물가", "물가", "fred", "CPILFESL", "%", "yoy"),
    ("pce", "미국 PCE 물가 (전년비)", "물가", "물가", "fred", "PCEPI", "%", "yoy"),
    # ── 유동성 ──
    ("m2", "미국 M2 (전년비)", "유동성", "유동성", "fred", "M2SL", "%", "yoy"),
    ("walcl", "연준 총자산", "유동성", "유동성", "fred", "WALCL", "백만 USD", ""),
    ("rrp", "연준 역레포 잔액", "유동성", "유동성", "fred", "RRPONTSYD", "십억 USD", ""),
    # ── 환율·달러 ──
    ("usdkrw", "원/달러", "환율", "환율·달러", "yf", "KRW=X", "원", ""),
    ("usdjpy", "엔/달러", "환율", "환율·달러", "yf", "JPY=X", "엔", ""),
    ("usdcny", "위안/달러", "환율", "환율·달러", "yf", "CNY=X", "위안", ""),
    ("eurusd", "유로/달러", "환율", "환율·달러", "yf", "EURUSD=X", "USD", ""),
    ("dxy", "달러 인덱스 (DXY)", "환율", "환율·달러", "yf", "DX-Y.NYB", "pt", ""),
    # ── 원자재 ──
    ("gold", "금 선물", "원자재", "원자재", "yf", "GC=F", "USD/oz", ""),
    ("silver", "은 선물", "원자재", "원자재", "yf", "SI=F", "USD/oz", ""),
    ("wti", "WTI 원유", "원자재", "원자재", "yf", "CL=F", "USD/bbl", ""),
    ("brent", "브렌트유", "원자재", "원자재", "yf", "BZ=F", "USD/bbl", ""),
    ("copper", "구리 선물", "원자재", "원자재", "yf", "HG=F", "USD/lb", ""),
    ("natgas", "천연가스", "원자재", "원자재", "yf", "NG=F", "USD", ""),
    # ── 가상자산 ──
    ("btc", "비트코인", "가상자산", "유동성", "yf", "BTC-USD", "USD", ""),
    ("eth", "이더리움", "가상자산", "유동성", "yf", "ETH-USD", "USD", ""),
]

ALERT_PCT = 5.0  # 전 관측 대비 ±5% 이상이면 경고
STALE_DAYS = 90


@dataclass
class Indicator:
    id: str
    name: str
    group: str
    macro: str
    source: str
    symbol: str
    unit: str
    value: float | None = None
    prev: float | None = None
    week_ago: float | None = None
    change_pct: float | None = None
    trend: str = ""  # ▲ ▼ ▬ (최근 1주)
    as_of: str = ""
    flag: str = ""  # 🚨 경고 / 🕰️ 오래됨
    error: str = ""
    history: list[tuple[str, float]] = field(default_factory=list)


def _finish(ind: Indicator, series: list[tuple[str, float]], monthly: bool) -> None:
    """(날짜, 값) 오름차순 시계열로 지표의 파생값을 채운다."""
    series = [(d, v) for d, v in series if v == v]
    if not series:
        ind.error = "데이터 없음"
        return
    ind.history = series[-60:]
    ind.as_of, ind.value = series[-1]
    if len(series) >= 2:
        ind.prev = series[-2][1]
        if ind.prev:
            ind.change_pct = (ind.value - ind.prev) / abs(ind.prev) * 100
    ref_idx = -2 if monthly else -6
    if len(series) >= -ref_idx:
        ind.week_ago = series[ref_idx][1]
        base = ind.week_ago
        if base:
            d = (ind.value - base) / abs(base) * 100
            ind.trend = "▲" if d > 0.5 else "▼" if d < -0.5 else "▬"
    flags = []
    if ind.change_pct is not None and abs(ind.change_pct) >= ALERT_PCT and ind.unit not in ("%", "%p"):
        flags.append("🚨")
    try:
        age = (dt.date.today() - dt.date.fromisoformat(ind.as_of)).days
        if age > STALE_DAYS:
            flags.append("🕰️")
    except ValueError:
        pass
    ind.flag = "".join(flags)


def _fetch_yf(inds: list[Indicator]) -> None:
    import yfinance as yf

    tickers = [i.symbol for i in inds]
    try:
        df = yf.download(tickers, period="3mo", interval="1d", progress=False, auto_adjust=False,
                         group_by="ticker", threads=True)
    except Exception as e:
        for i in inds:
            i.error = f"yfinance 실패: {e}"
        return
    for i in inds:
        try:
            col = df[i.symbol]["Close"] if len(tickers) > 1 else df["Close"]
            col = col.dropna()
            _finish(i, [(d.strftime("%Y-%m-%d"), float(v)) for d, v in col.items()], monthly=False)
        except Exception as e:
            i.error = f"조회 실패: {e}"


def _fetch_fred(inds: list[Indicator]) -> None:
    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        for i in inds:
            i.error = "FRED_API_KEY 없음"
        return
    start = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    for i in inds:
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": i.symbol, "api_key": key, "file_type": "json",
                        "observation_start": start},
                timeout=20,
            )
            r.raise_for_status()
            obs = [(o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] not in (".", "")]
            yoy = i.id in _YOY
            if yoy:
                obs = [(d, (v / obs[k - 12][1] - 1) * 100) for k, (d, v) in enumerate(obs) if k >= 12]
            # 일간 시리즈는 1주=5관측, 월·분기 시리즈는 직전 발표와 비교
            daily = i.symbol in _DAILY_FRED
            _finish(i, obs, monthly=not daily)
        except Exception as e:
            i.error = f"FRED 실패: {e}"


def _fetch_ecos(inds: list[Indicator]) -> None:
    key = os.environ.get("ECOS_API_KEY", "")
    for i in inds:
        if not key:
            i.error = "ECOS_API_KEY 없음"
            continue
        try:
            stat, item, cycle = i.symbol.split(":")
            end = dt.date.today().strftime("%Y%m")
            start = (dt.date.today() - dt.timedelta(days=800)).strftime("%Y%m")
            url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/{stat}/{cycle}/{start}/{end}/{item}"
            rows = requests.get(url, timeout=20).json()["StatisticSearch"]["row"]
            obs = [(f"{r['TIME'][:4]}-{r['TIME'][4:6]}-01", float(r["DATA_VALUE"])) for r in rows]
            _finish(i, obs, monthly=True)
        except Exception as e:
            i.error = f"ECOS 실패: {e}"


_YOY = {s[0] for s in _SPECS if s[7] == "yoy"}
_DAILY_FRED = {"DGS2", "DGS10", "DGS30", "T10Y2Y", "T10Y3M", "DFII10", "T10YIE", "BAMLH0A0HYM2",
               "RRPONTSYD"}


def collect_indicators() -> list[Indicator]:
    inds = [Indicator(id=s[0], name=s[1], group=s[2], macro=s[3], source=s[4], symbol=s[5], unit=s[6])
            for s in _SPECS]
    _fetch_yf([i for i in inds if i.source == "yf"])
    _fetch_fred([i for i in inds if i.source == "fred"])
    _fetch_ecos([i for i in inds if i.source == "ecos"])
    return inds
