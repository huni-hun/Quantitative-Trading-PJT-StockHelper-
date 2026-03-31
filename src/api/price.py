from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import requests

from config.settings import Settings, TickerInfo
from utils.logger import get_logger
from utils.error_handler import handle_api_error

logger = get_logger(__name__)


class PriceAPI:
    """KIS REST API를 통해 국내·해외 실시간 및 과거 주가 데이터를 조회한다."""

    # 국내
    DOMESTIC_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
    DOMESTIC_OHLCV_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

    # 해외
    OVERSEAS_PRICE_PATH = "/uapi/overseas-price/v1/quotations/price"
    OVERSEAS_OHLCV_PATH = "/uapi/overseas-price/v1/quotations/dailyprice"

    def __init__(self, auth) -> None:
        """
        Args:
            auth: 인증이 완료된 KISAuth 인스턴스.
        """
        self._auth = auth
        self._base_url = Settings.get_base_url()

    # ------------------------------------------------------------------
    # 공개 인터페이스 – 국내/해외 자동 분기
    # ------------------------------------------------------------------

    def get_current_price(self, ticker_info: TickerInfo) -> dict:
        """종목의 현재 시세를 조회한다. 국내/해외를 자동으로 분기한다.

        Args:
            ticker_info: TickerInfo 인스턴스.

        Returns:
            dict: 시세 정보가 담긴 JSON 응답.
        """
        if ticker_info.is_domestic:
            return self._get_domestic_price(ticker_info.code)
        return self._get_overseas_price(ticker_info.code, ticker_info.exchange)

    def get_ohlcv(self, ticker_info: TickerInfo, lookback_days: int = 60) -> pd.DataFrame:
        """일봉 OHLCV 데이터를 조회한다. 국내/해외를 자동으로 분기한다.

        Args:
            ticker_info:   TickerInfo 인스턴스.
            lookback_days: 조회할 과거 거래일 수.

        Returns:
            pd.DataFrame: ['date','open','high','low','close','volume'] 컬럼.
        """
        if ticker_info.is_domestic:
            return self._get_domestic_ohlcv(ticker_info.code, lookback_days)
        return self._get_overseas_ohlcv(ticker_info.code, ticker_info.exchange, lookback_days)

    # ------------------------------------------------------------------
    # 국내 주식
    # ------------------------------------------------------------------

    def _get_domestic_price(self, ticker: str) -> dict:
        """국내 종목의 현재 시세를 조회한다."""
        url = f"{self._base_url}{self.DOMESTIC_PRICE_PATH}"
        headers = self._auth.get_headers()
        headers["tr_id"] = "FHKST01010100"

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
        }

        logger.info("[국내] 현재 시세 조회 | 종목: %s", ticker)
        response = requests.get(url, headers=headers, params=params, timeout=10)
        handle_api_error(response)
        return response.json()

    def _get_domestic_ohlcv(self, ticker: str, lookback_days: int) -> pd.DataFrame:
        """국내 종목의 일봉 OHLCV를 조회한다."""
        url = f"{self._base_url}{self.DOMESTIC_OHLCV_PATH}"
        tr_id = "FHKST03010100"

        end_date = datetime.today()
        start_date = end_date - timedelta(days=int(lookback_days * 1.7))
        all_rows: list[dict] = []
        current_end = end_date

        for _ in range(3):
            headers = self._auth.get_headers()
            headers["tr_id"] = tr_id
            params = {
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": ticker,
                "fid_input_date_1": start_date.strftime("%Y%m%d"),
                "fid_input_date_2": current_end.strftime("%Y%m%d"),
                "fid_period_div_code": "D",
                "fid_org_adj_prc": "0",
            }
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                handle_api_error(response)
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[국내] OHLCV 조회 실패 | %s: %s", ticker, exc)
                return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

            for item in data.get("output2", []):
                try:
                    all_rows.append({
                        "date":   item["stck_bsop_date"],
                        "open":   int(item["stck_oprc"]),
                        "high":   int(item["stck_hgpr"]),
                        "low":    int(item["stck_lwpr"]),
                        "close":  int(item["stck_clpr"]),
                        "volume": int(item["acml_vol"]),
                    })
                except (KeyError, ValueError):
                    continue

            if len(all_rows) >= lookback_days or not all_rows:
                break

            oldest = min(r["date"] for r in all_rows)
            current_end = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)
            if current_end < start_date:
                break

        return self._to_dataframe(all_rows, ticker, lookback_days)

    # ------------------------------------------------------------------
    # 해외 주식
    # ------------------------------------------------------------------

    def _get_overseas_price(self, ticker: str, exchange: str) -> dict:
        """해외 종목의 현재 시세를 조회한다.

        Args:
            ticker:   해외 종목 티커 (예: AAPL).
            exchange: KIS 거래소 코드 (예: NAS, NYS, TSE).
        """
        url = f"{self._base_url}{self.OVERSEAS_PRICE_PATH}"
        headers = self._auth.get_headers()
        # 실전/모의 tr_id 동일
        headers["tr_id"] = "HHDFS00000300"

        params = {
            "AUTH": "",
            "EXCD": exchange,   # 거래소 코드
            "SYMB": ticker,     # 종목 티커
        }

        logger.info("[해외:%s] 현재 시세 조회 | 종목: %s", exchange, ticker)
        response = requests.get(url, headers=headers, params=params, timeout=10)
        handle_api_error(response)
        return response.json()

    def _get_overseas_ohlcv(self, ticker: str, exchange: str, lookback_days: int) -> pd.DataFrame:
        """해외 종목의 일봉 OHLCV를 조회한다.

        Args:
            ticker:        해외 종목 티커.
            exchange:      KIS 거래소 코드.
            lookback_days: 조회할 과거 거래일 수.
        """
        url = f"{self._base_url}{self.OVERSEAS_OHLCV_PATH}"
        headers = self._auth.get_headers()
        headers["tr_id"] = "HHDFS76240000"

        end_date = datetime.today()
        all_rows: list[dict] = []

        # 해외 API는 기간 지정 없이 최근 N건을 반환하므로 한 번만 호출
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": ticker,
            "GUBN": "0",    # 0=일봉
            "BYMD": end_date.strftime("%Y%m%d"),
            "MODP": "1",    # 수정주가
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            handle_api_error(response)
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[해외:%s] OHLCV 조회 실패 | %s: %s", exchange, ticker, exc)
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        for item in data.get("output2", []):
            try:
                all_rows.append({
                    "date":   item["xymd"],               # YYYYMMDD
                    "open":   float(item["open"]),
                    "high":   float(item["high"]),
                    "low":    float(item["low"]),
                    "close":  float(item["clos"]),
                    "volume": int(item["tvol"]),
                })
            except (KeyError, ValueError):
                continue

        return self._to_dataframe(all_rows, ticker, lookback_days)

    # ------------------------------------------------------------------
    # 공통 유틸
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dataframe(rows: list[dict], ticker: str, lookback_days: int) -> pd.DataFrame:
        """수집한 행 목록을 정제된 DataFrame으로 변환한다."""
        if not rows:
            logger.warning("%s OHLCV 데이터를 가져오지 못했습니다.", ticker)
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        df = df.tail(lookback_days).reset_index(drop=True)

        logger.info("%s OHLCV %d건 조회 완료.", ticker, len(df))
        return df
