import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()


@dataclass
class TickerInfo:
    """종목 정보를 담는 데이터 클래스."""
    code: str
    exchange: str
    is_domestic: bool
    qty: int = 1          # 종목별 1회 주문 수량 (0이면 글로벌 ORDER_QUANTITY 사용)
    interval: int = 0     # 종목별 전략 루프 주기(초) (0이면 글로벌 STRATEGY_INTERVAL_SECONDS 사용)


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
        코드[:거래소[:수량[:주기(초)]]]

    예시:
        005930                    → 삼성전자, KRX, qty=0(글로벌), interval=0(글로벌)
        005930:KRX                → 위와 동일
        005930:KRX:3              → 수량 3주
        005930:KRX:3:1800         → 수량 3주, 주기 1800초
        AAPL:NAS:1:3600           → 나스닥 AAPL, 1주, 3600초
    """
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

        is_domestic = bool(re.fullmatch(r"\d{6}", code))
        result.append(TickerInfo(
            code=code, exchange=exchange,
            is_domestic=is_domestic, qty=qty, interval=interval,
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
    @property
    def APP_KEY(cls) -> str:
        return cls._active()[0]

    @classmethod
    @property
    def APP_SECRET(cls) -> str:
        return cls._active()[1]

    @classmethod
    @property
    def ACCOUNT_NUMBER(cls) -> str:
        return cls._active()[2]

    # ── LLM 제공자 설정 ──────────────────────────────────────────
    # LLM_PROVIDER: openai | groq | ollama
    #   - openai : OpenAI API (유료)          모델 예: gpt-4o-mini
    #   - groq   : Groq API (무료 플랜 있음)  모델 예: llama-3.3-70b-versatile
    #   - ollama : 로컬 무료                  모델 예: llama3.2
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower()

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL:   str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL:   str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL:    str = os.getenv("OLLAMA_MODEL", "llama3.2")

    # ── 매매 설정 ────────────────────────────────────────────────
    TARGET_TICKERS: list[TickerInfo] = _parse_tickers(
        os.getenv(
            "TARGET_TICKERS",
            "005930:KRX,000660:KRX,AAPL:NAS,TSLA:NAS,MSFT:NAS",
        )
    )

    ORDER_QUANTITY: int = int(os.getenv("ORDER_QUANTITY", "1"))
    STRATEGY_INTERVAL_SECONDS: int = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "3600"))

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

        if missing:
            raise ValueError(
                f"[{mode_label}] 필수 환경 변수 누락: {', '.join(missing)}"
            )
