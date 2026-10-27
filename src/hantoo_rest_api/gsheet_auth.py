"""`uv run hantoo-gsheet-auth` — 구글 OAuth(데스크톱 클라이언트)로 시트 접근 토큰을 한 번 발급한다.

브라우저가 이 PC에 없어도 되도록 2단계로 나눴다.
  1) `hantoo-gsheet-auth url`            → 동의 URL 출력 (브라우저에서 열어 승인)
  2) `hantoo-gsheet-auth code "<주소>"`  → 승인 후 이동한 주소(http://localhost:8765/?code=...,
     페이지는 열리지 않아도 됨)의 전체 URL 또는 code 값을 붙여넣으면 토큰 저장

기본 권한은 시트 읽기+쓰기(`spreadsheets`)이고 `--readonly`를 주면 읽기 전용이다.
토큰은 ~/.config/hantoo/gsheet_token.json (0600)에 저장되며 refresh_token으로 자동 갱신된다.
OAuth 동의 화면이 "테스트" 상태면 refresh_token이 7일 뒤 만료되므로 그때 다시 실행한다.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from google_auth_oauthlib.flow import Flow

# 리다이렉트가 http://localhost(루프백)라서 oauthlib의 https 강제를 풀어야 한다. 구글이 돌려주는
# scope 순서/표기가 요청과 다르다고 실패하지 않게 scope 검사도 완화한다.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

REDIRECT_URI = "http://localhost:8765/"
CONFIG_DIR = Path.home() / ".config" / "hantoo"
TOKEN_PATH = CONFIG_DIR / "gsheet_token.json"
_PENDING = CONFIG_DIR / "gsheet_auth_pending.json"
SCOPE_RW = "https://www.googleapis.com/auth/spreadsheets"
SCOPE_RO = "https://www.googleapis.com/auth/spreadsheets.readonly"


def client_secret_path() -> str:
    load_dotenv(find_dotenv(usecwd=True))
    p = os.environ.get("GSHEET_OAUTH_CLIENT", "").strip()
    if p:
        return p
    found = sorted(glob.glob(str(Path(find_dotenv(usecwd=True)).parent / "client_secret*.json")))
    if not found:
        raise SystemExit("OAuth 클라이언트 JSON을 찾지 못했습니다 (.env의 GSHEET_OAUTH_CLIENT 지정)")
    return found[0]


def _flow(scope: str) -> Flow:
    return Flow.from_client_secrets_file(client_secret_path(), scopes=[scope], redirect_uri=REDIRECT_URI)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["url", "code"])
    ap.add_argument("value", nargs="?", help="step=code일 때 리다이렉트된 전체 URL 또는 code")
    ap.add_argument("--readonly", action="store_true", help="읽기 전용 권한만 요청")
    args = ap.parse_args()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if args.step == "url":
        scope = SCOPE_RO if args.readonly else SCOPE_RW
        flow = _flow(scope)
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        _PENDING.write_text(json.dumps({"scope": scope, "state": state, "verifier": flow.code_verifier}))
        _PENDING.chmod(0o600)
        print("아래 URL을 브라우저에서 열어 승인하세요 (이 PC가 아니어도 됩니다):\n")
        print(url)
        print('\n승인 후 주소창이 http://localhost:8765/?code=... 로 바뀌면(페이지 오류는 정상) 그 전체 주소를 복사해\n'
              'uv run hantoo-gsheet-auth code "<복사한 주소>" 로 실행하세요.')
        return

    if not args.value or not _PENDING.exists():
        raise SystemExit("먼저 `hantoo-gsheet-auth url`을 실행하고 code 값을 함께 주세요")
    pending = json.loads(_PENDING.read_text())
    flow = _flow(pending["scope"])
    flow.code_verifier = pending["verifier"]
    v = args.value.strip()
    if v.startswith("http"):
        flow.fetch_token(authorization_response=v)
    else:
        flow.fetch_token(code=v)
    TOKEN_PATH.write_text(flow.credentials.to_json())
    TOKEN_PATH.chmod(0o600)
    _PENDING.unlink(missing_ok=True)
    print(f"토큰 저장: {TOKEN_PATH} (권한: {pending['scope'].rsplit('/', 1)[-1]})")


if __name__ == "__main__":
    main()
