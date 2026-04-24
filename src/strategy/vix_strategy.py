"""VIX (공포지수) 기반 시장 심리 전략.

Yahoo Finance에서 CBOE VIX 지수를 실시간으로 조회하여 매매 점수를 생성한다.
VIX는 S&P500 옵션의 내재 변동성으로, 시장 참여자들의 공포/탐욕 심리를 반영한다.

점수 로직:
    VIX < 13       : +0.8  (극도의 탐욕 – 단기 매수 우호)
    VIX 13 ~ 16    : +0.4  (시장 안정, 낮은 공포)
    VIX 16 ~ 20    : +0.1  (평온, 중립에 가까운 매수)
    VIX 20 ~ 25    :  0.0  (중립 구간)
    VIX 25 ~ 30    : -0.3  (공포 증가 – 매도 경계)
    VIX 30 ~ 40    : -0.6  (높은 공포 – 매도 우위)
    VIX > 40       : -0.9  (패닉 – 강한 매도 압력)

※ 역발상 투자 옵션 (contrarian):
    VIX > 40이면 과도한 공포로 반등 가능성이 있어,
    contrarian=True 설정 시 VIX > 40 구간에서 점수를 반전.

캐싱: 30분 간격으로 갱신 (Yahoo Finance 과호출 방지).
"""

from __future__ import annotations

import time
import threading

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# Yahoo Finance VIX URL
_VIX_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d"
_HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 캐시 TTL (초) – 30분
_CACHE_TTL = 1800

# VIX 구간별 점수 테이블 (상한, 점수)
_VIX_SCORE_TABLE: list[tuple[float, float]] = [
    (13.0,  0.8),   # VIX < 13
    (16.0,  0.4),   # 13 ~ 16
    (20.0,  0.1),   # 16 ~ 20
    (25.0,  0.0),   # 20 ~ 25
    (30.0, -0.3),   # 25 ~ 30
    (40.0, -0.6),   # 30 ~ 40
    (float("inf"), -0.9),  # > 40
]


def _vix_to_score(vix: float, contrarian: bool = False) -> float:
    """VIX 값을 [-1.0, +1.0] 범위의 매매 점수로 변환한다."""
    for upper, score in _VIX_SCORE_TABLE:
        if vix < upper:
            if contrarian and vix >= 40.0:
                # 패닉 구간 역발상: 과도한 공포 → 반등 기대
                return 0.5
            return score
    return -0.9


class VIXStrategy:
    """VIX 공포지수 기반 시장 심리 전략."""

    def __init__(
        self,
        threshold: float     = 0.25,
        contrarian: bool     = False,
        cache_ttl: int       = _CACHE_TTL,
    ) -> None:
        """
        Args:
            threshold:   시그널 생성 임계값 (기본 0.25).
            contrarian:  역발상 모드 – VIX>40 구간에서 매수 신호 허용.
            cache_ttl:   VIX 캐시 갱신 주기(초), 기본 30분.
        """
        self.threshold  = threshold
        self.contrarian = contrarian
        self.cache_ttl  = cache_ttl

        self._cached_vix:   float | None = None
        self._cached_at:    float        = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # VIX 조회 (캐싱)
    # ------------------------------------------------------------------

    def fetch_vix(self) -> float | None:
        """Yahoo Finance에서 VIX 현재값을 조회한다.

        Returns:
            float: VIX 값, 조회 실패 시 None.
        """
        with self._lock:
            now = time.time()
            if self._cached_vix is not None and (now - self._cached_at) < self.cache_ttl:
                logger.debug("VIX 캐시 사용: %.2f (%.0f초 전 조회)", self._cached_vix, now - self._cached_at)
                return self._cached_vix

        try:
            resp = requests.get(_VIX_URL, headers=_HEADERS, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            vix = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            with self._lock:
                self._cached_vix = vix
                self._cached_at  = time.time()
            logger.info("VIX 조회 성공: %.2f", vix)
            return vix
        except Exception as exc:
            logger.warning("VIX 조회 실패: %s – 이전 캐시값 사용", exc)
            return self._cached_vix  # 실패 시 이전 캐시값 반환 (없으면 None)

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def generate_signal(self) -> str:
        """시그널만 반환한다."""
        return self.generate_signal_with_score()[0]

    def generate_signal_with_score(self) -> tuple[str, float]:
        """VIX 기반 시장 심리 시그널과 점수를 반환한다.

        Returns:
            (signal, score): 'BUY' / 'SELL' / 'HOLD', 점수 [-1.0, +1.0]
        """
        vix = self.fetch_vix()

        if vix is None:
            logger.warning("VIX 값을 가져올 수 없어 HOLD 반환.")
            return "HOLD", 0.0

        score = _vix_to_score(vix, self.contrarian)

        # VIX 구간 레이블 로깅
        if vix < 13:
            level = "극도 탐욕"
        elif vix < 20:
            level = "안정"
        elif vix < 25:
            level = "중립"
        elif vix < 30:
            level = "공포 경계"
        elif vix < 40:
            level = "높은 공포"
        else:
            level = "패닉"

        logger.info(
            "VIX=%.2f (%s) → 점수=%.2f%s",
            vix, level, score,
            " [역발상]" if self.contrarian and vix >= 40 else "",
        )

        if score >= self.threshold:
            return "BUY", score
        if score <= -self.threshold:
            return "SELL", score
        return "HOLD", score

