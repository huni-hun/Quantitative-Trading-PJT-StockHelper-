"""main.py – Entry point for the KIS Algorithmic Trading Bot.

Execution flow:
    1. Initialise the logger.
    2. Validate configuration (environment variables).
    3. Authenticate with the KIS REST API.
    4. Run the strategy loop indefinitely (press Ctrl-C to stop).
"""

import time

from config.settings import Settings
from src.api.auth import KISAuth
from src.api.price import PriceAPI
from src.api.order import OrderAPI
from src.strategy.news_sentiment_llm import NewsSentimentStrategy
from src.strategy.deadcat_technical import DeadcatTechnicalStrategy
from utils.logger import get_logger
from utils.error_handler import TradingBotError

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_TICKER = "005930"        # Samsung Electronics (example)
STRATEGY_INTERVAL_SECONDS = 60  # How often the strategy loop runs


def run_strategy_loop(
    price_api: PriceAPI,
    order_api: OrderAPI,
    ticker: str,
) -> None:
    """Continuously run both strategies and execute signals.

    Args:
        price_api:  Initialised PriceAPI instance.
        order_api:  Initialised OrderAPI instance.
        ticker:     KRX stock code to trade.
    """
    sentiment_strategy = NewsSentimentStrategy(ticker=ticker)
    technical_strategy = DeadcatTechnicalStrategy(ticker=ticker)

    logger.info("Strategy loop started for ticker: %s", ticker)

    while True:
        try:
            # ---- Fetch current market price ----
            price_data = price_api.get_current_price(ticker)
            logger.info("Current price data: %s", price_data)

            # ---- News Sentiment Signal ----
            sentiment_signal = sentiment_strategy.generate_signal()
            logger.info("Sentiment signal: %s", sentiment_signal)

            # ---- Technical Signal ----
            technical_signal = technical_strategy.generate_signal()
            logger.info("Technical signal: %s", technical_signal)

            # ---- Combine signals and act (simple majority / both-agree rule) ----
            if sentiment_signal == "BUY" and technical_signal == "BUY":
                logger.info("Both strategies agree: BUY – placing market buy order.")
                order_api.market_buy(ticker, quantity=1)
            elif sentiment_signal == "SELL" and technical_signal == "SELL":
                logger.info("Both strategies agree: SELL – placing market sell order.")
                order_api.market_sell(ticker, quantity=1)
            else:
                logger.info(
                    "No consensus signal (sentiment=%s, technical=%s) – HOLD.",
                    sentiment_signal,
                    technical_signal,
                )

        except TradingBotError as exc:
            logger.error("Trading bot error: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error in strategy loop: %s", exc)

        logger.info("Sleeping %d seconds until next cycle.", STRATEGY_INTERVAL_SECONDS)
        time.sleep(STRATEGY_INTERVAL_SECONDS)


def main() -> None:
    """Bot entry point: authenticate and start the strategy loop."""
    logger.info("=== KIS Algorithmic Trading Bot starting ===")

    # Validate required environment variables before doing anything else
    try:
        Settings.validate()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    # Authenticate
    auth = KISAuth()
    auth.authenticate()

    # Build API clients
    price_api = PriceAPI(auth=auth)
    order_api = OrderAPI(auth=auth)

    # Start strategy loop
    try:
        run_strategy_loop(price_api, order_api, ticker=TARGET_TICKER)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")


if __name__ == "__main__":
    main()
