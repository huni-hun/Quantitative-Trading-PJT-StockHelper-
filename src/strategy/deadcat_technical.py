"""강화된 기술적 분석 전략 – 멀티 지표 점수 시스템.

지표 구성:
    1. RSI (14)              : 과매도(+1) / 과매수(-1)
    2. 볼린저 밴드 (20, 2σ)  : 하단 이탈(+1) / 상단 이탈(-1)
    3. MACD (12/26/9)        : 골든크로스(+1) / 데드크로스(-1), 방향 유지(±0.5)
    4. EMA 추세 필터          : 정배열(+1) / 역배열(-1), 장기 위/아래(±0.5)
    5. 거래량 급증            : 20일 평균 1.5배 이상 급증 시 방향 확증(±0.5)
    6. Stochastic RSI (14,3) : K < 20(+0.5) / K > 80(-0.5)

점수 합산:
    BUY  : 총점 >= buy_threshold  (기본 2.0)
    SELL : 총점 <= sell_threshold (기본 -2.0)
    HOLD : 그 외
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import TickerInfo
from utils.logger import get_logger

logger = get_logger(__name__)

# tanh 정규화 스케일 팩터
# raw score가 이 값일 때 tanh 출력이 약 0.76 → 적당한 강도로 매핑
# 기술 전략 최대 raw score ≈ 5.5, scale=2.0 → tanh(5.5/2.0)=0.99
_SCORE_SCALE = 2.0


def _tanh_norm(score: float) -> float:
    """raw 전략 점수를 tanh로 [-1, +1]에 부드럽게 압착한다."""
    return float(np.tanh(score / _SCORE_SCALE))


class DeadcatTechnicalStrategy:
    """멀티 지표 점수 기반 강화 기술적 분석 전략."""

    def __init__(
        self,
        price_api,
        ticker_info: TickerInfo,
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        stoch_period: int = 14,
        stoch_smooth: int = 3,
        volume_surge_ratio: float = 1.5,
        buy_threshold: float = 2.0,
        sell_threshold: float = -2.0,
        lookback_days: int = 120,
    ) -> None:
        self._price_api = price_api
        self.ticker_info = ticker_info
        self.ticker = ticker_info.code
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.stoch_period = stoch_period
        self.stoch_smooth = stoch_smooth
        self.volume_surge_ratio = volume_surge_ratio
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.lookback_days = lookback_days

    # ------------------------------------------------------------------
    # 데이터 수집
    # ------------------------------------------------------------------

    def fetch_ohlcv(self) -> pd.DataFrame:
        """KIS API로 일봉 OHLCV 데이터를 조회한다.

        main.py에서 _cached_ohlcv 를 주입한 경우 API 호출을 건너뛴다 (중복 방지).
        """
        cached = getattr(self, "_cached_ohlcv", None)
        if cached is not None and not cached.empty:
            self._cached_ohlcv = None   # 1회 사용 후 초기화
            return cached
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

    def compute_macd(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        """MACD 라인, 시그널 라인."""
        if len(close) < self.macd_slow + self.macd_signal:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        return macd_line, signal_line

    def compute_ema(self, close: pd.Series, period: int) -> pd.Series:
        """EMA."""
        if len(close) < period:
            return pd.Series(dtype=float)
        return close.ewm(span=period, adjust=False).mean()

    def compute_stochastic_rsi(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        """Stochastic RSI (K, D)."""
        rsi = self.compute_rsi(close)
        if len(rsi) < self.stoch_period:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        rsi_min = rsi.rolling(self.stoch_period).min()
        rsi_max = rsi.rolling(self.stoch_period).max()
        stoch_k_raw = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan) * 100
        stoch_k = stoch_k_raw.rolling(self.stoch_smooth).mean()
        stoch_d = stoch_k.rolling(self.stoch_smooth).mean()
        return stoch_k.dropna(), stoch_d.dropna()

    def compute_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """ATR (Average True Range) – 변동성 기반 손절 계산에 활용."""
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def generate_signal(self) -> str:
        """멀티 지표 점수 합산으로 매매 시그널을 반환한다."""
        return self.generate_signal_with_score()[0]

    def generate_signal_with_score(self) -> tuple[str, float, dict]:
        """시그널과 점수 상세를 함께 반환한다.

        Returns:
            (signal, total_score, score_breakdown)
        """
        ohlcv = self.fetch_ohlcv()
        if ohlcv.empty or len(ohlcv) < 30:
            logger.warning("%s – OHLCV 데이터 부족 → HOLD", self.ticker)
            return "HOLD", 0.0, {}

        close  = ohlcv["close"].astype(float).reset_index(drop=True)
        high   = ohlcv["high"].astype(float).reset_index(drop=True)
        low    = ohlcv["low"].astype(float).reset_index(drop=True)
        volume = ohlcv["volume"].astype(float).reset_index(drop=True)

        score = 0.0
        breakdown: dict[str, float] = {}

        # ── 1. RSI ─────────────────────────────────────────────────────
        rsi = self.compute_rsi(close)
        if not rsi.empty:
            latest_rsi = float(rsi.iloc[-1])
            if latest_rsi < self.rsi_oversold:
                s = 1.0
            elif latest_rsi > self.rsi_overbought:
                s = -1.0
            else:
                s = 0.0
            score += s
            breakdown["RSI"] = s
            logger.info("%s RSI=%.1f → %+.1f", self.ticker, latest_rsi, s)

        # ── 2. 볼린저 밴드 ──────────────────────────────────────────────
        upper_bb, _, lower_bb = self.compute_bollinger_bands(close)
        if not lower_bb.empty:
            latest_close = float(close.iloc[-1])
            if latest_close <= float(lower_bb.iloc[-1]):
                s = 1.0
            elif latest_close >= float(upper_bb.iloc[-1]):
                s = -1.0
            else:
                s = 0.0
            score += s
            breakdown["BB"] = s
            logger.info(
                "%s BB(하단=%.0f, 상단=%.0f, 현재=%.0f) → %+.1f",
                self.ticker, float(lower_bb.iloc[-1]), float(upper_bb.iloc[-1]), latest_close, s,
            )

        # ── 3. MACD ────────────────────────────────────────────────────
        macd_line, signal_line = self.compute_macd(close)
        if len(macd_line) >= 2 and len(signal_line) >= 2:
            prev_diff = float(macd_line.iloc[-2]) - float(signal_line.iloc[-2])
            curr_diff = float(macd_line.iloc[-1]) - float(signal_line.iloc[-1])
            if prev_diff <= 0 < curr_diff:        # 골든크로스 발생
                s = 1.0
            elif prev_diff >= 0 > curr_diff:      # 데드크로스 발생
                s = -1.0
            elif curr_diff > 0:                   # MACD 양수 유지
                s = 0.5
            elif curr_diff < 0:                   # MACD 음수 유지
                s = -0.5
            else:
                s = 0.0
            score += s
            breakdown["MACD"] = s
            logger.info("%s MACD diff=%.4f → %+.1f", self.ticker, curr_diff, s)

        # ── 4. EMA 추세 필터 (20/50/200) ───────────────────────────────
        ema20  = self.compute_ema(close, 20)
        ema50  = self.compute_ema(close, 50)
        ema200 = self.compute_ema(close, 200)
        if not ema20.empty and not ema50.empty and not ema200.empty:
            e20, e50, e200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])
            if e20 > e50 > e200:
                s = 1.0   # 강세 정배열
            elif e20 < e50 < e200:
                s = -1.0  # 약세 역배열
            elif float(close.iloc[-1]) > e200:
                s = 0.5   # 장기 추세 위
            else:
                s = -0.5  # 장기 추세 아래
            score += s
            breakdown["EMA_trend"] = s
            logger.info("%s EMA(20=%.0f,50=%.0f,200=%.0f) → %+.1f", self.ticker, e20, e50, e200, s)
        elif not ema20.empty and not ema50.empty:
            e20, e50 = float(ema20.iloc[-1]), float(ema50.iloc[-1])
            s = 0.5 if e20 > e50 else -0.5
            score += s
            breakdown["EMA_trend"] = s

        # ── 5. 거래량 급증 확증 ─────────────────────────────────────────
        if len(volume) >= 21:
            avg_vol = float(volume.iloc[-21:-1].mean())
            latest_vol = float(volume.iloc[-1])
            if avg_vol > 0 and latest_vol >= avg_vol * self.volume_surge_ratio:
                direction = breakdown.get("RSI", 0.0)
                if direction != 0:
                    s = 0.5 if direction > 0 else -0.5
                    score += s
                    breakdown["Volume"] = s
                    logger.info(
                        "%s 거래량 급증(%.1f배) 방향 확증 → %+.1f",
                        self.ticker, latest_vol / avg_vol, s,
                    )

        # ── 6. Stochastic RSI ──────────────────────────────────────────
        stoch_k, _ = self.compute_stochastic_rsi(close)
        if not stoch_k.empty:
            k = float(stoch_k.iloc[-1])
            if k < 20:
                s = 0.5
            elif k > 80:
                s = -0.5
            else:
                s = 0.0
            score += s
            breakdown["StochRSI"] = s
            logger.info("%s StochRSI_K=%.1f → %+.1f", self.ticker, k, s)

        # ── 최종 결정 ──────────────────────────────────────────────────
        # tanh 정규화: raw score → [-1.0, +1.0] 연속값으로 압착
        norm_score = _tanh_norm(score)
        logger.info(
            "%s 기술점수 합계(raw=%.2f → norm=%.4f) | 상세=%s",
            self.ticker, score, norm_score, breakdown,
        )

        if score >= self.buy_threshold:
            return "BUY", norm_score, breakdown
        if score <= self.sell_threshold:
            return "SELL", norm_score, breakdown
        return "HOLD", norm_score, breakdown
