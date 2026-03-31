// ── 탭 전환 ──────────────────────────────────────────────────────────

document.querySelectorAll('.sidebar-menu li').forEach(li => {
  li.addEventListener('click', () => {
    document.querySelectorAll('.sidebar-menu li').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    li.classList.add('active');
    document.getElementById(`tab-${li.dataset.tab}`).classList.add('active');
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

// ── 설정 로드 ─────────────────────────────────────────────────────────

let _currentSettings = {};
let _tickers = [];   // [{code, exchange}]

function loadSettings() {
  fetch('/api/settings')
    .then(r => r.json())
    .then(d => {
      _currentSettings = d;

      // KIS
      setVal('KIS_IS_MOCK', d.KIS_IS_MOCK === 'true');
      updateMockLabel(d.KIS_IS_MOCK === 'true');

      // LLM 제공자
      const provider = d.LLM_PROVIDER || 'openai';
      document.querySelector(`input[name=LLM_PROVIDER][value="${provider}"]`).checked = true;
      switchLlmSection(provider);
      setVal('GROQ_MODEL',    d.GROQ_MODEL);
      setVal('OPENAI_MODEL',  d.OPENAI_MODEL);
      setVal('OLLAMA_BASE_URL', d.OLLAMA_BASE_URL);
      setVal('OLLAMA_MODEL',  d.OLLAMA_MODEL);

      // 종목
      _tickers = (d.TARGET_TICKERS || '').split(',')
        .filter(s => s.trim())
        .map(s => {
          const [code, exchange] = s.split(':');
          return { code: code?.trim(), exchange: (exchange || 'KRX').trim() };
        });
      renderTickers();
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

// ── 모의/실전 토글 ────────────────────────────────────────────────────

document.getElementById('KIS_IS_MOCK').addEventListener('change', function () {
  updateMockLabel(this.checked);
});

function updateMockLabel(isMock) {
  document.getElementById('mock-label').textContent = isMock ? '모의투자' : '⚠️ 실전투자';
}

// ── 종목 관리 ─────────────────────────────────────────────────────────

function renderTickers() {
  const container = document.getElementById('ticker-list');
  container.innerHTML = _tickers.map((t, i) => `
    <div class="ticker-tag">
      <strong>${t.code}</strong>
      <span style="color:var(--text-sub);font-size:11px;">${t.exchange}</span>
      <span class="remove" onclick="removeTicker(${i})">×</span>
    </div>`).join('');
}

function addTicker() {
  const code = document.getElementById('new-ticker-code').value.trim().toUpperCase();
  const exchange = document.getElementById('new-ticker-exchange').value;
  if (!code) { showToast('종목 코드를 입력하세요.', 'error'); return; }
  if (_tickers.find(t => t.code === code)) { showToast('이미 추가된 종목입니다.', 'error'); return; }
  _tickers.push({ code, exchange });
  document.getElementById('new-ticker-code').value = '';
  renderTickers();
}

function removeTicker(idx) {
  _tickers.splice(idx, 1);
  renderTickers();
}

// ── 섹션별 저장 ───────────────────────────────────────────────────────

function saveSection(section) {
  const payload = {};

  if (section === 'kis') {
    const appKey = document.getElementById('KIS_APP_KEY').value.trim();
    const appSec = document.getElementById('KIS_APP_SECRET').value.trim();
    const accNum = document.getElementById('KIS_ACCOUNT_NUMBER').value.trim();
    if (appKey) payload.KIS_APP_KEY = appKey;
    if (appSec) payload.KIS_APP_SECRET = appSec;
    if (accNum) payload.KIS_ACCOUNT_NUMBER = accNum;
    payload.KIS_IS_MOCK = document.getElementById('KIS_IS_MOCK').checked ? 'true' : 'false';
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
    payload.TARGET_TICKERS = _tickers.map(t => `${t.code}:${t.exchange}`).join(',');
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
