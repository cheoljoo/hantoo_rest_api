"""관심종목 설정파일(watchlist.yaml) 로딩."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_WATCHLIST_PATH = Path("watchlist.yaml")


@dataclass(frozen=True)
class WatchlistItem:
    """관심종목 한 건. `watchlist.yaml`의 `code`/`name` 필드에 대응한다."""

    code: str  # 6자리 종목코드 (예: "005930")
    name: str  # 표시용 종목명 (예: "삼성전자")


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[WatchlistItem]:
    """`watchlist.yaml`(형식: `watchlist: [{code, name}, ...]`)을 읽어 관심종목 목록을 반환한다.

    파일이 없으면(최초 clone 직후 등) 빈 목록을 반환한다 — 예외를 던지지 않는 이유는
    관심종목 없이도 계좌 조회 기능 자체는 정상 동작해야 하기 때문이다. `watchlist.yaml`은
    이 파일과 달리 종목코드/이름만 담고 금액 정보가 없어 git에 커밋해도 무방하다
    (반면 `transactions.yaml`은 실제 매매 금액을 담아 `.gitignore` 처리된다).
    """
    if not path.exists():
        return []

    data = yaml.safe_load(path.read_text()) or {}
    items = data.get("watchlist") or []
    return [WatchlistItem(code=str(item["code"]), name=item.get("name", "")) for item in items]
