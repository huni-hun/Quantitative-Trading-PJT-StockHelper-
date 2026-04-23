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
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from functools import lru_cache

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# .env를 최우선으로 로드 (STOCKHELPER_PYTHON 등 시스템 설정 포함)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ROOT / ".env", override=False)
except ImportError:
    pass

from config.settings import Settings, _parse_tickers, EXCHANGE_CODE_MAP
from src.strategy.trump_monitor import TrumpSignalStore
from utils.logger import LOG_FILE
from utils.llm_client import chat_complete

app = Flask(__name__)
CORS(app)

ENV_PATH = ROOT / ".env"

# 봇 상태 공유 객체 (main.py의 봇 스레드와 공유)
_bot_state: dict = {
    "running":           False,
    "started_at":        None,
    "signals":           {},     # {ticker: {sentiment, technical, decision, price, updated_at}}
    "cycle_count":       0,
    # ── 리스크 관리 ──────────────────────────────────────────────────
    "kill_switch":       False,  # True 이면 전략 루프 즉시 정지
    "kill_reason":       "",     # 킬 사유 (수동 / 일일손실한도 / 패닉셀 완료)
    "daily_loss_limit":  -5.0,   # % (기본 -5%). 0 이면 비활성화
    "daily_start_equity":0.0,    # 오늘 장 시작 시 평가금액 스냅샷
    "daily_pnl_pct":     0.0,    # 오늘 누적 손익률 (%)
}
_bot_lock = threading.Lock()
_log_queue: queue.Queue = queue.Queue(maxsize=500)

# ---------------------------------------------------------------------------
# 봇 서브프로세스 관리 (web/app.py에서 main.py를 자식 프로세스로 제어)
# ---------------------------------------------------------------------------
_bot_proc: subprocess.Popen | None = None   # 실행 중인 봇 프로세스
_bot_proc_lock = threading.Lock()


def _pipe_bot_logs(proc: subprocess.Popen) -> None:
    """봇 프로세스의 stdout/stderr를 _log_queue에 중계하는 스레드 함수."""
    try:
        for line in proc.stdout:                    # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                try:
                    _log_queue.put_nowait(line)
                except queue.Full:
                    pass
    except Exception:
        pass
    finally:
        # 프로세스 종료 → 봇 상태 false 로 갱신
        with _bot_lock:
            _bot_state["running"] = False

# ---------------------------------------------------------------------------
# 마켓 뉴스 캐시 (크롤링 부하 감소 – 10분 캐시)
# ---------------------------------------------------------------------------
_news_cache: dict = {
    "headlines": [],        # [{title, url, press}]
    "summary": "",          # LLM 핵심 요약
    "fetched_at": None,     # datetime
}
_news_cache_lock = threading.Lock()
_NEWS_CACHE_TTL = 7200      # 초 (2시간)

# ---------------------------------------------------------------------------
# 거래내역 저장소 (메모리 + JSON 파일 영속화)
# ---------------------------------------------------------------------------
_TRADES_FILE = ROOT / "logs" / "trades.json"

_trades: list[dict] = []          # [{id, ticker, name, side, price, qty, amount, decided_at, mode}]
_trades_lock = threading.Lock()


def _load_trades_from_file() -> None:
    """앱 시작 시 trades.json에서 거래내역을 복구한다."""
    global _trades
    if _TRADES_FILE.exists():
        try:
            with open(_TRADES_FILE, encoding="utf-8") as f:
                _trades = json.load(f)
        except Exception:
            _trades = []


def _save_trades_to_file() -> None:
    """거래내역을 JSON 파일에 저장한다."""
    _TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(_trades, f, ensure_ascii=False, indent=2)


_load_trades_from_file()

# ---------------------------------------------------------------------------
# 보유종목 저장소 (메모리 + JSON 파일 영속화)
# 구조: {ticker: {name, exchange, qty, avg_price, last_price, updated_at}}
# ---------------------------------------------------------------------------
_HOLDINGS_FILE = ROOT / "logs" / "holdings.json"

_holdings: dict = {}
_holdings_lock = threading.Lock()


def _load_holdings_from_file() -> None:
    global _holdings
    if _HOLDINGS_FILE.exists():
        try:
            with open(_HOLDINGS_FILE, encoding="utf-8") as f:
                _holdings = json.load(f)
        except Exception:
            _holdings = {}


def _save_holdings_to_file() -> None:
    _HOLDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_HOLDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(_holdings, f, ensure_ascii=False, indent=2)


_load_holdings_from_file()

# ---------------------------------------------------------------------------
# 종목명 캐시 (네이버 금융 크롤링)
# ---------------------------------------------------------------------------
_ticker_name_cache: dict[str, str] = {}   # {ticker_code: "삼성전자"}
_ticker_name_lock = threading.Lock()

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# 알림(Notification) 설정 저장소
# ---------------------------------------------------------------------------
_NOTIFY_FILE = ROOT / "logs" / "notify_settings.json"

# 기본 알림 설정 구조
_DEFAULT_NOTIFY: dict = {
    # 텔레그램
    "telegram_enabled":  False,
    "telegram_bot_token": "",   # 봇 토큰 (민감 – 마스킹)
    "telegram_chat_id":   "",   # 채팅방 ID

    # 알림 레벨 토글
    "notify_trade":       True,   # 매수/매도 발생
    "notify_trump":       True,   # 트럼프 시그널 변경
    "notify_error":       True,   # ERROR 로그
    "notify_market_open": False,  # 장 시작/종료
    "notify_daily_summary": False,# 일일 요약 (자정 전)
}

_notify_settings: dict = dict(_DEFAULT_NOTIFY)
_notify_lock = threading.Lock()


def _load_notify_settings() -> None:
    global _notify_settings
    if _NOTIFY_FILE.exists():
        try:
            with open(_NOTIFY_FILE, encoding="utf-8") as f:
                stored = json.load(f)
            # 기본값 유지하면서 저장된 값 덮어씀
            merged = dict(_DEFAULT_NOTIFY)
            merged.update(stored)
            _notify_settings = merged
        except Exception:
            _notify_settings = dict(_DEFAULT_NOTIFY)
    else:
        _notify_settings = dict(_DEFAULT_NOTIFY)


def _save_notify_settings() -> None:
    _NOTIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_NOTIFY_FILE, "w", encoding="utf-8") as f:
        json.dump(_notify_settings, f, ensure_ascii=False, indent=2)


_load_notify_settings()


# ---------------------------------------------------------------------------
# 텔레그램 메시지 전송 헬퍼
# ---------------------------------------------------------------------------

def _send_telegram(text: str, *, token: str = "", chat_id: str = "") -> tuple[bool, str]:
    """텔레그램 Bot API로 메시지를 전송한다.

    Returns:
        (success: bool, error_message: str)
    """
    with _notify_lock:
        tok = token or _notify_settings.get("telegram_bot_token", "")
        cid = chat_id or _notify_settings.get("telegram_chat_id", "")
        enabled = _notify_settings.get("telegram_enabled", False)

    if not enabled and not token:
        return False, "텔레그램 알림이 비활성화되어 있습니다."
    if not tok:
        return False, "텔레그램 봇 토큰이 설정되지 않았습니다."
    if not cid:
        return False, "텔레그램 Chat ID가 설정되지 않았습니다."

    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": cid, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, ""
        return False, f"API 오류 {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return False, str(exc)


def notify(event: str, message: str) -> None:
    """이벤트 종류에 따라 알림을 전송한다. 논블로킹(별도 스레드).

    event 종류:
        "trade"        – 매수/매도 체결
        "trump"        – 트럼프 시그널 변경
        "error"        – ERROR 로그
        "market_open"  – 장 오픈/클로즈
        "daily_summary"– 일일 요약
    """
    with _notify_lock:
        enabled      = _notify_settings.get("telegram_enabled", False)
        level_key    = f"notify_{event}"
        level_on     = _notify_settings.get(level_key, False)

    if not enabled or not level_on:
        return

    def _send():
        _send_telegram(message)

    threading.Thread(target=_send, daemon=True).start()

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

    for k, v in data.items():
        if k not in written_keys:
            new_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _reload_settings() -> None:
    """Settings 클래스 변수를 .env 기준으로 다시 로드한다."""
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    # 거래 모드
    Settings.IS_MOCK                 = os.getenv("KIS_IS_MOCK", "true").lower() == "true"
    # 실전투자 키
    Settings.REAL_APP_KEY            = os.getenv("KIS_REAL_APP_KEY", "")
    Settings.REAL_APP_SECRET         = os.getenv("KIS_REAL_APP_SECRET", "")
    Settings.REAL_ACCOUNT_NUMBER     = os.getenv("KIS_REAL_ACCOUNT_NUMBER", "")
    # 모의투자 키
    Settings.MOCK_APP_KEY            = os.getenv("KIS_MOCK_APP_KEY", "")
    Settings.MOCK_APP_SECRET         = os.getenv("KIS_MOCK_APP_SECRET", "")
    Settings.MOCK_ACCOUNT_NUMBER     = os.getenv("KIS_MOCK_ACCOUNT_NUMBER", "")
    # LLM
    Settings.LLM_PROVIDER            = os.getenv("LLM_PROVIDER", "openai").lower()
    Settings.OPENAI_API_KEY          = os.getenv("OPENAI_API_KEY", "")
    Settings.OPENAI_MODEL            = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    Settings.GROQ_API_KEY            = os.getenv("GROQ_API_KEY", "")
    Settings.GROQ_MODEL              = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    Settings.OLLAMA_BASE_URL         = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    Settings.OLLAMA_MODEL            = os.getenv("OLLAMA_MODEL", "llama3.2")
    # 매매
    Settings.ORDER_QUANTITY          = int(os.getenv("ORDER_QUANTITY", "1"))
    Settings.STRATEGY_INTERVAL_SECONDS = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "180"))
    Settings.TICKER_DELAY_SECONDS    = float(os.getenv("TICKER_DELAY_SECONDS", "1.5"))
    Settings.TARGET_TICKERS          = _parse_tickers(
        os.getenv("TARGET_TICKERS", "005930:KRX")
    )


# ---------------------------------------------------------------------------
# 마켓 뉴스 크롤링 & LLM 요약
# ---------------------------------------------------------------------------

def _fetch_market_headlines(max_count: int = 20) -> list[dict]:
    """네이버 금융 메인 뉴스 페이지에서 오늘의 증시 뉴스 헤드라인을 크롤링한다.

    실제 HTML 구조 분석 결과:
      - href 에 'news_read.naver' 가 포함된 <a> 태그가 뉴스 링크임.
      - CSS 셀렉터 방식은 JS 렌더링 전 구조와 맞지 않아 사용하지 않음.
    """
    crawl_targets = [
        "https://finance.naver.com/news/mainnews.naver",
        "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258",
    ]

    seen: set = set()
    headlines: list[dict] = []

    for url in crawl_targets:
        if len(headlines) >= max_count:
            break
        try:
            resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

            for a in soup.find_all("a", href=True):
                href  = a.get("href", "")
                title = a.get_text(strip=True)
                # 'news_read.naver' 포함 링크만 뉴스 기사로 인정
                if "news_read" not in href:
                    continue
                if not title or len(title) < 6 or title in seen:
                    continue
                if not href.startswith("http"):
                    href = "https://finance.naver.com" + href
                seen.add(title)
                headlines.append({"title": title, "url": href})
                if len(headlines) >= max_count:
                    break

        except Exception:
            continue

    return headlines


_NEWS_SUMMARY_PROMPT = """당신은 주식 투자 전문 애널리스트입니다.
아래 오늘의 증시 뉴스 헤드라인들을 읽고, 한국 투자자 관점에서 핵심 내용을
3~5줄로 간결하게 요약하세요. 시장에 미치는 영향(긍정/부정/혼조)도 한 줄로 덧붙이세요.
반드시 한국어로 답하세요."""


def _summarize_headlines(headlines: list[dict]) -> str:
    """LLM으로 헤드라인 목록을 핵심 요약한다."""
    if not headlines:
        return "수집된 뉴스가 없습니다."
    text = "\n".join(f"- {h['title']}" for h in headlines)
    try:
        return chat_complete(
            system_prompt=_NEWS_SUMMARY_PROMPT,
            user_message=f"[오늘의 증시 뉴스]\n{text}",
            temperature=0.3,
            max_tokens=400,
        ).strip()
    except Exception as exc:
        return f"(LLM 요약 실패: {exc})"


def _get_market_news(force_refresh: bool = False) -> dict:
    """캐시된 마켓 뉴스를 반환하거나, TTL 초과 시 재크롤링한다."""
    with _news_cache_lock:
        now = datetime.now()
        cached_at = _news_cache.get("fetched_at")
        is_stale = (
            cached_at is None
            or (now - cached_at).total_seconds() > _NEWS_CACHE_TTL
            or force_refresh
        )
        if is_stale:
            headlines = _fetch_market_headlines(max_count=20)
            summary   = _summarize_headlines(headlines)
            _news_cache["headlines"]  = headlines
            _news_cache["summary"]    = summary
            _news_cache["fetched_at"] = now

        return {
            "headlines":  _news_cache["headlines"],
            "summary":    _news_cache["summary"],
            "fetched_at": _news_cache["fetched_at"].strftime("%Y-%m-%d %H:%M:%S")
                          if _news_cache["fetched_at"] else None,
        }


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
            "bot_running":         _bot_state["running"],
            "started_at":          _bot_state["started_at"],
            "cycle_count":         _bot_state["cycle_count"],
            "trump_signal":        trump.latest_signal,
            "trump_score":         round(trump.latest_score, 3),
            "trump_posts":         recent,
            "signals":             _bot_state["signals"],
            "current_time":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            # ── 리스크 관리 ─────────────────────────────────────────
            "kill_switch":         _bot_state["kill_switch"],
            "kill_reason":         _bot_state["kill_reason"],
            "daily_loss_limit":    _bot_state["daily_loss_limit"],
            "daily_pnl_pct":       round(_bot_state["daily_pnl_pct"], 2),
        })


# ---------------------------------------------------------------------------
# API – 리스크 관리 (킬 스위치 / 일일 손실 한도)
# ---------------------------------------------------------------------------

@app.route("/api/risk/status", methods=["GET"])
def api_risk_status():
    """현재 리스크 관리 상태를 반환한다."""
    with _bot_lock:
        return jsonify({
            "kill_switch":       _bot_state["kill_switch"],
            "kill_reason":       _bot_state["kill_reason"],
            "daily_loss_limit":  _bot_state["daily_loss_limit"],
            "daily_pnl_pct":     round(_bot_state["daily_pnl_pct"], 2),
            "daily_start_equity":_bot_state["daily_start_equity"],
        })


@app.route("/api/risk/kill-switch", methods=["POST"])
def api_kill_switch():
    """킬 스위치 발동 / 해제 API.

    Body:
        activate   : true = 발동, false = 해제
        panic_sell : true = 발동 + 전 종목 시장가 매도 (패닉 셀)
        reason     : 킬 사유 문자열 (선택)
    """
    body        = request.json or {}
    activate    = bool(body.get("activate", True))
    panic_sell  = bool(body.get("panic_sell", False))
    reason      = str(body.get("reason", "수동 킬 스위치"))

    with _bot_lock:
        _bot_state["kill_switch"] = activate
        _bot_state["kill_reason"] = reason if activate else ""

    if activate:
        msg = f"🚨 <b>킬 스위치 발동!</b>\n사유: {reason}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        notify("error", msg)

    # 패닉 셀: 보유 종목 전체 시장가 매도
    sell_results: list[dict] = []
    if activate and panic_sell:
        try:
            from src.api.auth import KISAuth
            from src.api.order import OrderAPI
            from config.settings import TickerInfo as TI
            auth = KISAuth()
            auth.authenticate()
            order_api = OrderAPI(auth=auth)

            with _holdings_lock:
                holdings_snapshot = {
                    k: dict(v) for k, v in _holdings.items()
                    if v.get("qty", 0) > 0
                }

            for ticker, h in holdings_snapshot.items():
                qty      = h.get("qty", 0)
                exchange = h.get("exchange", "KRX")
                name     = h.get("name", ticker)
                is_dom   = bool(__import__("re").fullmatch(r"\d{6}", ticker))
                ti       = TI(code=ticker, exchange=exchange, is_domestic=is_dom)
                try:
                    order_api.market_sell(ti, quantity=qty)
                    last_p = h.get("last_price", 0)
                    _record_trade(
                        ticker=ticker, name=name, side="SELL",
                        price=float(last_p), qty=qty, exchange=exchange,
                    )
                    sell_results.append({"ticker": ticker, "qty": qty, "ok": True})
                    notify("trade",
                        f"📉 <b>[패닉 셀] {name}({ticker})</b>\n"
                        f"수량: {qty}주 | 킬 스위치 발동\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                except Exception as exc:
                    sell_results.append({"ticker": ticker, "qty": qty, "ok": False, "error": str(exc)})

            with _bot_lock:
                _bot_state["kill_reason"] = f"패닉 셀 완료 ({len(sell_results)}종목 청산)"

        except Exception as exc:
            return jsonify({"ok": False, "message": f"패닉 셀 실패: {exc}"}), 500

    return jsonify({
        "ok": True,
        "kill_switch": activate,
        "panic_sell":  panic_sell,
        "sell_results": sell_results,
        "message": ("킬 스위치 해제됨" if not activate
                    else f"킬 스위치 발동 {'+ 패닉 셀 완료' if panic_sell else ''}"),
    })


@app.route("/api/risk/daily-loss-limit", methods=["POST"])
def api_set_daily_loss_limit():
    """일일 최대 손실 한도를 설정한다.

    Body:
        limit_pct         : 손실 한도 % (음수, 예: -5.0). 0 = 비활성화
        reset_daily_equity: true = 오늘 기준 자산 스냅샷 재설정
    """
    body      = request.json or {}
    limit     = float(body.get("limit_pct", -5.0))
    do_reset  = bool(body.get("reset_daily_equity", False))

    # 항상 음수로 저장
    if limit > 0:
        limit = -limit

    with _bot_lock:
        _bot_state["daily_loss_limit"] = limit
        if do_reset:
            # 현재 보유 평가금액을 기준점으로 설정
            with _holdings_lock:
                equity = sum(
                    h.get("last_price", h.get("avg_price", 0)) * h.get("qty", 0)
                    for h in _holdings.values()
                )
            _bot_state["daily_start_equity"] = equity
            _bot_state["daily_pnl_pct"]      = 0.0
            _bot_state["kill_switch"]         = False
            _bot_state["kill_reason"]         = ""

    return jsonify({
        "ok": True,
        "daily_loss_limit": limit,
        "message": f"일일 손실 한도 {'비활성화' if limit == 0 else f'{limit}% 로 설정됨'}",
    })



# ---------------------------------------------------------------------------
# API – 봇 시작 / 중지 (서브프로세스 제어)
# ---------------------------------------------------------------------------

@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    """main.py 봇을 서브프로세스로 시작한다.

    이미 실행 중이면 409를 반환한다.
    """
    global _bot_proc
    with _bot_proc_lock:
        # 기존 프로세스가 살아있으면 중복 시작 방지
        if _bot_proc is not None and _bot_proc.poll() is None:
            return jsonify({"ok": False, "message": "봇이 이미 실행 중입니다."}), 409

        try:
            main_py = str(ROOT / "main.py")
            env = os.environ.copy()
            env["STOCKHELPER_SUBPROCESS"] = "1"   # main.py가 HTTP 콜백 모드로 동작
            env["DASHBOARD_URL"]          = "http://127.0.0.1:5000"

            # QGIS 등 외부 Python이 등록한 PYTHONPATH/PYTHONHOME 이 venv 격리를 뚫지 못하도록 제거
            env.pop("PYTHONPATH",    None)
            env.pop("PYTHONHOME",    None)
            env.pop("PYTHONSTARTUP", None)

            # PATH도 최소화하여 QGIS Python이 끼어들 여지를 차단
            system32 = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "system32")
            systemroot = os.environ.get("SystemRoot", "C:\\Windows")
            python313  = "C:\\Python313"
            env["PATH"] = f"{python313};{python313}\\Scripts;{system32};{systemroot}"

            # Python 실행 파일 우선순위:
            #   1) 프로젝트 venv (QGIS 완전 격리)
            #   2) .env / 환경변수의 STOCKHELPER_PYTHON
            #   3) 현재 프로세스의 sys.executable
            venv_python = ROOT / "venv" / "Scripts" / "python.exe"
            if venv_python.exists():
                python_exe = str(venv_python)
            else:
                python_exe = os.getenv("STOCKHELPER_PYTHON", sys.executable)

            proc = subprocess.Popen(
                [python_exe, main_py],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(ROOT),
                env=env,
            )
            _bot_proc = proc

            # 로그 중계 스레드 시작
            t = threading.Thread(target=_pipe_bot_logs, args=(proc,), daemon=True)
            t.start()

            # _bot_state running 플래그 갱신 (set_bot_running은 main.py가 import 후 호출하지만
            # 서브프로세스 모드에서는 별도 프로세스이므로 여기서 직접 갱신)
            with _bot_lock:
                _bot_state["running"]    = True
                _bot_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # 킬 스위치가 발동 중이면 해제하고 시작
                if _bot_state.get("kill_switch"):
                    _bot_state["kill_switch"] = False
                    _bot_state["kill_reason"] = ""

            msg = f"🤖 봇이 시작되었습니다. PID: {proc.pid}"
            notify("market_open", f"🤖 <b>StockHelper 봇 시작</b>\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            return jsonify({"ok": True, "pid": proc.pid, "message": msg})

        except Exception as exc:
            return jsonify({"ok": False, "message": f"봇 시작 실패: {exc}"}), 500


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    """실행 중인 봇 서브프로세스를 종료한다.

    Body (선택):
        force: true → SIGKILL (강제 종료), false(기본) → SIGTERM (정상 종료)
    """
    global _bot_proc
    body  = request.json or {}
    force = bool(body.get("force", False))

    with _bot_proc_lock:
        if _bot_proc is None or _bot_proc.poll() is not None:
            # 프로세스가 없어도 상태만 갱신
            with _bot_lock:
                _bot_state["running"] = False
            return jsonify({"ok": True, "message": "봇이 이미 중지 상태입니다."})

        try:
            if force:
                _bot_proc.kill()
            else:
                _bot_proc.terminate()

            # 최대 5초 대기
            try:
                _bot_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _bot_proc.kill()

            _bot_proc = None

        except Exception as exc:
            return jsonify({"ok": False, "message": f"봇 종료 실패: {exc}"}), 500

    with _bot_lock:
        _bot_state["running"] = False

    notify("market_open", f"⏹ <b>StockHelper 봇 중지</b>\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return jsonify({"ok": True, "message": "봇이 중지되었습니다."})


@app.route("/api/bot/status", methods=["GET"])
def api_bot_proc_status():
    """봇 서브프로세스 상태를 반환한다."""
    with _bot_proc_lock:
        pid     = _bot_proc.pid if _bot_proc else None
        running = (_bot_proc is not None and _bot_proc.poll() is None)

    # _bot_state.running 도 동기화
    if not running:
        with _bot_lock:
            _bot_state["running"] = False

    with _bot_lock:
        return jsonify({
            "running":    running,
            "pid":        pid,
            "started_at": _bot_state.get("started_at"),
        })


# ---------------------------------------------------------------------------
# API – 봇 HTTP 콜백 (main.py 서브프로세스 → 대시보드 상태 보고)
# ---------------------------------------------------------------------------

@app.route("/api/bot/signal", methods=["POST"])
def api_bot_signal():
    """서브프로세스 main.py가 시그널 상태를 보고하는 콜백 엔드포인트."""
    body = request.json or {}
    ticker    = body.get("ticker", "")
    sentiment = body.get("sentiment", "HOLD")
    technical = body.get("technical", "HOLD")
    decision  = body.get("decision", "HOLD")
    price     = str(body.get("price", "N/A"))
    momentum        = body.get("momentum", "HOLD")
    composite_score = float(body.get("composite_score", 0.0))
    tech_score      = float(body.get("tech_score", 0.0))
    sentiment_score = float(body.get("sentiment_score", 0.0))
    momentum_score  = float(body.get("momentum_score", 0.0))
    tech_breakdown  = body.get("tech_breakdown", {})
    if ticker:
        update_bot_signal(
            ticker, sentiment, technical, decision, price,
            momentum=momentum,
            composite_score=composite_score,
            tech_score=tech_score,
            sentiment_score=sentiment_score,
            momentum_score=momentum_score,
            tech_breakdown=tech_breakdown,
        )
    return jsonify({"ok": True})


@app.route("/api/bot/running", methods=["POST"])
def api_bot_running_cb():
    """서브프로세스 main.py가 실행/중지 상태를 보고하는 콜백 엔드포인트."""
    body    = request.json or {}
    running = bool(body.get("running", False))
    with _bot_lock:
        _bot_state["running"] = running
        if running:
            _bot_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return jsonify({"ok": True})


@app.route("/api/bot/trade", methods=["POST"])
def api_bot_trade_cb():
    """서브프로세스 main.py가 체결된 거래를 보고하는 콜백 엔드포인트."""
    body = request.json or {}
    _record_trade(
        ticker   = body.get("ticker", ""),
        name     = body.get("name", body.get("ticker", "")),
        side     = body.get("side", "BUY"),
        price    = float(body.get("price", 0) or 0),
        qty      = int(body.get("qty", 0) or 0),
        exchange = body.get("exchange", "KRX"),
    )
    return jsonify({"ok": True})


@app.route("/api/market-news")
def api_market_news():
    """오늘의 증시 뉴스 헤드라인 + LLM 핵심 요약을 반환한다.

    Query params:
        refresh=1  →  캐시 무시하고 즉시 재크롤링
    """
    force = request.args.get("refresh", "0") == "1"
    data = _get_market_news(force_refresh=force)
    return jsonify(data)


# ---------------------------------------------------------------------------
# 종목명 조회 헬퍼
# ---------------------------------------------------------------------------

def _fetch_ticker_name(code: str, exchange: str = "KRX") -> str:
    """네이버 금융에서 종목명을 크롤링한다. 실패 시 code 그대로 반환.

    네이버 금융 종목 페이지는 실제로 UTF-8로 서빙되므로
    r.content를 utf-8로 직접 디코딩해야 인코딩 깨짐이 없다.
    """
    with _ticker_name_lock:
        if code in _ticker_name_cache:
            return _ticker_name_cache[code]

    name = code  # 기본값
    try:
        is_domestic = bool(re.fullmatch(r"\d{6}", code))
        if is_domestic:
            url = f"https://finance.naver.com/item/main.naver?code={code}"
            resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=8)
            # 실제 응답은 UTF-8 → r.content로 직접 디코딩
            html = resp.content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            # 종목명 셀렉터 순서대로 시도
            tag = (
                soup.select_one(".h_company h2")
                or soup.select_one(".wrap_company h2 a")
                or soup.select_one(".wrap_company h2")
            )
            if tag:
                name = tag.get_text(strip=True)
            elif soup.title:
                # fallback: "삼성전자 : Npay 증권" → 앞부분만
                name = soup.title.string.split(":")[0].strip()
        else:
            # 해외 종목: 티커 자체를 이름으로 사용 (네이버 검색 결과 불안정)
            name = code
    except Exception:
        pass

    with _ticker_name_lock:
        _ticker_name_cache[code] = name
    return name


# ---------------------------------------------------------------------------
# API – 종목명 일괄 조회
# ---------------------------------------------------------------------------

@app.route("/api/ticker-names", methods=["POST"])
def api_ticker_names():
    """티커 코드 목록을 받아 종목명 dict를 반환한다.

    Body: {"tickers": [{"code": "005930", "exchange": "KRX"}, ...]}
    """
    body = request.json or {}
    tickers = body.get("tickers", [])
    result = {}
    for t in tickers:
        code = t.get("code", "")
        exchange = t.get("exchange", "KRX")
        if code:
            result[code] = _fetch_ticker_name(code, exchange)
    return jsonify(result)


# ---------------------------------------------------------------------------
# 펀더멘털 지표 조회 헬퍼 (PER / PBR / PSR / ROE)
# ---------------------------------------------------------------------------

_fundamentals_cache: dict = {}          # {code: {per, pbr, psr, roe, updated_at}}
_fundamentals_lock = threading.Lock()

def _fetch_fundamentals(code: str, exchange: str = "KRX") -> dict:
    """PER / PBR / PSR / ROE 를 조회한다.

    - 국내(KRX 6자리): pykrx get_market_fundamental 사용
    - 해외: yfinance 사용
    캐시 유효시간 1시간.
    """
    with _fundamentals_lock:
        cached = _fundamentals_cache.get(code)
        if cached:
            try:
                diff = (datetime.now() - datetime.fromisoformat(cached["updated_at"])).seconds
                if diff < 3600:
                    return cached
            except Exception:
                pass

    result = {"per": "-", "pbr": "-", "psr": "-", "roe": "-", "updated_at": datetime.now().isoformat()}

    try:
        is_domestic = bool(re.fullmatch(r"\d{6}", code))
        if is_domestic:
            # 네이버 금융 크롤링으로 PER / PBR / ROE 조회
            url = f"https://finance.naver.com/item/main.naver?code={code}"
            resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=10)
            html = resp.content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")

            per_tag = soup.select_one("em#_per")
            pbr_tag = soup.select_one("em#_pbr")
            if per_tag:
                v = per_tag.get_text(strip=True).replace(",", "")
                result["per"] = v if v and v != "N/A" else "-"
            if pbr_tag:
                v = pbr_tag.get_text(strip=True).replace(",", "")
                result["pbr"] = v if v and v != "N/A" else "-"

            # ROE → tb_type1 테이블에서 마지막 값(연간 기준) 사용
            for tr in soup.select("table.tb_type1 tr"):
                th = tr.select_one("th")
                tds = tr.select("td")
                if th and "ROE" in th.get_text() and tds:
                    # 마지막 td (가장 최근 연간)
                    v = tds[-1].get_text(strip=True).replace(",", "")
                    if v and v not in ("-", "N/A", ""):
                        result["roe"] = v
                        break

            result["psr"] = "-"  # 네이버 금융 미제공
        else:
            # 해외 종목: yfinance
            import yfinance as yf
            # 거래소별 suffix 매핑
            suffix_map = {"TSE": ".T", "HKS": ".HK", "SHS": ".SS", "SZS": ".SZ", "FRA": ".F"}
            suffix = suffix_map.get(exchange, "")
            ticker_yf = f"{code}{suffix}"
            info = yf.Ticker(ticker_yf).info
            def _fmt_yf(val):
                try:
                    v = float(val)
                    return "-" if v == 0 else f"{v:.2f}"
                except Exception:
                    return "-"
            result["per"] = _fmt_yf(info.get("trailingPE") or info.get("forwardPE", 0))
            result["pbr"] = _fmt_yf(info.get("priceToBook", 0))
            result["psr"] = _fmt_yf(info.get("priceToSalesTrailing12Months", 0))
            roe_raw = info.get("returnOnEquity", 0)
            try:
                result["roe"] = f"{float(roe_raw)*100:.2f}" if roe_raw else "-"
            except Exception:
                result["roe"] = "-"
    except Exception as e:
        app.logger.warning("펀더멘털 조회 실패 [%s]: %s", code, e)

    result["updated_at"] = datetime.now().isoformat()
    with _fundamentals_lock:
        _fundamentals_cache[code] = result
    return result


# ---------------------------------------------------------------------------
# API – 펀더멘털 지표 일괄 조회
# ---------------------------------------------------------------------------

@app.route("/api/fundamentals", methods=["POST"])
def api_fundamentals():
    """티커 목록의 PER/PBR/PSR/ROE를 반환한다.

    Body: {"tickers": [{"code": "005930", "exchange": "KRX"}, ...]}
    Returns: {"005930": {"per": "12.34", "pbr": "1.23", "psr": "-", "roe": "15.67"}, ...}
    """
    body = request.json or {}
    tickers = body.get("tickers", [])
    result = {}
    for t in tickers:
        code = t.get("code", "")
        exchange = t.get("exchange", "KRX")
        if code:
            result[code] = _fetch_fundamentals(code, exchange)
    return jsonify(result)




# ---------------------------------------------------------------------------
# API – 거래내역 조회 / 추가
# ---------------------------------------------------------------------------

@app.route("/api/trades", methods=["GET"])
def api_get_trades():
    """최근 거래내역을 반환한다 (최대 200건, 최신순)."""
    with _trades_lock:
        return jsonify(list(reversed(_trades[-200:])))


@app.route("/api/trades", methods=["POST"])
def api_add_trade():
    """거래내역을 수동으로 추가한다 (테스트/수동 입력용)."""
    body = request.json or {}
    _record_trade(
        ticker=body.get("ticker", ""),
        name=body.get("name", ""),
        side=body.get("side", "BUY"),
        price=body.get("price", 0),
        qty=body.get("qty", 0),
        exchange=body.get("exchange", "KRX"),
    )
    return jsonify({"ok": True})


@app.route("/api/trades/clear", methods=["POST"])
def api_clear_trades():
    """거래내역을 전체 삭제한다."""
    with _trades_lock:
        _trades.clear()
        _save_trades_to_file()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API – 보유종목 조회
# ---------------------------------------------------------------------------

@app.route("/api/holdings", methods=["GET"])
def api_get_holdings():
    """현재 보유종목을 반환한다."""
    with _holdings_lock:
        result = []
        for ticker, h in _holdings.items():
            qty = h.get("qty", 0)
            if qty > 0:
                avg = h.get("avg_price", 0)
                last = h.get("last_price", avg)
                pnl_pct = round((last - avg) / avg * 100, 2) if avg else 0
                exchange = h.get("exchange", "KRX")
                fund = _fetch_fundamentals(ticker, exchange)
                result.append({
                    "ticker":     ticker,
                    "name":       h.get("name", ticker),
                    "exchange":   exchange,
                    "qty":        qty,
                    "avg_price":  avg,
                    "last_price": last,
                    "pnl_pct":    pnl_pct,
                    "updated_at": h.get("updated_at", ""),
                    "per":        fund.get("per", "-"),
                    "pbr":        fund.get("pbr", "-"),
                    "psr":        fund.get("psr", "-"),
                    "roe":        fund.get("roe", "-"),
                })
        return jsonify(result)


# ---------------------------------------------------------------------------
# API – 성과 분석
# ---------------------------------------------------------------------------

@app.route("/api/analytics")
def api_analytics():
    """거래내역을 분석하여 성과 지표와 시각화 데이터를 반환한다.

    반환 구조:
        equity_curve  : [{date, equity, pnl}]         누적 손익 커브
        summary       : {total_trades, wins, losses,  핵심 지표 요약
                         win_rate, profit_factor,
                         avg_win, avg_loss, mdd,
                         total_pnl, best_trade, worst_trade}
        heatmap       : {YYYY-MM: {DD: pnl}}           월별 일별 손익 히트맵
        ticker_stats  : [{ticker, name, trades,        종목별 통계
                          wins, win_rate, total_pnl}]
    """
    with _trades_lock:
        trades = list(_trades)

    if not trades:
        return jsonify({
            "equity_curve": [],
            "summary": {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "profit_factor": 0,
                "avg_win": 0, "avg_loss": 0, "mdd": 0,
                "total_pnl": 0, "best_trade": 0, "worst_trade": 0,
            },
            "heatmap": {},
            "ticker_stats": [],
        })

    # ── 1. 매수/매도 페어링으로 실현 손익 계산 ─────────────────────────
    # 단순 FIFO 방식: 매수 평균단가 추적 → 매도 시 손익 확정
    cost_basis: dict[str, dict] = {}   # {ticker: {qty, avg_price}}
    realized: list[dict] = []          # [{date, ticker, pnl, side}]

    for t in sorted(trades, key=lambda x: x.get("decided_at", "")):
        ticker  = t.get("ticker", "")
        side    = t.get("side", "")
        price   = float(t.get("price", 0) or 0)
        qty     = int(t.get("qty", 0) or 0)
        date_str = t.get("decided_at", "")[:10]   # YYYY-MM-DD
        name    = t.get("name", ticker)

        if side == "BUY":
            cb = cost_basis.setdefault(ticker, {"qty": 0, "avg_price": 0.0, "name": name})
            total = cb["avg_price"] * cb["qty"] + price * qty
            cb["qty"] += qty
            cb["avg_price"] = total / cb["qty"] if cb["qty"] else 0

        elif side == "SELL":
            cb = cost_basis.get(ticker)
            if cb and cb["qty"] > 0:
                pnl = (price - cb["avg_price"]) * min(qty, cb["qty"])
                cb["qty"] = max(0, cb["qty"] - qty)
                realized.append({
                    "date":   date_str,
                    "ticker": ticker,
                    "name":   cb.get("name", ticker),
                    "pnl":    round(pnl, 2),
                })

    # ── 2. 에쿼티 커브 ─────────────────────────────────────────────────
    equity_by_date: dict[str, float] = {}
    for r in realized:
        equity_by_date[r["date"]] = equity_by_date.get(r["date"], 0) + r["pnl"]

    cumulative = 0.0
    equity_curve = []
    for date in sorted(equity_by_date):
        cumulative += equity_by_date[date]
        equity_curve.append({
            "date":   date,
            "pnl":    round(equity_by_date[date], 2),
            "equity": round(cumulative, 2),
        })

    # ── 3. MDD 계산 ────────────────────────────────────────────────────
    mdd = 0.0
    peak = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100
            if dd > mdd:
                mdd = dd

    # ── 4. 핵심 지표 요약 ──────────────────────────────────────────────
    pnls = [r["pnl"] for r in realized]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl      = round(sum(pnls), 2)
    win_rate       = round(len(wins) / len(pnls) * 100, 1) if pnls else 0
    avg_win        = round(sum(wins) / len(wins), 2)   if wins   else 0
    avg_loss       = round(sum(losses) / len(losses), 2) if losses else 0
    gross_profit   = sum(wins)
    gross_loss     = abs(sum(losses))
    profit_factor  = round(gross_profit / gross_loss, 2) if gross_loss else float("inf")
    best_trade     = round(max(pnls), 2) if pnls else 0
    worst_trade    = round(min(pnls), 2) if pnls else 0

    summary = {
        "total_trades":  len(pnls),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      win_rate,
        "profit_factor": profit_factor if profit_factor != float("inf") else 9999,
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "mdd":           round(mdd, 2),
        "total_pnl":     total_pnl,
        "best_trade":    best_trade,
        "worst_trade":   worst_trade,
    }

    # ── 5. 히트맵 (월별 → 일별 손익) ──────────────────────────────────
    heatmap: dict[str, dict[str, float]] = {}
    for r in realized:
        ym = r["date"][:7]    # YYYY-MM
        dd = r["date"][8:]    # DD
        heatmap.setdefault(ym, {})
        heatmap[ym][dd] = round(heatmap[ym].get(dd, 0) + r["pnl"], 2)

    # ── 6. 종목별 통계 ─────────────────────────────────────────────────
    ticker_map: dict[str, dict] = {}
    for r in realized:
        tk = r["ticker"]
        s  = ticker_map.setdefault(tk, {
            "ticker": tk, "name": r["name"],
            "trades": 0, "wins": 0, "total_pnl": 0.0,
        })
        s["trades"]    += 1
        s["total_pnl"] += r["pnl"]
        if r["pnl"] > 0:
            s["wins"] += 1

    ticker_stats = []
    for s in ticker_map.values():
        s["win_rate"]  = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0
        s["total_pnl"] = round(s["total_pnl"], 2)
        ticker_stats.append(s)
    ticker_stats.sort(key=lambda x: x["total_pnl"], reverse=True)

    return jsonify({
        "equity_curve": equity_curve,
        "summary":      summary,
        "heatmap":      heatmap,
        "ticker_stats": ticker_stats,
    })


# ---------------------------------------------------------------------------
# API – 백테스팅 / 시뮬레이션
# ---------------------------------------------------------------------------

def _run_backtest_on_df(
    df: "pd.DataFrame",
    rsi_period: int,
    rsi_oversold: float,
    rsi_overbought: float,
    bb_period: int,
    bb_std: float,
    initial_cash: float,
    order_qty: int,
) -> dict:
    """주어진 OHLCV DataFrame과 파라미터로 백테스트를 수행하고 결과를 반환한다.

    단순 시장가 매매 시뮬레이션 (슬리피지·수수료 미적용).
    매수/매도 중복 방지: 이미 보유 중이면 매수 안 함, 미보유면 매도 안 함.
    """
    import numpy as np
    import pandas as pd

    close = df["close"].astype(float).reset_index(drop=True)
    dates = df["date"].tolist()

    # ── RSI 계산 (Wilder EWM) ──────────────────────────────────────────
    if len(close) < rsi_period + 1:
        return {"error": f"데이터 부족: {len(close)}일 (RSI에 최소 {rsi_period+1}일 필요)"}
    delta     = close.diff()
    gain      = delta.clip(lower=0)
    loss      = (-delta).clip(lower=0)
    avg_gain  = gain.ewm(alpha=1/rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss  = loss.ewm(alpha=1/rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs        = avg_gain / avg_loss.replace(0, np.nan)
    rsi_s     = (100 - 100 / (1 + rs)).fillna(50)

    # ── 볼린저 밴드 ────────────────────────────────────────────────────
    mid   = close.rolling(bb_period).mean()
    std_s = close.rolling(bb_period).std(ddof=0)
    upper = (mid + bb_std * std_s).bfill().ffill()
    lower = (mid - bb_std * std_s).bfill().ffill()

    # ── 시뮬레이션 ─────────────────────────────────────────────────────
    cash      = float(initial_cash)
    position  = 0      # 보유 수량
    avg_cost  = 0.0    # 매수 평균단가
    equity_curve: list[dict] = []
    trades:       list[dict] = []

    start_idx = max(rsi_period, bb_period)
    for i in range(start_idx, len(close)):
        price  = float(close.iloc[i])
        r      = float(rsi_s.iloc[i])
        lo     = float(lower.iloc[i])
        hi     = float(upper.iloc[i])
        d      = dates[i]

        # ── 신호 판단 (OR 조건: RSI 또는 BB 중 하나만 충족해도 시그널) ──
        # BUY:  RSI 과매도 OR 볼린저 하단 이탈 (미보유 상태)
        # SELL: RSI 과매수 OR 볼린저 상단 돌파 (보유 상태)
        signal = "HOLD"
        if position == 0:
            if r < rsi_oversold or price <= lo:
                signal = "BUY"
        else:
            if r > rsi_overbought or price >= hi:
                signal = "SELL"

        # 매매 체결
        if signal == "BUY" and cash >= price * order_qty:
            cost       = price * order_qty
            cash      -= cost
            position  += order_qty
            avg_cost   = price
            trades.append({"date": d, "side": "BUY", "price": round(price, 4), "qty": order_qty})

        elif signal == "SELL" and position > 0:
            qty       = position
            pnl       = (price - avg_cost) * qty
            cash     += price * qty
            trades.append({
                "date": d, "side": "SELL", "price": round(price, 4),
                "qty": qty, "pnl": round(pnl, 2),
            })
            position = 0
            avg_cost = 0.0

        # 에쿼티 = 현금 + 평가금액
        equity = cash + position * price
        equity_curve.append({
            "date":   d,
            "equity": round(equity, 2),
            "rsi":    round(r, 2),
            "close":  round(price, 4),
            "signal": signal,   # "BUY" / "SELL" / "HOLD" — 차트 마커용
        })

    # ── 미체결 포지션 강제 청산 (마지막 가격) ──────────────────────────
    if position > 0:
        last_price = float(close.iloc[-1])
        pnl = (last_price - avg_cost) * position
        cash += last_price * position
        trades.append({
            "date": dates[-1], "side": "SELL(청산)",
            "price": round(last_price, 4), "qty": position,
            "pnl": round(pnl, 2),
        })
        if equity_curve:
            equity_curve[-1]["equity"] = round(cash, 2)

    # ── 성과 지표 ──────────────────────────────────────────────────────
    sell_trades = [t for t in trades if "pnl" in t]
    pnls        = [t["pnl"] for t in sell_trades]
    wins        = [p for p in pnls if p > 0]
    losses      = [p for p in pnls if p <= 0]
    total_pnl   = round(sum(pnls), 2)
    win_rate    = round(len(wins) / len(pnls) * 100, 1) if pnls else 0
    pf          = round(sum(wins) / abs(sum(losses)), 2) if losses and sum(wins) > 0 else (999 if wins else 0)
    ret_pct     = round((equity_curve[-1]["equity"] - initial_cash) / initial_cash * 100, 2) if equity_curve else 0

    # MDD
    mdd = 0.0
    peak = initial_cash
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak: peak = eq
        if peak > 0: mdd = max(mdd, (peak - eq) / peak * 100)

    return {
        "equity_curve": equity_curve,
        "trades":       trades[-100:],   # 최근 100건만
        "summary": {
            "initial_cash":    initial_cash,
            "final_equity":    equity_curve[-1]["equity"] if equity_curve else initial_cash,
            "total_return_pct": ret_pct,
            "total_pnl":       total_pnl,
            "total_trades":    len(pnls),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate":        win_rate,
            "profit_factor":   pf,
            "mdd":             round(mdd, 2),
            "best_trade":      round(max(pnls), 2) if pnls else 0,
            "worst_trade":     round(min(pnls), 2) if pnls else 0,
        },
        "params": {
            "rsi_period": rsi_period, "rsi_oversold": rsi_oversold,
            "rsi_overbought": rsi_overbought,
            "bb_period": bb_period, "bb_std": bb_std,
        },
    }


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """백테스트 실행 API."""
    try:
        return _api_backtest_inner()
    except Exception as exc:
        import traceback
        return jsonify({"error": f"서버 오류: {exc}", "detail": traceback.format_exc()[-300:]}), 500


def _api_backtest_inner():
    """백테스트 실제 로직 (api_backtest 래퍼에서 호출)."""
    import pandas as pd
    from config.settings import TickerInfo as TI

    body         = request.json or {}
    ticker       = body.get("ticker", "").upper().strip()
    exchange     = body.get("exchange", "KRX").upper().strip()
    start_date   = body.get("start_date", "")
    end_date     = body.get("end_date", "")
    initial_cash = float(body.get("initial_cash", 10_000_000))
    order_qty    = int(body.get("order_qty", 1))
    params_a     = body.get("params_a", {})
    params_b     = body.get("params_b", None)

    if not ticker:
        return jsonify({"error": "ticker 필드가 필요합니다."}), 400

    # ── 조회 기간 → lookback_days 동적 계산 ────────────────────────────
    # start_date ~ end_date 사이 캘린더 일수의 1.5배 (주말·공휴일 여유)
    # 최소 800일 보장
    try:
        from datetime import date as _date
        _end   = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else _date.today()
        _start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else (_end.replace(year=_end.year - 3))
        calendar_days = (_end - _start).days
        lookback_days = max(800, int(calendar_days * 1.5))
    except Exception:
        lookback_days = 800

    # ── OHLCV 조회 ──────────────────────────────────────────────────────
    try:
        from src.api.auth import KISAuth
        from src.api.price import PriceAPI
        auth      = KISAuth()
        auth.authenticate()
        price_api = PriceAPI(auth=auth)
        is_dom    = bool(re.fullmatch(r"\d{6}", ticker))
        ti        = TI(code=ticker, exchange=exchange, is_domestic=is_dom)
        df_full   = price_api.get_ohlcv(ti, lookback_days=lookback_days)

        if df_full.empty:
            return jsonify({"error": f"OHLCV 데이터를 가져올 수 없습니다: {ticker}. KIS API 인증 정보를 확인하세요."}), 400

        # 날짜 형식을 YYYY-MM-DD로 정규화
        def _fmt(d: str) -> str:
            d = str(d).replace("-", "")
            return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d
        df_full["date"] = df_full["date"].astype(str).apply(_fmt)

        # 날짜 필터 (YYYY-MM-DD 형식으로 통일된 후 비교)
        if start_date:
            df_full = df_full[df_full["date"] >= start_date]
        if end_date:
            df_full = df_full[df_full["date"] <= end_date]

        if len(df_full) < 30:
            return jsonify({"error": f"기간 내 데이터가 너무 적습니다 ({len(df_full)}일). 기간을 늘려주세요."}), 400

    except Exception as exc:
        return jsonify({"error": f"KIS API 데이터 조회 실패: {exc}"}), 500

    def _p(src: dict, key: str, default):
        try: return type(default)(src.get(key, default))
        except: return default

    def _run(p: dict) -> dict:
        return _run_backtest_on_df(
            df           = df_full.copy(),
            rsi_period   = _p(p, "rsi_period",   14),
            rsi_oversold    = _p(p, "rsi_oversold",    30.0),
            rsi_overbought  = _p(p, "rsi_overbought",  70.0),
            bb_period    = _p(p, "bb_period",    20),
            bb_std       = _p(p, "bb_std",       2.0),
            initial_cash = initial_cash,
            order_qty    = order_qty,
        )

    result_a = _run(params_a)
    result_b = _run(params_b) if params_b else None

    return jsonify({
        "result_a":    result_a,
        "result_b":    result_b,
        "ohlcv_range": {
            "start": df_full["date"].iloc[0]  if not df_full.empty else "",
            "end":   df_full["date"].iloc[-1] if not df_full.empty else "",
            "bars":  len(df_full),
        },
    })


# ---------------------------------------------------------------------------
# API – 알림(Notification) 설정
# ---------------------------------------------------------------------------

@app.route("/api/notify/settings", methods=["GET"])
def api_get_notify_settings():
    """알림 설정을 반환한다. 봇 토큰은 마스킹."""
    with _notify_lock:
        result = dict(_notify_settings)
    # 봇 토큰 마스킹 (있으면 ****만 반환)
    if result.get("telegram_bot_token"):
        result["telegram_bot_token"] = "****"
    return jsonify(result)


@app.route("/api/notify/settings", methods=["POST"])
def api_save_notify_settings():
    """알림 설정을 저장한다."""
    body = request.json or {}
    with _notify_lock:
        # 토큰이 **** 이면 기존 값 유지
        if body.get("telegram_bot_token", "") == "****":
            body.pop("telegram_bot_token", None)
        for k, v in body.items():
            if k in _notify_settings or k == "telegram_bot_token":
                _notify_settings[k] = v
        _save_notify_settings()
    return jsonify({"ok": True, "message": "알림 설정이 저장되었습니다."})


@app.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    """테스트 메시지를 전송한다. 저장 전 연결 확인용."""
    body  = request.json or {}
    token = body.get("telegram_bot_token", "").strip()
    cid   = body.get("telegram_chat_id",   "").strip()

    # 토큰이 ****이면 저장된 값 사용
    if token == "****":
        with _notify_lock:
            token = _notify_settings.get("telegram_bot_token", "")
    if not cid:
        with _notify_lock:
            cid = _notify_settings.get("telegram_chat_id", "")

    text = (
        "✅ <b>StockHelper 알림 테스트</b>\n\n"
        "텔레그램 연결이 정상입니다!\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    ok, err = _send_telegram(text, token=token, chat_id=cid)
    if ok:
        return jsonify({"ok": True, "message": "테스트 메시지를 전송했습니다."})
    return jsonify({"ok": False, "message": err}), 400


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
        # 거래 모드
        "KIS_IS_MOCK":              env.get("KIS_IS_MOCK", "true"),
        # 실전투자 키
        "KIS_REAL_APP_KEY":         mask(env.get("KIS_REAL_APP_KEY", "")),
        "KIS_REAL_APP_SECRET":      mask(env.get("KIS_REAL_APP_SECRET", "")),
        "KIS_REAL_ACCOUNT_NUMBER":  mask(env.get("KIS_REAL_ACCOUNT_NUMBER", "")),
        # 모의투자 키
        "KIS_MOCK_APP_KEY":         mask(env.get("KIS_MOCK_APP_KEY", "")),
        "KIS_MOCK_APP_SECRET":      mask(env.get("KIS_MOCK_APP_SECRET", "")),
        "KIS_MOCK_ACCOUNT_NUMBER":  mask(env.get("KIS_MOCK_ACCOUNT_NUMBER", "")),
        "LLM_PROVIDER":       env.get("LLM_PROVIDER", "openai"),
        "OPENAI_API_KEY":     mask(env.get("OPENAI_API_KEY", "")),
        "OPENAI_MODEL":       env.get("OPENAI_MODEL", "gpt-4o-mini"),
        "GROQ_API_KEY":       mask(env.get("GROQ_API_KEY", "")),
        "GROQ_MODEL":         env.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "OLLAMA_BASE_URL":    env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "OLLAMA_MODEL":       env.get("OLLAMA_MODEL", "llama3.2"),
        "TARGET_TICKERS":     env.get("TARGET_TICKERS", ""),
        "ORDER_QUANTITY":     env.get("ORDER_QUANTITY", "1"),
        "STRATEGY_INTERVAL_SECONDS": env.get("STRATEGY_INTERVAL_SECONDS", "180"),
        "TICKER_DELAY_SECONDS":      env.get("TICKER_DELAY_SECONDS", "1.5"),
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
        "exchanges": EXCHANGE_CODE_MAP,
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """프론트에서 넘어온 설정을 .env에 저장하고 Settings를 즉시 반영한다."""
    body: dict = request.json or {}
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
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-50:]:
                yield f"data: {json.dumps(line)}\n\n"
        with open(log_path, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {json.dumps(line.rstrip())}\n\n"
                else:
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

def update_bot_signal(
    ticker: str,
    sentiment: str,
    technical: str,
    decision: str,
    price: str,
    momentum: str = "HOLD",
    composite_score: float = 0.0,
    tech_score: float = 0.0,
    sentiment_score: float = 0.0,
    momentum_score: float = 0.0,
    tech_breakdown: dict | None = None,
):
    """main.py 전략 루프에서 호출하여 시그널 상태를 갱신한다."""
    with _bot_lock:
        _bot_state["signals"][ticker] = {
            "sentiment":       sentiment,
            "technical":       technical,
            "momentum":        momentum,
            "decision":        decision,
            "composite_score": round(composite_score, 3),
            "tech_score":      round(tech_score, 2),
            "sentiment_score": round(sentiment_score, 3),
            "momentum_score":  round(momentum_score, 2),
            "tech_breakdown":  tech_breakdown or {},
            "price":           price,
            "updated_at":      datetime.now().strftime("%H:%M:%S"),
        }
        _bot_state["cycle_count"] += 1

    # 보유종목 last_price 갱신
    try:
        p = float(price)
        with _holdings_lock:
            if ticker in _holdings:
                _holdings[ticker]["last_price"] = p
                _holdings[ticker]["updated_at"]  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _save_holdings_to_file()
    except Exception:
        pass


def _record_trade(
    ticker: str,
    name: str,
    side: str,       # "BUY" | "SELL"
    price: float,
    qty: int,
    exchange: str = "KRX",
) -> None:
    """거래내역을 기록하고 보유종목을 갱신한다. main.py에서 매매 직후 호출."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode    = "모의투자" if Settings.IS_MOCK else "실전투자"
    amount  = round(price * qty, 2)

    with _trades_lock:
        _trades.append({
            "id":         len(_trades) + 1,
            "ticker":     ticker,
            "name":       name,
            "exchange":   exchange,
            "side":       side,
            "price":      price,
            "qty":        qty,
            "amount":     amount,
            "decided_at": now_str,
            "mode":       mode,
        })
        _save_trades_to_file()

    # 보유종목 업데이트
    with _holdings_lock:
        h = _holdings.get(ticker, {
            "name": name, "exchange": exchange,
            "qty": 0, "avg_price": 0.0,
            "last_price": price, "updated_at": now_str,
        })
        if side == "BUY":
            total_cost = h["avg_price"] * h["qty"] + price * qty
            h["qty"]       += qty
            h["avg_price"]  = round(total_cost / h["qty"], 4) if h["qty"] else 0
        elif side == "SELL":
            h["qty"] = max(0, h["qty"] - qty)
        h["last_price"] = price
        h["updated_at"] = now_str
        h["name"]       = name
        _holdings[ticker] = h
        _save_holdings_to_file()

    # ── 텔레그램 알림 ──────────────────────────────────────────────────
    icon   = "📈" if side == "BUY" else "📉"
    label  = "매수" if side == "BUY" else "매도"
    price_fmt = f"{price:,.0f}" if price >= 1 else f"{price:.4f}"
    amount_fmt = f"{amount:,.0f}"
    msg = (
        f"{icon} <b>[{label}] {name}({ticker})</b>\n"
        f"거래소: {exchange} | {mode}\n"
        f"단가: {price_fmt} × {qty}주\n"
        f"거래금액: {amount_fmt}\n"
        f"🕐 {now_str}"
    )
    notify("trade", msg)

    # ── 일일 손익 추적 + 손실 한도 자동 체크 ─────────────────────────
    if side == "SELL":
        _update_daily_pnl()


def _update_daily_pnl() -> None:
    """SELL 체결 후 오늘 누적 손익률을 갱신하고, 손실 한도 초과 시 킬스위치를 발동한다."""
    with _bot_lock:
        start_equity = _bot_state.get("daily_start_equity", 0.0)
        limit_pct    = _bot_state.get("daily_loss_limit", 0.0)

    if start_equity <= 0 or limit_pct == 0:
        return  # 기준 자산 미설정 또는 한도 비활성화

    # 현재 총 평가금액 계산
    with _holdings_lock:
        current_equity = sum(
            h.get("last_price", h.get("avg_price", 0)) * h.get("qty", 0)
            for h in _holdings.values()
        )

    pnl_pct = (current_equity - start_equity) / start_equity * 100

    with _bot_lock:
        _bot_state["daily_pnl_pct"] = pnl_pct
        # 손실 한도 초과 & 아직 킬스위치 미발동 상태
        if pnl_pct <= limit_pct and not _bot_state["kill_switch"]:
            _bot_state["kill_switch"] = True
            _bot_state["kill_reason"] = (
                f"일일 손실 한도 초과 ({pnl_pct:.2f}% ≤ {limit_pct:.2f}%)"
            )
            reason = _bot_state["kill_reason"]

    if pnl_pct <= limit_pct:
        notify("error",
            f"🛑 <b>일일 손실 한도 초과 — 봇 자동 정지</b>\n"
            f"현재 손익률: {pnl_pct:.2f}%  /  한도: {limit_pct:.2f}%\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )


def check_kill_switch() -> tuple[bool, str]:
    """main.py 전략 루프에서 매 사이클마다 호출하여 킬스위치 상태를 확인한다.

    Returns:
        (kill_active: bool, reason: str)
    """
    with _bot_lock:
        return _bot_state["kill_switch"], _bot_state.get("kill_reason", "")


def set_bot_running(running: bool):
    with _bot_lock:
        _bot_state["running"] = running
        if running:
            _bot_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    # host="127.0.0.1" → 본인 PC에서만 접근 가능 (보안 강화)
    # host="0.0.0.0"   → 같은 와이파이의 다른 기기(핸드폰 등)도 접근 가능
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


