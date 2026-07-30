"""한국투자증권 Open API 접근토큰(access token) 발급 및 캐싱."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .config import KisConfig

TOKEN_CACHE_PATH = Path.home() / ".cache" / "hantoo_rest_api" / "token.json"

# 토큰 만료 전에 미리 재발급하기 위한 여유 시간
_EXPIRY_MARGIN_SECONDS = 60 * 10


def _cache_key(cfg: KisConfig) -> str:
    """토큰 캐시 키. base_url(실전/모의)만으로는 부족하다 — 계좌를 여러 개
    쓰면(각 계좌가 서로 다른 앱키를 씀, `config.load_configs` 참고) 같은
    실전투자 서버를 공유하는 두 계좌가 base_url만으로 키를 삼으면 서로의
    토큰을 덮어써서 다른 계좌 자격증명으로 API를 호출하는 사고가 날 수
    있다. 그래서 반드시 app_key까지 포함해 계좌별로 캐시를 분리한다.
    """
    return f"{cfg.base_url}:{cfg.app_key}"


def _load_cached_token(cache_key: str) -> str | None:
    """`TOKEN_CACHE_PATH`에서 `cache_key`(base_url+app_key)에 해당하는 토큰을 읽어온다.

    캐시 파일이 없거나 JSON 파싱에 실패하거나(다른 프로세스가 쓰는 도중 등),
    만료 시각이 `_EXPIRY_MARGIN_SECONDS` 이내로 임박했으면 None을 반환해
    상위 호출자가 새로 발급받도록 한다.
    """
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(TOKEN_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    entry = cache.get(cache_key)
    if not entry:
        return None
    if time.time() >= entry.get("expires_at", 0) - _EXPIRY_MARGIN_SECONDS:
        return None
    return entry.get("access_token")


def _save_cached_token(cache_key: str, access_token: str, expires_at: float) -> None:
    """새로 발급받은 토큰을 `cache_key`별로 캐시 파일에 병합 저장한다.

    기존 캐시를 읽어서 병합하는 이유는, 여러 계좌(서버×앱키 조합)의 토큰을
    같은 파일에 함께 캐시하기 때문에 하나를 갱신할 때 다른 항목을 덮어쓰지
    않기 위해서다.
    """
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if TOKEN_CACHE_PATH.exists():
        try:
            cache = json.loads(TOKEN_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}
    cache[cache_key] = {"access_token": access_token, "expires_at": expires_at}
    TOKEN_CACHE_PATH.write_text(json.dumps(cache))


def get_access_token(cfg: KisConfig) -> str:
    """OAuth2 client_credentials 방식으로 접근토큰(access token)을 발급받는다.

    한투 API는 동일 앱키로 토큰을 너무 자주(예: 분당 1회 이상) 재발급 요청하면
    거부 응답을 반환하므로, `~/.cache/hantoo_rest_api/token.json`에 파일 캐시를
    두고 만료 `_EXPIRY_MARGIN_SECONDS`(10분) 전까지는 캐시된 토큰을 재사용한다.
    여러 프로세스(CLI + Streamlit 등)가 같은 캐시 파일을 공유해도 안전하도록
    설계했지만, 동시에 캐시가 만료된 순간 여러 프로세스가 동시에 재발급을
    시도하면 레이스 컨디션이 생길 수 있다(락 없음) — 지금까지는 문제된 적 없지만
    다중 프로세스 환경에서 재발급 오류가 잦아지면 파일 락 도입을 고려할 것.

    여러 계좌(앱키)를 동시에 다룰 때도 안전하도록, 캐시 키에 base_url뿐
    아니라 app_key까지 포함한다 (`_cache_key` 참고).

    Returns:
        Bearer 토큰으로 사용할 access_token 문자열.
    """
    cache_key = _cache_key(cfg)
    cached = _load_cached_token(cache_key)
    if cached:
        return cached

    resp = requests.post(
        f"{cfg.base_url}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": cfg.app_key,
            "appsecret": cfg.app_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    access_token = data["access_token"]
    expires_at = time.time() + int(data.get("expires_in", 86400))
    _save_cached_token(cache_key, access_token, expires_at)
    return access_token
