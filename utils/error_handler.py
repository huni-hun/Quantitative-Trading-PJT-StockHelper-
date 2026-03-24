from __future__ import annotations

import requests
from utils.logger import get_logger

logger = get_logger(__name__)


class TradingBotError(Exception):
    """Base exception for all trading-bot errors."""


class APIError(TradingBotError):
    """Raised when the KIS API returns an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class AuthenticationError(TradingBotError):
    """Raised when authentication with the KIS API fails."""


class OrderError(TradingBotError):
    """Raised when an order cannot be placed or is rejected."""


def handle_api_error(response: requests.Response) -> None:
    """Inspect an HTTP response and raise an appropriate exception on failure.

    Args:
        response: The :class:`requests.Response` object to inspect.

    Raises:
        AuthenticationError: For HTTP 401 responses.
        APIError:            For any other non-2xx HTTP response.
    """
    if response.ok:
        return

    status = response.status_code
    try:
        body = response.json()
        message = body.get("msg1") or body.get("message") or response.text
    except ValueError:
        message = response.text

    logger.error("API request failed | status=%d | message=%s", status, message)

    if status == 401:
        raise AuthenticationError(f"Authentication failed: {message}")

    raise APIError(status_code=status, message=message)
