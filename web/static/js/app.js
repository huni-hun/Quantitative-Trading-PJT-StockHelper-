// ── 탭 전환 ──────────────────────────────────────────────────────────

document.querySelectorAll('.sidebar-menu li').forEach(li => {
  li.addEventListener('click', () => {
    document.querySelectorAll('.sidebar-menu li').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    li.classList.add('active');
    document.getElementById(`tab-${li.dataset.tab}`).classList.add('active');
    // 탭별 데이터 자동 로드
    const tab = li.dataset.tab;
    if (tab === 'holdings') loadHoldings();
    if (tab === 'trades')   loadTrades();
  });
});

// ── 토스트 알림 ──────────────────────────────────────────────────────

function showToast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 3000);
}

// ── 대시보드 폴링 ─────────────────────────────────────────────────────

function sigClass(s) {
  if (!s) return '';
  const u = s.toUpperCase();
  if (u === 'BUY'  || u === 'BULLISH') return 'sig-buy sig-bullish';
  if (u === 'SELL' || u === 'BEARISH') return 'sig-sell sig-bearish';
  return 'sig-hold sig-neutral';
}

function refreshDashboard() {
  fetch('/api/status')
    .then(r => r.json())
    .then(d => {
      // 봇 상태
      const running = d.bot_running;
      document.getElementById('d-bot-status').textContent = running ? '🟢 실행 중' : '🔴 중지됨';
      document.getElementById('d-started-at').textContent = d.started_at ? `시작: ${d.started_at}` : '';
      document.getElementById('d-cycle').textContent = d.cycle_count;
      document.getElementById('d-time').textContent = d.current_time;

      const badge = document.getElementById('bot-status-badge');
      badge.textContent = running ? '● 봇 실행 중' : '● 봇 중지됨';
      badge.className = `badge ${running ? 'badge-running' : 'badge-stopped'}`;

      // 트럼프 시그널
      const ts = d.trump_signal || 'NEUTRAL';
      document.getElementById('d-trump-signal').innerHTML =
        `<span class="${sigClass(ts)}">${ts}</span>`;
      document.getElementById('d-trump-score').textContent =
        `점수: ${d.trump_score ?? '--'}`;

      // 종목 시그널 테이블
      const tbody = document.getElementById('signal-tbody');
      const signals = d.signals || {};
      if (Object.keys(signals).length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty">아직 데이터 없음 — 봇 실행 후 갱신됩니다.</td></tr>';
      } else {
        tbody.innerHTML = Object.entries(signals).map(([ticker, v]) => `
          <tr>
            <td><strong>${ticker}</strong></td>
            <td><span class="${sigClass(v.sentiment)}">${v.sentiment || '--'}</span></td>
            <td><span class="${sigClass(v.technical)}">${v.technical || '--'}</span></td>
            <td><span class="${sigClass(v.decision)}">${v.decision || '--'}</span></td>
            <td>${v.price || '--'}</td>
            <td>${v.updated_at || '--'}</td>
          </tr>`).join('');
      }

      // 트럼프 포스팅 카드
      const postsEl = document.getElementById('trump-posts');
      if (!d.trump_posts || d.trump_posts.length === 0) {
        postsEl.innerHTML = '<p style="color:var(--text-sub);font-size:13px;">아직 분석된 포스팅 없음</p>';
      } else {
        // 최신 시그널 배지 갱신
        const latestBadge = document.getElementById('trump-latest-badge');
        if (latestBadge && d.trump_posts[0]) {
          const sig = d.trump_posts[0].signal;
          latestBadge.className = `trump-latest-badge badge ${sig==='BULLISH'?'badge-running':sig==='BEARISH'?'badge-stopped':''}`;
          latestBadge.textContent = sig;
        }
        postsEl.innerHTML = d.trump_posts.map(p => {
          const kws = (p.keywords || []).map(k => `<span class="trump-kw">${k}</span>`).join('');
          return `
            <div class="trump-post">
              <div class="trump-post-header">
                <span class="badge ${p.signal==='BULLISH'?'badge-running':p.signal==='BEARISH'?'badge-stopped':''}">${p.signal}</span>
                <span style="font-weight:600;">점수: ${p.score}</span>
                <span style="color:var(--text-sub);font-size:12px;">${p.published}</span>
              </div>
              <div class="trump-post-text">${p.text}</div>
              <div class="trump-post-meta">📌 ${p.reason}</div>
              ${kws ? `<div class="trump-keywords">${kws}</div>` : ''}
            </div>`;
        }).join('');
      }
    })
    .catch(() => {});
}

refreshDashboard();
setInterval(refreshDashboard, 5000);

// ── 마켓 뉴스 폴링 ───────────────────────────────────────────────────

let _newsLoading = false;

function refreshMarketNews(force = false) {
  if (_newsLoading && !force) return;
  _newsLoading = true;

  const summaryEl   = document.getElementById('news-summary-text');
  const listEl      = document.getElementById('news-headline-list');
  const metaEl      = document.getElementById('news-fetched-at');
  const refreshBtn  = document.querySelector('.btn-news-refresh');

  if (force && refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.textContent = '⟳ 갱신 중...';
  }
  if (force && summaryEl) summaryEl.textContent = 'AI 요약 생성 중...';

  const url = force ? '/api/market-news?refresh=1' : '/api/market-news';
  fetch(url)
    .then(r => r.json())
    .then(d => {
      // 요약
      if (summaryEl) {
        summaryEl.innerHTML = (d.summary || '요약 정보 없음')
          .replace(/\n/g, '<br>');
      }
      // 헤드라인 목록
      if (listEl) {
        if (!d.headlines || d.headlines.length === 0) {
          listEl.innerHTML = '<li class="news-loading">헤드라인을 수집하지 못했습니다.</li>';
        } else {
          listEl.innerHTML = d.headlines.map(h =>
            `<li class="news-item">
               <a href="${h.url || '#'}" target="_blank" rel="noopener" class="news-link">${h.title}</a>
             </li>`
          ).join('');
        }
      }
      // 갱신 시각
      if (metaEl && d.fetched_at) {
        metaEl.textContent = `갱신: ${d.fetched_at}`;
      }
    })
    .catch(() => {
      if (summaryEl) summaryEl.textContent = '뉴스 로드 실패. 서버 연결을 확인하세요.';
    })
    .finally(() => {
      _newsLoading = false;
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '⟳ 새로고침';
      }
    });
}

// 대시보드 첫 진입 시 뉴스 자동 로드
refreshMarketNews();
// 2시간마다 자동 갱신
setInterval(() => refreshMarketNews(false), 7_200_000);

// ── 설정 로드 ─────────────────────────────────────────────────────────

let _currentSettings = {};
let _tickers = [];   // [{code, exchange, qty, interval}]

function loadSettings() {
  fetch('/api/settings')
    .then(r => r.json())
    .then(d => {
      _currentSettings = d;

      // KIS 거래 모드 버튼 상태 복원
      const isMock = d.KIS_IS_MOCK === 'true';
      _kisIsMock = isMock;
      updateKisModeUI(isMock);

      // 실전 / 모의 입력값 (마스킹된 값 표시 – 실제 입력 시 덮어씀)
      ['KIS_REAL_APP_KEY','KIS_REAL_APP_SECRET','KIS_REAL_ACCOUNT_NUMBER',
       'KIS_MOCK_APP_KEY','KIS_MOCK_APP_SECRET','KIS_MOCK_ACCOUNT_NUMBER'
      ].forEach(k => setVal(k, ''));   // 비밀값은 빈칸으로 유지 (마스킹만 hint로 표시)

      // LLM 제공자
      const provider = d.LLM_PROVIDER || 'openai';
      document.querySelector(`input[name=LLM_PROVIDER][value="${provider}"]`).checked = true;
      switchLlmSection(provider);
      setVal('GROQ_MODEL',    d.GROQ_MODEL);
      setVal('OPENAI_MODEL',  d.OPENAI_MODEL);
      setVal('OLLAMA_BASE_URL', d.OLLAMA_BASE_URL);
      setVal('OLLAMA_MODEL',  d.OLLAMA_MODEL);

      // 종목 (코드:거래소:수량:주기 형식)
      _tickers = (d.TARGET_TICKERS || '').split(',')
        .filter(s => s.trim())
        .map(s => {
          const parts = s.split(':');
          return {
            code:     (parts[0] || '').trim().toUpperCase(),
            exchange: (parts[1] || 'KRX').trim().toUpperCase(),
            qty:      parseInt(parts[2]) || 0,
            interval: parseInt(parts[3]) || 0,
          };
        }).filter(t => t.code);
      fetchTickerNames(_tickers);  // 종목명 조회 후 renderTickers 호출
      setVal('ORDER_QUANTITY',            d.ORDER_QUANTITY);
      setVal('STRATEGY_INTERVAL_SECONDS', d.STRATEGY_INTERVAL_SECONDS);

      // 전략 파라미터
      ['RSI_PERIOD','RSI_OVERSOLD','RSI_OVERBOUGHT',
       'BB_PERIOD','BB_STD','LOOKBACK_DAYS',
       'SENTIMENT_THRESHOLD','MAX_HEADLINES',
       'TRUMP_BULL_THRESHOLD','TRUMP_BEAR_THRESHOLD','TRUMP_POLL_INTERVAL'
      ].forEach(k => setVal(k, d[k]));
    });
}

function setVal(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.type === 'checkbox') { el.checked = !!value; }
  else { el.value = value ?? ''; }
}

loadSettings();

// ── LLM 섹션 전환 ─────────────────────────────────────────────────────

document.querySelectorAll('input[name=LLM_PROVIDER]').forEach(r => {
  r.addEventListener('change', () => switchLlmSection(r.value));
});

function switchLlmSection(provider) {
  ['groq', 'ollama', 'openai'].forEach(p => {
    const el = document.getElementById(`llm-${p}`);
    if (el) el.style.display = p === provider ? 'block' : 'none';
  });
}

// ── KIS 거래 모드 전환 ────────────────────────────────────────────────

let _kisIsMock = true;  // 현재 선택된 모드

function setKisMode(isMock) {
  _kisIsMock = isMock;
  updateKisModeUI(isMock);
}

function updateKisModeUI(isMock) {
  const btnMock = document.getElementById('btn-mode-mock');
  const btnReal = document.getElementById('btn-mode-real');
  const warn    = document.getElementById('kis-mode-warn');
  const realCard = document.getElementById('kis-real-card');
  const mockCard = document.getElementById('kis-mock-card');

  if (btnMock) btnMock.classList.toggle('active', isMock);
  if (btnReal) btnReal.classList.toggle('active', !isMock);
  if (warn)    warn.style.display = isMock ? 'none' : 'block';

  // 현재 활성 모드 카드 강조
  if (realCard) realCard.classList.toggle('kis-active-card', !isMock);
  if (mockCard) mockCard.classList.toggle('kis-active-card',  isMock);
}

// ── 종목명 캐시 & 조회 ───────────────────────────────────────────────

const _nameCache = {};   // {code: "삼성전자"}

function fetchTickerNames(tickers) {
  // 캐시에 없는 것만 서버에 요청
  const missing = tickers.filter(t => !_nameCache[t.code]);
  if (missing.length === 0) { renderTickers(); return; }

  fetch('/api/ticker-names', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ tickers: missing }),
  })
    .then(r => r.json())
    .then(d => {
      Object.assign(_nameCache, d);
      renderTickers();
    })
    .catch(() => renderTickers());
}

// ── 종목 관리 ─────────────────────────────────────────────────────────

let _sortable = null;

function renderTickers() {
  const container = document.getElementById('ticker-list');
  const countEl   = document.getElementById('ticker-count');
  if (!container) return;
  if (countEl) countEl.textContent = _tickers.length;

  container.innerHTML = _tickers.map((t, i) => {
    const name = _nameCache[t.code] || '';
    const qtyLabel      = t.qty      > 0 ? `${t.qty}주` : '글로벌';
    const intervalLabel = t.interval > 0 ? `${t.interval}초` : '글로벌';
    return `
    <div class="ticker-card" data-idx="${i}">
      <div class="ticker-card-drag">☰</div>
      <div class="ticker-card-body">
        <div class="ticker-card-top">
          <strong class="ticker-card-code">${t.code}</strong>
          ${name && name !== t.code ? `<span class="ticker-card-name">${name}</span>` : ''}
          <span class="exch-badge">${t.exchange}</span>
        </div>
        <div class="ticker-card-meta">
          <span class="ticker-meta-item">📦 수량: <b>${qtyLabel}</b></span>
          <span class="ticker-meta-item">⏱ 주기: <b>${intervalLabel}</b></span>
        </div>
      </div>
      <div class="ticker-card-actions">
        <button class="btn-ticker-edit"  onclick="editTicker(${i})" title="수정">✏️</button>
        <button class="btn-ticker-remove" onclick="removeTicker(${i})" title="삭제">×</button>
      </div>
    </div>`;
  }).join('') || '<div class="ticker-empty">등록된 종목이 없습니다.</div>';

  // SortableJS 초기화 (중복 방지)
  if (_sortable) { _sortable.destroy(); _sortable = null; }
  if (window.Sortable && container.querySelector('.ticker-card')) {
    _sortable = Sortable.create(container, {
      handle:    '.ticker-card-drag',
      animation: 150,
      ghostClass: 'ticker-card-ghost',
      onEnd(evt) {
        const moved = _tickers.splice(evt.oldIndex, 1)[0];
        _tickers.splice(evt.newIndex, 0, moved);
        renderTickers();
      },
    });
  }
}

function addTicker() {
  const code     = document.getElementById('new-ticker-code').value.trim().toUpperCase();
  const exchange = document.getElementById('new-ticker-exchange').value;
  const qty      = parseInt(document.getElementById('new-ticker-qty').value) || 0;
  const interval = parseInt(document.getElementById('new-ticker-interval').value) || 0;

  if (!code) { showToast('종목 코드를 입력하세요.', 'error'); return; }
  if (_tickers.find(t => t.code === code)) { showToast('이미 추가된 종목입니다.', 'error'); return; }

  _tickers.push({ code, exchange, qty, interval });
  document.getElementById('new-ticker-code').value = '';
  document.getElementById('new-ticker-qty').value  = '0';
  document.getElementById('new-ticker-interval').value = '0';
  fetchTickerNames(_tickers);
}

function editTicker(idx) {
  const t = _tickers[idx];
  if (!t) return;
  // 폼에 값 채우고 기존 항목 제거 (재추가 방식)
  document.getElementById('new-ticker-code').value     = t.code;
  document.getElementById('new-ticker-exchange').value = t.exchange;
  document.getElementById('new-ticker-qty').value      = t.qty;
  document.getElementById('new-ticker-interval').value = t.interval;
  _tickers.splice(idx, 1);
  renderTickers();
  document.getElementById('new-ticker-code').focus();
}

function removeTicker(idx) {
  _tickers.splice(idx, 1);
  renderTickers();
}

// ── 보유종목 ──────────────────────────────────────────────────────────

function loadHoldings() {
  fetch('/api/holdings')
    .then(r => r.json())
    .then(data => {
      const tbody = document.getElementById('holdings-tbody');
      if (!tbody) return;
      if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty">보유종목 없음</td></tr>';
        return;
      }
      tbody.innerHTML = data.map(h => {
        const pnlClass = h.pnl_pct > 0 ? 'sig-buy' : h.pnl_pct < 0 ? 'sig-sell' : '';
        const pnlSign  = h.pnl_pct > 0 ? '+' : '';
        return `<tr>
          <td><strong>${h.ticker}</strong></td>
          <td>${h.name || '-'}</td>
          <td><span class="exch-badge">${h.exchange}</span></td>
          <td>${h.qty.toLocaleString()}</td>
          <td>${Number(h.avg_price).toLocaleString()}</td>
          <td>${Number(h.last_price).toLocaleString()}</td>
          <td><span class="${pnlClass}">${pnlSign}${h.pnl_pct}%</span></td>
          <td style="font-size:11px;color:var(--text-sub)">${h.updated_at}</td>
        </tr>`;
      }).join('');
    })
    .catch(() => {});
}

// ── 거래내역 ──────────────────────────────────────────────────────────

function loadTrades() {
  fetch('/api/trades')
    .then(r => r.json())
    .then(data => {
      const tbody = document.getElementById('trades-tbody');
      const countEl = document.getElementById('trades-count');
      if (!tbody) return;
      if (countEl) countEl.textContent = `총 ${data.length}건`;
      if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty">거래내역 없음</td></tr>';
        return;
      }
      tbody.innerHTML = data.map(tr => {
        const sideClass = tr.side === 'BUY' ? 'sig-buy' : 'sig-sell';
        const sideLabel = tr.side === 'BUY' ? '매수' : '매도';
        return `<tr>
          <td style="color:var(--text-sub)">${tr.id}</td>
          <td style="font-size:12px">${tr.decided_at}</td>
          <td><strong>${tr.ticker}</strong></td>
          <td>${tr.name || '-'}</td>
          <td><span class="exch-badge">${tr.exchange}</span></td>
          <td><span class="${sideClass}" style="font-weight:700">${sideLabel}</span></td>
          <td>${Number(tr.price).toLocaleString()}</td>
          <td>${tr.qty}</td>
          <td>${Number(tr.amount).toLocaleString()}</td>
          <td><span class="mode-badge ${tr.mode === '실전투자' ? 'mode-real' : 'mode-mock'}">${tr.mode}</span></td>
        </tr>`;
      }).join('');
    })
    .catch(() => {});
}

function clearTrades() {
  if (!confirm('거래내역을 전체 삭제할까요?')) return;
  fetch('/api/trades/clear', { method: 'POST' })
    .then(r => r.json())
    .then(d => { if (d.ok) { showToast('거래내역이 삭제되었습니다.'); loadTrades(); } })
    .catch(() => showToast('삭제 실패', 'error'));
}

// ── 섹션별 저장 ───────────────────────────────────────────────────────

function saveSection(section) {
  const payload = {};

  if (section === 'kis') {
    // 거래 모드
    payload.KIS_IS_MOCK = _kisIsMock ? 'true' : 'false';

    // 실전투자 키 (비어있지 않은 경우만)
    const realKey  = document.getElementById('KIS_REAL_APP_KEY').value.trim();
    const realSec  = document.getElementById('KIS_REAL_APP_SECRET').value.trim();
    const realAcc  = document.getElementById('KIS_REAL_ACCOUNT_NUMBER').value.trim();
    if (realKey) payload.KIS_REAL_APP_KEY         = realKey;
    if (realSec) payload.KIS_REAL_APP_SECRET      = realSec;
    if (realAcc) payload.KIS_REAL_ACCOUNT_NUMBER  = realAcc;

    // 모의투자 키 (비어있지 않은 경우만)
    const mockKey  = document.getElementById('KIS_MOCK_APP_KEY').value.trim();
    const mockSec  = document.getElementById('KIS_MOCK_APP_SECRET').value.trim();
    const mockAcc  = document.getElementById('KIS_MOCK_ACCOUNT_NUMBER').value.trim();
    if (mockKey) payload.KIS_MOCK_APP_KEY         = mockKey;
    if (mockSec) payload.KIS_MOCK_APP_SECRET      = mockSec;
    if (mockAcc) payload.KIS_MOCK_ACCOUNT_NUMBER  = mockAcc;
  }

  if (section === 'llm') {
    payload.LLM_PROVIDER = document.querySelector('input[name=LLM_PROVIDER]:checked')?.value || 'openai';
    const groqKey  = document.getElementById('GROQ_API_KEY').value.trim();
    const oaiKey   = document.getElementById('OPENAI_API_KEY').value.trim();
    if (groqKey) payload.GROQ_API_KEY = groqKey;
    if (oaiKey)  payload.OPENAI_API_KEY = oaiKey;
    payload.GROQ_MODEL    = document.getElementById('GROQ_MODEL').value;
    payload.OPENAI_MODEL  = document.getElementById('OPENAI_MODEL').value;
    payload.OLLAMA_BASE_URL = document.getElementById('OLLAMA_BASE_URL').value.trim();
    payload.OLLAMA_MODEL    = document.getElementById('OLLAMA_MODEL').value.trim();
  }

  if (section === 'tickers') {
    if (_tickers.length === 0) { showToast('최소 1개 이상의 종목을 추가하세요.', 'error'); return; }
    // 형식: 코드:거래소:수량:주기
    payload.TARGET_TICKERS = _tickers
      .map(t => `${t.code}:${t.exchange}:${t.qty || 0}:${t.interval || 0}`)
      .join(',');
    payload.ORDER_QUANTITY            = document.getElementById('ORDER_QUANTITY').value;
    payload.STRATEGY_INTERVAL_SECONDS = document.getElementById('STRATEGY_INTERVAL_SECONDS').value;
  }

  if (section === 'strategy') {
    ['RSI_PERIOD','RSI_OVERSOLD','RSI_OVERBOUGHT',
     'BB_PERIOD','BB_STD','LOOKBACK_DAYS',
     'SENTIMENT_THRESHOLD','MAX_HEADLINES',
     'TRUMP_BULL_THRESHOLD','TRUMP_BEAR_THRESHOLD','TRUMP_POLL_INTERVAL'
    ].forEach(k => {
      const v = document.getElementById(k)?.value.trim();
      if (v) payload[k] = v;
    });
  }

  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { showToast('✅ ' + d.message); loadSettings(); }
      else       showToast('저장 실패: ' + d.message, 'error');
    })
    .catch(() => showToast('서버 오류', 'error'));
}

// ── 로그 SSE 스트리밍 ─────────────────────────────────────────────────

const logContainer = document.getElementById('log-container');

function levelOf(line) {
  if (line.includes(' ERROR '))   return 'ERROR';
  if (line.includes(' WARNING ')) return 'WARNING';
  if (line.includes(' DEBUG '))   return 'DEBUG';
  return 'INFO';
}

function appendLog(line) {
  const filterError = document.getElementById('log-filter-error').checked;
  const level = levelOf(line);
  if (filterError && level !== 'ERROR') return;

  const div = document.createElement('div');
  div.className = `log-line ${level}`;
  div.textContent = line;
  logContainer.appendChild(div);

  // 최대 500줄 유지
  while (logContainer.children.length > 500) {
    logContainer.removeChild(logContainer.firstChild);
  }

  if (document.getElementById('log-autoscroll').checked) {
    logContainer.scrollTop = logContainer.scrollHeight;
  }
}

function clearLog() {
  logContainer.innerHTML = '';
}

// SSE 연결
const evtSource = new EventSource('/api/logs/stream');
evtSource.onmessage = e => {
  try {
    const line = JSON.parse(e.data);
    appendLog(line);
  } catch {}
};
evtSource.onerror = () => {
  appendLog('[시스템] 로그 스트림 연결 끊김 – 재연결 중...');
};
