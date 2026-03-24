"""News Sentiment LLM Strategy (stub).

This module will implement a trading strategy that:
- Fetches recent financial news articles for a target ticker.
- Runs the articles through an LLM (e.g., GPT or a fine-tuned BERT model)
  to produce a sentiment score in [-1, 1].
- Generates a BUY signal when the score exceeds a positive threshold,
  a SELL signal when it falls below a negative threshold, and HOLD otherwise.

TODO:
    - Integrate a news data provider (e.g., Naver Finance, Korea Exchange API).
    - Connect to an LLM endpoint or local model for sentiment scoring.
    - Implement position sizing logic.
"""

from utils.logger import get_logger

logger = get_logger(__name__)


class NewsSentimentStrategy:
    """Stub for the news-driven LLM sentiment trading strategy."""

    def __init__(self, ticker: str, threshold: float = 0.3) -> None:
        """
        Args:
            ticker:    KRX stock code to trade.
            threshold: Absolute sentiment score threshold for signal generation.
        """
        self.ticker = ticker
        self.threshold = threshold

    def fetch_news(self) -> list[str]:
        """Fetch recent news headlines for the target ticker.

        Returns:
            list[str]: A list of news headline strings.
        """
        logger.debug("fetch_news called for %s (stub)", self.ticker)
        return []

    def score_sentiment(self, headlines: list[str]) -> float:
        """Score the aggregate sentiment of the provided headlines.

        Args:
            headlines: List of news headline strings.

        Returns:
            float: Sentiment score in [-1.0, 1.0].
        """
        logger.debug("score_sentiment called (stub) – returning 0.0")
        return 0.0

    def generate_signal(self) -> str:
        """Run the full strategy pipeline and return a trading signal.

        Returns:
            str: One of 'BUY', 'SELL', or 'HOLD'.
        """
        headlines = self.fetch_news()
        score = self.score_sentiment(headlines)
        logger.info("Sentiment score for %s: %.4f", self.ticker, score)

        if score >= self.threshold:
            return "BUY"
        if score <= -self.threshold:
            return "SELL"
        return "HOLD"
