"""web/app.py – StockHelper 대시보드 & 설정 Flask 서버.

실행:
    python web/app.py

접속:
    http://localhost:5000
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings, _parse_tickers, EXCHANGE_CODE_MAP
from src.strategy.trump_monitor import TrumpSignalStore
from utils.logger import LOG_FILE

app = Flask(__name__)
CORS(app)

ENV_PATH = ROOT / ".env"

# 봇 상태 공유 객체 (main.py의 봇 스레드와 공유)
_bot_state: dict = {
    "running": False,
    "started_at": None,
    "signals": {},          # {ticker: {sentiment, technical, decision, price, updated_at}}
    "cycle_count": 0,
}
_bot_lock = threading.Lock()
_log_queue: queue.Queue = queue.Queue(maxsize=500)


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def _read_env() -> dict:
    """현재 .env 파일을 파싱하여 dict로 반환한다."""
    env: dict = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _write_env(data: dict) -> None:
    """dict를 .env 파일에 덮어씀. 기존 주석 블록은 유지한다."""
    existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    written_keys: set = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in data:
                new_lines.append(f"{k}={data[k]}")
                written_keys.add(k)
            else:
                new_lines.append(line)

    # 새로 추가된 키 append
    for k, v in data.items():
        if k not in written_keys:
            new_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _reload_settings() -> None:
    """Settings 클래스 변수를 .env 기준으로 다시 로드한다."""
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    # 클래스 변수 직접 갱신
    Settings.APP_KEY              = os.getenv("KIS_APP_KEY", "")
    Settings.APP_SECRET           = os.getenv("KIS_APP_SECRET", "")
    Settings.ACCOUNT_NUMBER       = os.getenv("KIS_ACCOUNT_NUMBER", "")
    Settings.IS_MOCK              = os.getenv("KIS_IS_MOCK", "true").lower() == "true"
    Settings.LLM_PROVIDER         = os.getenv("LLM_PROVIDER", "openai").lower()
    Settings.OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY", "")
    Settings.OPENAI_MODEL         = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    Settings.GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "")
    Settings.GROQ_MODEL           = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    Settings.OLLAMA_BASE_URL      = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    Settings.OLLAMA_MODEL         = os.getenv("OLLAMA_MODEL", "llama3.2")
    Settings.ORDER_QUANTITY       = int(os.getenv("ORDER_QUANTITY", "1"))
    Settings.STRATEGY_INTERVAL_SECONDS = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "60"))
    Settings.TARGET_TICKERS       = _parse_tickers(
        os.getenv("TARGET_TICKERS", "005930:KRX")
    )


# ---------------------------------------------------------------------------
# API – 상태
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    """봇 실행 상태 및 트럼프 시그널 반환."""
    trump = TrumpSignalStore()
    recent = [
        {
            "text":      p.text[:120],
            "published": p.published.strftime("%Y-%m-%d %H:%M UTC"),
            "score":     round(p.score, 3),
            "signal":    p.signal,
            "reason":    p.reason,
            "keywords":  p.keywords,
        }
        for p in reversed(trump.recent_posts[-5:])
    ]
    with _bot_lock:
        return jsonify({
            "bot_running":    _bot_state["running"],
            "started_at":     _bot_state["started_at"],
            "cycle_count":    _bot_state["cycle_count"],
            "trump_signal":   trump.latest_signal,
            "trump_score":    round(trump.latest_score, 3),
            "trump_posts":    recent,
            "signals":        _bot_state["signals"],
            "current_time":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })


# ---------------------------------------------------------------------------
# API – 설정 조회 / 저장
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """현재 .env 값을 반환한다. 민감한 키는 마스킹."""
    env = _read_env()

    def mask(v: str) -> str:
        return v[:4] + "****" + v[-4:] if len(v) > 10 else ("****" if v else "")

    return jsonify({
        # KIS
        "KIS_APP_KEY":        mask(env.get("KIS_APP_KEY", "")),
        "KIS_APP_SECRET":     mask(env.get("KIS_APP_SECRET", "")),
        "KIS_ACCOUNT_NUMBER": mask(env.get("KIS_ACCOUNT_NUMBER", "")),
        "KIS_IS_MOCK":        env.get("KIS_IS_MOCK", "true"),
        # LLM
        "LLM_PROVIDER":       env.get("LLM_PROVIDER", "openai"),
        "OPENAI_API_KEY":     mask(env.get("OPENAI_API_KEY", "")),
        "OPENAI_MODEL":       env.get("OPENAI_MODEL", "gpt-4o-mini"),
        "GROQ_API_KEY":       mask(env.get("GROQ_API_KEY", "")),
        "GROQ_MODEL":         env.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "OLLAMA_BASE_URL":    env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "OLLAMA_MODEL":       env.get("OLLAMA_MODEL", "llama3.2"),
        # 매매
        "TARGET_TICKERS":     env.get("TARGET_TICKERS", ""),
        "ORDER_QUANTITY":     env.get("ORDER_QUANTITY", "1"),
        "STRATEGY_INTERVAL_SECONDS": env.get("STRATEGY_INTERVAL_SECONDS", "60"),
        # 전략 파라미터
        "RSI_PERIOD":         env.get("RSI_PERIOD", "14"),
        "RSI_OVERSOLD":       env.get("RSI_OVERSOLD", "30"),
        "RSI_OVERBOUGHT":     env.get("RSI_OVERBOUGHT", "70"),
        "BB_PERIOD":          env.get("BB_PERIOD", "20"),
        "BB_STD":             env.get("BB_STD", "2.0"),
        "LOOKBACK_DAYS":      env.get("LOOKBACK_DAYS", "60"),
        "SENTIMENT_THRESHOLD":env.get("SENTIMENT_THRESHOLD", "0.3"),
        "MAX_HEADLINES":      env.get("MAX_HEADLINES", "15"),
        "TRUMP_BULL_THRESHOLD":  env.get("TRUMP_BULL_THRESHOLD", "0.35"),
        "TRUMP_BEAR_THRESHOLD":  env.get("TRUMP_BEAR_THRESHOLD", "-0.35"),
        "TRUMP_POLL_INTERVAL":   env.get("TRUMP_POLL_INTERVAL", "30"),
        # 거래소 코드
        "exchanges": EXCHANGE_CODE_MAP,
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """프론트에서 넘어온 설정을 .env에 저장하고 Settings를 즉시 반영한다."""
    body: dict = request.json or {}

    # 빈 문자열이거나 마스킹된 값("****")은 기존 값 유지
    env = _read_env()
    for k, v in body.items():
        if k == "exchanges":
            continue
        v_str = str(v).strip()
        if v_str and "****" not in v_str:
            env[k] = v_str

    _write_env(env)
    _reload_settings()
    return jsonify({"ok": True, "message": "설정이 저장되었습니다."})


# ---------------------------------------------------------------------------
# API – 로그 SSE 스트림
# ---------------------------------------------------------------------------

@app.route("/api/logs/stream")
def api_log_stream():
    """Server-Sent Events로 실시간 로그를 스트리밍한다."""
    log_path = Path(LOG_FILE)

    def generate():
        # 기존 로그 마지막 50줄 먼저 전송
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-50:]:
                yield f"data: {json.dumps(line)}\n\n"

        # 이후 신규 내용 tail
        with open(log_path, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # 파일 끝으로 이동
            while True:
                line = f.readline()
                if line:
                    yield f"data: {json.dumps(line.rstrip())}\n\n"
                else:
                    import time
                    time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 페이지
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# 봇 상태 업데이트 헬퍼 (main.py에서 import해서 사용)
# ---------------------------------------------------------------------------

def update_bot_signal(ticker: str, sentiment: str, technical: str, decision: str, price: str):
    """main.py 전략 루프에서 호출하여 시그널 상태를 갱신한다."""
    with _bot_lock:
        _bot_state["signals"][ticker] = {
            "sentiment":  sentiment,
            "technical":  technical,
            "decision":   decision,
            "price":      price,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
        _bot_state["cycle_count"] += 1


def set_bot_running(running: bool):
    with _bot_lock:
        _bot_state["running"] = running
        if running:
            _bot_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
