"""관심종목 설정파일(watchlist.yaml) 로딩."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_WATCHLIST_PATH = Path("watchlist.yaml")


@dataclass(frozen=True)
class WatchlistItem:
    code: str
    name: str


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[WatchlistItem]:
    """watchlist.yaml에 등록된 관심종목 목록을 읽는다. 파일이 없으면 빈 목록을 반환한다."""
    if not path.exists():
        return []

    data = yaml.safe_load(path.read_text()) or {}
    items = data.get("watchlist") or []
    return [WatchlistItem(code=str(item["code"]), name=item.get("name", "")) for item in items]
