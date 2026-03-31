# 📈 StockHelper — 한국투자증권 자동 매매 봇

한국투자증권(KIS) OpenAPI 기반의 **국내·해외 주식 자동 매매 봇**.  
뉴스 감성 분석(LLM), 기술적 분석(RSI + 볼린저 밴드), 트럼프 포스팅 실시간 모니터링을 결합하여 매매 시그널을 생성한다.  
**웹 대시보드(Flask)** 에서 모든 설정을 GUI로 관리하고, 실시간 시그널·로그를 확인할 수 있다.

---

## 목차

1. [프로젝트 구조](#프로젝트-구조)
2. [사전 준비](#사전-준비)
3. [설치](#설치)
4. [빠른 시작](#빠른-시작)
5. [웹 대시보드](#웹-대시보드)
6. [환경 변수 전체 목록](#환경-변수-전체-목록)
7. [LLM 제공자 선택](#llm-제공자-선택)
8. [지원 거래소 및 종목 형식](#지원-거래소-및-종목-형식)
9. [전략 상세](#전략-상세)
10. [매매 결정 로직](#매매-결정-로직)
11. [장 운영시간 관리](#장-운영시간-관리)
12. [로그](#로그)
13. [주의사항](#주의사항)

---

## 프로젝트 구조

```
├── main.py                          # 봇 진입점 – 인증, TrumpMonitor, 전략 루프
├── .env                             # 환경 변수 (직접 작성, Git 제외)
├── .env.example                     # 환경 변수 양식 예시 (Git 포함)
├── requirements.txt                 # 의존 패키지 목록
│
├── config/
│   └── settings.py                  # 환경 변수 로드 / TickerInfo 파싱 / 유효성 검사
│
├── src/
│   ├── api/
│   │   ├── auth.py                  # KIS OAuth2 토큰 발급 및 갱신
│   │   ├── price.py                 # 국내·해외 현재가 / 일봉 OHLCV 조회
│   │   └── order.py                 # 국내·해외 시장가 매수·매도 주문
│   └── strategy/
│       ├── news_sentiment_llm.py    # 전략 1 – 네이버 뉴스 크롤링 + LLM 감성 분석
│       ├── deadcat_technical.py     # 전략 2 – RSI + 볼린저 밴드 기술적 분석
│       └── trump_monitor.py         # 전략 3 – 트럼프 Truth Social 실시간 모니터
│
├── utils/
│   ├── llm_client.py                # LLM 추상화 클라이언트 (OpenAI / Groq / Ollama)
│   ├── logger.py                    # 콘솔 + 로테이팅 파일 로거
│   └── error_handler.py             # API 오류 처리 및 커스텀 예외
│
├── web/
│   ├── app.py                       # Flask 웹 대시보드 서버
│   ├── templates/index.html         # 단일 페이지 대시보드 UI
│   └── static/
│       ├── css/style.css
│       └── js/app.js
│
└── logs/
    └── trading_bot.log              # 자동 생성 (Git 제외)
```

---

## 사전 준비

| 항목 | 발급처 | 필수 여부 |
|---|---|---|
| KIS OpenAPI 앱키 / 앱시크릿 | [KIS 개발자센터](https://apiportal.koreainvestment.com) → 앱 등록 | ✅ 필수 |
| KIS 계좌번호 | 한국투자증권 계좌 (모의투자 계좌 권장) | ✅ 필수 |
| Groq API 키 | [console.groq.com](https://console.groq.com) (무료) | LLM_PROVIDER=groq 시 |
| OpenAI API 키 | [platform.openai.com](https://platform.openai.com/api-keys) (유료) | LLM_PROVIDER=openai 시 |
| Ollama 로컬 설치 | [ollama.com](https://ollama.com) (무료) | LLM_PROVIDER=ollama 시 |

- **Python 3.10 이상** 필요

---

## 설치

```bash
# 1. 저장소 클론
git clone https://github.com/your-repo/Quantitative-Trading-PJT-StockHelper-.git
cd Quantitative-Trading-PJT-StockHelper-

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경 변수 파일 생성
copy .env.example .env   # Windows
# cp .env.example .env   # Mac/Linux
```

---

## 빠른 시작

### 방법 A — 웹 대시보드에서 설정 후 봇 실행 (권장)

```bash
# 1단계: 웹 대시보드만 먼저 실행
python web/app.py

# 브라우저에서 http://localhost:5000 접속
# → KIS API, LLM, 종목, 전략 파라미터 설정 후 저장

# 2단계: 설정 완료 후 봇 실행 (봇 + 웹 대시보드 동시 가동)
python main.py
```

### 방법 B — .env 직접 편집 후 봇 실행

```bash
# .env 파일에 직접 값 입력 후
python main.py
```

> 봇을 중지하려면 `Ctrl + C`

---

## 웹 대시보드

`http://localhost:5000` 에서 접속하며, 6개 탭으로 구성된다.

| 탭 | 기능 |
|---|---|
| 🏠 **대시보드** | 봇 실행 상태, 누적 사이클 수, 트럼프 시그널, 종목별 시그널 테이블, 트럼프 최근 포스팅 분석 |
| 🔑 **KIS API 설정** | 앱키·앱시크릿·계좌번호 입력, 모의투자 ↔ 실전투자 토글 |
| 🤖 **LLM 설정** | Groq / Ollama / OpenAI 선택, 각 제공자별 API 키·모델 설정 |
| 📋 **종목 관리** | 종목코드 + 거래소 추가/삭제, 주문 수량, 전략 실행 주기 설정 |
| ⚙️ **전략 파라미터** | RSI·볼린저밴드·뉴스감성·트럼프모니터 전 수치 조정 |
| 📜 **실시간 로그** | SSE 스트리밍, 자동 스크롤, ERROR 필터링 |

- 설정은 저장 즉시 `.env` 파일에 반영되며 봇이 실행 중이어도 **다음 사이클부터 즉시 적용**된다.
- 민감한 API 키는 화면에 마스킹(`****`)으로 표시된다.

---

## 환경 변수 전체 목록

`.env` 파일에 아래 항목을 설정한다. 웹 대시보드에서도 동일하게 관리 가능.

### KIS API

| 변수 | 설명 | 기본값 |
|---|---|---|
| `KIS_APP_KEY` | KIS 앱키 | — |
| `KIS_APP_SECRET` | KIS 앱시크릿 | — |
| `KIS_ACCOUNT_NUMBER` | 계좌번호 (8자리+상품코드 2자리) | — |
| `KIS_IS_MOCK` | `true` = 모의투자 / `false` = 실전투자 | `true` |

### LLM 설정

| 변수 | 설명 | 기본값 |
|---|---|---|
| `LLM_PROVIDER` | `groq` / `openai` / `ollama` | `openai` |
| `OPENAI_API_KEY` | OpenAI API 키 | — |
| `OPENAI_MODEL` | OpenAI 모델명 | `gpt-4o-mini` |
| `GROQ_API_KEY` | Groq API 키 | — |
| `GROQ_MODEL` | Groq 모델명 | `llama-3.3-70b-versatile` |
| `OLLAMA_BASE_URL` | Ollama 서버 URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama 모델명 | `llama3.2` |

### 매매 기본 설정

| 변수 | 설명 | 기본값 |
|---|---|---|
| `TARGET_TICKERS` | 거래 종목 목록 (형식: `종목코드:거래소`, 쉼표 구분) | `005930:KRX` |
| `ORDER_QUANTITY` | 종목당 1회 주문 수량 (주) | `1` |
| `STRATEGY_INTERVAL_SECONDS` | 전략 루프 실행 주기 (초) | `3600` |

### 기술적 분석 파라미터

| 변수 | 설명 | 기본값 |
|---|---|---|
| `RSI_PERIOD` | RSI 계산 기간 | `14` |
| `RSI_OVERSOLD` | 과매도 기준값 (이 값 미만 → BUY 후보) | `30` |
| `RSI_OVERBOUGHT` | 과매수 기준값 (이 값 초과 → SELL 후보) | `70` |
| `BB_PERIOD` | 볼린저 밴드 기간 | `20` |
| `BB_STD` | 볼린저 밴드 표준편차 배수 | `2.0` |
| `LOOKBACK_DAYS` | OHLCV 조회 기간 (거래일 수) | `60` |

### 뉴스 감성 분석 파라미터

| 변수 | 설명 | 기본값 |
|---|---|---|
| `SENTIMENT_THRESHOLD` | 시그널 발생 감성 점수 임계값 | `0.3` |
| `MAX_HEADLINES` | LLM에 전달할 최대 헤드라인 수 | `15` |

### 트럼프 모니터 파라미터

| 변수 | 설명 | 기본값 |
|---|---|---|
| `TRUMP_BULL_THRESHOLD` | BULLISH 판단 점수 기준 | `0.35` |
| `TRUMP_BEAR_THRESHOLD` | BEARISH 판단 점수 기준 | `-0.35` |
| `TRUMP_POLL_INTERVAL` | Truth Social 폴링 간격 (초) | `30` |

---

## LLM 제공자 선택

| 제공자 | 비용 | 필요 설정 | 권장 모델 | 특징 |
|---|---|---|---|---|
| `groq` | **무료 플랜 있음** | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | 빠르고 무료, **가장 권장** |
| `ollama` | **완전 무료** | 로컬 설치 필요 | `llama3.2` | 인터넷 불필요, PC 사양 의존 |
| `openai` | 유료 | `OPENAI_API_KEY` | `gpt-4o-mini` | 가장 정확하나 비용 발생 |

### Groq 무료 키 발급

1. [console.groq.com](https://console.groq.com) → 회원가입
2. **API Keys** 메뉴 → `Create API Key`
3. `.env`의 `GROQ_API_KEY`에 입력 후 `LLM_PROVIDER=groq` 설정

### Ollama 로컬 설치 (완전 무료)

```bash
# 1. https://ollama.com 에서 설치 후
ollama pull llama3.2     # 모델 다운로드 (~2 GB)

# 2. .env 설정
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
```

---

## 지원 거래소 및 종목 형식

`TARGET_TICKERS` 형식: `종목코드:거래소코드` (쉼표로 여러 종목 구분)

```
TARGET_TICKERS=005930:KRX,000660:KRX,AAPL:NAS,TSLA:NAS,9984:TSE
```

| 거래소 코드 | 거래소 | 종목 예시 |
|---|---|---|
| `KRX` | 한국거래소 | `005930:KRX` (삼성전자) |
| `NAS` | 나스닥 | `AAPL:NAS`, `TSLA:NAS` |
| `NYS` | 뉴욕증권거래소 | `BRK.B:NYS` |
| `AMS` | 아멕스 | `SPY:AMS` |
| `TSE` | 도쿄증권거래소 | `9984:TSE` (소프트뱅크) |
| `HKS` | 홍콩증권거래소 | `0700:HKS` (텐센트) |
| `SHS` | 상해증권거래소 | `600519:SHS` (마오타이) |
| `SZS` | 심천증권거래소 | `000858:SZS` |
| `FRA` | 프랑크푸르트 | `SAP:FRA` |

> 국내 종목(숫자 6자리)은 거래소 코드 생략 시 자동으로 `KRX`로 처리된다.

---

## 전략 상세

### 전략 1 — 뉴스 감성 분석 (`NewsSentimentStrategy`)

```
네이버 금융 뉴스 크롤링
  - 국내 종목: 네이버 금융 종목 전용 뉴스 페이지
  - 해외 종목: 네이버 뉴스 검색 (티커명 + 주가)
        ↓  최신 헤드라인 최대 MAX_HEADLINES건 수집
LLM (Groq / OpenAI / Ollama)
        ↓  감성 점수 산출 [-1.0 ~ +1.0]
시그널 생성
```

| 조건 | 시그널 |
|---|---|
| 감성 점수 `≥ +SENTIMENT_THRESHOLD` | **BUY** |
| 감성 점수 `≤ -SENTIMENT_THRESHOLD` | **SELL** |
| 그 외 | **HOLD** |

- 실행 주기: `STRATEGY_INTERVAL_SECONDS`마다 (기본 1시간)

---

### 전략 2 — 기술적 분석 (`DeadcatTechnicalStrategy`)

```
KIS API 일봉 OHLCV (최근 LOOKBACK_DAYS 거래일)
        ↓
RSI (RSI_PERIOD일, Wilder EWM 방식)
볼린저 밴드 (BB_PERIOD일, ±BB_STD×σ)
        ↓
시그널 생성
```

| 조건 | 시그널 |
|---|---|
| RSI `< RSI_OVERSOLD` **AND** 현재가 `≤ 하단 밴드` | **BUY** |
| RSI `> RSI_OVERBOUGHT` **OR** 현재가 `≥ 상단 밴드` | **SELL** |
| 그 외 / 데이터 부족 | **HOLD** |

- 실행 주기: `STRATEGY_INTERVAL_SECONDS`마다 (기본 1시간)

---

### 전략 3 — 트럼프 포스팅 모니터 (`TrumpMonitor`)

```
Nitter RSS (@realDonaldTrump) 폴링
  ← TRUMP_POLL_INTERVAL초마다, 24시간 상시 백그라운드 감시
        ↓  새 포스팅 감지 즉시
LLM (Groq / OpenAI / Ollama)
        ↓  시장 영향도 점수 [-1.0 ~ +1.0] + 키워드 + 이유 추출
TrumpSignalStore (싱글톤) 저장
        ↓
전략 루프에서 거부권(Veto)으로 활용
```

| 조건 | 시그널 | 예시 |
|---|---|---|
| 점수 `≥ TRUMP_BULL_THRESHOLD` | **BULLISH** | 감세, 규제완화, 경제 호황, 특정 기업 칭찬 |
| 점수 `≤ TRUMP_BEAR_THRESHOLD` | **BEARISH** | 관세, 무역전쟁, 기업 비판, 금리 압박 |
| 그 외 / 신규 포스팅 없음 | **NEUTRAL** | 일반 정치 발언 |

- Nitter 인스턴스 3개 자동 폴백 (한 곳이 막히면 다음으로 전환)
- 봇 시작 시 기존 포스트 자동 스킵 → **재시작해도 과거 글로 주문 발생 없음**
- Truth Social 새 글 감지 즉시 LLM 분석 (폴링 방식, 기본 30초 간격)

---

## 매매 결정 로직

세 전략의 시그널을 `_decide_order()`에서 통합한다.

```
전략1(뉴스) = BUY  AND  전략2(기술) = BUY
    └→ 트럼프 BEARISH?  YES → ❌ HOLD (차단)
                        NO  → ✅ BUY 실행

전략1(뉴스) = SELL AND  전략2(기술) = SELL
    └→ 트럼프 BULLISH? YES → ❌ HOLD (차단)
                       NO  → ✅ SELL 실행

그 외 모든 경우 → HOLD (관망)
```

> **핵심 원칙**
> - 전략 1 **AND** 전략 2가 **동시에 동의**해야 주문이 발생한다.
> - 트럼프 시그널은 **거부권(Veto)** 역할만 수행한다 — 단독으로 주문을 생성하지 않는다.

---

## 장 운영시간 관리

봇은 **장 외 시간에는 주문 없이 대기**한다. 트럼프 모니터는 **24시간 상시 동작**.

| 시장 | 운영 시간 (KST) | 비고 |
|---|---|---|
| 국내 (KRX) | 09:00 ~ 15:35 | 평일 기준 |
| 미국 (NAS/NYS/AMS) | 22:30 ~ 05:00 | 서머타임 적용 시 21:30 ~ 04:00 |

- 장 외 시간에는 **최대 1시간 단위**로 재확인 후 대기
- 국내 + 해외 종목 혼합 시 양쪽 시간대 모두 커버

---

## 로그

- 위치: `logs/trading_bot.log`
- 콘솔과 파일 **동시 출력**
- 최대 5 MB × 3개 파일 로테이션
- 웹 대시보드 **📜 실시간 로그** 탭에서 SSE 스트리밍으로 확인 가능

---

## 주의사항

> ⚠️ **반드시 모의투자(`KIS_IS_MOCK=true`)로 충분히 검증 후 실전 투자로 전환하세요.**

- `.env` 파일에는 민감한 인증 정보가 담겨 있으므로 **절대 Git에 커밋하지 마세요** (`.gitignore`에 이미 등록됨)
- 이 프로그램은 **투자 손실에 대한 어떠한 책임도 지지 않습니다** — 자동 매매는 항상 원금 손실 위험이 있습니다
- KIS OpenAPI는 **초당 요청 제한(Rate Limit)**이 있으므로 `STRATEGY_INTERVAL_SECONDS`를 너무 짧게 설정하지 마세요
- Nitter 퍼블릭 인스턴스는 **운영 상태가 불안정**할 수 있습니다 — 모든 인스턴스가 불통이면 트럼프 시그널은 NEUTRAL로 유지됩니다
- OpenAI API는 **유료 서비스**입니다 — 종목 수와 실행 주기에 따라 비용이 발생하므로 Groq(무료) 사용을 권장합니다
