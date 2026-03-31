from __future__ import annotations

import requests
from config.settings import Settings, TickerInfo
from utils.logger import get_logger
from utils.error_handler import handle_api_error

logger = get_logger(__name__)


class OrderAPI:
    """KIS REST API를 통해 국내·해외 시장가 및 지정가 주문을 실행한다."""

    # 국내 주문
    DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
    # 해외 주문
    OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"

    # 국내 tr_id
    _DOMESTIC_TR_IDS = {
        "real": {"buy": "TTTC0802U", "sell": "TTTC0801U"},
        "mock": {"buy": "VTTC0802U", "sell": "VTTC0801U"},
    }

    # 해외 tr_id (실전 매수/매도, 모의 매수/매도)
    _OVERSEAS_TR_IDS = {
        "real": {"buy": "TTTT1002U", "sell": "TTTT1006U"},
        "mock": {"buy": "VTTT1002U", "sell": "VTTT1001U"},
    }

    def __init__(self, auth) -> None:
        """
        Args:
            auth: 인증이 완료된 KISAuth 인스턴스.
        """
        self._auth = auth
        self._base_url = Settings.get_base_url()
        self._env = "mock" if Settings.IS_MOCK else "real"

    # ------------------------------------------------------------------
    # 공개 인터페이스 – 국내/해외 자동 분기
    # ------------------------------------------------------------------

    def market_buy(self, ticker_info: TickerInfo, quantity: int) -> dict:
        """시장가 매수 주문을 제출한다. 국내/해외를 자동으로 분기한다.

        Args:
            ticker_info: TickerInfo 인스턴스.
            quantity:    매수 수량 (주).
        """
        if ticker_info.is_domestic:
            return self._domestic_order(ticker_info.code, quantity, order_type="00", side="buy")
        return self._overseas_order(ticker_info.code, ticker_info.exchange, quantity, side="buy")

    def market_sell(self, ticker_info: TickerInfo, quantity: int) -> dict:
        """시장가 매도 주문을 제출한다. 국내/해외를 자동으로 분기한다.

        Args:
            ticker_info: TickerInfo 인스턴스.
            quantity:    매도 수량 (주).
        """
        if ticker_info.is_domestic:
            return self._domestic_order(ticker_info.code, quantity, order_type="00", side="sell")
        return self._overseas_order(ticker_info.code, ticker_info.exchange, quantity, side="sell")

    def limit_buy(self, ticker_info: TickerInfo, quantity: int, price: float) -> dict:
        """지정가 매수 주문을 제출한다. 국내/해외를 자동으로 분기한다.

        Args:
            ticker_info: TickerInfo 인스턴스.
            quantity:    매수 수량 (주).
            price:       지정가 (국내: 원, 해외: 현지 통화).
        """
        if ticker_info.is_domestic:
            return self._domestic_order(ticker_info.code, quantity, order_type="01", side="buy", price=int(price))
        return self._overseas_order(ticker_info.code, ticker_info.exchange, quantity, side="buy", price=price)

    def limit_sell(self, ticker_info: TickerInfo, quantity: int, price: float) -> dict:
        """지정가 매도 주문을 제출한다. 국내/해외를 자동으로 분기한다.

        Args:
            ticker_info: TickerInfo 인스턴스.
            quantity:    매도 수량 (주).
            price:       지정가 (국내: 원, 해외: 현지 통화).
        """
        if ticker_info.is_domestic:
            return self._domestic_order(ticker_info.code, quantity, order_type="01", side="sell", price=int(price))
        return self._overseas_order(ticker_info.code, ticker_info.exchange, quantity, side="sell", price=price)

    # ------------------------------------------------------------------
    # 국내 주문
    # ------------------------------------------------------------------

    def _domestic_order(
        self,
        ticker: str,
        quantity: int,
        order_type: str,
        side: str,
        price: int = 0,
    ) -> dict:
        """국내 주식 주문을 KIS API에 제출한다.

        Args:
            ticker:     6자리 KRX 종목 코드.
            quantity:   주문 수량.
            order_type: '00' 시장가, '01' 지정가.
            side:       'buy' 또는 'sell'.
            price:      지정가 (시장가 주문 시 0).
        """
        url = f"{self._base_url}{self.DOMESTIC_ORDER_PATH}"
        headers = self._auth.get_headers()
        headers["tr_id"] = self._DOMESTIC_TR_IDS[self._env][side]

        payload = {
            "CANO":        Settings.ACCOUNT_NUMBER[:8],
            "ACNT_PRDT_CD": Settings.ACCOUNT_NUMBER[8:],
            "PDNO":        ticker,
            "ORD_DVSN":    order_type,
            "ORD_QTY":     str(quantity),
            "ORD_UNPR":    str(price),
        }

        logger.info("[국내/%s] %s 주문 | 종목=%s 수량=%d 가격=%d", self._env, side, ticker, quantity, price)
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        handle_api_error(response)

        data = response.json()
        logger.info("[국내] 주문 응답: %s", data)
        return data

    # ------------------------------------------------------------------
    # 해외 주문
    # ------------------------------------------------------------------

    def _overseas_order(
        self,
        ticker: str,
        exchange: str,
        quantity: int,
        side: str,
        price: float = 0.0,
    ) -> dict:
        """해외 주식 주문을 KIS API에 제출한다.

        Args:
            ticker:   해외 종목 티커 (예: AAPL).
            exchange: KIS 거래소 코드 (예: NAS, NYS).
            quantity: 주문 수량.
            side:     'buy' 또는 'sell'.
            price:    지정가 (0이면 시장가로 처리).
        """
        url = f"{self._base_url}{self.OVERSEAS_ORDER_PATH}"
        headers = self._auth.get_headers()
        headers["tr_id"] = self._OVERSEAS_TR_IDS[self._env][side]

        # 시장가: ORD_DVSN='00', 지정가: ORD_DVSN='00' (해외는 지정가도 00 사용, 가격으로 구분)
        # 해외 시장가 주문 시 ORD_UNPR='0'
        payload = {
            "CANO":         Settings.ACCOUNT_NUMBER[:8],
            "ACNT_PRDT_CD": Settings.ACCOUNT_NUMBER[8:],
            "OVRS_EXCG_CD": exchange,
            "PDNO":         ticker,
            "ORD_DVSN":     "00",
            "ORD_QTY":      str(quantity),
            "OVRS_ORD_UNPR": str(price) if price else "0",
        }

        logger.info("[해외:%s/%s] %s 주문 | 종목=%s 수량=%d 가격=%.4f", exchange, self._env, side, ticker, quantity, price)
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        handle_api_error(response)

        data = response.json()
        logger.info("[해외] 주문 응답: %s", data)
        return data
