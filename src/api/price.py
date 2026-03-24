import requests
from config.settings import Settings
from utils.logger import get_logger
from utils.error_handler import handle_api_error

logger = get_logger(__name__)


class PriceAPI:
    """Fetches real-time and historical stock price data via the KIS REST API."""

    CURRENT_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"

    def __init__(self, auth) -> None:
        """
        Args:
            auth: An authenticated KISAuth instance.
        """
        self._auth = auth
        self._base_url = Settings.get_base_url()

    def get_current_price(self, ticker: str) -> dict:
        """Fetch the current price for a given stock ticker.

        Args:
            ticker: The 6-digit KRX stock code (e.g., '005930' for Samsung).

        Returns:
            dict: Parsed JSON response containing price information.

        Raises:
            requests.HTTPError: If the API returns a non-2xx status.
        """
        url = f"{self._base_url}{self.CURRENT_PRICE_PATH}"
        headers = self._auth.get_headers()
        headers["tr_id"] = "FHKST01010100"

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
        }

        logger.info("Fetching current price for ticker: %s", ticker)
        response = requests.get(url, headers=headers, params=params, timeout=10)
        handle_api_error(response)

        data = response.json()
        logger.debug("Price data for %s: %s", ticker, data)
        return data
