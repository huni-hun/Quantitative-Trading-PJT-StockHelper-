"""모멘텀(추세 추종) 전략.

평균회귀 기반인 기술적 분석 전략과 반대 성격의 추세 추종 전략.
강한 상승 추세를 탑승하고, 하락 추세에서 청산한다.

지표 구성:
    1. EMA 20/50 크로스        : 골든크로스(+2) / 데드크로스(-2)
    2. 52일 신고가 근접 (5%)   : 브레이크아웃 모멘텀(+1.5)
    3. 가격 모멘텀 (20일 수익률): 상위 모멘텀(+1) / 하위 모멘텀(-1)
    4. ADX (추세 강도 필터)     : ADX > 25이면 추세 신호 유효, 미만이면 감쇠
    5. 고점 대비 낙폭 필터      : -15% 이상 하락 시 추가 확증

점수 합산:
    BUY  : 총점 >= buy_threshold  (기본 2.5)
    SELL : 총점 <= sell_threshold (기본 -2.5)
    HOLD : 그 외
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import TickerInfo
from utils.logger import get_logger

logger = get_logger(__name__)

# tanh 정규화 스케일 팩터
# 모멘텀 최대 raw score ≈ 4.5 (EMA_cross=2+Breakout=1.5+Momentum=1)
# scale=2.0 → tanh(4.5/2.0) ≈ 0.978 → 거의 1.0에 수렴
_SCORE_SCALE = 2.0


def _tanh_norm(score: float) -> float:
    """raw 전략 점수를 tanh로 [-1, +1]에 부드럽게 압착한다."""
    return float(np.tanh(score / _SCORE_SCALE))


class MomentumStrategy:
    """EMA 크로스 + 52일 신고가 + ADX 기반 모멘텀 전략."""

    def __init__(
        self,
        price_api,
        ticker_info: TickerInfo,
        ema_fast: int = 20,
        ema_slow: int = 50,
        momentum_period: int = 20,
        high_window: int = 52,
        high_proximity_pct: float = 0.05,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        buy_threshold: float = 2.5,
        sell_threshold: float = -2.5,
        lookback_days: int = 120,
    ) -> None:
        self._price_api = price_api
        self.ticker_info = ticker_info
        self.ticker = ticker_info.code
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.momentum_period = momentum_period
        self.high_window = high_window
        self.high_proximity_pct = high_proximity_pct
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.lookback_days = lookback_days

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

    def compute_ema(self, close: pd.Series, period: int) -> pd.Series:
        if len(close) < period:
            return pd.Series(dtype=float)
        return close.ewm(span=period, adjust=False).mean()

    def compute_adx(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """ADX (Average Directional Index) – 추세 강도 지표."""
        if len(close) < period * 2:
            return pd.Series(dtype=float)

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Directional Movement
        up_move   = high.diff()
        down_move = -low.diff()
        dm_plus  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        dm_plus  = pd.Series(dm_plus,  index=close.index)
        dm_minus = pd.Series(dm_minus, index=close.index)

        atr   = tr.ewm(span=period, adjust=False).mean()
        di_plus  = 100 * dm_plus.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan)
        di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan)

        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
        adx = dx.ewm(span=period, adjust=False).mean()
        return adx.dropna()

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def generate_signal(self) -> str:
        return self.generate_signal_with_score()[0]

    def generate_signal_with_score(self) -> tuple[str, float, dict]:
        """모멘텀 시그널과 점수를 반환한다.

        Returns:
            (signal, total_score, breakdown)
        """
        ohlcv = self.fetch_ohlcv()
        if ohlcv.empty or len(ohlcv) < max(self.ema_slow, self.high_window, 30):
            logger.warning("%s [모멘텀] OHLCV 데이터 부족 → HOLD", self.ticker)
            return "HOLD", 0.0, {}

        close  = ohlcv["close"].astype(float).reset_index(drop=True)
        high   = ohlcv["high"].astype(float).reset_index(drop=True)
        low    = ohlcv["low"].astype(float).reset_index(drop=True)

        score = 0.0
        breakdown: dict[str, float] = {}

        # ── 1. EMA 20/50 크로스 ────────────────────────────────────────
        ema_f = self.compute_ema(close, self.ema_fast)
        ema_s = self.compute_ema(close, self.ema_slow)
        if len(ema_f) >= 2 and len(ema_s) >= 2:
            prev_gap = float(ema_f.iloc[-2]) - float(ema_s.iloc[-2])
            curr_gap = float(ema_f.iloc[-1]) - float(ema_s.iloc[-1])
            if prev_gap <= 0 < curr_gap:        # 골든크로스
                s = 2.0
            elif prev_gap >= 0 > curr_gap:      # 데드크로스
                s = -2.0
            elif curr_gap > 0:                  # 정배열 유지
                s = 1.0
            elif curr_gap < 0:                  # 역배열 유지
                s = -1.0
            else:
                s = 0.0
            score += s
            breakdown["EMA_cross"] = s
            logger.info(
                "%s [모멘텀] EMA(%d/%d) gap=%.2f → %+.1f",
                self.ticker, self.ema_fast, self.ema_slow, curr_gap, s,
            )

        # ── 2. 52일(high_window) 신고가 근접 ──────────────────────────
        window = min(self.high_window, len(close))
        recent_high = float(close.iloc[-window:].max())
        latest_close = float(close.iloc[-1])
        if recent_high > 0:
            proximity = (recent_high - latest_close) / recent_high
            if proximity <= self.high_proximity_pct:           # 신고가 5% 이내 = 브레이크아웃
                s = 1.5
            elif latest_close >= recent_high * 0.9:            # 고점 90% 이상
                s = 0.5
            elif proximity >= 0.20:                            # 고점에서 20% 이상 하락
                s = -1.0
            else:
                s = 0.0
            score += s
            breakdown["Breakout"] = s
            logger.info(
                "%s [모멘텀] %d일고점=%.0f, 현재=%.0f(%.1f%% 하락) → %+.1f",
                self.ticker, window, recent_high, latest_close, proximity * 100, s,
            )

        # ── 3. 가격 모멘텀 (N일 수익률) ───────────────────────────────
        if len(close) > self.momentum_period:
            prev_price = float(close.iloc[-self.momentum_period - 1])
            if prev_price > 0:
                momentum_return = (latest_close - prev_price) / prev_price
                if momentum_return >= 0.10:
                    s = 1.0   # 10% 이상 상승 모멘텀
                elif momentum_return >= 0.03:
                    s = 0.5   # 3% 이상 상승
                elif momentum_return <= -0.10:
                    s = -1.0  # 10% 이상 하락 모멘텀
                elif momentum_return <= -0.03:
                    s = -0.5  # 3% 이상 하락
                else:
                    s = 0.0
                score += s
                breakdown["Momentum_ret"] = s
                logger.info(
                    "%s [모멘텀] %d일수익률=%.2f%% → %+.1f",
                    self.ticker, self.momentum_period, momentum_return * 100, s,
                )

        # ── 4. ADX 추세 강도 필터 ──────────────────────────────────────
        adx = self.compute_adx(high, low, close, self.adx_period)
        adx_multiplier = 1.0
        if not adx.empty:
            adx_val = float(adx.iloc[-1])
            if adx_val >= self.adx_threshold:
                adx_multiplier = 1.0    # 강한 추세 → 신호 그대로
                breakdown["ADX"] = adx_val
                logger.info("%s [모멘텀] ADX=%.1f (강한 추세 유효)", self.ticker, adx_val)
            elif adx_val >= 15:
                adx_multiplier = 0.6    # 약한 추세 → 점수 감쇠
                breakdown["ADX"] = adx_val
                logger.info("%s [모멘텀] ADX=%.1f (약한 추세 → 점수 60%%)", self.ticker, adx_val)
            else:
                adx_multiplier = 0.3    # 횡보 → 대폭 감쇠
                breakdown["ADX"] = adx_val
                logger.info("%s [모멘텀] ADX=%.1f (횡보 → 점수 30%%)", self.ticker, adx_val)
            # ADX는 점수에 더하는 방식 대신 multiplier로 사용
            score *= adx_multiplier

        logger.info("%s [모멘텀] 총점(raw=%.2f) | 상세=%s", self.ticker, score, breakdown)

        # tanh 정규화: raw score → [-1.0, +1.0] 연속값으로 압착
        norm_score = _tanh_norm(score)
        logger.info("%s [모멘텀] norm_score=%.4f", self.ticker, norm_score)

        if score >= self.buy_threshold:
            return "BUY", norm_score, breakdown
        if score <= self.sell_threshold:
            return "SELL", norm_score, breakdown
        return "HOLD", norm_score, breakdown




