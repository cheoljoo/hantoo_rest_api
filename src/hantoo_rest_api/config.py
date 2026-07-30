"""환경변수 기반 설정 로딩. 계좌 1개 또는 여러 개(번호가 붙은 접미사)를 지원한다."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
VIRTUAL_BASE_URL = "https://openapivts.koreainvestment.com:29443"


@dataclass(frozen=True)
class KisConfig:
    """`.env`에서 읽어온 한국투자증권(KIS) Open API 접속 정보 (계좌 1개당 1개).

    한투 Open API는 신청 시 "보유 계좌 중 1개를 선택해 인증"하는 방식이라(전체계좌
    표시는 필요 없음, README 참고), 앱키/앱시크릿이 계좌 하나에 고정된다(실사용자
    확인: 계좌마다 앱키가 각각 필요함). 계좌를 여러 개 쓰려면(예: 일반 위탁계좌 +
    퇴직연금 계좌) 계좌마다 별도로 앱을 등록하고 별도의 `KisConfig`를 만들어야
    한다 — `load_configs()`가 이 다중 계좌 설정을 담당한다.

    Attributes:
        app_key: KIS Developers 포털에서 발급받은 앱키.
        app_secret: 앱키와 짝을 이루는 앱시크릿.
        account_no: 계좌번호 10자리 중 앞 8자리(종합계좌번호, CANO 파라미터에 사용).
        account_product_cd: 계좌번호 뒤 2자리(상품코드, ACNT_PRDT_CD 파라미터에 사용).
            일반 위탁계좌는 보통 "01"이지만, 퇴직연금 DC형 계좌는 "55"처럼 특수 코드가
            쓰이며 이 경우 일부 API(체결내역 조회, 매수/매도 주문 등)가 지원되지 않는다.
            (`account_activity.is_pension_account`, `PENSION_DC_ACCOUNT_PRODUCT_CD` 참고)
        is_virtual: True면 모의투자 서버(`VIRTUAL_BASE_URL`), False면 실전투자 서버
            (`REAL_BASE_URL`)로 요청을 보낸다. 실전/모의는 API 엔드포인트뿐 아니라
            거래ID(tr_id)도 다른 경우가 많다(예: 계좌잔고 조회는 실전 TTTC8434R,
            모의 VTTC8434R).
        label: 웹 대시보드 등에서 계좌를 구분해 표시할 이름. 지정하지 않으면
            `account_no`를 그대로 쓴다.
    """

    app_key: str
    app_secret: str
    account_no: str
    account_product_cd: str
    is_virtual: bool
    label: str = ""

    @property
    def base_url(self) -> str:
        """실전/모의 여부에 따른 API 서버 기본 URL."""
        return VIRTUAL_BASE_URL if self.is_virtual else REAL_BASE_URL

    @property
    def display_label(self) -> str:
        """빈 label이면 계좌번호로 대체해 항상 표시 가능한 이름을 준다."""
        return self.label or self.account_no


def _load_one(suffix: str) -> KisConfig | None:
    """`suffix`(빈 문자열 또는 "_2", "_3", ...)가 붙은 환경변수 세트 하나를 읽는다.

    필수값(APP_KEY/APP_SECRET/ACCOUNT_NO) 중 하나라도 비어있으면 이 계좌
    프로필 자체가 설정되지 않은 것으로 보고 None을 반환한다 — `load_configs()`가
    이 None을 "더 이상 다음 번호 계좌가 없다"는 신호로 사용해 순회를 멈춘다.
    계좌마다 앱키/앱시크릿이 각각 필요하므로(실사용자 확인), 공유 폴백 없이
    suffix별로 전부 채워져 있어야 한다.
    """
    app_key = os.environ.get(f"KIS_APP_KEY{suffix}", "")
    app_secret = os.environ.get(f"KIS_APP_SECRET{suffix}", "")
    account_no = os.environ.get(f"KIS_ACCOUNT_NO{suffix}", "")
    if not (app_key and app_secret and account_no):
        return None

    account_product_cd = os.environ.get(f"KIS_ACCOUNT_PRODUCT_CD{suffix}", "01")
    is_virtual = os.environ.get(f"KIS_IS_VIRTUAL{suffix}", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    label = os.environ.get(f"KIS_ACCOUNT_LABEL{suffix}", "")

    return KisConfig(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        account_product_cd=account_product_cd,
        is_virtual=is_virtual,
        label=label,
    )


def load_configs(*, max_accounts: int = 10) -> list[KisConfig]:
    """`.env`에서 계좌 1개 이상을 읽어 `KisConfig` 목록으로 반환한다.

    첫 번째 계좌는 접미사 없는 변수(`KIS_APP_KEY` 등)로, 두 번째부터는
    `KIS_APP_KEY_2`, `KIS_APP_KEY_3`, ... 형식의 번호 접미사로 읽는다. 계좌마다
    앱키/앱시크릿이 각각 필요하므로(실사용자 확인) 매번 전체 세트를 채워야
    한다. 번호가 중간에 비어있으면(예: `_2`는 없는데 `_3`은 있는 경우) 그
    지점에서 순회를 멈춘다 — 설정 실수를 조용히 넘어가지 않기 위함이다.
    `max_accounts`는 무한 루프 방지용 상한이며, 실사용에서 이만큼 계좌를
    등록할 일은 거의 없다.

    Raises:
        RuntimeError: 첫 번째(접미사 없는) 계좌조차 필수값이 비어있을 때.
    """
    load_dotenv()

    first = _load_one("")
    if first is None:
        missing = [
            name
            for name, value in (
                ("KIS_APP_KEY", os.environ.get("KIS_APP_KEY", "")),
                ("KIS_APP_SECRET", os.environ.get("KIS_APP_SECRET", "")),
                ("KIS_ACCOUNT_NO", os.environ.get("KIS_ACCOUNT_NO", "")),
            )
            if not value
        ]
        raise RuntimeError(
            f"다음 환경변수가 설정되지 않았습니다: {', '.join(missing)} "
            "(.env 파일을 .env.example 참고해서 만들어주세요)"
        )

    configs = [first]
    for i in range(2, max_accounts + 1):
        cfg = _load_one(f"_{i}")
        if cfg is None:
            break
        configs.append(cfg)
    return configs


def load_config() -> KisConfig:
    """첫 번째(주) 계좌의 `KisConfig`만 필요한 기존 코드(CLI 등)를 위한 하위 호환 함수.

    여러 계좌를 다뤄야 하면 `load_configs()`를 쓸 것.

    Raises:
        RuntimeError: 필수 환경변수가 하나 이상 비어있을 때.
    """
    return load_configs()[0]
