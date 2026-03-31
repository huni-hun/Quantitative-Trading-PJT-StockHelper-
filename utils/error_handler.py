from __future__ import annotations

import requests
from utils.logger import get_logger

logger = get_logger(__name__)


class TradingBotError(Exception):
    """트레이딩 봇 전체 예외의 기반 클래스."""


class APIError(TradingBotError):
    """KIS API가 오류 응답을 반환했을 때 발생하는 예외."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"API 오류 {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class AuthenticationError(TradingBotError):
    """KIS API 인증 실패 시 발생하는 예외."""


class OrderError(TradingBotError):
    """주문을 제출할 수 없거나 거부된 경우 발생하는 예외."""


def handle_api_error(response: requests.Response) -> None:
    """HTTP 응답을 검사하고 실패 시 적절한 예외를 발생시킨다.

    Args:
        response: 검사할 :class:`requests.Response` 객체.

    Raises:
        AuthenticationError: HTTP 401 응답인 경우.
        APIError:            그 외 2xx가 아닌 HTTP 응답인 경우.
    """
    if response.ok:
        return

    status = response.status_code
    try:
        body = response.json()
        message = body.get("msg1") or body.get("message") or response.text
    except ValueError:
        message = response.text

    logger.error("API 요청 실패 | 상태코드=%d | 메시지=%s", status, message)

    if status == 401:
        raise AuthenticationError(f"인증 실패: {message}")

    raise APIError(status_code=status, message=message)
