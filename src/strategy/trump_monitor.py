"""트럼프 포스팅 실시간 모니터 전략.

X(트위터) @realDonaldTrump 계정의 Nitter RSS 피드를 백그라운드 스레드로
폴링하여 새 포스팅이 감지되면 즉시 GPT로 시장 영향도를 분석한다.

결과는 TrumpSignalStore(싱글톤)에 저장되며, 기존 매매 전략 루프에서
트럼프 시그널을 추가 필터로 활용한다.

시그널 로직:
    - trump_score >= BULL_THRESHOLD  → BULLISH (시장 긍정)
    - trump_score <= BEAR_THRESHOLD  → BEARISH (시장 부정)
    - 그 외 또는 신규 포스팅 없음   → NEUTRAL
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

from config.settings import Settings
from utils.llm_client import chat_complete
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 설정 상수
# ---------------------------------------------------------------------------

# Nitter 퍼블릭 인스턴스 목록 (하나가 막히면 다음 시도)
_NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]
_TRUMP_HANDLE = "realDonaldTrump"

# 폴링 간격 (초) – 트럼프는 24시간 언제든 포스팅 가능하므로 장외에도 감시
POLL_INTERVAL_SECONDS = 30

# GPT 판단 임계값
BULL_THRESHOLD =  0.35   # 이 이상이면 BULLISH
BEAR_THRESHOLD = -0.35   # 이 이하면 BEARISH

_TRUMP_SYSTEM_PROMPT = """당신은 글로벌 금융 시장 전문가입니다.
트럼프 전(현) 대통령의 소셜미디어 포스팅이 주식 시장 전반에 미치는 영향을
-1.0(매우 부정적/하락 압력) ~ +1.0(매우 긍정적/상승 압력) 사이의 실수로 평가하세요.

관세·무역전쟁 언급 → 부정, 감세·규제완화·경제 호황 언급 → 긍정,
특정 기업 비판 → 부정, 특정 기업 칭찬 → 긍정, 일반 정치 발언 → 중립에 가깝게 판단.

반드시 다음 JSON 형식으로만 응답하세요:
{"score": <float>, "reason": "<한 문장 이유>", "keywords": ["키워드1", "키워드2"]}"""

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# 트럼프 시그널 저장소 (싱글톤)
# ---------------------------------------------------------------------------

@dataclass
class TrumpPost:
    """트럼프 포스팅 한 건을 나타내는 데이터 클래스."""
    post_id:   str
    text:      str
    published: datetime
    score:     float        # GPT 분석 점수 [-1, 1]
    reason:    str
    keywords:  list[str] = field(default_factory=list)
    signal:    str = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL


class TrumpSignalStore:
    """백그라운드 스레드와 전략 루프 간 시그널을 공유하는 스레드 안전 저장소."""

    _instance: TrumpSignalStore | None = None
    _lock = threading.Lock()

    def __new__(cls) -> TrumpSignalStore:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._posts: list[TrumpPost] = []
                cls._instance._latest_signal: str = "NEUTRAL"
                cls._instance._latest_score: float = 0.0
                cls._instance._rw_lock = threading.Lock()
        return cls._instance

    def add_post(self, post: TrumpPost) -> None:
        """새 포스팅 분석 결과를 저장한다."""
        with self._rw_lock:
            self._posts.append(post)
            self._latest_signal = post.signal
            self._latest_score  = post.score
            # 최대 100건만 유지
            if len(self._posts) > 100:
                self._posts = self._posts[-100:]

    @property
    def latest_signal(self) -> str:
        """가장 최근 트럼프 포스팅의 시장 시그널 (BULLISH/BEARISH/NEUTRAL)."""
        with self._rw_lock:
            return self._latest_signal

    @property
    def latest_score(self) -> float:
        """가장 최근 트럼프 포스팅의 GPT 점수."""
        with self._rw_lock:
            return self._latest_score

    @property
    def recent_posts(self) -> list[TrumpPost]:
        """최근 분석된 포스팅 목록 (복사본)."""
        with self._rw_lock:
            return list(self._posts)


# ---------------------------------------------------------------------------
# 트럼프 모니터 (백그라운드 스레드)
# ---------------------------------------------------------------------------

class TrumpMonitor:
    """@realDonaldTrump RSS 피드를 폴링하여 새 포스트를 즉시 분석하는 모니터."""

    def __init__(self, poll_interval: int = POLL_INTERVAL_SECONDS) -> None:
        """
        Args:
            poll_interval: 피드 폴링 간격 (초). 기본 30초.
        """
        self._poll_interval = poll_interval
        self._store = TrumpSignalStore()
        self._seen_ids: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # 스레드 제어
    # ------------------------------------------------------------------

    def start(self) -> None:
        """백그라운드 폴링 스레드를 시작한다."""
        if self._thread and self._thread.is_alive():
            logger.warning("TrumpMonitor 이미 실행 중.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="TrumpMonitor",
            daemon=True,   # 메인 프로세스 종료 시 자동 종료
        )
        self._thread.start()
        logger.info("TrumpMonitor 시작 | 폴링 간격: %d초", self._poll_interval)

    def stop(self) -> None:
        """백그라운드 폴링 스레드를 중지한다."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TrumpMonitor 중지.")

    # ------------------------------------------------------------------
    # 폴링 루프
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """RSS 피드를 주기적으로 폴링하는 메인 루프."""
        # 첫 실행 시 기존 포스트를 seen으로 마킹 (과거 포스트 재분석 방지)
        self._initialize_seen_ids()

        while not self._stop_event.is_set():
            try:
                new_posts = self._fetch_new_posts()
                for post_text, post_id, published in new_posts:
                    logger.info(
                        "🔔 트럼프 새 포스팅 감지! [%s] %.80s…",
                        published.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        post_text,
                    )
                    self._analyze_and_store(post_text, post_id, published)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TrumpMonitor 폴링 오류: %s", exc)

            self._stop_event.wait(self._poll_interval)

    def _initialize_seen_ids(self) -> None:
        """시작 시점의 기존 포스트 ID를 모두 seen으로 등록한다."""
        entries = self._fetch_feed()
        for entry in entries:
            self._seen_ids.add(entry.get("id", ""))
        logger.info("TrumpMonitor 초기화 완료 | 기존 포스트 %d건 마킹.", len(self._seen_ids))

    def _fetch_feed(self) -> list:
        """Nitter RSS 피드를 조회한다. 여러 인스턴스를 순서대로 시도한다."""
        for base in _NITTER_INSTANCES:
            url = f"{base}/{_TRUMP_HANDLE}/rss"
            try:
                resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=10)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                if feed.entries:
                    return feed.entries
            except Exception as exc:  # noqa: BLE001
                logger.debug("Nitter 인스턴스 실패 (%s): %s", base, exc)
                continue
        return []

    def _fetch_new_posts(self) -> list[tuple[str, str, datetime]]:
        """seen_ids에 없는 새 포스트만 반환한다.

        Returns:
            list of (텍스트, post_id, published_datetime)
        """
        entries = self._fetch_feed()
        new: list[tuple[str, str, datetime]] = []

        for entry in entries:
            post_id = entry.get("id", entry.get("link", ""))
            if post_id in self._seen_ids:
                continue

            # HTML 태그 제거 후 순수 텍스트 추출
            raw = entry.get("summary", entry.get("title", ""))
            text = BeautifulSoup(raw, "lxml").get_text(separator=" ", strip=True)

            # 발행 시각 파싱
            published_struct = entry.get("published_parsed")
            if published_struct:
                published = datetime(*published_struct[:6], tzinfo=timezone.utc)
            else:
                published = datetime.now(tz=timezone.utc)

            self._seen_ids.add(post_id)
            new.append((text, post_id, published))

        return new

    # ------------------------------------------------------------------
    # GPT 분석
    # ------------------------------------------------------------------

    def _analyze_and_store(
        self, text: str, post_id: str, published: datetime
    ) -> None:
        """GPT로 포스팅을 분석하고 결과를 TrumpSignalStore에 저장한다."""
        score, reason, keywords = self._gpt_analyze(text)

        if score >= BULL_THRESHOLD:
            signal = "BULLISH"
        elif score <= BEAR_THRESHOLD:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        post = TrumpPost(
            post_id=post_id,
            text=text,
            published=published,
            score=score,
            reason=reason,
            keywords=keywords,
            signal=signal,
        )
        self._store.add_post(post)

        logger.info(
            "🇺🇸 트럼프 시그널 → %s (score=%.3f) | 이유: %s | 키워드: %s",
            signal, score, reason, keywords,
        )

    def _gpt_analyze(self, text: str) -> tuple[float, str, list[str]]:
        """LLM으로 트럼프 포스팅의 시장 영향도를 분석한다.

        Returns:
            (score, reason, keywords)
        """
        try:
            raw = chat_complete(
                system_prompt=_TRUMP_SYSTEM_PROMPT,
                user_message=f"포스팅 내용:\n{text}",
                temperature=0.0,
                max_tokens=150,
            )
            logger.debug("LLM 트럼프 분석 원본: %s", raw)

            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if not json_match:
                raise ValueError(f"JSON 패턴 없음: {raw}")

            parsed   = json.loads(json_match.group())
            score    = max(-1.0, min(1.0, float(parsed["score"])))
            reason   = parsed.get("reason", "")
            keywords = parsed.get("keywords", [])
            return score, reason, keywords

        except Exception as exc:  # noqa: BLE001
            logger.error("트럼프 GPT 분석 실패: %s – 0.0 반환", exc)
            return 0.0, "", []
