"""리스크 매니저 모듈.

기능:
    1. 종목별 손절 기준 (ATR 기반 또는 고정 비율)
    2. 일일 최대 손실 한도 (daily drawdown limit)
    3. 포지션 사이징 (고정 비율 Kelly 단순화)
    4. 진입가 추적 및 수익률 계산

사용 예시:
    risk = RiskManager()
    risk.record_entry("005930", 70000, 1)
    if risk.should_stop_loss("005930", current_price=66000):
        order_api.market_sell(...)
    qty = risk.calculate_position_size(capital=1_000_000, price=70000)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Position:
    """단일 종목 포지션 정보."""
    ticker: str
    entry_price: float
    quantity: int
    entry_date: date = field(default_factory=date.today)
    atr: float = 0.0  # 진입 시점의 ATR (선택적)


class RiskManager:
    """포지션별 손절 / 일일 손실 한도 / 사이징 관리."""

    # 싱글톤 (전략 루프 + 웹 양쪽에서 공유)
    _instance: Optional["RiskManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "RiskManager":
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._positions = {}               # dict[str, Position]
                inst._daily_realized_pnl = 0.0    # float
                inst._daily_date = date.today()   # date
                inst._rw_lock = threading.Lock()
                cls._instance = inst
        return cls._instance

    # ------------------------------------------------------------------
    # 포지션 추적
    # ------------------------------------------------------------------

    def record_entry(
        self,
        ticker: str,
        entry_price: float,
        quantity: int,
        atr: float = 0.0,
    ) -> None:
        """매수 진입가를 기록한다."""
        with self._rw_lock:
            self._positions[ticker] = Position(
                ticker=ticker,
                entry_price=entry_price,
                quantity=quantity,
                atr=atr,
            )
        logger.info(
            "[리스크] %s 포지션 기록 | 진입가=%.2f, 수량=%d, ATR=%.2f",
            ticker, entry_price, quantity, atr,
        )

    def clear_position(self, ticker: str, exit_price: float) -> None:
        """포지션을 청산하고 실현 손익을 업데이트한다."""
        with self._rw_lock:
            self._reset_daily_if_needed()
            pos = self._positions.pop(ticker, None)
            if pos:
                pnl = (exit_price - pos.entry_price) * pos.quantity
                self._daily_realized_pnl += pnl
                logger.info(
                    "[리스크] %s 포지션 청산 | 진입=%.2f, 청산=%.2f, 손익=%.2f (일일누적=%.2f)",
                    ticker, pos.entry_price, exit_price, pnl, self._daily_realized_pnl,
                )

    def get_position(self, ticker: str) -> Optional[Position]:
        with self._rw_lock:
            return self._positions.get(ticker)

    # ------------------------------------------------------------------
    # 손절 체크
    # ------------------------------------------------------------------

    def should_stop_loss(
        self,
        ticker: str,
        current_price: float,
        stop_pct: float = 0.07,
        atr_multiplier: float = 2.5,
    ) -> bool:
        """손절 조건을 확인한다.

        ATR이 기록된 경우 ATR 기반 손절을 우선 적용하고,
        없으면 고정 비율(stop_pct)을 사용한다.

        Args:
            ticker:          종목 코드.
            current_price:   현재가.
            stop_pct:        고정 손절 비율 (기본 7%).
            atr_multiplier:  ATR 배수 (기본 2.5배).

        Returns:
            bool: 손절 조건 충족 시 True.
        """
        pos = self.get_position(ticker)
        if not pos:
            return False

        if pos.atr > 0:
            stop_price = pos.entry_price - atr_multiplier * pos.atr
        else:
            stop_price = pos.entry_price * (1 - stop_pct)

        triggered = current_price <= stop_price
        if triggered:
            logger.warning(
                "[리스크] 🛑 %s 손절 발동! 현재가=%.2f, 손절기준=%.2f (진입가=%.2f, 손익률=%.2f%%)",
                ticker,
                current_price,
                stop_price,
                pos.entry_price,
                (current_price - pos.entry_price) / pos.entry_price * 100,
            )
        return triggered

    def should_take_profit(
        self,
        ticker: str,
        current_price: float,
        profit_pct: float = 0.15,
        atr_multiplier: float = 4.0,
    ) -> bool:
        """익절 조건을 확인한다.

        Args:
            ticker:         종목 코드.
            current_price:  현재가.
            profit_pct:     고정 익절 비율 (기본 15%).
            atr_multiplier: ATR 배수 (기본 4.0배).

        Returns:
            bool: 익절 조건 충족 시 True.
        """
        pos = self.get_position(ticker)
        if not pos:
            return False

        if pos.atr > 0:
            target_price = pos.entry_price + atr_multiplier * pos.atr
        else:
            target_price = pos.entry_price * (1 + profit_pct)

        triggered = current_price >= target_price
        if triggered:
            logger.info(
                "[리스크] ✅ %s 익절 발동! 현재가=%.2f, 목표가=%.2f (수익률=%.2f%%)",
                ticker,
                current_price,
                target_price,
                (current_price - pos.entry_price) / pos.entry_price * 100,
            )
        return triggered

    # ------------------------------------------------------------------
    # 일일 손실 한도
    # ------------------------------------------------------------------

    def _reset_daily_if_needed(self) -> None:
        """날짜가 바뀐 경우 일일 손익을 초기화한다. (lock 내부에서 호출)"""
        today = date.today()
        if today != self._daily_date:
            logger.info(
                "[리스크] 날짜 변경 (%s → %s) | 일일 누적 손익 초기화.",
                self._daily_date, today,
            )
            self._daily_realized_pnl = 0.0
            self._daily_date = today

    def is_daily_loss_limit_hit(self, daily_loss_limit: float = 500_000.0) -> bool:
        """일일 최대 손실 한도 초과 여부를 확인한다.

        Args:
            daily_loss_limit: 일일 최대 허용 손실 금액 (원, 기본 50만원).

        Returns:
            bool: 한도 초과 시 True.
        """
        with self._rw_lock:
            self._reset_daily_if_needed()
            hit = self._daily_realized_pnl <= -abs(daily_loss_limit)
        if hit:
            logger.warning(
                "[리스크] 🛑 일일 손실 한도 초과! 누적 손익=%.2f, 한도=%.2f",
                self._daily_realized_pnl, -daily_loss_limit,
            )
        return hit

    @property
    def daily_pnl(self) -> float:
        with self._rw_lock:
            self._reset_daily_if_needed()
            return self._daily_realized_pnl

    # ------------------------------------------------------------------
    # 포지션 사이징
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        capital: float,
        price: float,
        risk_pct: float = 0.02,
        atr: float = 0.0,
        stop_pct: float = 0.07,
    ) -> int:
        """리스크 기반 포지션 사이즈(주수)를 계산한다.

        공식: qty = (capital * risk_pct) / stop_amount_per_share
        - stop_amount_per_share = ATR * multiplier 또는 price * stop_pct

        Args:
            capital:   총 운용 자본 (원).
            price:     매수 희망 가격 (원).
            risk_pct:  자본 대비 최대 리스크 비율 (기본 2%).
            atr:       ATR 값 (0이면 고정 비율 사용).
            stop_pct:  고정 손절 비율 (기본 7%).

        Returns:
            int: 주문 수량 (최소 1).
        """
        if price <= 0:
            return 1

        risk_amount = capital * risk_pct
        if atr > 0:
            stop_amount = atr * 2.5
        else:
            stop_amount = price * stop_pct

        if stop_amount <= 0:
            return 1

        qty = int(risk_amount / stop_amount)
        qty = max(1, qty)
        logger.info(
            "[리스크] 포지션 사이징 | 자본=%.0f, 리스크%.1f%%, "
            "손절금액/주=%.2f → 수량=%d주",
            capital, risk_pct * 100, stop_amount, qty,
        )
        return qty


