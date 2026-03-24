import requests
from config.settings import Settings
from utils.logger import get_logger
from utils.error_handler import handle_api_error

logger = get_logger(__name__)


class OrderAPI:
    """Executes market and limit orders via the KIS REST API."""

    ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"

    # Transaction IDs differ between real and paper-trading environments
    _TR_IDS = {
        "real": {"buy": "TTTC0802U", "sell": "TTTC0801U"},
        "mock": {"buy": "VTTC0802U", "sell": "VTTC0801U"},
    }

    def __init__(self, auth) -> None:
        """
        Args:
            auth: An authenticated KISAuth instance.
        """
        self._auth = auth
        self._base_url = Settings.get_base_url()
        self._env = "mock" if Settings.IS_MOCK else "real"

    def _place_order(
        self,
        ticker: str,
        quantity: int,
        order_type: str,
        side: str,
        price: int = 0,
    ) -> dict:
        """Internal helper that submits an order to the KIS API.

        Args:
            ticker:     6-digit KRX stock code.
            quantity:   Number of shares.
            order_type: '01' for limit order, '00' for market order.
            side:       'buy' or 'sell'.
            price:      Limit price (0 for market orders).

        Returns:
            dict: Parsed JSON response from the API.
        """
        url = f"{self._base_url}{self.ORDER_PATH}"
        headers = self._auth.get_headers()
        headers["tr_id"] = self._TR_IDS[self._env][side]

        payload = {
            "CANO": Settings.ACCOUNT_NUMBER[:8],
            "ACNT_PRDT_CD": Settings.ACCOUNT_NUMBER[8:],
            "PDNO": ticker,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }

        logger.info(
            "Placing %s %s order | ticker=%s qty=%d price=%d",
            self._env,
            side,
            ticker,
            quantity,
            price,
        )
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        handle_api_error(response)

        data = response.json()
        logger.info("Order response: %s", data)
        return data

    def market_buy(self, ticker: str, quantity: int) -> dict:
        """Submit a market buy order.

        Args:
            ticker:   6-digit KRX stock code.
            quantity: Number of shares to buy.

        Returns:
            dict: API response.
        """
        return self._place_order(ticker, quantity, order_type="00", side="buy")

    def market_sell(self, ticker: str, quantity: int) -> dict:
        """Submit a market sell order.

        Args:
            ticker:   6-digit KRX stock code.
            quantity: Number of shares to sell.

        Returns:
            dict: API response.
        """
        return self._place_order(ticker, quantity, order_type="00", side="sell")

    def limit_buy(self, ticker: str, quantity: int, price: int) -> dict:
        """Submit a limit buy order.

        Args:
            ticker:   6-digit KRX stock code.
            quantity: Number of shares to buy.
            price:    Limit price in KRW.

        Returns:
            dict: API response.
        """
        return self._place_order(
            ticker, quantity, order_type="01", side="buy", price=price
        )

    def limit_sell(self, ticker: str, quantity: int, price: int) -> dict:
        """Submit a limit sell order.

        Args:
            ticker:   6-digit KRX stock code.
            quantity: Number of shares to sell.
            price:    Limit price in KRW.

        Returns:
            dict: API response.
        """
        return self._place_order(
            ticker, quantity, order_type="01", side="sell", price=price
        )
