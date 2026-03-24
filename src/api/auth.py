import requests
from config.settings import Settings
from utils.logger import get_logger
from utils.error_handler import handle_api_error

logger = get_logger(__name__)


class KISAuth:
    """Handles OAuth2 token issuance and refresh for the KIS REST API."""

    TOKEN_PATH = "/oauth2/tokenP"

    def __init__(self) -> None:
        self._settings = Settings()
        self._base_url = Settings.get_base_url()
        self.access_token: str = ""

    def authenticate(self) -> str:
        """Request a new access token from the KIS API.

        Returns:
            str: The access token string.

        Raises:
            RuntimeError: If the token request fails.
        """
        Settings.validate()

        url = f"{self._base_url}{self.TOKEN_PATH}"
        payload = {
            "grant_type": "client_credentials",
            "appkey": Settings.APP_KEY,
            "appsecret": Settings.APP_SECRET,
        }

        logger.info("Requesting KIS access token (mock=%s).", Settings.IS_MOCK)
        response = requests.post(url, json=payload, timeout=10)
        handle_api_error(response)

        data = response.json()
        self.access_token = data.get("access_token", "")
        if not self.access_token:
            raise RuntimeError("Access token not found in API response.")

        logger.info("Authentication successful.")
        return self.access_token

    def get_headers(self) -> dict:
        """Build common authorization headers for subsequent API calls.

        Returns:
            dict: HTTP headers including the bearer token.
        """
        if not self.access_token:
            self.authenticate()

        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": Settings.APP_KEY,
            "appsecret": Settings.APP_SECRET,
        }
