"""main.py – 한국투자증권(KIS) 자동 매매 봇 진입점 (강화판).

실행 흐름:
    1. 로거 초기화.
    2. 설정 유효성 검사 (환경 변수 확인).
    3. KIS REST API 인증.
    4. TrumpMonitor 백그라운드 스레드 시작 (24시간 상시 감시).
    5. 전략 루프 무한 실행 – 장 운영시간에만 종목 순회, 장외엔 대기 (Ctrl-C로 중지).

전략 통합 방식 (가중 점수):
    - 기술적 분석   : 40%
    - 감성 분석     : 30%
    - 모멘텀 전략   : 20%
    - 트럼프 시그널 : 10%

    composite >= +0.45 → BUY
    composite <= -0.45 → SELL
    그 외 → HOLD

리스크 관리:
    - 진입 시 포지션 사이징 (ATR 기반)
    - 매 사이클 손절/익절 체크
    - 일일 손실 한도 초과 시 전략 일시 정지
"""

import os
import time
from datetime import datetime, time as dtime

import pandas as pd

from config.settings import Settings, TickerInfo
from src.api.auth import KISAuth
from src.api.price import PriceAPI
from src.api.order import OrderAPI
from src.strategy.news_sentiment_llm import NewsSentimentStrategy
from src.strategy.deadcat_technical import DeadcatTechnicalStrategy
from src.strategy.momentum_strategy import MomentumStrategy
from src.strategy.risk_manager import RiskManager
from src.strategy.trump_monitor import TrumpMonitor, TrumpSignalStore
from utils.logger import get_logger
from utils.error_handler import TradingBotError

# ---------------------------------------------------------------------------
# 가중치 설정
# ---------------------------------------------------------------------------
_W_TECHNICAL  = 0.40
_W_SENTIMENT  = 0.30
_W_MOMENTUM   = 0.20
_W_TRUMP      = 0.10

# 복합 점수 임계값
_BUY_COMPOSITE  =  0.45
_SELL_COMPOSITE = -0.45

# 일일 손실 한도 (원) – 환경 변수로 덮어쓰기 가능
_DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "500000"))

# ---------------------------------------------------------------------------
# 웹 대시보드 상태 업데이트
#   - 동일 프로세스(직접 실행)에서는 web.app 모듈을 직접 import하여 메모리 공유.
#   - 서브프로세스(web/app.py가 main.py를 띄운 경우)에서는 HTTP API로 상태 보고.
# ---------------------------------------------------------------------------
_DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://127.0.0.1:5000")
_IS_SUBPROCESS = os.getenv("STOCKHELPER_SUBPROCESS", "0") == "1"

try:
    if _IS_SUBPROCESS:
        raise ImportError("서브프로세스 모드 — HTTP 콜백 사용")
    from web.app import update_bot_signal as _web_update_signal
    from web.app import set_bot_running as _web_set_running
    from web.app import _record_trade as _web_record_trade
    from web.app import check_kill_switch as _web_check_kill
    _WEB_MODE = "direct"
except ImportError:
    _WEB_MODE = "http"
    _web_update_signal = None
    _web_set_running   = None
    _web_record_trade  = None
    _web_check_kill    = None


def _http_post(path: str, payload: dict) -> bool:
    """대시보드 HTTP API에 상태를 보고한다. 실패해도 봇 동작에는 영향 없음."""
    try:
        import requests as _req
        _req.post(f"{_DASHBOARD_URL}{path}", json=payload, timeout=3)
        return True
    except Exception:
        return False


def update_bot_signal(
    ticker: str,
    sentiment: str,
    technical: str,
    decision: str,
    price: str,
    momentum: str = "HOLD",
    composite_score: float = 0.0,
    tech_score: float = 0.0,
    sentiment_score: float = 0.0,
    momentum_score: float = 0.0,
    tech_breakdown: dict | None = None,
):
    if _WEB_MODE == "direct" and _web_update_signal:
        _web_update_signal(
            ticker, sentiment, technical, decision, price,
            momentum=momentum,
            composite_score=composite_score,
            tech_score=tech_score,
            sentiment_score=sentiment_score,
            momentum_score=momentum_score,
            tech_breakdown=tech_breakdown,
        )
    else:
        _http_post("/api/bot/signal", {
            "ticker": ticker, "sentiment": sentiment,
            "technical": technical, "decision": decision, "price": price,
            "momentum": momentum,
            "composite_score": composite_score,
            "tech_score": tech_score,
            "sentiment_score": sentiment_score,
            "momentum_score": momentum_score,
            "tech_breakdown": tech_breakdown or {},
        })


def set_bot_running(running: bool):
    if _WEB_MODE == "direct" and _web_set_running:
        _web_set_running(running)
    else:
        _http_post("/api/bot/running", {"running": running})


def _record_trade(ticker: str, name: str, side: str, price: float, qty: int, exchange: str = "KRX"):
    if _WEB_MODE == "direct" and _web_record_trade:
        _web_record_trade(ticker, name, side, price, qty, exchange)
    else:
        _http_post("/api/bot/trade", {
            "ticker": ticker, "name": name, "side": side,
            "price": price, "qty": qty, "exchange": exchange,
        })


def check_kill_switch() -> tuple[bool, str]:
    if _WEB_MODE == "direct" and _web_check_kill:
        return _web_check_kill()
    else:
        try:
            import requests as _req
            r = _req.get(f"{_DASHBOARD_URL}/api/risk/status", timeout=3)
            d = r.json()
            return bool(d.get("kill_switch", False)), d.get("kill_reason", "")
        except Exception:
            return False, ""


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 장 운영 시간 정의
# ---------------------------------------------------------------------------
_KR_OPEN  = dtime(9, 0)
_KR_CLOSE = dtime(15, 35)
_US_OPEN  = dtime(22, 30)
_US_CLOSE = dtime(5, 0)


def _is_ticker_market_open(t: TickerInfo) -> bool:
    """개별 종목의 거래소 장 운영 시간 여부를 반환한다."""
    now = datetime.now().time()
    if t.is_domestic:
        return _KR_OPEN <= now <= _KR_CLOSE
    else:
        # 미국장: 22:30 ~ 익일 05:00 (KST)
        return now >= _US_OPEN or now <= _US_CLOSE


def _is_market_open(tickers: list[TickerInfo]) -> bool:
    """현재 시각이 보유 종목 중 하나 이상의 장 운영 시간인지 확인한다."""
    return any(_is_ticker_market_open(t) for t in tickers)


def _seconds_until_next_open(tickers: list[TickerInfo]) -> int:
    """다음 장 오픈까지 남은 초를 반환한다. 최소 60초."""
    now = datetime.now().time()
    has_domestic = any(t.is_domestic for t in tickers)
    has_overseas = any(not t.is_domestic for t in tickers)

    candidates: list[dtime] = []
    if has_domestic:
        candidates.append(_KR_OPEN)
    if has_overseas:
        candidates.append(_US_OPEN)

    if not candidates:
        return 60

    now_secs = now.hour * 3600 + now.minute * 60 + now.second
    min_wait = None
    for open_time in candidates:
        open_secs = open_time.hour * 3600 + open_time.minute * 60
        diff = open_secs - now_secs
        if diff <= 0:
            diff += 86400
        if min_wait is None or diff < min_wait:
            min_wait = diff

    return max(60, min_wait or 60)


# ---------------------------------------------------------------------------
# 가중 점수 통합 결정 엔진
# ---------------------------------------------------------------------------

def _signal_to_num(signal: str) -> float:
    """BUY=+1, SELL=-1, HOLD/NEUTRAL/BULLISH/BEARISH 변환."""
    if signal in ("BUY", "BULLISH"):
        return 1.0
    if signal in ("SELL", "BEARISH"):
        return -1.0
    return 0.0


def _decide_order(
    sentiment_signal: str,
    technical_signal: str,
    trump_signal: str,
    momentum_signal: str = "HOLD",
) -> tuple[str, float]:
    """네 가지 시그널을 가중 점수로 통합하여 최종 매매 결정을 반환한다.

    Returns:
        (decision, composite_score): 결정 문자열 + 복합 점수
    """
    composite = (
        _signal_to_num(technical_signal)  * _W_TECHNICAL
        + _signal_to_num(sentiment_signal) * _W_SENTIMENT
        + _signal_to_num(momentum_signal)  * _W_MOMENTUM
        + _signal_to_num(trump_signal)     * _W_TRUMP
    )

    logger.info(
        "복합점수=%.3f | 기술(%s)×%.0f%% + 감성(%s)×%.0f%% + "
        "모멘텀(%s)×%.0f%% + 트럼프(%s)×%.0f%%",
        composite,
        technical_signal,  _W_TECHNICAL * 100,
        sentiment_signal,  _W_SENTIMENT * 100,
        momentum_signal,   _W_MOMENTUM  * 100,
        trump_signal,      _W_TRUMP     * 100,
    )

    if composite >= _BUY_COMPOSITE:
        return "BUY", composite
    if composite <= _SELL_COMPOSITE:
        return "SELL", composite
    return "HOLD", composite


# ---------------------------------------------------------------------------
# 전략 루프
# ---------------------------------------------------------------------------

def run_strategy_loop(
    price_api: PriceAPI,
    order_api: OrderAPI,
    tickers: list[TickerInfo],
) -> None:
    """전체 종목 리스트를 순회하며 전략을 실행하고 매매 시그널을 처리한다."""
    strategies = {
        t.code: {
            "sentiment": NewsSentimentStrategy(ticker_info=t),
            "technical": DeadcatTechnicalStrategy(price_api=price_api, ticker_info=t),
            "momentum":  MomentumStrategy(price_api=price_api, ticker_info=t),
        }
        for t in tickers
    }
    trump_store = TrumpSignalStore()
    risk = RiskManager()

    logger.info(
        "전략 루프 시작 | 대상 종목 %d개: %s",
        len(tickers),
        ", ".join(f"{t.code}({t.exchange})" for t in tickers),
    )

    while True:
        # ---- 킬 스위치 체크 ----
        kill_active, kill_reason = check_kill_switch()
        if kill_active:
            logger.warning("🛑 킬 스위치 발동 — 전략 루프 정지. 사유: %s", kill_reason)
            while True:
                time.sleep(60)
                kill_active, _ = check_kill_switch()
                if not kill_active:
                    logger.info("✅ 킬 스위치 해제 — 전략 루프 재개.")
                    break

        # ---- 일일 손실 한도 체크 ----
        if risk.is_daily_loss_limit_hit(_DAILY_LOSS_LIMIT):
            logger.warning(
                "🛑 일일 손실 한도(%.0f원) 초과 → 신규 매매 정지. 내일 재개.",
                _DAILY_LOSS_LIMIT,
            )
            # 다음 날까지 대기
            time.sleep(3600)
            continue

        # ---- 장 운영 시간 체크 ----
        if not _is_market_open(tickers):
            wait = _seconds_until_next_open(tickers)
            logger.info(
                "현재 장 운영 시간 외 (KST %s) – 다음 장 오픈까지 %d분 대기.",
                datetime.now().strftime("%H:%M:%S"),
                wait // 60,
            )
            time.sleep(min(wait, 3600))
            continue

        # ---- 트럼프 시그널 조회 ----
        trump_signal = trump_store.latest_signal
        trump_score  = trump_store.latest_score
        logger.info("🇺🇸 현재 트럼프 시그널: %s (score=%.3f)", trump_signal, trump_score)

        # ---- 전체 종목 순회 ----
        for t in tickers:
            # 개별 종목 거래소가 현재 장외이면 스킵
            if not _is_ticker_market_open(t):
                market_type = "코스피/코스닥" if t.is_domestic else "미국"
                logger.debug(
                    "[%s:%s] %s 장 운영 시간 외 — 스킵",
                    t.code, t.exchange, market_type,
                )
                continue

            logger.info("=" * 55)
            logger.info("[%s:%s] 전략 실행 시작", t.code, t.exchange)

            try:
                # ---- 현재 시세 조회 ----
                price_data = price_api.get_current_price(t)
                output = price_data.get("output", {})
                current_price_raw = output.get("stck_prpr") or output.get("last", "0")
                try:
                    current_price = float(str(current_price_raw).replace(",", ""))
                except ValueError:
                    current_price = 0.0
                logger.info("[%s] 현재가: %.2f", t.code, current_price)

                # ---- 손절 / 익절 체크 (포지션 보유 중인 경우) ----
                if current_price > 0:
                    if risk.should_stop_loss(t.code, current_price):
                        logger.warning("[%s] 손절 주문 실행", t.code)
                        qty_pos = (risk.get_position(t.code).quantity
                                   if risk.get_position(t.code) else (t.qty or Settings.ORDER_QUANTITY))
                        order_api.market_sell(t, quantity=qty_pos)
                        _record_trade(t.code, t.code, "SELL", current_price, qty_pos, t.exchange)
                        risk.clear_position(t.code, current_price)
                        continue  # 이 종목 이번 사이클 추가 처리 스킵

                    if risk.should_take_profit(t.code, current_price):
                        logger.info("[%s] 익절 주문 실행", t.code)
                        qty_pos = (risk.get_position(t.code).quantity
                                   if risk.get_position(t.code) else (t.qty or Settings.ORDER_QUANTITY))
                        order_api.market_sell(t, quantity=qty_pos)
                        _record_trade(t.code, t.code, "SELL", current_price, qty_pos, t.exchange)
                        risk.clear_position(t.code, current_price)
                        continue

                # ---- 전략 시그널 생성 (OHLCV는 1회만 조회해 공유) ----
                # OHLCV 사전 조회 → technical·momentum 전략에 직접 주입하여 중복 API 호출 방지
                ohlcv_df = price_api.get_ohlcv(t, lookback_days=120)

                sentiment_signal, sent_score = strategies[t.code]["sentiment"].generate_signal_with_score()
                logger.info("[%s] 감성 시그널: %s (점수=%.3f)", t.code, sentiment_signal, sent_score)

                # OHLCV를 전략 내부에 주입 (fetch_ohlcv 재호출 방지)
                strategies[t.code]["technical"]._cached_ohlcv = ohlcv_df
                technical_signal, tech_score, tech_breakdown = strategies[t.code]["technical"].generate_signal_with_score()
                logger.info("[%s] 기술 시그널: %s (점수=%.2f)", t.code, technical_signal, tech_score)

                strategies[t.code]["momentum"]._cached_ohlcv = ohlcv_df
                momentum_signal, mom_score, _ = strategies[t.code]["momentum"].generate_signal_with_score()
                logger.info("[%s] 모멘텀 시그널: %s (점수=%.2f)", t.code, momentum_signal, mom_score)

                # ---- 가중 통합 결정 ----
                decision, composite = _decide_order(
                    sentiment_signal, technical_signal, trump_signal, momentum_signal,
                )
                logger.info(
                    "[%s] 최종결정: %s | composite=%.3f "
                    "(감성=%s, 기술=%s, 모멘텀=%s, 트럼프=%s)",
                    t.code, decision, composite,
                    sentiment_signal, technical_signal, momentum_signal, trump_signal,
                )

                # 웹 대시보드 업데이트 (점수 포함)
                update_bot_signal(
                    ticker=t.code,
                    sentiment=sentiment_signal,
                    technical=technical_signal,
                    decision=decision,
                    price=str(current_price),
                    momentum=momentum_signal,
                    composite_score=composite,
                    tech_score=tech_score,
                    sentiment_score=sent_score,
                    momentum_score=mom_score,
                    tech_breakdown=tech_breakdown,
                )

                # ---- 주문 실행 ----
                if decision == "BUY" and current_price > 0:
                    # 이미 조회된 OHLCV로 ATR 계산 (추가 API 호출 없음)
                    atr_val = 0.0
                    if not ohlcv_df.empty:
                        try:
                            atr_series = strategies[t.code]["technical"].compute_atr(
                                ohlcv_df["high"].astype(float).reset_index(drop=True),
                                ohlcv_df["low"].astype(float).reset_index(drop=True),
                                ohlcv_df["close"].astype(float).reset_index(drop=True),
                            )
                            if not atr_series.empty:
                                atr_val = float(atr_series.iloc[-1])
                        except Exception:
                            pass

                    qty = t.qty if t.qty > 0 else Settings.ORDER_QUANTITY
                    order_api.market_buy(t, quantity=qty)
                    _record_trade(t.code, t.code, "BUY", current_price, qty, t.exchange)
                    risk.record_entry(t.code, current_price, qty, atr=atr_val)

                elif decision == "SELL":
                    qty = t.qty if t.qty > 0 else Settings.ORDER_QUANTITY
                    order_api.market_sell(t, quantity=qty)
                    _record_trade(t.code, t.code, "SELL", current_price, qty, t.exchange)
                    risk.clear_position(t.code, current_price)

            except TradingBotError as exc:
                logger.error("[%s] 트레이딩 봇 오류: %s", t.code, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[%s] 예기치 않은 오류: %s", t.code, exc)

            # 종목 간 딜레이 – KIS API 초당 거래건수 초과 방지
            ticker_delay = Settings.TICKER_DELAY_SECONDS
            if ticker_delay > 0:
                logger.debug("다음 종목 처리 전 %.1f초 대기...", ticker_delay)
                time.sleep(ticker_delay)

        logger.info("=" * 55)
        logger.info(
            "전체 종목 순회 완료. 일일 누적 손익=%.0f원",
            risk.daily_pnl,
        )
        intervals = [t.interval for t in tickers if t.interval > 0]
        sleep_sec = min(intervals) if intervals else Settings.STRATEGY_INTERVAL_SECONDS
        logger.info("%d초 후 다음 사이클 시작.", sleep_sec)
        time.sleep(sleep_sec)


def main() -> None:
    """봇 진입점: 인증, TrumpMonitor 시작 후 전략 루프를 실행한다."""
    logger.info("=== 한국투자증권 자동 매매 봇 시작 (강화판) ===")

    try:
        Settings.validate()
    except ValueError as exc:
        logger.error("설정 오류: %s", exc)
        raise SystemExit(1) from exc

    logger.info("매매 대상 종목: %s", [(t.code, t.exchange) for t in Settings.TARGET_TICKERS])
    logger.info("종목당 주문 수량: %d주", Settings.ORDER_QUANTITY)
    logger.info("실행 주기: %d초", Settings.STRATEGY_INTERVAL_SECONDS)
    logger.info(
        "전략 가중치: 기술=%.0f%%, 감성=%.0f%%, 모멘텀=%.0f%%, 트럼프=%.0f%%",
        _W_TECHNICAL*100, _W_SENTIMENT*100, _W_MOMENTUM*100, _W_TRUMP*100,
    )

    # 인증
    auth = KISAuth()
    auth.authenticate()

    # API 클라이언트 생성
    price_api = PriceAPI(auth=auth)
    order_api = OrderAPI(auth=auth)

    # TrumpMonitor 백그라운드 스레드 시작
    trump_monitor = TrumpMonitor()
    trump_monitor.start()

    # 전략 루프 시작
    set_bot_running(True)
    try:
        run_strategy_loop(price_api, order_api, tickers=Settings.TARGET_TICKERS)
    except KeyboardInterrupt:
        logger.info("사용자에 의해 봇 중지 (KeyboardInterrupt).")
    finally:
        set_bot_running(False)
        trump_monitor.stop()


if __name__ == "__main__":
    main()
