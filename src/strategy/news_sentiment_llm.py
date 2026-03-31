"""뉴스 감성 LLM 전략.

네이버 금융에서 종목 관련 최신 뉴스 헤드라인을 수집하고,
OpenAI GPT를 통해 감성 점수를 산출하여 매매 시그널을 생성한다.

감성 점수: [-1.0 (매우 부정) ~ +1.0 (매우 긍정)]
시그널 로직:
    - 매수(BUY):  score >= threshold
    - 매도(SELL): score <= -threshold
    - 관망(HOLD): 그 외
"""

from __future__ import annotations

import json
import re

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from config.settings import Settings, TickerInfo
from utils.logger import get_logger

logger = get_logger(__name__)

# 네이버 금융 종목 뉴스 URL (국내)
_NAVER_NEWS_URL = (
    "https://finance.naver.com/item/news_news.naver"
    "?code={ticker}&page=1&sm=title_entity_id.basic&clusterId="
)
# 네이버 금융 해외 종목 뉴스 검색 URL
_NAVER_SEARCH_URL = (
    "https://search.naver.com/search.naver?where=news&query={ticker}+주가&sm=tab_jum"
)
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# GPT에게 전달할 시스템 프롬프트
_SYSTEM_PROMPT = """당신은 글로벌 주식 시장 전문 금융 애널리스트입니다.
주어진 뉴스 헤드라인 목록을 분석하여 해당 종목에 대한 전반적인 시장 감성을
-1.0(매우 부정적) ~ +1.0(매우 긍정적) 사이의 실수 하나로 평가하세요.

반드시 다음 JSON 형식으로만 응답하세요:
{"score": <float>, "reason": "<한 문장 이유>"}"""


class NewsSentimentStrategy:
    """네이버 금융 뉴스 + OpenAI GPT 기반 감성 분석 매매 전략."""

    def __init__(
        self,
        ticker_info: TickerInfo,
        threshold: float = 0.3,
        max_headlines: int = 15,
    ) -> None:
        """
        Args:
            ticker_info:    매매할 종목의 TickerInfo 인스턴스.
            threshold:      시그널 생성을 위한 감성 점수 절댓값 임계값.
            max_headlines:  GPT에 전달할 최대 헤드라인 수.
        """
        self.ticker_info = ticker_info
        self.ticker = ticker_info.code
        self.threshold = threshold
        self.max_headlines = max_headlines

    def fetch_news(self) -> list[str]:
        """종목의 최신 뉴스 헤드라인을 수집한다.

        - 국내 종목: 네이버 금융 종목 뉴스 페이지에서 수집.
        - 해외 종목: 네이버 뉴스 검색에서 티커명으로 수집.

        Returns:
            list[str]: 뉴스 헤드라인 문자열 목록 (최대 max_headlines개).
                       수집 실패 시 빈 리스트 반환.
        """
        if self.ticker_info.is_domestic:
            return self._fetch_domestic_news(self.ticker)
        return self._fetch_overseas_news(self.ticker)

    def _fetch_domestic_news(self, ticker: str) -> list[str]:
        """네이버 금융에서 국내 종목 뉴스를 수집한다."""
        url = _NAVER_NEWS_URL.format(ticker=ticker)
        try:
            resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            resp.encoding = "euc-kr"
        except requests.RequestException as exc:
            logger.warning("[국내] 뉴스 수집 실패 | %s: %s", ticker, exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        headlines: list[str] = []
        for tag in soup.select("table.type5 td.title a, .news_area .title a"):
            text = tag.get_text(strip=True)
            if text and len(text) > 5:
                headlines.append(text)
            if len(headlines) >= self.max_headlines:
                break

        logger.info("[국내] %s 뉴스 %d건 수집 완료.", ticker, len(headlines))
        return headlines

    def _fetch_overseas_news(self, ticker: str) -> list[str]:
        """네이버 뉴스 검색에서 해외 종목 뉴스를 수집한다."""
        url = _NAVER_SEARCH_URL.format(ticker=ticker)
        try:
            resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[해외] 뉴스 수집 실패 | %s: %s", ticker, exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        headlines: list[str] = []
        for tag in soup.select(".news_tit, .title_link"):
            text = tag.get_text(strip=True)
            if text and len(text) > 5:
                headlines.append(text)
            if len(headlines) >= self.max_headlines:
                break

        logger.info("[해외] %s 뉴스 %d건 수집 완료.", ticker, len(headlines))
        return headlines

    # ------------------------------------------------------------------
    # 감성 분석
    # ------------------------------------------------------------------

    def score_sentiment(self, headlines: list[str]) -> float:
        """GPT를 호출하여 헤드라인 목록의 종합 감성 점수를 산출한다.

        Args:
            headlines: 뉴스 헤드라인 문자열 목록.

        Returns:
            float: 감성 점수 [-1.0, 1.0]. 분석 실패 시 0.0 반환.
        """
        if not headlines:
            logger.warning("헤드라인이 없어 감성 점수 0.0 반환.")
            return 0.0

        headlines_text = "\n".join(
            f"{i+1}. {h}" for i, h in enumerate(headlines)
        )
        user_message = (
            f"아래는 종목코드 {self.ticker}에 대한 최신 뉴스 헤드라인입니다.\n\n"
            f"{headlines_text}\n\n"
            "위 헤드라인을 종합하여 감성 점수를 JSON으로 반환하세요."
        )

        try:
            raw = chat_complete(
                system_prompt=_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.0,
                max_tokens=120,
            )
            logger.debug("LLM 원본 응답: %s", raw)

            # JSON 파싱
            # 응답에 마크다운 코드블록이 붙는 경우 대비
            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if not json_match:
                raise ValueError(f"JSON 패턴을 찾을 수 없음: {raw}")

            parsed = json.loads(json_match.group())
            score = float(parsed["score"])
            reason = parsed.get("reason", "")

            # 범위 클리핑
            score = max(-1.0, min(1.0, score))
            logger.info(
                "%s 감성 점수=%.4f | 이유: %s", self.ticker, score, reason
            )
            return score

        except Exception as exc:  # noqa: BLE001
            logger.error("GPT 감성 분석 실패: %s – 0.0 반환", exc)
            return 0.0

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def generate_signal(self) -> str:
        """전체 전략 파이프라인을 실행하고 매매 시그널을 반환한다.

        Returns:
            str: 'BUY', 'SELL', 'HOLD' 중 하나.
        """
        headlines = self.fetch_news()
        score = self.score_sentiment(headlines)

        if score >= self.threshold:
            logger.info("%s 감성 시그널 → BUY (score=%.4f)", self.ticker, score)
            return "BUY"
        if score <= -self.threshold:
            logger.info("%s 감성 시그널 → SELL (score=%.4f)", self.ticker, score)
            return "SELL"

        logger.info("%s 감성 시그널 → HOLD (score=%.4f)", self.ticker, score)
        return "HOLD"
