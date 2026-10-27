"""구글시트(my_finance)의 ALL 탭을 읽어 API로 볼 수 없는 자산(미래에셋/SBi/토스/DC 보험·TDF 등)을 가져온다.

읽는 방법은 `.env`로 고른다.
- `GSHEET_SA_FILE`이 있으면: 서비스 계정 JSON으로 Sheets API(values.get)를 호출한다. 시트를
  그 계정 이메일(client_email)에 뷰어로만 공유하면 되므로 "링크 공개" 없이 비공개로 쓸 수 있다.
- 없고 OAuth 토큰(~/.config/hantoo/gsheet_token.json, `hantoo-gsheet-auth`로 발급)이 있으면: 그 사용자
  권한으로 Sheets API를 호출한다. 토큰이 읽기+쓰기 권한이면 `write_range`로 시트 수정도 된다.
- 둘 다 없으면: 시트가 "링크가 있는 모든 사용자"로 공유된 경우에 한해 gviz CSV 주소로 읽는다.

금액 칸은 시트 표시 문자열("₩1,234", "-₩5", "12.3%")을 숫자로 바꿔서 읽는다.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from urllib.parse import quote

import requests
from dotenv import find_dotenv, load_dotenv

_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


@dataclass(frozen=True)
class SheetRow:
    broker: str  # 증권사
    kind: str  # 종류
    account: str  # 통장
    ticker: str
    name: str  # 종목
    quantity: float
    buy_price: float  # 산가격
    buy_date: str  # 산날짜
    buy_total: float  # 산총합
    price: float  # 현재주당가격
    eval_amount: float  # 현재총가격
    note: str


def _num(s: str) -> float:
    s = (s or "").strip().replace("₩", "").replace(",", "").replace("%", "")
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _oauth_credentials():
    """OAuth 토큰 파일이 있으면 (필요 시 갱신해서) Credentials를, 없으면 None."""
    from .gsheet_auth import TOKEN_PATH

    path = os.environ.get("GSHEET_OAUTH_TOKEN", "").strip() or str(TOKEN_PATH)
    if not os.path.exists(path):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(path)
    if not creds.valid:
        creds.refresh(Request())
        with open(path, "w") as f:
            f.write(creds.to_json())
    return creds


def _api_values(token: str, sheet_id: str, name: str) -> list[list[str]]:
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(name)}",
        headers={"Authorization": f"Bearer {token}"},
        params={"valueRenderOption": "FORMATTED_VALUE"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("values", [])


def write_range(a1_range: str, values: list[list]) -> dict:
    """OAuth 토큰(읽기+쓰기 권한)으로 시트 범위를 덮어쓴다. 예: write_range("ALL!Q2", [["메모"]]).

    읽기 전용 토큰/서비스 계정이면 403이 난다. 값은 사용자가 직접 입력한 것처럼 해석된다(USER_ENTERED).
    """
    load_dotenv(find_dotenv(usecwd=True))
    creds = _oauth_credentials()
    if creds is None:
        raise RuntimeError("OAuth 토큰이 없습니다 (hantoo-gsheet-auth 실행 필요)")
    sheet_id = os.environ["GSHEET_ID"].strip()
    resp = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(a1_range)}",
        headers={"Authorization": f"Bearer {creds.token}"},
        params={"valueInputOption": "USER_ENTERED"},
        json={"values": values},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_values() -> list[list[str]]:
    load_dotenv(find_dotenv(usecwd=True))
    sheet_id = os.environ.get("GSHEET_ID", "").strip()
    name = os.environ.get("GSHEET_NAME", "ALL").strip() or "ALL"
    if not sheet_id:
        raise RuntimeError("GSHEET_ID가 .env에 없습니다")
    sa_file = os.environ.get("GSHEET_SA_FILE", "").strip()
    if sa_file:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(sa_file, scopes=[_SCOPE])
        creds.refresh(Request())
        resp = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(name)}",
            headers={"Authorization": f"Bearer {creds.token}"},
            params={"valueRenderOption": "FORMATTED_VALUE"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("values", [])
    oauth = _oauth_credentials()
    if oauth is not None:
        return _api_values(oauth.token, sheet_id, name)
    resp = requests.get(
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq",
        params={"tqx": "out:csv", "sheet": name},
        timeout=20,
    )
    if resp.status_code != 200 or resp.text.lstrip().startswith("<"):
        raise RuntimeError(
            f"시트를 읽지 못했습니다 (HTTP {resp.status_code}). 링크 공유가 꺼져 있다면 "
            "GSHEET_SA_FILE(서비스 계정)을 설정하세요"
        )
    return list(csv.reader(io.StringIO(resp.content.decode("utf-8"))))


def fetch_sheet_rows() -> list[SheetRow]:
    """ALL 탭의 보유 행(증권사 칸이 채워진 행)을 읽는다."""
    values = _fetch_values()

    def cell(r: list[str], i: int) -> str:
        return r[i].strip() if i < len(r) else ""

    rows: list[SheetRow] = []
    for r in values[1:]:
        if not cell(r, 0) or not cell(r, 8):
            continue
        rows.append(
            SheetRow(
                broker=cell(r, 0), kind=cell(r, 1), account=cell(r, 2),
                ticker=cell(r, 3), name=cell(r, 8),
                quantity=_num(cell(r, 10)), buy_price=_num(cell(r, 9)),
                buy_date=cell(r, 11), buy_total=_num(cell(r, 12)),
                price=_num(cell(r, 13)), eval_amount=_num(cell(r, 14)),
                note=cell(r, 16),
            )
        )
    return rows
