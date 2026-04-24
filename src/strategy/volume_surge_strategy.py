"""거래량 서프라이즈 + OBV 기반 매매 전략.

뉴스 스크래핑 의존 없이 OHLCV 데이터만으로 강력한 시장 참여 신호를 생성한다.

핵심 지표:
    1. 거래량 서프라이즈 (Volume Surge Ratio, VSR)
       - 현재 거래량 / N일 평균 거래량
       - 급등 시 기관/외국인 수급 집중 신호

    2. OBV (On-Balance Volume) 추세
       - 상승일 : OBV += 거래량
       - 하락일 : OBV -= 거래량
       - OBV 기울기로 매수/매도 압력 방향 판단

    3. VWAP 이탈 (Volume-Weighted Average Price)
       - 현재가 > VWAP → 강세 (매수 우위)
       - 현재가 < VWAP → 약세 (매도 우위)

    4. 가격-거래량 다이버전스
       - 가격 상승 + 거래량 감소 → 추세 약화 경보
       - 가격 하락 + 거래량 급증 → 투매/바닥 신호

시그널 점수 범위: [-1.0, +1.0]
    - BUY  : score >= threshold
    - SELL : score <= -threshold
    - HOLD : 그 외
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import TickerInfo
from src.api.price import PriceAPI
from utils.logger import get_logger

logger = get_logger(__name__)

# 기본 파라미터
_DEFAULT_LOOKBACK     = 60   # OHLCV 기본 조회 기간 (거래일)
_DEFAULT_VSR_WINDOW   = 20   # 거래량 서프라이즈 기준 이동평균 기간
_DEFAULT_OBV_WINDOW   = 14   # OBV 기울기 계산 기간
_DEFAULT_VWAP_WINDOW  = 20   # VWAP 계산 기간
_DEFAULT_THRESHOLD    = 0.25 # 시그널 임계값


class VolumeSurgeStrategy:
    """거래량 서프라이즈 + OBV + VWAP 복합 매매 전략."""

    def __init__(
        self,
        price_api: PriceAPI,
        ticker_info: TickerInfo,
        threshold: float = _DEFAULT_THRESHOLD,
        vsr_window: int = _DEFAULT_VSR_WINDOW,
        obv_window: int = _DEFAULT_OBV_WINDOW,
        vwap_window: int = _DEFAULT_VWAP_WINDOW,
        lookback_days: int = _DEFAULT_LOOKBACK,
    ) -> None:
        self.price_api     = price_api
        self.ticker_info   = ticker_info
        self.ticker        = ticker_info.code
        self.threshold     = threshold
        self.vsr_window    = vsr_window
        self.obv_window    = obv_window
        self.vwap_window   = vwap_window
        self.lookback_days = lookback_days

        # 외부에서 주입 가능한 캐시 (main.py에서 OHLCV 재사용)
        self._cached_ohlcv: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    # OHLCV 로딩
    # ------------------------------------------------------------------

    def _get_ohlcv(self) -> pd.DataFrame:
        if self._cached_ohlcv is not None and not self._cached_ohlcv.empty:
            df = self._cached_ohlcv.copy()
            self._cached_ohlcv = None  # 사용 후 초기화
            return df
        return self.price_api.get_ohlcv(self.ticker_info, lookback_days=self.lookback_days)

    # ------------------------------------------------------------------
    # 지표 계산
    # ------------------------------------------------------------------

    def _calc_vsr(self, df: pd.DataFrame) -> float:
        """거래량 서프라이즈 비율 (현재 거래량 / N일 평균)."""
        vol = df["volume"].astype(float)
        if len(vol) < self.vsr_window + 1:
            return 1.0
        avg_vol = vol.iloc[-(self.vsr_window + 1):-1].mean()
        if avg_vol <= 0:
            return 1.0
        vsr = float(vol.iloc[-1]) / avg_vol
        return vsr

    def _calc_obv_slope(self, df: pd.DataFrame) -> float:
        """OBV 기울기 (선형회귀 기울기를 평균 OBV로 정규화)."""
        close = df["close"].astype(float).values
        vol   = df["volume"].astype(float).values

        obv = np.zeros(len(close))
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + vol[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - vol[i]
            else:
                obv[i] = obv[i - 1]

        window = min(self.obv_window, len(obv))
        recent_obv = obv[-window:]
        if len(recent_obv) < 2:
            return 0.0

        x = np.arange(len(recent_obv), dtype=float)
        slope = float(np.polyfit(x, recent_obv, 1)[0])
        avg_vol_total = float(np.mean(np.abs(vol))) if np.mean(np.abs(vol)) > 0 else 1.0
        return slope / avg_vol_total  # 정규화

    def _calc_vwap_signal(self, df: pd.DataFrame) -> float:
        """VWAP 대비 현재가 위치 신호 (+1 / -1 / 0)."""
        window = min(self.vwap_window, len(df))
        recent = df.iloc[-window:]
        close  = recent["close"].astype(float)
        high   = recent["high"].astype(float)
        low    = recent["low"].astype(float)
        vol    = recent["volume"].astype(float)

        typical = (high + low + close) / 3.0
        vwap = float((typical * vol).sum() / vol.sum()) if vol.sum() > 0 else float(close.iloc[-1])
        current = float(close.iloc[-1])

        diff_pct = (current - vwap) / vwap if vwap > 0 else 0.0
        # ±2% 이상 이탈 시 강한 신호
        if diff_pct >= 0.02:
            return 1.0
        if diff_pct <= -0.02:
            return -1.0
        return diff_pct / 0.02  # 선형 보간

    def _calc_pv_divergence(self, df: pd.DataFrame) -> float:
        """가격-거래량 다이버전스 신호.

        Returns:
            양수: 긍정적 다이버전스 (가격↑ + 거래량↑)
            음수: 부정적 다이버전스 (가격↑ + 거래량↓ or 투매)
        """
        if len(df) < 5:
            return 0.0

        close = df["close"].astype(float)
        vol   = df["volume"].astype(float)

        price_chg = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] if close.iloc[-5] > 0 else 0.0
        vol_chg   = (vol.iloc[-1] - vol.iloc[-5:].mean()) / vol.iloc[-5:].mean() if vol.iloc[-5:].mean() > 0 else 0.0

        # 같은 방향 → 추세 강화, 반대 방향 → 추세 약화/반전
        if price_chg > 0 and vol_chg > 0.3:
            return 0.5   # 상승 + 거래량 급증 → 강한 매수
        if price_chg > 0 and vol_chg < -0.3:
            return -0.3  # 상승 + 거래량 감소 → 추세 약화 경보
        if price_chg < 0 and vol_chg > 0.5:
            return -0.5  # 하락 + 거래량 급증 → 투매 or 바닥 (방향 불명확)
        if price_chg < 0 and vol_chg < -0.3:
            return 0.2   # 하락 + 거래량 감소 → 추세 소진 (반등 기대)
        return 0.0

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def _compute_score(self, df: pd.DataFrame) -> tuple[float, dict]:
        """복합 거래량 점수를 계산한다.

        Returns:
            (score, breakdown): 종합 점수 [-1, +1], 세부 지표 딕셔너리
        """
        vsr        = self._calc_vsr(df)
        obv_slope  = self._calc_obv_slope(df)
        vwap_sig   = self._calc_vwap_signal(df)
        pv_div     = self._calc_pv_divergence(df)

        # VSR → 방향성 점수 변환
        # VSR >= 2.0 → 급등 (±0.5 부여, 방향은 OBV로 결정)
        # VSR <  0.5 → 거래 소강 (신호 약화)
        vsr_score = 0.0
        if vsr >= 3.0:
            vsr_score = 0.6
        elif vsr >= 2.0:
            vsr_score = 0.4
        elif vsr >= 1.5:
            vsr_score = 0.2
        elif vsr <= 0.5:
            vsr_score = -0.1  # 거래 소강 → 약한 부정

        # OBV 기울기 방향 적용
        obv_norm = max(-1.0, min(1.0, obv_slope * 3.0))

        # VSR 방향 = OBV 방향
        vsr_directional = vsr_score * (1.0 if obv_norm >= 0 else -1.0)

        # 가중 합산
        # - OBV 방향성 : 35%
        # - VSR 방향성 : 30%
        # - VWAP 이탈  : 20%
        # - PV 다이버전스: 15%
        score = (
            obv_norm       * 0.35
            + vsr_directional * 0.30
            + vwap_sig       * 0.20
            + pv_div         * 0.15
        )
        score = max(-1.0, min(1.0, score))

        breakdown = {
            "vsr":       round(vsr, 3),
            "obv_slope": round(obv_norm, 4),
            "vwap_sig":  round(vwap_sig, 4),
            "pv_div":    round(pv_div, 4),
            "score":     round(score, 4),
        }
        return score, breakdown

    def generate_signal(self) -> str:
        """시그널만 반환한다."""
        return self.generate_signal_with_score()[0]

    def generate_signal_with_score(self) -> tuple[str, float, dict]:
        """시그널, 점수, 세부 지표를 함께 반환한다."""
        try:
            df = self._get_ohlcv()
            if df is None or df.empty or len(df) < max(self.vsr_window, self.obv_window) + 2:
                logger.warning("[%s] 거래량서프라이즈: OHLCV 데이터 부족 → HOLD 반환", self.ticker)
                return "HOLD", 0.0, {}

            score, breakdown = self._compute_score(df)
            logger.info(
                "[%s] 거래량서프라이즈 점수=%.4f | VSR=%.2f, OBV기울기=%.4f, "
                "VWAP신호=%.4f, PV다이버전스=%.4f",
                self.ticker,
                score,
                breakdown.get("vsr", 0),
                breakdown.get("obv_slope", 0),
                breakdown.get("vwap_sig", 0),
                breakdown.get("pv_div", 0),
            )

            if score >= self.threshold:
                logger.info("[%s] 거래량 시그널 → BUY (score=%.4f)", self.ticker, score)
                return "BUY", score, breakdown
            if score <= -self.threshold:
                logger.info("[%s] 거래량 시그널 → SELL (score=%.4f)", self.ticker, score)
                return "SELL", score, breakdown

            logger.info("[%s] 거래량 시그널 → HOLD (score=%.4f)", self.ticker, score)
            return "HOLD", score, breakdown

        except Exception as exc:
            logger.error("[%s] 거래량서프라이즈 전략 오류: %s → HOLD 반환", self.ticker, exc)
            return "HOLD", 0.0, {}

