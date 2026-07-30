"""환경변수 기반 설정 로딩."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
VIRTUAL_BASE_URL = "https://openapivts.koreainvestment.com:29443"


@dataclass(frozen=True)
class KisConfig:
    """`.env`에서 읽어온 한국투자증권(KIS) Open API 접속 정보.

    Attributes:
        app_key: KIS Developers 포털에서 발급받은 앱키.
        app_secret: 앱키와 짝을 이루는 앱시크릿.
        account_no: 계좌번호 10자리 중 앞 8자리(종합계좌번호, CANO 파라미터에 사용).
        account_product_cd: 계좌번호 뒤 2자리(상품코드, ACNT_PRDT_CD 파라미터에 사용).
            일반 위탁계좌는 보통 "01"이지만, 퇴직연금 DC형 계좌는 "55"처럼 특수 코드가
            쓰이며 이 경우 일부 API(체결내역 조회 등)가 지원되지 않는다.
            (`account_activity.is_pension_account`, `PENSION_DC_ACCOUNT_PRODUCT_CD` 참고)
        is_virtual: True면 모의투자 서버(`VIRTUAL_BASE_URL`), False면 실전투자 서버
            (`REAL_BASE_URL`)로 요청을 보낸다. 실전/모의는 API 엔드포인트뿐 아니라
            거래ID(tr_id)도 다른 경우가 많다(예: 계좌잔고 조회는 실전 TTTC8434R,
            모의 VTTC8434R).
    """

    app_key: str
    app_secret: str
    account_no: str
    account_product_cd: str
    is_virtual: bool

    @property
    def base_url(self) -> str:
        """실전/모의 여부에 따른 API 서버 기본 URL."""
        return VIRTUAL_BASE_URL if self.is_virtual else REAL_BASE_URL


def load_config() -> KisConfig:
    """`.env` 파일(및 프로세스 환경변수)에서 `KisConfig`를 구성한다.

    `KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_ACCOUNT_NO`는 필수이며, 하나라도 비어있으면
    어떤 변수가 빠졌는지 알려주는 `RuntimeError`를 던진다. `KIS_ACCOUNT_PRODUCT_CD`는
    기본값 "01", `KIS_IS_VIRTUAL`은 기본값 False(실전투자)로 처리한다.

    Raises:
        RuntimeError: 필수 환경변수가 하나 이상 비어있을 때.
    """
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
