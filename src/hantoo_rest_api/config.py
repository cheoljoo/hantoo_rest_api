"""환경변수 기반 설정 로딩."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
VIRTUAL_BASE_URL = "https://openapivts.koreainvestment.com:29443"


@dataclass(frozen=True)
class KisConfig:
    app_key: str
    app_secret: str
    account_no: str
    account_product_cd: str
    is_virtual: bool

    @property
    def base_url(self) -> str:
        return VIRTUAL_BASE_URL if self.is_virtual else REAL_BASE_URL


def load_config() -> KisConfig:
    load_dotenv()

    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    account_no = os.environ.get("KIS_ACCOUNT_NO", "")
    account_product_cd = os.environ.get("KIS_ACCOUNT_PRODUCT_CD", "01")
    is_virtual = os.environ.get("KIS_IS_VIRTUAL", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    missing = [
        name
        for name, value in (
            ("KIS_APP_KEY", app_key),
            ("KIS_APP_SECRET", app_secret),
            ("KIS_ACCOUNT_NO", account_no),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"다음 환경변수가 설정되지 않았습니다: {', '.join(missing)} "
            "(.env 파일을 .env.example 참고해서 만들어주세요)"
        )

    return KisConfig(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        account_product_cd=account_product_cd,
        is_virtual=is_virtual,
    )
