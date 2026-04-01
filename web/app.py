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
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings, _parse_tickers, EXCHANGE_CODE_MAP
from src.strategy.trump_monitor import TrumpSignalStore
from utils.logger import LOG_FILE
from utils.llm_client import chat_complete

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
    Settings.STRATEGY_INTERVAL_SECONDS = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "3600"))
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
# API – 마켓 뉴스 (오늘의 증시 뉴스 + LLM 핵심 요약)
# ---------------------------------------------------------------------------

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
                result.append({
                    "ticker":     ticker,
                    "name":       h.get("name", ticker),
                    "exchange":   h.get("exchange", "KRX"),
                    "qty":        qty,
                    "avg_price":  avg,
                    "last_price": last,
                    "pnl_pct":    pnl_pct,
                    "updated_at": h.get("updated_at", ""),
                })
        return jsonify(result)


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
        "STRATEGY_INTERVAL_SECONDS": env.get("STRATEGY_INTERVAL_SECONDS", "60"),
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


def set_bot_running(running: bool):
    with _bot_lock:
        _bot_state["running"] = running
        if running:
            _bot_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
