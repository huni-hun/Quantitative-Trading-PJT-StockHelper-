// ── 탭 전환 ──────────────────────────────────────────────────────────

document.querySelectorAll('.sidebar-menu li').forEach(li => {
  li.addEventListener('click', () => {
    document.querySelectorAll('.sidebar-menu li').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    li.classList.add('active');
    document.getElementById(`tab-${li.dataset.tab}`).classList.add('active');
    // 탭별 데이터 자동 로드
    const tab = li.dataset.tab;
    if (tab === 'holdings')  loadHoldings();
    if (tab === 'trades')    loadTrades();
    if (tab === 'analytics') loadAnalytics();
    if (tab === 'backtest')  btInitDates();
    if (tab === 'notify')    loadNotifySettings();
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

      // ── 리스크 배너 업데이트 ────────────────────────────────────
      updateRiskBanner(d);

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
        tbody.innerHTML = '<tr><td colspan="8" class="empty">아직 데이터 없음 — 봇 실행 후 갱신됩니다.</td></tr>';
      } else {
        tbody.innerHTML = Object.entries(signals).map(([ticker, v]) => {
          // 복합점수 색상: ≥0.45 초록, ≤-0.45 빨강, 그외 회색
          const cs = v.composite_score ?? 0;
          const csColor = cs >= 0.45 ? '#4caf50' : cs <= -0.45 ? '#f44336' : 'var(--text-sub)';
          const csText = cs >= 0.45 ? `▲ ${cs}` : cs <= -0.45 ? `▼ ${cs}` : cs;
          // 세부 점수 툴팁
          const bdMap = v.tech_breakdown || {};
          const bdStr = Object.entries(bdMap).map(([k,sv]) => `${k}:${sv>0?'+':''}${sv}`).join(', ');
          const techTitle = `기술점수: ${v.tech_score ?? '-'}${bdStr ? '\n' + bdStr : ''}`;
          return `
          <tr>
            <td><strong>${ticker}</strong></td>
            <td><span class="${sigClass(v.sentiment)}" title="감성점수: ${v.sentiment_score ?? '-'}">${v.sentiment || '--'}</span></td>
            <td><span class="${sigClass(v.technical)}" title="${techTitle}">${v.technical || '--'} <small style="opacity:.6">${v.tech_score != null ? v.tech_score : ''}</small></span></td>
            <td><span class="${sigClass(v.momentum)}" title="모멘텀점수: ${v.momentum_score ?? '-'}">${v.momentum || '--'}</span></td>
            <td style="color:${csColor};font-weight:600">${csText}</td>
            <td><span class="${sigClass(v.decision)}">${v.decision || '--'}</span></td>
            <td>${v.price || '--'}</td>
            <td>${v.updated_at || '--'}</td>
          </tr>`;
        }).join('');
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

// ══════════════════════════════════════════════════════════════════════
// 리스크 관리 (킬 스위치 / 일일 손실 한도)
// ══════════════════════════════════════════════════════════════════════

/* 리스크 배너 상태 반영 */
function updateRiskBanner(d) {
  const botRunning  = !!d.bot_running;
  const killActive  = !!d.kill_switch;
  const killReason  = d.kill_reason  || '';
  const pnlPct      = d.daily_pnl_pct != null ? Number(d.daily_pnl_pct) : null;
  const limitPct    = d.daily_loss_limit != null ? Number(d.daily_loss_limit) : 0;

  // ── 상태 표시기 ──────────────────────────────────────────────────
  // 3가지 상태 구분:
  //   kill_switch=true           → 🛑 킬스위치 발동 (최우선)
  //   bot_running=false (정상종료) → ⏸ 봇 중지됨
  //   bot_running=true            → ● 봇 정상 작동
  const indicator = document.getElementById('risk-kill-indicator');
  const label     = document.getElementById('risk-kill-label');
  const reasonEl  = document.getElementById('risk-kill-reason');

  if (indicator && label) {
    if (killActive) {
      indicator.className = 'risk-kill-indicator risk-active';
      label.textContent   = '🛑 킬스위치 발동';
      if (reasonEl) {
        reasonEl.textContent = killReason || '';
        reasonEl.style.display = killReason ? '' : 'none';
      }
    } else if (!botRunning) {
      indicator.className = 'risk-kill-indicator risk-stopped';
      label.textContent   = '⏸ 봇 중지됨';
      if (reasonEl) reasonEl.style.display = 'none';
    } else {
      indicator.className = 'risk-kill-indicator risk-safe';
      label.textContent   = '● 봇 정상 작동';
      if (reasonEl) reasonEl.style.display = 'none';
    }
  }

  // ── 버튼 토글 ────────────────────────────────────────────────────
  const killBtn   = document.getElementById('risk-kill-btn');
  const panicBtn  = document.getElementById('risk-panic-btn');
  const resumeBtn = document.getElementById('risk-resume-btn');
  if (killBtn)   killBtn.style.display   = killActive ? 'none' : '';
  if (panicBtn)  panicBtn.style.display  = killActive ? 'none' : '';
  if (resumeBtn) resumeBtn.style.display = killActive ? ''     : 'none';

  // ── 봇 시작/중지 버튼 토글 ───────────────────────────────────────
  const startBtn = document.getElementById('bot-start-btn');
  const stopBtn  = document.getElementById('bot-stop-btn');
  if (startBtn && stopBtn) {
    if (botRunning) {
      startBtn.style.display = 'none';
      stopBtn.style.display  = '';
    } else {
      startBtn.style.display = '';
      stopBtn.style.display  = 'none';
    }
  }

  // ── 오늘 손익 ────────────────────────────────────────────────────
  const pnlEl = document.getElementById('risk-daily-pnl');
  if (pnlEl) {
    if (pnlPct != null) {
      const sign = pnlPct > 0 ? '+' : '';
      pnlEl.textContent = `${sign}${pnlPct.toFixed(2)}%`;
      pnlEl.className   = `risk-pnl-value ${pnlPct >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
    } else {
      pnlEl.textContent = '--';
      pnlEl.className   = 'risk-pnl-value';
    }
  }

  // ── 한도 표시 ────────────────────────────────────────────────────
  const limitEl = document.getElementById('risk-limit-value');
  if (limitEl) {
    limitEl.textContent = limitPct === 0 ? '비활성화' : `${limitPct}%`;
    limitEl.className   = `risk-pnl-limit ${limitPct !== 0 ? 'limit-set' : ''}`;
  }

  // ── 배너 경고 스타일 (킬스위치 발동 시만) ────────────────────────
  const banner = document.getElementById('risk-banner');
  if (banner) {
    banner.classList.toggle('risk-banner-alert', killActive);
  }
}

/* 봇 정지 (킬 스위치만 발동, 청산 없음) */
function triggerKillSwitch() {
  if (!confirm('봇을 즉시 정지합니다.\n보유 종목은 그대로 유지됩니다. 계속하시겠습니까?')) return;
  fetch('/api/risk/kill-switch', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ activate: true, panic_sell: false, reason: '수동 킬 스위치' }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast('⏹ 봇이 정지되었습니다.', 'error');
      else       showToast(d.message, 'error');
    })
    .catch(() => showToast('서버 오류', 'error'));
}

/* 패닉 셀 — 봇 정지 + 전 종목 시장가 청산 */
function triggerPanicSell() {
  if (!confirm(
    '🚨 패닉 셀 — 봇을 정지하고 보유 중인 모든 종목을 시장가로 즉시 매도합니다.\n\n이 작업은 되돌릴 수 없습니다. 정말 실행하시겠습니까?'
  )) return;

  const btn = document.getElementById('risk-panic-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 청산 중...'; }

  fetch('/api/risk/kill-switch', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ activate: true, panic_sell: true, reason: '패닉 셀 (수동)' }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        const sold = (d.sell_results || []).filter(r => r.ok).length;
        const fail = (d.sell_results || []).filter(r => !r.ok).length;
        showToast(
          `🚨 패닉 셀 완료: ${sold}종목 청산${fail ? ` (${fail}건 실패)` : ''}`,
          fail ? 'error' : 'success'
        );
      } else {
        showToast(d.message, 'error');
      }
    })
    .catch(() => showToast('서버 오류', 'error'))
    .finally(() => {
      if (btn) { btn.disabled = false; btn.textContent = '🚨 패닉 셀'; }
    });
}

/* 봇 재개 (킬 스위치 해제) */
function resumeBot() {
  if (!confirm('킬 스위치를 해제하고 봇을 재개합니다. 계속하시겠습니까?')) return;
  fetch('/api/risk/kill-switch', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ activate: false }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast('✅ 봇이 재개됩니다.');
      else       showToast(d.message, 'error');
    })
    .catch(() => showToast('서버 오류', 'error'));
}

/* 일일 손실 한도 설정 */
function setDailyLossLimit() {
  const val = parseFloat(document.getElementById('risk-limit-input')?.value);
  if (isNaN(val)) { showToast('올바른 숫자를 입력하세요.', 'error'); return; }
  const limitPct = val > 0 ? -val : val;   // 항상 음수
  fetch('/api/risk/daily-loss-limit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit_pct: limitPct }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast(`✅ 일일 손실 한도: ${limitPct === 0 ? '비활성화' : limitPct + '%'}`);
      else       showToast(d.message, 'error');
    })
    .catch(() => showToast('서버 오류', 'error'));
}

/* 오늘 기준 자산 재설정 */
function resetDailyEquity() {
  if (!confirm('오늘의 기준 자산을 현재 보유 평가금액으로 재설정합니다.\n이전 손익 기록이 초기화됩니다. 계속하시겠습니까?')) return;
  const val = parseFloat(document.getElementById('risk-limit-input')?.value) || -5;
  fetch('/api/risk/daily-loss-limit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit_pct: val > 0 ? -val : val, reset_daily_equity: true }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast('🔄 기준 자산이 현재 평가금액으로 재설정되었습니다.');
      else       showToast(d.message, 'error');
    })
    .catch(() => showToast('서버 오류', 'error'));
}

// ══════════════════════════════════════════════════════════════════════
// 봇 시작 / 중지 (서브프로세스 제어)
// ══════════════════════════════════════════════════════════════════════

/* 봇 시작 */
function startBot() {
  const btn = document.getElementById('bot-start-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 시작 중...'; }

  fetch('/api/bot/start', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        showToast(`✅ ${d.message}`);
        // 상태 즉시 반영
        refreshDashboard();
      } else {
        showToast(d.message || '봇 시작 실패', 'error');
      }
    })
    .catch(() => showToast('서버 오류: 봇 시작 실패', 'error'))
    .finally(() => {
      if (btn) { btn.disabled = false; btn.textContent = '▶ 봇 시작'; }
    });
}

/* 봇 중지 */
function stopBot() {
  if (!confirm('거래봇을 종료합니다.\n보유 종목은 그대로 유지됩니다. 계속하시겠습니까?')) return;

  const btn = document.getElementById('bot-stop-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 종료 중...'; }

  fetch('/api/bot/stop', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force: false }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        showToast('⏹ ' + d.message);
        refreshDashboard();
      } else {
        showToast(d.message || '봇 종료 실패', 'error');
      }
    })
    .catch(() => showToast('서버 오류: 봇 종료 실패', 'error'))
    .finally(() => {
      if (btn) { btn.disabled = false; btn.textContent = '⏹ 봇 종료'; }
    });
}


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
const _fundCache = {};   // {code: {per, pbr, psr, roe}}

function fetchTickerNames(tickers) {
  // 캐시에 없는 것만 서버에 요청
  const missing = tickers.filter(t => !_nameCache[t.code]);
  if (missing.length === 0) { renderTickers(); fetchFundamentals(tickers); return; }

  fetch('/api/ticker-names', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ tickers: missing }),
  })
    .then(r => r.json())
    .then(d => {
      Object.assign(_nameCache, d);
      renderTickers();
      fetchFundamentals(tickers);
    })
    .catch(() => { renderTickers(); fetchFundamentals(tickers); });
}

function fetchFundamentals(tickers) {
  fetch('/api/fundamentals', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ tickers }),
  })
    .then(r => r.json())
    .then(d => {
      Object.assign(_fundCache, d);
      renderTickers();
    })
    .catch(() => {});
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
    const fund = _fundCache[t.code] || {};
    const per  = fund.per  || '…';
    const pbr  = fund.pbr  || '…';
    const psr  = fund.psr  || '…';
    const roe  = fund.roe  || '…';
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
        <div class="ticker-card-fund">
          <span class="fund-item">PER <b>${per}</b></span>
          <span class="fund-item">PBR <b>${pbr}</b></span>
          <span class="fund-item">PSR <b>${psr}</b></span>
          <span class="fund-item">ROE <b>${roe}${roe !== '…' && roe !== '-' ? '%' : ''}</b></span>
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
        tbody.innerHTML = '<tr><td colspan="12" class="empty">보유종목 없음</td></tr>';
        return;
      }
      tbody.innerHTML = data.map(h => {
        const pnlClass = h.pnl_pct > 0 ? 'sig-buy' : h.pnl_pct < 0 ? 'sig-sell' : '';
        const pnlSign  = h.pnl_pct > 0 ? '+' : '';
        const roe = h.roe && h.roe !== '-' ? `${h.roe}%` : (h.roe || '-');
        return `<tr>
          <td><strong>${h.ticker}</strong></td>
          <td>${h.name || '-'}</td>
          <td><span class="exch-badge">${h.exchange}</span></td>
          <td>${h.qty.toLocaleString()}</td>
          <td>${Number(h.avg_price).toLocaleString()}</td>
          <td>${Number(h.last_price).toLocaleString()}</td>
          <td><span class="${pnlClass}">${pnlSign}${h.pnl_pct}%</span></td>
          <td class="fund-cell">${h.per || '-'}</td>
          <td class="fund-cell">${h.pbr || '-'}</td>
          <td class="fund-cell">${h.psr || '-'}</td>
          <td class="fund-cell">${roe}</td>
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

// ── 성과 분석 ──────────────────────────────────────────────────────────

let _equityChart = null;

function loadAnalytics() {
  fetch('/api/analytics')
    .then(r => r.json())
    .then(data => {
      renderKPI(data.summary);
      renderEquityCurve(data.equity_curve);
      renderHeatmap(data.heatmap);
      renderTickerStats(data.ticker_stats);

      // 기간 레이블
      const rangeEl = document.getElementById('analytics-range');
      if (data.equity_curve.length >= 2) {
        rangeEl.textContent =
          `${data.equity_curve[0].date} ~ ${data.equity_curve[data.equity_curve.length-1].date}`;
      } else {
        rangeEl.textContent = '';
      }
    })
    .catch(() => showToast('성과 데이터 로드 실패', 'error'));
}

/* ── KPI 카드 ── */
function renderKPI(s) {
  const pnlColor = s.total_pnl >= 0 ? 'var(--green)' : 'var(--red)';
  const mddColor = s.mdd > 20 ? 'var(--red)' : s.mdd > 10 ? '#f5a623' : 'var(--text)';

  setText('kpi-total',   s.total_trades);
  setText('kpi-winrate', s.total_trades ? `${s.win_rate}%` : '--',
          s.win_rate >= 50 ? 'var(--green)' : 'var(--red)');
  setText('kpi-wl',      `${s.wins}승 ${s.losses}패`);
  setText('kpi-pf',      s.total_trades ? s.profit_factor : '--',
          s.profit_factor >= 1 ? 'var(--green)' : 'var(--red)');
  setText('kpi-avgwl',   s.total_trades
    ? `평균 +${fmt(s.avg_win)} / -${fmt(Math.abs(s.avg_loss))}` : '--');
  setText('kpi-mdd',     s.total_trades ? `${s.mdd}%` : '--', mddColor);
  setText('kpi-pnl',     s.total_trades ? fmt(s.total_pnl) : '--', pnlColor);
  setText('kpi-bestworse', s.total_trades
    ? `최고 +${fmt(s.best_trade)} / 최저 ${fmt(s.worst_trade)}` : '--');
}

function setText(id, val, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = val;
  if (color) el.style.color = color;
}

function fmt(n) {
  if (n == null) return '--';
  return Number(n).toLocaleString('ko-KR', { maximumFractionDigits: 0 });
}

/* ── 에쿼티 커브 ── */
function renderEquityCurve(curve) {
  const canvas  = document.getElementById('equity-chart');
  const emptyEl = document.getElementById('equity-empty');
  if (!canvas) return;

  if (!curve || curve.length === 0) {
    canvas.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'block';
    return;
  }
  canvas.style.display = 'block';
  if (emptyEl) emptyEl.style.display = 'none';

  const labels  = curve.map(p => p.date);
  const equities = curve.map(p => p.equity);

  // 색상: 0 이상 구간 초록, 이하 빨강 그라데이션
  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, canvas.offsetHeight || 300);
  const maxEq = Math.max(...equities);
  const minEq = Math.min(...equities);
  const zeroRatio = maxEq === minEq ? 0.5
    : Math.max(0, Math.min(1, 1 - (0 - minEq) / (maxEq - minEq)));

  gradient.addColorStop(0,          'rgba(0,210,110,0.35)');
  gradient.addColorStop(zeroRatio,  'rgba(0,210,110,0.05)');
  gradient.addColorStop(zeroRatio,  'rgba(255,80,80,0.05)');
  gradient.addColorStop(1,          'rgba(255,80,80,0.35)');

  if (_equityChart) { _equityChart.destroy(); _equityChart = null; }

  _equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: '누적 손익',
        data: equities,
        borderColor: equities[equities.length-1] >= 0 ? '#00d26e' : '#ff5050',
        borderWidth: 2,
        pointRadius: curve.length > 60 ? 0 : 3,
        pointHoverRadius: 5,
        fill: true,
        backgroundColor: gradient,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` 누적: ${fmt(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8899aa', maxTicksLimit: 12, maxRotation: 0 },
          grid:  { color: 'rgba(255,255,255,0.05)' },
        },
        y: {
          ticks: {
            color: '#8899aa',
            callback: v => fmt(v),
          },
          grid: { color: 'rgba(255,255,255,0.05)' },
          // 0 기준선 강조
          afterBuildTicks(axis) {
            axis.ticks.push({ value: 0 });
          },
        },
      },
    },
  });
}

/* ── 월별 히트맵 ── */
function renderHeatmap(heatmap) {
  const container = document.getElementById('heatmap-container');
  if (!container) return;

  const months = Object.keys(heatmap).sort();
  if (months.length === 0) {
    container.innerHTML = '<p class="analytics-empty">실현 손익 데이터가 없습니다.</p>';
    return;
  }

  // 전체 손익 범위 파악 (색상 정규화용)
  let allPnl = [];
  months.forEach(ym => Object.values(heatmap[ym]).forEach(v => allPnl.push(v)));
  const maxAbs = Math.max(...allPnl.map(Math.abs), 1);

  const DOW = ['일', '월', '화', '수', '목', '금', '토'];

  container.innerHTML = months.map(ym => {
    const [year, month] = ym.split('-').map(Number);
    const daysInMonth = new Date(year, month, 0).getDate();
    const firstDow = new Date(year, month - 1, 1).getDay();  // 0=일

    let cells = '';
    // 첫 주 빈칸
    for (let i = 0; i < firstDow; i++) {
      cells += `<div class="hm-cell hm-empty"></div>`;
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const dd   = String(d).padStart(2, '0');
      const pnl  = heatmap[ym][dd];
      const cls  = pnl == null ? 'hm-none'
                 : pnl === 0  ? 'hm-zero'
                 : pnl > 0
                   ? (pnl / maxAbs > 0.5 ? 'hm-win-deep' : 'hm-win')
                   : (Math.abs(pnl) / maxAbs > 0.5 ? 'hm-loss-deep' : 'hm-loss');
      const tip  = pnl != null ? `${ym}-${dd}: ${fmt(pnl)}` : `${ym}-${dd}`;
      cells += `<div class="hm-cell ${cls}" title="${tip}">
                  <span class="hm-day">${d}</span>
                </div>`;
    }

    const dowHeader = DOW.map(d => `<div class="hm-dow">${d}</div>`).join('');
    return `
      <div class="hm-month-block">
        <div class="hm-month-label">${year}년 ${month}월</div>
        <div class="hm-grid">
          ${dowHeader}
          ${cells}
        </div>
      </div>`;
  }).join('');
}

/* ── 종목별 통계 ── */
function renderTickerStats(stats) {
  const tbody = document.getElementById('ticker-stats-tbody');
  if (!tbody) return;
  if (!stats || stats.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">데이터 없음</td></tr>';
    return;
  }
  tbody.innerHTML = stats.map(s => {
    const pnlColor = s.total_pnl >= 0 ? 'sig-buy' : 'sig-sell';
    const wrColor  = s.win_rate >= 50  ? 'sig-buy' : 'sig-sell';
    return `<tr>
      <td><strong>${s.ticker}</strong></td>
      <td>${s.name || '-'}</td>
      <td>${s.trades}</td>
      <td>${s.wins}승 ${s.trades - s.wins}패</td>
      <td><span class="${wrColor}">${s.win_rate}%</span></td>
      <td><span class="${pnlColor}">${fmt(s.total_pnl)}</span></td>
    </tr>`;
  }).join('');
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

// 매매 관련 키워드 판별
const _TRADE_KEYWORDS = [
  '매수', '매도', 'BUY', 'SELL', '체결', '주문', 'ORDER',
  '📈', '📉', '[매수]', '[매도]', 'market_buy', 'market_sell',
];
function isTradeLog(line) {
  const u = line.toUpperCase();
  return _TRADE_KEYWORDS.some(k => u.includes(k.toUpperCase()));
}

function levelOf(line) {
  if (line.includes(' ERROR '))   return 'ERROR';
  if (line.includes(' WARNING ')) return 'WARNING';
  if (line.includes(' DEBUG '))   return 'DEBUG';
  return 'INFO';
}

function appendLog(line) {
  const filterError = document.getElementById('log-filter-error')?.checked;
  const filterTrade = document.getElementById('log-filter-trade')?.checked;
  const level = levelOf(line);

  // 로그 탭 필터 적용
  let show = true;
  if (filterError && level !== 'ERROR') show = false;
  if (filterTrade && !isTradeLog(line))  show = false;

  if (show) {
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

  // 대시보드 매매 로그창에 매매 관련 로그 추가
  if (isTradeLog(line)) {
    appendTradeLog(line, level);
  }
}

function clearLog() {
  logContainer.innerHTML = '';
}

// ── 대시보드 매매 로그창 ──────────────────────────────────────────────

const _tradeLogMax = 200;

function appendTradeLog(line, level) {
  const container = document.getElementById('trade-log-container');
  if (!container) return;

  const div = document.createElement('div');
  div.className = `log-line trade-log-line ${level || levelOf(line)}`;
  div.textContent = line;
  container.appendChild(div);

  while (container.children.length > _tradeLogMax) {
    container.removeChild(container.firstChild);
  }
  // 항상 최신 로그가 아래
  container.scrollTop = container.scrollHeight;

  // 건수 배지 업데이트
  const countEl = document.getElementById('trade-log-count');
  if (countEl) countEl.textContent = `(${container.children.length}건)`;
}

function clearTradeLog() {
  const container = document.getElementById('trade-log-container');
  if (container) container.innerHTML = '';
  const countEl = document.getElementById('trade-log-count');
  if (countEl) countEl.textContent = '';
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

// ══════════════════════════════════════════════════════════════════════
// 백테스팅 & 시뮬레이션
// ══════════════════════════════════════════════════════════════════════

let _btEquityChart = null;
let _btRsiChart    = null;

/* 탭 진입 시 날짜 기본값 설정 (오늘 ~ 1개월 전) */
function btInitDates() {
  const endEl   = document.getElementById('bt-end');
  const startEl = document.getElementById('bt-start');
  if (!endEl || !startEl) return;
  const today  = new Date();
  const m1ago  = new Date(today);
  m1ago.setMonth(m1ago.getMonth() - 1);
  endEl.value   = today.toISOString().slice(0, 10);
  startEl.value = m1ago.toISOString().slice(0, 10);
}

/* 날짜 프리셋 버튼 (1M / 3M / 6M / 1Y / 3Y) */
function btSetDateRange(n, unit) {
  const endEl   = document.getElementById('bt-end');
  const startEl = document.getElementById('bt-start');
  if (!endEl || !startEl) return;
  const today = new Date();
  const from  = new Date(today);
  if (unit === 'M') from.setMonth(from.getMonth() - n);
  if (unit === 'Y') from.setFullYear(from.getFullYear() - n);
  endEl.value   = today.toISOString().slice(0, 10);
  startEl.value = from.toISOString().slice(0, 10);
  // 활성 버튼 표시
  document.querySelectorAll('.bt-date-presets button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
}

/* 현재 전략 설정 → 파라미터 필드에 자동 채우기 */
function btLoadCurrent(set) {
  const p = _currentSettings;
  const prefix = `bt-${set}-`;
  const map = {
    'rsi-period':    p.RSI_PERIOD    || '14',
    'rsi-oversold':  p.RSI_OVERSOLD  || '30',
    'rsi-overbought':p.RSI_OVERBOUGHT|| '70',
    'bb-period':     p.BB_PERIOD     || '20',
    'bb-std':        p.BB_STD        || '2.0',
  };
  Object.entries(map).forEach(([k, v]) => {
    const el = document.getElementById(prefix + k);
    if (el) el.value = v;
  });
  showToast(`세트 ${set.toUpperCase()}에 현재 설정 불러옴`);
}

/* 비교 세트 B 활성/비활성 토글 */
function btToggleB(on) {
  const fields = document.getElementById('bt-b-fields');
  if (fields) {
    fields.style.opacity       = on ? '1'    : '0.35';
    fields.style.pointerEvents = on ? 'auto' : 'none';
  }
}

/* 파라미터 객체 수집 헬퍼 */
function btCollectParams(prefix) {
  const g = id => parseFloat(document.getElementById(prefix + id)?.value) || 0;
  return {
    rsi_period:     g('rsi-period'),
    rsi_oversold:   g('rsi-oversold'),
    rsi_overbought: g('rsi-overbought'),
    bb_period:      g('bb-period'),
    bb_std:         g('bb-std'),
  };
}

/* ── 메인 실행 함수 ── */
function runBacktest() {
  const ticker   = document.getElementById('bt-ticker')?.value.trim().toUpperCase();
  const exchange = document.getElementById('bt-exchange')?.value || 'KRX';
  const start    = document.getElementById('bt-start')?.value;
  const end      = document.getElementById('bt-end')?.value;
  const cash     = parseFloat(document.getElementById('bt-cash')?.value) || 10_000_000;
  const qty      = parseInt(document.getElementById('bt-qty')?.value) || 1;
  const useB     = document.getElementById('bt-use-b')?.checked;

  if (!ticker) { showToast('종목코드를 입력하세요.', 'error'); return; }

  const paramsA = btCollectParams('bt-a-');
  const paramsB = useB ? btCollectParams('bt-b-') : null;

  // UI 상태
  const btn    = document.getElementById('bt-run-btn');
  const status = document.getElementById('bt-status');
  btn.disabled    = true;
  btn.textContent = '⏳ 실행 중...';
  status.textContent = 'KIS API에서 과거 데이터를 조회 중입니다...';
  status.className   = 'bt-status bt-status-loading';

  document.getElementById('bt-empty').style.display       = 'none';
  document.getElementById('bt-result-inner').style.display = 'none';

  fetch('/api/backtest', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ticker, exchange,
      start_date:   start,
      end_date:     end,
      initial_cash: cash,
      order_qty:    qty,
      params_a:     paramsA,
      params_b:     paramsB,
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        status.textContent = `❌ ${data.error}`;
        status.className   = 'bt-status bt-status-error';
        document.getElementById('bt-empty').style.display = 'flex';
        return;
      }
      status.textContent = '';
      status.className   = 'bt-status';
      btRenderResult(data, ticker, paramsA, paramsB);
    })
    .catch(e => {
      status.textContent = `❌ 서버 오류: ${e.message}`;
      status.className   = 'bt-status bt-status-error';
      document.getElementById('bt-empty').style.display = 'flex';
    })
    .finally(() => {
      btn.disabled    = false;
      btn.textContent = '▶ 백테스트 실행';
    });
}

/* ── 결과 렌더링 ── */
function btRenderResult(data, ticker, paramsA, paramsB) {
  const inner = document.getElementById('bt-result-inner');
  inner.style.display = 'block';

  const ra = data.result_a;
  const rb = data.result_b;

  // 범위 정보
  const rangeBar = document.getElementById('bt-range-bar');
  if (rangeBar && data.ohlcv_range) {
    const r = data.ohlcv_range;
    rangeBar.innerHTML =
      `<strong>${ticker}</strong> &nbsp;|&nbsp; ${r.start} ~ ${r.end} &nbsp;|&nbsp; ${r.bars}거래일`;
  }

  // KPI 비교 카드
  btRenderKPI(ra, rb);

  // 에쿼티 커브
  btRenderEquityChart(ra, rb);

  // RSI 차트
  btRenderRsiChart(ra);

  // 체결 내역
  btRenderTrades(ra.trades || []);
}

/* ── KPI 비교 카드 ── */
function btRenderKPI(ra, rb) {
  const wrap = document.getElementById('bt-kpi-wrap');
  if (!wrap) return;

  const s = ra.summary;
  const sb = rb?.summary;

  const kpis = [
    { label: '총 수익률',    va: s.total_return_pct + '%',   vb: sb ? sb.total_return_pct + '%' : null,
      colorA: s.total_return_pct >= 0,   colorB: sb ? sb.total_return_pct >= 0 : null },
    { label: '누적 손익',    va: fmt(s.total_pnl),           vb: sb ? fmt(sb.total_pnl) : null,
      colorA: s.total_pnl >= 0,          colorB: sb ? sb.total_pnl >= 0 : null },
    { label: '승률',         va: s.win_rate + '%',           vb: sb ? sb.win_rate + '%' : null,
      colorA: s.win_rate >= 50,          colorB: sb ? sb.win_rate >= 50 : null },
    { label: 'Profit Factor',va: s.profit_factor,            vb: sb ? sb.profit_factor : null,
      colorA: s.profit_factor >= 1,      colorB: sb ? sb.profit_factor >= 1 : null },
    { label: 'MDD',          va: s.mdd + '%',                vb: sb ? sb.mdd + '%' : null,
      colorA: s.mdd < 20,               colorB: sb ? sb.mdd < 20 : null, invertColor: true },
    { label: '총 거래 수',   va: s.total_trades,             vb: sb ? sb.total_trades : null,  noColor: true },
    { label: '최고 단일 거래', va: fmt(s.best_trade),         vb: sb ? fmt(sb.best_trade) : null, colorA: true, colorB: true },
    { label: '최악 단일 거래', va: fmt(s.worst_trade),        vb: sb ? fmt(sb.worst_trade) : null, colorA: s.worst_trade >= 0, colorB: sb ? sb.worst_trade >= 0 : null },
  ];

  wrap.innerHTML = kpis.map(k => {
    const cA = k.noColor ? '' : (k.colorA ? 'bt-kpi-green' : 'bt-kpi-red');
    const cB = !rb || k.noColor ? '' : (k.colorB ? 'bt-kpi-green' : 'bt-kpi-red');
    const bCol = rb && !k.noColor
      ? (() => {
          const numA = parseFloat(String(k.va).replace(/,/g,''));
          const numB = parseFloat(String(k.vb).replace(/,/g,''));
          if (isNaN(numA) || isNaN(numB)) return '';
          const better = k.invertColor ? numB < numA : numB > numA;
          return better ? ' bt-kpi-better' : numB < numA ? ' bt-kpi-worse' : '';
        })()
      : '';

    return `<div class="bt-kpi-card">
      <div class="bt-kpi-label">${k.label}</div>
      <div class="bt-kpi-row">
        <div class="bt-kpi-val ${cA}"><span class="bt-set-badge bt-a">A</span>${k.va}</div>
        ${rb ? `<div class="bt-kpi-val ${cB}${bCol}"><span class="bt-set-badge bt-b">B</span>${k.vb}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

/* ── 에쿼티 커브 차트 ── */
function btRenderEquityChart(ra, rb) {
  const canvas = document.getElementById('bt-equity-chart');
  if (!canvas) return;
  if (_btEquityChart) { _btEquityChart.destroy(); _btEquityChart = null; }

  const curveA = ra.equity_curve || [];
  const curveB = rb?.equity_curve || [];
  const labels = curveA.map(p => p.date);

  const ctx = canvas.getContext('2d');
  const datasets = [
    {
      label: '세트 A',
      data: curveA.map(p => p.equity),
      borderColor: '#4f8ef7', borderWidth: 2,
      pointRadius: labels.length > 100 ? 0 : 2,
      fill: false, tension: 0.3,
    },
  ];
  if (curveB.length) {
    datasets.push({
      label: '세트 B',
      data: curveB.map(p => p.equity),
      borderColor: '#f5c842', borderWidth: 2,
      borderDash: [5, 4],
      pointRadius: 0,
      fill: false, tension: 0.3,
    });
  }
  // 초기 자본 기준선
  const initCash = ra.summary.initial_cash;
  datasets.push({
    label: '초기 자본',
    data: labels.map(() => initCash),
    borderColor: 'rgba(255,255,255,0.15)', borderWidth: 1,
    borderDash: [4, 4], pointRadius: 0, fill: false,
  });

  _btEquityChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#8899aa', font: { size: 12 } } },
        tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: '#8899aa', maxTicksLimit: 10, maxRotation: 0 },
             grid:  { color: 'rgba(255,255,255,0.04)' } },
        y: { ticks: { color: '#8899aa', callback: v => fmt(v) },
             grid:  { color: 'rgba(255,255,255,0.04)' } },
      },
    },
  });
}

/* ── RSI 차트 ── */
function btRenderRsiChart(ra) {
  const canvas = document.getElementById('bt-rsi-chart');
  if (!canvas) return;
  if (_btRsiChart) { _btRsiChart.destroy(); _btRsiChart = null; }

  const curve  = ra.equity_curve || [];
  const labels = curve.map(p => p.date);
  const rsiVal = curve.map(p => p.rsi);
  const oversold   = parseFloat(document.getElementById('bt-a-rsi-oversold')?.value)   || 30;
  const overbought = parseFloat(document.getElementById('bt-a-rsi-overbought')?.value) || 70;

  // 매수/매도 시점 마커 (null이면 포인트 없음)
  const buyMarkers  = curve.map(p => p.signal === 'BUY'  ? p.rsi : null);
  const sellMarkers = curve.map(p => p.signal === 'SELL' ? p.rsi : null);

  const ctx = canvas.getContext('2d');
  _btRsiChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'RSI', data: rsiVal, borderColor: '#27c98f', borderWidth: 1.5,
          pointRadius: 0, fill: false, tension: 0.2 },
        { label: `과매도(${oversold})`,   data: labels.map(() => oversold),
          borderColor: 'rgba(80,200,120,0.4)', borderWidth:1, borderDash:[4,4], pointRadius:0, fill:false },
        { label: `과매수(${overbought})`, data: labels.map(() => overbought),
          borderColor: 'rgba(240,91,91,0.4)', borderWidth:1, borderDash:[4,4], pointRadius:0, fill:false },
        { label: '매수', data: buyMarkers,
          borderColor: 'transparent', backgroundColor: '#4fc97a',
          pointRadius: curve.map(p => p.signal === 'BUY' ? 6 : 0),
          pointStyle: 'triangle', showLine: false },
        { label: '매도', data: sellMarkers,
          borderColor: 'transparent', backgroundColor: '#f05b5b',
          pointRadius: curve.map(p => p.signal === 'SELL' ? 6 : 0),
          pointStyle: 'triangle', rotation: 180, showLine: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#8899aa', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color:'#8899aa', maxTicksLimit:8, maxRotation:0 },
             grid: { color:'rgba(255,255,255,0.03)' } },
        y: { min: 0, max: 100,
             ticks: { color:'#8899aa', stepSize: 25 },
             grid: { color:'rgba(255,255,255,0.03)' } },
      },
    },
  });
}

/* ── 체결 내역 테이블 ── */
function btRenderTrades(trades) {
  const tbody = document.getElementById('bt-trades-tbody');
  if (!tbody) return;
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">체결 내역 없음</td></tr>';
    return;
  }
  tbody.innerHTML = trades.map(t => {
    const isBuy  = t.side.startsWith('BUY');
    const cls    = isBuy ? 'sig-buy' : (t.pnl != null && t.pnl < 0 ? 'sig-sell' : 'sig-buy');
    const pnlStr = t.pnl != null ? `<span class="${t.pnl >= 0 ? 'sig-buy':'sig-sell'}">${fmt(t.pnl)}</span>` : '-';
    return `<tr>
      <td style="font-size:12px">${t.date}</td>
      <td><span class="${cls}" style="font-weight:700">${t.side}</span></td>
      <td>${Number(t.price).toLocaleString()}</td>
      <td>${t.qty}</td>
      <td>${pnlStr}</td>
    </tr>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════════════════
// 알림 설정 (Notifications)
// ══════════════════════════════════════════════════════════════════════

/* 알림 레벨 키 목록 */
const _NOTIFY_LEVELS = [
  'trade', 'trump', 'error', 'market_open', 'daily_summary',
];

/* 설정 불러오기 */
function loadNotifySettings() {
  fetch('/api/notify/settings')
    .then(r => r.json())
    .then(d => {
      // 마스터 토글
      const enabled = !!d.telegram_enabled;
      const masterEl = document.getElementById('notify-telegram-enabled');
      if (masterEl) masterEl.checked = enabled;
      notifyToggleMaster(enabled, /* silent */ true);

      // 봇 토큰 (마스킹된 값만 hint로 표시)
      const tokenEl = document.getElementById('notify-bot-token');
      if (tokenEl) tokenEl.placeholder = d.telegram_bot_token === '****'
        ? '저장된 토큰 있음 (변경 시 입력)'
        : '봇 토큰을 입력하세요';

      // Chat ID
      const cidEl = document.getElementById('notify-chat-id');
      if (cidEl) cidEl.value = d.telegram_chat_id || '';

      // 레벨별 토글
      _NOTIFY_LEVELS.forEach(key => {
        const el = document.getElementById(`notify-${key}`);
        if (el) el.checked = !!d[`notify_${key}`];
      });
    })
    .catch(() => showToast('알림 설정 로드 실패', 'error'));
}

/* 마스터 토글 ON/OFF */
function notifyToggleMaster(enabled, silent = false) {
  const statusEl = document.getElementById('notify-master-status');
  const fieldsEl = document.getElementById('notify-telegram-fields');
  if (statusEl) {
    statusEl.textContent = enabled ? '활성화' : '비활성화';
    statusEl.className   = `notify-status-text ${enabled ? 'on' : 'off'}`;
  }
  if (fieldsEl) {
    fieldsEl.style.opacity      = enabled ? '1'    : '0.5';
    fieldsEl.style.pointerEvents= enabled ? 'auto' : 'none';
  }
  if (!silent) saveNotifySettings();
}

/* 알림 설정 저장 */
function saveNotifySettings() {
  const token = document.getElementById('notify-bot-token')?.value.trim() || '****';
  const cid   = document.getElementById('notify-chat-id')?.value.trim() || '';

  const payload = {
    telegram_enabled:   document.getElementById('notify-telegram-enabled')?.checked ?? false,
    telegram_chat_id:   cid,
  };
  // 토큰이 비어있으면 기존 값 유지 (****로 서버에 전달 → 유지 처리)
  payload.telegram_bot_token = token || '****';

  _NOTIFY_LEVELS.forEach(key => {
    const el = document.getElementById(`notify-${key}`);
    payload[`notify_${key}`] = el ? el.checked : false;
  });

  fetch('/api/notify/settings', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast('✅ 알림 설정이 저장되었습니다.');
      else       showToast('저장 실패: ' + d.message, 'error');
    })
    .catch(() => showToast('서버 오류', 'error'));
}

/* 테스트 메시지 전송 */
function testNotify() {
  const btn      = document.querySelector('.btn-notify-test');
  const resultEl = document.getElementById('notify-test-result');
  const token    = document.getElementById('notify-bot-token')?.value.trim() || '****';
  const cid      = document.getElementById('notify-chat-id')?.value.trim() || '';

  if (btn) { btn.disabled = true; btn.textContent = '⏳ 전송 중...'; }
  if (resultEl) { resultEl.textContent = ''; resultEl.className = 'notify-test-result'; }

  fetch('/api/notify/test', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      telegram_bot_token: token,
      telegram_chat_id:   cid,
    }),
  })
    .then(r => r.json())
    .then(d => {
      if (resultEl) {
        resultEl.textContent = d.ok
          ? '✅ 테스트 메시지를 전송했습니다! 텔레그램을 확인하세요.'
          : `❌ 전송 실패: ${d.message}`;
        resultEl.className = `notify-test-result ${d.ok ? 'ok' : 'err'}`;
      }
      if (!d.ok) showToast(d.message, 'error');
    })
    .catch(e => {
      if (resultEl) {
        resultEl.textContent = `❌ 서버 오류: ${e.message}`;
        resultEl.className   = 'notify-test-result err';
      }
    })
    .finally(() => {
      if (btn) { btn.disabled = false; btn.textContent = '📨 테스트 전송'; }
    });
}

