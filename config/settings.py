import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()


@dataclass
class TickerInfo:
    """종목 정보를 담는 데이터 클래스."""
    code: str        # 종목 코드 (예: 005930, AAPL)
    exchange: str    # 거래소 코드 (예: KRX, NAS, NYS, AMS, TSE 등)
    is_domestic: bool  # 국내 종목 여부


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

    형식:
        - 국내: '005930' 또는 '005930:KRX'
        - 해외: 'AAPL:NAS', 'TSLA:NAS', '9984:TSE'

    국내 종목은 거래소 코드를 생략하면 자동으로 KRX로 설정된다.
    해외 종목은 반드시 거래소 코드를 명시해야 한다.
    """
    result: list[TickerInfo] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue

        if ":" in item:
            code, exchange = item.split(":", 1)
            code = code.strip().upper()
            exchange = exchange.strip().upper()
        else:
            code = item.strip().upper()
            exchange = "KRX"  # 거래소 미지정 시 국내로 간주

        # 숫자 6자리면 국내, 그 외(알파벳 포함 등)면 해외
        is_domestic = bool(re.fullmatch(r"\d{6}", code))
        result.append(TickerInfo(code=code, exchange=exchange, is_domestic=is_domestic))

    return result


class Settings:
    """환경 변수에서 모든 설정값을 불러오고 유효성을 검사한다."""

    # KIS API 인증 정보
    APP_KEY: str = os.getenv("KIS_APP_KEY", "")
    APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
    ACCOUNT_NUMBER: str = os.getenv("KIS_ACCOUNT_NUMBER", "")

    # KIS API 기본 URL
    REAL_DOMAIN: str = "https://openapi.koreainvestment.com:9443"
    MOCK_DOMAIN: str = "https://openapivts.koreainvestment.com:29443"

    # 실전 투자 vs 모의 투자 환경 선택
    IS_MOCK: bool = os.getenv("KIS_IS_MOCK", "true").lower() == "true"

    # ── LLM 제공자 설정 ──────────────────────────────────────────────
    # LLM_PROVIDER: openai | groq | ollama
    #   - openai : OpenAI API (유료)          모델 예: gpt-4o-mini
    #   - groq   : Groq API (무료 플랜 있음)  모델 예: llama-3.3-70b-versatile
    #   - ollama : 로컬 무료                  모델 예: llama3.2
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower()

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL:   str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Groq (무료 플랜: https://console.groq.com)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL:   str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Ollama (로컬 서버, 완전 무료: https://ollama.com)
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL:    str = os.getenv("OLLAMA_MODEL", "llama3.2")

    # 매매 대상 종목 리스트
    TARGET_TICKERS: list[TickerInfo] = _parse_tickers(
        os.getenv(
            "TARGET_TICKERS",
            "005930:KRX,000660:KRX,AAPL:NAS,TSLA:NAS,MSFT:NAS",
        )
    )

    # 종목당 1회 매매 수량
    ORDER_QUANTITY: int = int(os.getenv("ORDER_QUANTITY", "1"))

    # 전략 루프 실행 주기 (초)
    STRATEGY_INTERVAL_SECONDS: int = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "60"))

    @classmethod
    def get_base_url(cls) -> str:
        """거래 모드에 따라 적절한 기본 URL을 반환한다."""
        return cls.MOCK_DOMAIN if cls.IS_MOCK else cls.REAL_DOMAIN

    @classmethod
    def validate(cls) -> None:
        """필수 인증 정보가 누락된 경우 ValueError를 발생시킨다."""
        # KIS 필수 항목
        missing = [
            name
            for name, value in {
                "KIS_APP_KEY":        cls.APP_KEY,
                "KIS_APP_SECRET":     cls.APP_SECRET,
                "KIS_ACCOUNT_NUMBER": cls.ACCOUNT_NUMBER,
            }.items()
            if not value
        ]

        # LLM 제공자별 필수 항목 추가 체크
        if cls.LLM_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        elif cls.LLM_PROVIDER == "groq" and not cls.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        # ollama는 로컬이므로 API 키 불필요

        if missing:
            raise ValueError(f"필수 환경 변수 누락: {', '.join(missing)}")


