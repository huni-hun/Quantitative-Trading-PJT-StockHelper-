"""main.py – 한국투자증권(KIS) 자동 매매 봇 진입점.

실행 흐름:
    1. 로거 초기화.
    2. 설정 유효성 검사 (환경 변수 확인).
    3. KIS REST API 인증.
    4. TrumpMonitor 백그라운드 스레드 시작 (24시간 상시 감시).
    5. 전략 루프 무한 실행 – 장 운영시간에만 종목 순회, 장외엔 대기 (Ctrl-C로 중지).
"""

import os
import time
from datetime import datetime, time as dtime

from config.settings import Settings, TickerInfo
from src.api.auth import KISAuth
from src.api.price import PriceAPI
from src.api.order import OrderAPI
from src.strategy.news_sentiment_llm import NewsSentimentStrategy
from src.strategy.deadcat_technical import DeadcatTechnicalStrategy
from src.strategy.trump_monitor import TrumpMonitor, TrumpSignalStore
from utils.logger import get_logger
from utils.error_handler import TradingBotError

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


def update_bot_signal(ticker: str, sentiment: str, technical: str, decision: str, price: str):
    if _WEB_MODE == "direct" and _web_update_signal:
        _web_update_signal(ticker, sentiment, technical, decision, price)
    else:
        _http_post("/api/bot/signal", {
            "ticker": ticker, "sentiment": sentiment,
            "technical": technical, "decision": decision, "price": price,
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
# 국내: 09:00 ~ 15:35 (KST)
# 해외(미국): 22:30 ~ 05:00 다음날 (KST 기준, 서머타임 미적용 시 23:30~06:00)
# ---------------------------------------------------------------------------
_KR_OPEN  = dtime(9, 0)
_KR_CLOSE = dtime(15, 35)
_US_OPEN  = dtime(22, 30)
_US_CLOSE = dtime(5, 0)


def _is_market_open(tickers: list[TickerInfo]) -> bool:
    """현재 시각이 보유 종목 중 하나 이상의 장 운영 시간인지 확인한다."""
    now = datetime.now().time()
    has_domestic = any(t.is_domestic for t in tickers)
    has_overseas = any(not t.is_domestic for t in tickers)

    if has_domestic and _KR_OPEN <= now <= _KR_CLOSE:
        return True
    if has_overseas and (now >= _US_OPEN or now <= _US_CLOSE):
        return True
    return False


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


def _decide_order(
    sentiment_signal: str,
    technical_signal: str,
    trump_signal: str,
) -> str:
    """세 가지 시그널을 통합하여 최종 매매 결정을 반환한다.

    규칙:
        - BEARISH 트럼프 시그널은 BUY를 즉시 차단 (하락 압력 우선)
        - BULLISH 트럼프 시그널은 SELL을 즉시 차단 (상승 압력 우선)
        - 나머지는 기존 두 전략 합의 룰 유지

    Returns:
        str: 'BUY', 'SELL', 'HOLD' 중 하나.
    """
    # 트럼프 BEARISH → 매수 차단
    if trump_signal == "BEARISH" and sentiment_signal == "BUY" and technical_signal == "BUY":
        return "HOLD"

    # 트럼프 BULLISH → 매도 차단
    if trump_signal == "BULLISH" and sentiment_signal == "SELL" and technical_signal == "SELL":
        return "HOLD"

    # 기본 두 전략 합의 룰
    if sentiment_signal == "BUY" and technical_signal == "BUY":
        return "BUY"
    if sentiment_signal == "SELL" and technical_signal == "SELL":
        return "SELL"

    return "HOLD"


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
        }
        for t in tickers
    }
    trump_store = TrumpSignalStore()

    logger.info(
        "전략 루프 시작 | 대상 종목 %d개: %s",
        len(tickers),
        ", ".join(f"{t.code}({t.exchange})" for t in tickers),
    )

    while True:
        # ---- 킬 스위치 체크 (매 사이클 최우선 확인) ----
        kill_active, kill_reason = check_kill_switch()
        if kill_active:
            logger.warning("🛑 킬 스위치 발동 — 전략 루프 정지. 사유: %s", kill_reason)
            # 킬스위치가 해제될 때까지 60초마다 재확인
            while True:
                time.sleep(60)
                kill_active, _ = check_kill_switch()
                if not kill_active:
                    logger.info("✅ 킬 스위치 해제 — 전략 루프 재개.")
                    break

        # ---- 장 운영 시간 체크 ----
        if not _is_market_open(tickers):
            wait = _seconds_until_next_open(tickers)
            logger.info(
                "현재 장 운영 시간 외 (KST %s) – 다음 장 오픈까지 %d분 대기.",
                datetime.now().strftime("%H:%M:%S"),
                wait // 60,
            )
            time.sleep(min(wait, 3600))   # 최대 1시간 단위로 깨어나 재확인
            continue

        # ---- 트럼프 시그널 조회 (백그라운드 스레드가 갱신) ----
        trump_signal = trump_store.latest_signal
        trump_score  = trump_store.latest_score
        logger.info("🇺🇸 현재 트럼프 시그널: %s (score=%.3f)", trump_signal, trump_score)

        # ---- 전체 종목 순회 ----
        for t in tickers:
            logger.info("=" * 50)
            logger.info("[%s:%s] 전략 실행 시작", t.code, t.exchange)

            try:
                # ---- 현재 시세 조회 ----
                price_data = price_api.get_current_price(t)
                output = price_data.get("output", {})
                current_price = output.get("stck_prpr") or output.get("last", "N/A")
                logger.info("[%s] 현재가: %s", t.code, current_price)

                # ---- 뉴스 감성 시그널 ----
                sentiment_signal = strategies[t.code]["sentiment"].generate_signal()
                logger.info("[%s] 감성 분석 시그널: %s", t.code, sentiment_signal)

                # ---- 기술적 분석 시그널 ----
                technical_signal = strategies[t.code]["technical"].generate_signal()
                logger.info("[%s] 기술적 분석 시그널: %s", t.code, technical_signal)

                # ---- 트럼프 시그널 통합 최종 결정 ----
                decision = _decide_order(sentiment_signal, technical_signal, trump_signal)
                logger.info(
                    "[%s] 최종 결정: %s (감성=%s, 기술=%s, 트럼프=%s)",
                    t.code, decision, sentiment_signal, technical_signal, trump_signal,
                )

                # 웹 대시보드 상태 업데이트
                update_bot_signal(
                    ticker=t.code,
                    sentiment=sentiment_signal,
                    technical=technical_signal,
                    decision=decision,
                    price=str(current_price),
                )

                if decision == "BUY":
                    qty = t.qty if t.qty > 0 else Settings.ORDER_QUANTITY
                    order_api.market_buy(t, quantity=qty)
                    _record_trade(
                        ticker=t.code, name=t.code,
                        side="BUY",
                        price=float(current_price) if str(current_price).replace('.', '').isdigit() else 0,
                        qty=qty, exchange=t.exchange,
                    )
                elif decision == "SELL":
                    qty = t.qty if t.qty > 0 else Settings.ORDER_QUANTITY
                    order_api.market_sell(t, quantity=qty)
                    _record_trade(
                        ticker=t.code, name=t.code,
                        side="SELL",
                        price=float(current_price) if str(current_price).replace('.', '').isdigit() else 0,
                        qty=qty, exchange=t.exchange,
                    )

            except TradingBotError as exc:
                logger.error("[%s] 트레이딩 봇 오류: %s", t.code, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[%s] 예기치 않은 오류: %s", t.code, exc)

        logger.info("=" * 50)
        logger.info(
            "전체 종목 순회 완료. %d초 후 다음 사이클 시작.",
            Settings.STRATEGY_INTERVAL_SECONDS,
        )
        intervals = [t.interval for t in tickers if t.interval > 0]
        sleep_sec = min(intervals) if intervals else Settings.STRATEGY_INTERVAL_SECONDS
        time.sleep(sleep_sec)


def main() -> None:
    """봇 진입점: 인증, TrumpMonitor 시작 후 전략 루프를 실행한다."""
    logger.info("=== 한국투자증권 자동 매매 봇 시작 ===")

    try:
        Settings.validate()
    except ValueError as exc:
        logger.error("설정 오류: %s", exc)
        raise SystemExit(1) from exc

    logger.info("매매 대상 종목: %s", [(t.code, t.exchange) for t in Settings.TARGET_TICKERS])
    logger.info("종목당 주문 수량: %d주", Settings.ORDER_QUANTITY)
    logger.info("실행 주기: %d초", Settings.STRATEGY_INTERVAL_SECONDS)

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
