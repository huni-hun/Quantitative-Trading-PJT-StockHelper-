"""데드캣 바운스(Dead-cat Bounce) 기술적 분석 전략.

두 가지 고전적인 기술 지표를 활용한 평균회귀(Mean-Reversion) 전략으로,
"데드캣 바운스" 패턴을 탐지한다.

* RSI (상대강도지수) – 과매도 / 과매수 상태를 판별한다.
* 볼린저 밴드 – 가격 변동성을 측정하고 이탈 구간을 탐지한다.

시그널 로직:
    - 매수(BUY):  RSI < rsi_oversold 이고 가격이 하단 볼린저 밴드 이하일 때.
    - 매도(SELL): RSI > rsi_overbought 이거나 가격이 상단 볼린저 밴드 이상일 때.
    - 관망(HOLD): 그 외의 경우.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import TickerInfo
from utils.logger import get_logger

logger = get_logger(__name__)


class DeadcatTechnicalStrategy:
    """RSI + 볼린저 밴드 기반 데드캣 바운스 전략."""

    def __init__(
        self,
        price_api,
        ticker_info: TickerInfo,
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        lookback_days: int = 60,
    ) -> None:
        """
        Args:
            price_api:      초기화된 PriceAPI 인스턴스 (OHLCV 조회에 사용).
            ticker_info:    매매할 종목의 TickerInfo 인스턴스.
            rsi_period:     RSI 계산을 위한 기간(룩백 윈도우).
            bb_period:      볼린저 밴드 계산을 위한 기간.
            bb_std:         밴드 폭에 적용할 표준편차 배수.
            rsi_oversold:   이 값 미만이면 과매도 구간으로 판단.
            rsi_overbought: 이 값 초과이면 과매수 구간으로 판단.
            lookback_days:  OHLCV 조회 기간 (거래일 수).
        """
        self._price_api = price_api
        self.ticker_info = ticker_info
        self.ticker = ticker_info.code   # 로그 출력용 단축 참조
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.lookback_days = lookback_days

    # ------------------------------------------------------------------
    # 데이터 수집
    # ------------------------------------------------------------------

    def fetch_ohlcv(self) -> pd.DataFrame:
        """KIS API로 일봉 OHLCV 데이터를 조회한다.

        Returns:
            pd.DataFrame: ['date','open','high','low','close','volume'] 컬럼.
                          조회 실패 시 빈 DataFrame 반환.
        """
        return self._price_api.get_ohlcv(self.ticker_info, lookback_days=self.lookback_days)

    # ------------------------------------------------------------------
    # 지표 계산
    # ------------------------------------------------------------------

    def compute_rsi(self, close: pd.Series) -> pd.Series:
        """Wilder 방식의 RSI를 계산한다.

        Args:
            close: 종가 pandas Series.

        Returns:
            pd.Series: RSI 값 (0~100). 데이터 부족 시 빈 Series.
        """
        if len(close) < self.rsi_period + 1:
            logger.warning("RSI 계산에 필요한 데이터 부족 (필요: %d, 보유: %d)", self.rsi_period + 1, len(close))
            return pd.Series(dtype=float)

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        # Wilder 평활 이동평균 (EMA with alpha=1/period)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi.name = "rsi"
        return rsi.dropna()

    def compute_bollinger_bands(
        self, close: pd.Series
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """볼린저 밴드(상단·중간·하단)를 계산한다.

        Args:
            close: 종가 pandas Series.

        Returns:
            tuple: (upper_band, middle_band, lower_band) – 각각 pd.Series.
                   데이터 부족 시 세 개 모두 빈 Series.
        """
        if len(close) < self.bb_period:
            logger.warning("볼린저 밴드 계산에 필요한 데이터 부족 (필요: %d, 보유: %d)", self.bb_period, len(close))
            empty = pd.Series(dtype=float)
            return empty, empty, empty

        middle = close.rolling(window=self.bb_period).mean()
        std = close.rolling(window=self.bb_period).std(ddof=0)
        upper = middle + self.bb_std * std
        lower = middle - self.bb_std * std

        # NaN 제거
        valid = middle.notna()
        return upper[valid], middle[valid], lower[valid]

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def generate_signal(self) -> str:
        """전체 전략 파이프라인을 실행하고 매매 시그널을 반환한다.

        Returns:
            str: 'BUY', 'SELL', 'HOLD' 중 하나.
        """
        ohlcv = self.fetch_ohlcv()
        if ohlcv.empty:
            logger.warning("%s – OHLCV 데이터 없음 → HOLD", self.ticker)
            return "HOLD"

        close = ohlcv["close"].astype(float).reset_index(drop=True)

        rsi = self.compute_rsi(close)
        upper_band, _, lower_band = self.compute_bollinger_bands(close)

        if rsi.empty or lower_band.empty:
            logger.warning("%s – 지표 계산 불가 (데이터 부족) → HOLD", self.ticker)
            return "HOLD"

        latest_rsi = float(rsi.iloc[-1])
        latest_close = float(close.iloc[-1])
        latest_lower = float(lower_band.iloc[-1])
        latest_upper = float(upper_band.iloc[-1])

        logger.info(
            "%s 기술 지표 | RSI=%.2f  하단BB=%.0f  상단BB=%.0f  현재가=%.0f",
            self.ticker,
            latest_rsi,
            latest_lower,
            latest_upper,
            latest_close,
        )

        # 매수 조건: 과매도 + 하단밴드 이탈
        if latest_rsi < self.rsi_oversold and latest_close <= latest_lower:
            logger.info("%s 기술 시그널 → BUY (RSI=%.1f, 하단BB 이탈)", self.ticker, latest_rsi)
            return "BUY"

        # 매도 조건: 과매수 또는 상단밴드 돌파
        if latest_rsi > self.rsi_overbought or latest_close >= latest_upper:
            logger.info("%s 기술 시그널 → SELL (RSI=%.1f, 상단BB 돌파)", self.ticker, latest_rsi)
            return "SELL"

        logger.info("%s 기술 시그널 → HOLD", self.ticker)
        return "HOLD"
