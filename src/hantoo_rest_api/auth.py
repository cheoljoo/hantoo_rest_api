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


def _load_cached_token(base_url: str) -> str | None:
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(TOKEN_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    entry = cache.get(base_url)
    if not entry:
        return None
    if time.time() >= entry.get("expires_at", 0) - _EXPIRY_MARGIN_SECONDS:
        return None
    return entry.get("access_token")


def _save_cached_token(base_url: str, access_token: str, expires_at: float) -> None:
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if TOKEN_CACHE_PATH.exists():
        try:
            cache = json.loads(TOKEN_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}
    cache[base_url] = {"access_token": access_token, "expires_at": expires_at}
    TOKEN_CACHE_PATH.write_text(json.dumps(cache))


def get_access_token(cfg: KisConfig) -> str:
    """접근토큰을 발급받는다.

    한투 API는 동일 앱키로 토큰을 너무 자주 재발급하면 오류가 나므로,
    파일에 캐시해두고 만료 임박 전까지는 재사용한다.
    """
    cached = _load_cached_token(cfg.base_url)
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
    _save_cached_token(cfg.base_url, access_token, expires_at)
    return access_token
