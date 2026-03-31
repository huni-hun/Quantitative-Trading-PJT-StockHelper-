"""LLM 클라이언트 팩토리.

LLM_PROVIDER 환경 변수에 따라 OpenAI / Groq / Ollama 중 하나를 선택하여
동일한 인터페이스로 채팅 완성(Chat Completion)을 호출한다.

지원 제공자:
    - openai : OpenAI API (유료)
    - groq   : Groq API  (무료 플랜 있음, https://console.groq.com)
    - ollama : 로컬 LLM  (완전 무료, https://ollama.com)
"""

from __future__ import annotations

from config.settings import Settings
from utils.logger import get_logger

logger = get_logger(__name__)


def _get_model() -> str:
    """현재 제공자에 맞는 모델명을 반환한다."""
    p = Settings.LLM_PROVIDER
    if p == "groq":
        return Settings.GROQ_MODEL
    if p == "ollama":
        return Settings.OLLAMA_MODEL
    return Settings.OPENAI_MODEL


def chat_complete(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
    max_tokens: int = 150,
) -> str:
    """설정된 LLM 제공자로 채팅 완성을 호출하고 응답 텍스트를 반환한다.

    Args:
        system_prompt: 시스템 프롬프트.
        user_message:  사용자 메시지.
        temperature:   샘플링 온도 (0.0 = 결정론적).
        max_tokens:    최대 응답 토큰 수.

    Returns:
        str: LLM 응답 텍스트. 실패 시 빈 문자열 반환.
    """
    provider = Settings.LLM_PROVIDER
    model = _get_model()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ]

    try:
        if provider == "openai":
            return _call_openai(messages, model, temperature, max_tokens)
        if provider == "groq":
            return _call_groq(messages, model, temperature, max_tokens)
        if provider == "ollama":
            return _call_ollama(messages, model, temperature, max_tokens)

        logger.error("알 수 없는 LLM 제공자: %s – openai/groq/ollama 중 선택하세요.", provider)
        return ""

    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] LLM 호출 실패: %s", provider, exc)
        return ""


def _call_openai(messages: list, model: str, temperature: float, max_tokens: int) -> str:
    """OpenAI API 호출."""
    from openai import OpenAI
    client = OpenAI(api_key=Settings.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _call_groq(messages: list, model: str, temperature: float, max_tokens: int) -> str:
    """Groq API 호출 (OpenAI 호환 인터페이스)."""
    from groq import Groq
    client = Groq(api_key=Settings.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _call_ollama(messages: list, model: str, temperature: float, max_tokens: int) -> str:
    """Ollama 로컬 서버 호출 (OpenAI 호환 REST 엔드포인트 사용)."""
    import requests
    url = f"{Settings.OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")
