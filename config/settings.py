import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()



# ---------------------------------------------------------------------------
# 종목 그룹 자동 감지 기준
# ---------------------------------------------------------------------------
# 지수 ETF: 레버리지/인버스 ETF, 대표 지수 ETF
_ETF_TICKERS = frozenset({
    "TQQQ", "SOXL", "SOXS", "SPXL", "SPXS", "UPRO", "SPXU", "TECL",
    "QQQ", "SPY", "IWM", "DIA", "EEM", "GLD", "SLV", "TLT", "HYG",
    "ARKK", "ARKG", "ARKW", "ARKF",
    # 국내 ETF (KODEX, TIGER 등) – 숫자 6자리로 구분 어려우므로 명시 등록
    "069500", "102110", "252670", "233740", "122630", "114800",
})

# 대형 우량주: M7 + 코스피 시총 상위 + 주요 빅테크
_LARGE_CAP_TICKERS = frozenset({
    # 미국 M7
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA",
    # 미국 기타 대형주
    "TSLA", "NFLX", "AMD", "INTC", "BABA", "TSM", "ORCL", "CSCO",
    "JPM", "GS", "BAC", "XOM", "CVX", "JNJ", "PFE", "WMT", "KO",
    # 한국 시총 상위 (~30위권)
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "000270",  # 기아
    "068270",  # 셀트리온
    "051910",  # LG화학
    "035720",  # 카카오
    "105560",  # KB금융
    "055550",  # 신한지주
    "028260",  # 삼성물산
    "012330",  # 현대모비스
    "003670",  # 포스코홀딩스
    "207940",  # 삼성바이오로직스
    "017670",  # SK텔레콤
    "066570",  # LG전자
    "032830",  # 삼성생명
    "034730",  # SK
    "015760",  # 한국전력
    "011200",  # HMM
})


def _auto_detect_group(code: str) -> str:
    """종목 코드로 그룹을 자동 감지한다.

    Returns:
        "etf"       – 지수 ETF / 레버리지 ETF
        "large_cap" – 대형 우량주 (M7, 코스피 시총 상위)
        "small_cap" – 중소형주 / 테마주 (나머지)
    """
    upper = code.upper()
    if upper in _ETF_TICKERS:
        return "etf"
    if upper in _LARGE_CAP_TICKERS:
        return "large_cap"
    return "small_cap"


@dataclass
class TickerInfo:
    """종목 정보를 담는 데이터 클래스."""
    code: str
    exchange: str
    is_domestic: bool
    qty: int = 1          # 종목별 1회 주문 수량 (0이면 글로벌 ORDER_QUANTITY 사용)
    interval: int = 0     # 종목별 전략 루프 주기(초) (0이면 글로벌 STRATEGY_INTERVAL_SECONDS 사용)
    group: str = "auto"   # "large_cap" | "small_cap" | "etf" | "auto"(자동감지)

    def __post_init__(self) -> None:
        if self.group == "auto":
            self.group = _auto_detect_group(self.code)


# KIS API 해외 거래소 코드표
# https://apiportal.koreainvestment.com 참고
EXCHANGE_CODE_MAP: dict[str, str] = {
    "NAS": "나스닥",
    "NYS": "뉴욕증권거래소",
    "AMS": "아멕스",
    "TSE": "도쿄증권거래소",
    "HKS": "홍콩증권거래소",
    "SHS": "상해증권거래소",
    "SZS": "심천증권거래소",
    "BAY": "바이에른증권거래소(독일)",
    "FRA": "프랑크푸르트증권거래소",
    "KRX": "한국거래소",
}


def _parse_tickers(raw: str) -> list[TickerInfo]:
    """쉼표로 구분된 종목 문자열을 TickerInfo 리스트로 파싱한다.

    형식 (콜론 구분):
        코드[:거래소[:수량[:주기(초)[:그룹]]]]

    그룹 값:
        L / large_cap  → 대형 우량주 (M7, 코스피 시총 상위)
        S / small_cap  → 중소형주 / 테마주
        E / etf        → 지수 ETF / 레버리지 ETF
        auto (생략)    → 코드로 자동 감지

    예시:
        005930                      → 삼성전자, KRX, qty=0, 자동감지(large_cap)
        005930:KRX                  → 위와 동일
        005930:KRX:3                → 수량 3주
        005930:KRX:3:1800           → 수량 3주, 주기 1800초
        AAPL:NAS:1:3600             → 나스닥 AAPL, 1주, 3600초 (자동감지 → large_cap)
        TQQQ:NAS:1:3600:E          → ETF 그룹 명시
        012345:KRX:1:0:S           → 중소형 테마주 명시
    """
    # 그룹 약어 → 정규명 변환
    _GROUP_ALIAS = {
        "l": "large_cap", "large": "large_cap", "large_cap": "large_cap",
        "s": "small_cap", "small": "small_cap", "small_cap": "small_cap",
        "e": "etf",       "etf": "etf",
    }

    result: list[TickerInfo] = []
    for item in raw.split(","):
        parts = [p.strip() for p in item.split(":")]
        if not parts or not parts[0]:
            continue

        code     = parts[0].upper()
        exchange = parts[1].upper() if len(parts) > 1 and parts[1] else "KRX"
        try:
            qty = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        except ValueError:
            qty = 0
        try:
            interval = int(parts[3]) if len(parts) > 3 and parts[3] else 0
        except ValueError:
            interval = 0

        raw_group = parts[4].strip().lower() if len(parts) > 4 and parts[4] else "auto"
        group = _GROUP_ALIAS.get(raw_group, "auto")

        is_domestic = bool(re.fullmatch(r"\d{6}", code))
        result.append(TickerInfo(
            code=code, exchange=exchange,
            is_domestic=is_domestic, qty=qty, interval=interval,
            group=group,
        ))

    return result


class Settings:
    """환경 변수에서 모든 설정값을 불러오고 유효성을 검사한다."""

    # ── KIS API 도메인 ────────────────────────────────────────────
    REAL_DOMAIN: str = "https://openapi.koreainvestment.com:9443"
    MOCK_DOMAIN: str = "https://openapivts.koreainvestment.com:29443"

    # ── 거래 모드 선택 ────────────────────────────────────────────
    # true=모의투자, false=실전투자
    IS_MOCK: bool = os.getenv("KIS_IS_MOCK", "true").lower() == "true"

    # ── 실전투자 인증 정보 ────────────────────────────────────────
    REAL_APP_KEY:       str = os.getenv("KIS_REAL_APP_KEY", "")
    REAL_APP_SECRET:    str = os.getenv("KIS_REAL_APP_SECRET", "")
    REAL_ACCOUNT_NUMBER: str = os.getenv("KIS_REAL_ACCOUNT_NUMBER", "")

    # ── 모의투자 인증 정보 ────────────────────────────────────────
    MOCK_APP_KEY:       str = os.getenv("KIS_MOCK_APP_KEY", "")
    MOCK_APP_SECRET:    str = os.getenv("KIS_MOCK_APP_SECRET", "")
    MOCK_ACCOUNT_NUMBER: str = os.getenv("KIS_MOCK_ACCOUNT_NUMBER", "")

    # ── 하위 호환: 현재 모드에 맞는 키를 자동 선택 ──────────────
    @classmethod
    def _active(cls) -> tuple[str, str, str]:
        """현재 IS_MOCK 값에 따라 (app_key, app_secret, account_number) 반환."""
        if cls.IS_MOCK:
            return cls.MOCK_APP_KEY, cls.MOCK_APP_SECRET, cls.MOCK_ACCOUNT_NUMBER
        return cls.REAL_APP_KEY, cls.REAL_APP_SECRET, cls.REAL_ACCOUNT_NUMBER

    @classmethod
    def get_app_key(cls) -> str:
        return cls._active()[0]

    @classmethod
    def get_app_secret(cls) -> str:
        return cls._active()[1]

    @classmethod
    def get_account_number(cls) -> str:
        return cls._active()[2]

    # ── LLM 제공자 설정 ──────────────────────────────────────────
    # LLM_PROVIDER: openai | groq | ollama | gemini
    #   - openai : OpenAI API (유료)          모델 예: gpt-4o-mini
    #   - groq   : Groq API (무료 플랜 있음)  모델 예: llama-3.3-70b-versatile
    #   - ollama : 로컬 무료                  모델 예: llama3.2
    #   - gemini : Google Gemini API (유료)   모델 예: gemini-2.0-flash, gemini-1.5-pro
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower()

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL:   str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL:   str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL:    str = os.getenv("OLLAMA_MODEL", "llama3.2")

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL:   str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # ── 매매 설정 ────────────────────────────────────────────────
    TARGET_TICKERS: list[TickerInfo] = _parse_tickers(
        os.getenv(
            "TARGET_TICKERS",
            "005930:KRX,000660:KRX,AAPL:NAS,TSLA:NAS,MSFT:NAS",
        )
    )

    ORDER_QUANTITY: int = int(os.getenv("ORDER_QUANTITY", "1"))
    STRATEGY_INTERVAL_SECONDS: int = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "180"))
    # 종목 간 처리 딜레이 (초) – API 레이트 리밋 방지용
    TICKER_DELAY_SECONDS: float = float(os.getenv("TICKER_DELAY_SECONDS", "1.5"))

    @classmethod
    def get_base_url(cls) -> str:
        """거래 모드에 따라 적절한 기본 URL을 반환한다."""
        return cls.MOCK_DOMAIN if cls.IS_MOCK else cls.REAL_DOMAIN

    @classmethod
    def validate(cls) -> None:
        """필수 인증 정보가 누락된 경우 ValueError를 발생시킨다."""
        app_key, app_secret, account_number = cls._active()
        mode_label = "모의투자" if cls.IS_MOCK else "실전투자"
        prefix = "KIS_MOCK" if cls.IS_MOCK else "KIS_REAL"

        missing = []
        if not app_key:
            missing.append(f"{prefix}_APP_KEY")
        if not app_secret:
            missing.append(f"{prefix}_APP_SECRET")
        if not account_number:
            missing.append(f"{prefix}_ACCOUNT_NUMBER")

        if cls.LLM_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        elif cls.LLM_PROVIDER == "groq" and not cls.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        elif cls.LLM_PROVIDER == "gemini" and not cls.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")

        if missing:
            raise ValueError(
                f"[{mode_label}] 필수 환경 변수 누락: {', '.join(missing)}"
            )
