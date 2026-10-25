"""키움증권 REST API(https://openapi.kiwoom.com) 계좌 현황/거래내역 조회.

한투와 달리 키움은 요청에 계좌번호를 보내지 않는다 — 앱키(AppKey)가 계좌 하나에
묶여 있어서 앱키마다 계좌가 정해진다. 그래서 계좌 N개 = 앱키 N쌍이고 `.env`에
`KIWOOM_APP_KEY_1`/`KIWOOM_APP_SECRET_1`/`KIWOOM_ACCOUNT_NO_1`/
`KIWOOM_ACCOUNT_LABEL_1`, `_2`, ... 형식으로 등록한다. 계좌번호는 표시용이다.

사용한 API (모두 POST `/api/dostk/acnt`, 헤더 `api-id`로 구분):
- kt00018 계좌평가잔고내역: 보유종목 + 합계
- kt00001 예수금상세현황: 예수금/주문가능금액
- kt00015 위탁종합거래내역: 입출금/매매/배당 등 모든 거래 이력

실측 메모 (연금저축 계좌 2개로 확인):
- 숫자는 앞이 0으로 채워진 문자열("000000001581065"), 종목코드는 "A" 접두사.
- kt00015는 조회기간이 1년 이내로 제한되고(초과 시 return_code 20), 한 번에 20건씩
  주며 응답 헤더 `cont-yn: Y` + `next-key`로 이어서 조회한다. 더 긴 기간은 364일씩
  창을 나눠 거슬러 올라가며 모은다.
- kt00007(주문체결내역)은 당일 주문만 보는 것으로 보여 이력 용도로 쓰지 않았다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.kiwoom.com"
_ACNT_PATH = "/api/dostk/acnt"
_TOKEN_CACHE_PATH = Path.home() / ".cache" / "hantoo_rest_api" / "kiwoom_token.json"
_EXPIRY_MARGIN_SECONDS = 60 * 10
_WINDOW_DAYS = 364  # kt00015 조회기간 상한(1년) 아래로 유지


@dataclass(frozen=True)
class KiwoomConfig:
    app_key: str
    app_secret: str
    account_no: str = ""
    label: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.account_no or "키움"


def load_kiwoom_configs(*, max_accounts: int = 10) -> list[KiwoomConfig]:
    """`.env`의 `KIWOOM_*_N`(N=1,2,...)을 읽는다. 하나도 없으면 빈 목록.

    번호가 중간에 비면 거기서 멈춘다(`config.load_configs`와 같은 규칙).
    """
    load_dotenv()
    configs: list[KiwoomConfig] = []
    for i in range(1, max_accounts + 1):
        key = os.environ.get(f"KIWOOM_APP_KEY_{i}", "")
        secret = os.environ.get(f"KIWOOM_APP_SECRET_{i}", "")
        if not (key and secret):
            break
        configs.append(
            KiwoomConfig(
                app_key=key,
                app_secret=secret,
                account_no=os.environ.get(f"KIWOOM_ACCOUNT_NO_{i}", ""),
                label=os.environ.get(f"KIWOOM_ACCOUNT_LABEL_{i}", ""),
            )
        )
    return configs


def _cache_key(cfg: KiwoomConfig) -> str:
    return hashlib.sha256(cfg.app_key.encode()).hexdigest()[:16]


def get_kiwoom_token(cfg: KiwoomConfig) -> str:
    """접근토큰을 파일 캐시(앱키 해시별)에서 읽고, 없거나 만료 임박이면 새로 발급한다."""
    key = _cache_key(cfg)
    cache: dict = {}
    try:
        cache = json.loads(_TOKEN_CACHE_PATH.read_text())
        entry = cache.get(key)
        if entry and time.time() < entry["expires_at"] - _EXPIRY_MARGIN_SECONDS:
            return entry["token"]
    except (OSError, json.JSONDecodeError, KeyError):
        cache = {}

    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "appkey": cfg.app_key,
            "secretkey": cfg.app_secret,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("return_code") != 0 or not data.get("token"):
        raise RuntimeError(f"키움 토큰 발급 실패: {data.get('return_msg')}")
    # expires_dt는 KST "YYYYMMDDHHMMSS"
    expires_at = (
        dt.datetime.strptime(data["expires_dt"], "%Y%m%d%H%M%S")
        .replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
        .timestamp()
    )
    cache[key] = {"token": data["token"], "expires_at": expires_at}
    _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_CACHE_PATH.write_text(json.dumps(cache))
    try:
        _TOKEN_CACHE_PATH.chmod(0o600)
    except OSError:
        pass
    return data["token"]


def _post(token: str, api_id: str, body: dict, cont_key: str = "") -> tuple[dict, str, str]:
    """(응답 JSON, cont-yn, next-key)를 반환한다. return_code != 0이면 RuntimeError."""
    resp = requests.post(
        f"{BASE_URL}{_ACNT_PATH}",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
            "authorization": f"Bearer {token}",
            "cont-yn": "Y" if cont_key else "",
            "next-key": cont_key,
        },
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("return_code", 0) != 0:
        raise RuntimeError(f"키움 {api_id} 실패: {data.get('return_msg')}")
    return data, resp.headers.get("cont-yn", "N"), resp.headers.get("next-key", "")


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _code(v: str) -> str:
    return v[1:] if v[:1] == "A" and v[1:].isdigit() else v


@dataclass(frozen=True)
class KiwoomHolding:
    code: str
    name: str
    quantity: int
    avg_price: float  # pur_pric
    current_price: float  # cur_prc
    purchase_amount: float  # pur_amt
    eval_amount: float  # evlt_amt
    eval_profit_loss: float  # evltv_prft
    profit_rate: float  # prft_rt (%)
    weight: float  # poss_rt (%)


@dataclass(frozen=True)
class KiwoomSummary:
    purchase_amount: float  # tot_pur_amt
    eval_amount: float  # tot_evlt_amt
    eval_profit_loss: float  # tot_evlt_pl
    profit_rate: float  # tot_prft_rt
    estimated_asset: float  # prsm_dpst_aset_amt (추정예탁자산)
    deposit: float  # kt00001 entr
    orderable: float  # kt00001 ord_alow_amt
    withdrawable: float  # kt00001 pymn_alow_amt


@dataclass(frozen=True)
class KiwoomBalance:
    holdings: list[KiwoomHolding]
    summary: KiwoomSummary


@dataclass(frozen=True)
class KiwoomRecord:
    """위탁종합거래내역 1건. `kind`는 입출금/매매 등(trde_kind_nm), `memo`는 적요."""

    date: dt.date
    time: str
    kind: str  # trde_kind_nm
    memo: str  # rmrk_nm (장내매수, 수익분배금입금 ...)
    io: str  # io_tp_nm (입금/출금/매수/매도)
    code: str
    name: str
    quantity: float  # trde_qty_jwa_cnt
    unit_price: float  # trde_unit
    amount: float  # trde_amt
    commission: float
    settle_amount: float  # exct_amt
    balance: float  # entra_remn (거래 후 예수금)

    @property
    def is_trade(self) -> bool:
        return self.kind == "매매"


def get_kiwoom_balance(cfg: KiwoomConfig, token: str | None = None) -> KiwoomBalance:
    """보유종목(kt00018)과 예수금(kt00001)을 합쳐 현재 상태를 반환한다."""
    token = token or get_kiwoom_token(cfg)
    holdings: list[KiwoomHolding] = []
    cont_key, first = "", None
    for _ in range(10):
        data, cont, nxt = _post(token, "kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"}, cont_key)
        first = first or data
        for i in data.get("acnt_evlt_remn_indv_tot") or []:
            qty = int(_num(i.get("rmnd_qty")))
            if qty <= 0:
                continue
            holdings.append(
                KiwoomHolding(
                    code=_code(i.get("stk_cd", "")),
                    name=i.get("stk_nm", ""),
                    quantity=qty,
                    avg_price=_num(i.get("pur_pric")),
                    current_price=_num(i.get("cur_prc")),
                    purchase_amount=_num(i.get("pur_amt")),
                    eval_amount=_num(i.get("evlt_amt")),
                    eval_profit_loss=_num(i.get("evltv_prft")),
                    profit_rate=_num(i.get("prft_rt")),
                    weight=_num(i.get("poss_rt")),
                )
            )
        if cont != "Y" or not nxt:
            break
        cont_key = nxt
        time.sleep(0.2)
    dep, _, _ = _post(token, "kt00001", {"qry_tp": "3"})
    first = first or {}
    summary = KiwoomSummary(
        purchase_amount=_num(first.get("tot_pur_amt")),
        eval_amount=_num(first.get("tot_evlt_amt")),
        eval_profit_loss=_num(first.get("tot_evlt_pl")),
        profit_rate=_num(first.get("tot_prft_rt")),
        estimated_asset=_num(first.get("prsm_dpst_aset_amt")),
        deposit=_num(dep.get("entr")),
        orderable=_num(dep.get("ord_alow_amt")),
        withdrawable=_num(dep.get("pymn_alow_amt")),
    )
    return KiwoomBalance(holdings=holdings, summary=summary)


def get_kiwoom_history(
    cfg: KiwoomConfig, token: str | None = None, years: int = 10
) -> list[KiwoomRecord]:
    """위탁종합거래내역(kt00015)을 최근 `years`년치 모아 최신순으로 반환한다.

    API가 1년 이내 기간만 받으므로 364일 창을 `years`번 거슬러 올라가며 조회하고,
    창마다 `cont-yn`/`next-key`로 끝까지 읽는다(창당 최대 50페이지).
    """
    token = token or get_kiwoom_token(cfg)
    fmt = lambda d: d.strftime("%Y%m%d")
    end = dt.date.today()
    records: list[KiwoomRecord] = []
    for _ in range(years):
        start = end - dt.timedelta(days=_WINDOW_DAYS - 1)
        body = {
            "strt_dt": fmt(start), "end_dt": fmt(end), "tp": "0", "stk_cd": "",
            "crnc_cd": "", "gds_tp": "0", "frgn_stex_code": "", "dmst_stex_tp": "%",
            "qry_sort_tp": "1",  # 최근 거래순
        }
        cont_key = ""
        for _page in range(50):
            data, cont, nxt = _post(token, "kt00015", body, cont_key)
            for i in data.get("trst_ovrl_trde_prps_array") or []:
                try:
                    d = dt.datetime.strptime(i["trde_dt"], "%Y%m%d").date()
                except (KeyError, ValueError):
                    continue
                records.append(
                    KiwoomRecord(
                        date=d,
                        time=i.get("proc_tm", ""),
                        kind=i.get("trde_kind_nm", ""),
                        memo=i.get("rmrk_nm", ""),
                        io=i.get("io_tp_nm", ""),
                        code=_code(i.get("stk_cd", "")),
                        name=i.get("stk_nm", ""),
                        quantity=_num(i.get("trde_qty_jwa_cnt")),
                        unit_price=_num(i.get("trde_unit")),
                        amount=_num(i.get("trde_amt")),
                        commission=_num(i.get("cmsn")),
                        settle_amount=_num(i.get("exct_amt")),
                        balance=_num(i.get("entra_remn")),
                    )
                )
            if cont != "Y" or not nxt:
                break
            cont_key = nxt
            time.sleep(0.2)
        end = start - dt.timedelta(days=1)
    records.sort(key=lambda r: (r.date, r.time), reverse=True)
    return records
