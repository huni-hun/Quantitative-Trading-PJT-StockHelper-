import requests
from config.settings import Settings
from utils.logger import get_logger
from utils.error_handler import handle_api_error

logger = get_logger(__name__)


class KISAuth:
    """KIS REST API의 OAuth2 토큰 발급 및 갱신을 처리한다."""

    TOKEN_PATH = "/oauth2/tokenP"

    def __init__(self) -> None:
        self._settings = Settings()
        self._base_url = Settings.get_base_url()
        self.access_token: str = ""

    def authenticate(self) -> str:
        """KIS API에 새 액세스 토큰을 요청한다.

        Returns:
            str: 발급된 액세스 토큰 문자열.

        Raises:
            RuntimeError: 토큰 요청 실패 시 발생.
        """
        Settings.validate()

        url = f"{self._base_url}{self.TOKEN_PATH}"
        payload = {
            "grant_type": "client_credentials",
            "appkey": Settings.APP_KEY,
            "appsecret": Settings.APP_SECRET,
        }

        logger.info("KIS 액세스 토큰 요청 중 (모의투자=%s).", Settings.IS_MOCK)
        response = requests.post(url, json=payload, timeout=10)
        handle_api_error(response)

        data = response.json()
        self.access_token = data.get("access_token", "")
        if not self.access_token:
            raise RuntimeError("API 응답에서 액세스 토큰을 찾을 수 없습니다.")

        logger.info("인증 성공.")
        return self.access_token

    def get_headers(self) -> dict:
        """이후 API 호출에 사용할 공통 인증 헤더를 생성한다.

        Returns:
            dict: Bearer 토큰을 포함한 HTTP 헤더.
        """
        if not self.access_token:
            self.authenticate()

        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": Settings.APP_KEY,
            "appsecret": Settings.APP_SECRET,
        }
