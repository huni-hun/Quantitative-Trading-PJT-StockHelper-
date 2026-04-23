"""뉴스 감성 LLM 전략 (강화판).

네이버 금융에서 종목 관련 최신 뉴스 헤드라인을 수집하고,
LLM을 통해 감성 점수를 산출하여 매매 시그널을 생성한다.

개선 사항:
    - 롤링 감성 캐시: 최근 N회 점수의 지수이동평균으로 노이즈 제거
    - 개선된 프롬프트: 정량적 팩터(실적/매출/EPS/가이던스) 판별 강화
    - 뉴스 신선도 가중치: 최신 헤드라인에 더 높은 가중치 부여 요청
    - 감성 점수 추세: 이전 점수 대비 방향성을 시그널에 반영

감성 점수: [-1.0 (매우 부정) ~ +1.0 (매우 긍정)]
시그널 로직:
    - 매수(BUY):  rolling_score >= threshold
    - 매도(SELL): rolling_score <= -threshold
    - 관망(HOLD): 그 외
"""

from __future__ import annotations

import json
import re
from collections import deque

import requests
from bs4 import BeautifulSoup

from config.settings import TickerInfo
from utils.llm_client import chat_complete
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

# 개선된 GPT 시스템 프롬프트
_SYSTEM_PROMPT = """당신은 글로벌 주식 시장 전문 퀀트 애널리스트입니다.
주어진 뉴스 헤드라인 목록을 분석하여 해당 종목의 단기(1~5영업일) 주가 방향성에 대한
시장 감성을 -1.0(매우 부정적) ~ +1.0(매우 긍정적) 사이의 실수로 평가하세요.

판단 기준 (가중치 순서):
1. [HIGH] 실적 서프라이즈/쇼크, EPS Beat/Miss, 매출 성장/감소, 가이던스 상향/하향
2. [HIGH] M&A, 대형 계약 체결/취소, 주요 파트너십, 임원 교체
3. [MED]  업황 뉴스, 경쟁사 동향, 시장 점유율 변화
4. [MED]  규제 변화, 소송/리스크, 국가 정책 영향
5. [LOW]  일반 투자자 의견, 목표주가 변경, 단순 주가 언급

최신 헤드라인일수록 더 높은 가중치를 부여하세요.
헤드라인이 부족하거나 정보가 없으면 0.0에 가깝게 응답하세요.

반드시 다음 JSON 형식으로만 응답하세요:
{"score": <float>, "confidence": <0.0~1.0>, "key_factors": ["팩터1", "팩터2"], "reason": "<한 문장 이유>"}"""


class NewsSentimentStrategy:
    """네이버 금융 뉴스 + LLM 기반 강화 감성 분석 매매 전략."""

    def __init__(
        self,
        ticker_info: TickerInfo,
        threshold: float = 0.25,
        max_headlines: int = 20,
        rolling_window: int = 5,
        ema_alpha: float = 0.4,
    ) -> None:
        """
        Args:
            ticker_info:    매매할 종목의 TickerInfo 인스턴스.
            threshold:      시그널 생성을 위한 감성 점수 절댓값 임계값.
            max_headlines:  LLM에 전달할 최대 헤드라인 수.
            rolling_window: 롤링 감성 점수 보관 개수.
            ema_alpha:      EMA 평활 계수 (높을수록 최신 점수에 민감).
        """
        self.ticker_info = ticker_info
        self.ticker = ticker_info.code
        self.threshold = threshold
        self.max_headlines = max_headlines
        self.ema_alpha = ema_alpha
        # 롤링 감성 점수 캐시 (최근 rolling_window개 보관)
        self._score_cache: deque[float] = deque(maxlen=rolling_window)
        self._rolling_score: float = 0.0

    # ------------------------------------------------------------------
    # 뉴스 수집
    # ------------------------------------------------------------------

    def fetch_news(self) -> list[str]:
        """종목의 최신 뉴스 헤드라인을 수집한다."""
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

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.warning("[국내] HTML 파싱 실패 | %s: %s", ticker, exc)
            return []

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

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.warning("[해외] HTML 파싱 실패 | %s: %s", ticker, exc)
            return []

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

    def score_sentiment(self, headlines: list[str]) -> tuple[float, float]:
        """LLM으로 헤드라인의 감성 점수와 신뢰도를 산출한다.

        Returns:
            (score, confidence): 감성 점수 [-1.0, 1.0], 신뢰도 [0.0, 1.0].
        """
        if not headlines:
            logger.warning("헤드라인이 없어 감성 점수 0.0 반환.")
            return 0.0, 0.0

        # 헤드라인에 번호 + 최신순 표시
        headlines_text = "\n".join(
            f"{i+1}. {h}" for i, h in enumerate(headlines)
        )
        user_message = (
            f"종목코드: {self.ticker}\n"
            f"뉴스 헤드라인 (1번이 최신):\n\n"
            f"{headlines_text}\n\n"
            "위 헤드라인을 종합하여 단기 주가 방향성 감성 점수를 JSON으로 반환하세요."
        )

        try:
            raw = chat_complete(
                system_prompt=_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.0,
                max_tokens=200,
            )
            logger.debug("LLM 원본 응답: %s", raw)

            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if not json_match:
                raise ValueError(f"JSON 패턴을 찾을 수 없음: {raw}")

            parsed = json.loads(json_match.group())
            score = float(parsed["score"])
            confidence = float(parsed.get("confidence", 0.5))
            key_factors = parsed.get("key_factors", [])
            reason = parsed.get("reason", "")

            score = max(-1.0, min(1.0, score))
            confidence = max(0.0, min(1.0, confidence))

            logger.info(
                "%s 감성 점수=%.4f (신뢰도=%.2f) | 핵심팩터: %s | 이유: %s",
                self.ticker, score, confidence, key_factors, reason,
            )
            return score, confidence

        except Exception as exc:
            logger.error("LLM 감성 분석 실패: %s – 0.0 반환", exc)
            return 0.0, 0.0

    def _update_rolling_score(self, new_score: float) -> float:
        """EMA 방식으로 롤링 감성 점수를 업데이트한다."""
        self._score_cache.append(new_score)
        if len(self._score_cache) == 1:
            self._rolling_score = new_score
        else:
            self._rolling_score = (
                self.ema_alpha * new_score
                + (1 - self.ema_alpha) * self._rolling_score
            )
        logger.info(
            "%s 롤링 감성 점수=%.4f (최근 %d개: %s)",
            self.ticker,
            self._rolling_score,
            len(self._score_cache),
            [f"{s:.2f}" for s in self._score_cache],
        )
        return self._rolling_score

    # ------------------------------------------------------------------
    # 시그널 생성
    # ------------------------------------------------------------------

    def generate_signal(self) -> str:
        """전체 전략 파이프라인을 실행하고 매매 시그널을 반환한다."""
        return self.generate_signal_with_score()[0]

    def generate_signal_with_score(self) -> tuple[str, float]:
        """시그널과 롤링 감성 점수를 함께 반환한다.

        Returns:
            (signal, rolling_score)
        """
        headlines = self.fetch_news()
        raw_score, confidence = self.score_sentiment(headlines)

        # 신뢰도가 매우 낮으면 점수를 0쪽으로 보정
        adjusted_score = raw_score * max(0.5, confidence)
        rolling_score = self._update_rolling_score(adjusted_score)

        # 추세 방향성 보너스: 연속 같은 방향이면 점수 강화
        if len(self._score_cache) >= 3:
            recent = list(self._score_cache)[-3:]
            if all(s > 0 for s in recent):
                rolling_score = min(1.0, rolling_score * 1.1)
            elif all(s < 0 for s in recent):
                rolling_score = max(-1.0, rolling_score * 1.1)

        if rolling_score >= self.threshold:
            logger.info("%s 감성 시그널 → BUY (rolling=%.4f)", self.ticker, rolling_score)
            return "BUY", rolling_score
        if rolling_score <= -self.threshold:
            logger.info("%s 감성 시그널 → SELL (rolling=%.4f)", self.ticker, rolling_score)
            return "SELL", rolling_score

        logger.info("%s 감성 시그널 → HOLD (rolling=%.4f)", self.ticker, rolling_score)
        return "HOLD", rolling_score
