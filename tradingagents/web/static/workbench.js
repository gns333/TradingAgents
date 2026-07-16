(() => {
  const COLLAPSE_LIMIT = 260;
  const TEAM_ROLES = {
    market: { label: '市场分析师', kind: '价格/技术面', report: 'market_report' },
    news: { label: '新闻分析师', kind: '资讯催化', report: 'news_report' },
    fundamentals: { label: '基本面分析师', kind: '财务/估值', report: 'fundamentals_report' },
    social: { label: '情绪分析师', kind: '社交/舆情', report: 'sentiment_report' },
    research_manager: { label: '研究经理', kind: '多空观点整合', report: 'investment_plan' },
    trader: { label: '交易员', kind: '交易计划', report: 'trader_investment_plan' },
    portfolio_manager: { label: '组合经理', kind: '最终决策', report: 'final_trade_decision' }
  };
  const SECTION_TO_ROLE = Object.fromEntries(
    Object.entries(TEAM_ROLES).map(([key, role]) => [role.report, key])
  );

  // Official provider presets: selecting a provider auto-fills its base URL and
  // offers curated model dropdowns so admins never hand-type an endpoint.
  const PROVIDER_PRESETS = {
    deepseek: {
      label: 'DeepSeek',
      base_url: 'https://api.deepseek.com',
      editable_base_url: false,
      catalog_supported: true
    },
    openai: {
      label: 'OpenAI',
      base_url: '',
      editable_base_url: false,
      catalog_supported: true
    },
    anthropic: {
      label: 'Anthropic',
      base_url: '',
      editable_base_url: false,
      catalog_supported: true
    },
    google: {
      label: 'Google Gemini',
      base_url: '',
      editable_base_url: false,
      catalog_supported: true
    },
    'qwen-cn': {
      label: '通义千问（国内）',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      editable_base_url: false,
      catalog_supported: false
    },
    'glm-cn': {
      label: '智谱 GLM（国内）',
      base_url: 'https://open.bigmodel.cn/api/paas/v4/',
      editable_base_url: false,
      catalog_supported: false
    },
    kimi: {
      label: 'Kimi（月之暗面）',
      base_url: 'https://api.moonshot.cn/v1',
      editable_base_url: false,
      catalog_supported: true
    },
    openai_compatible: {
      label: 'OpenAI 兼容 / 自建',
      base_url: '',
      editable_base_url: true,
      catalog_supported: true
    }
  };

  const state = {
    view: 'analysis',
    theme: localStorage.getItem('ta_theme') || 'dark',
    adminToken: localStorage.getItem('ta_admin_token') || '',
    adminPasswordConfigured: false,
    identityEmail: localStorage.getItem('ta_identity_email') || '',
    runState: '空闲',
    activeRunId: '',
    currentRun: null,
    lastEventSeq: 0,
    pollTimer: null,
    streamDone: false,
    eventTotal: 0,
    roleStates: {},
    reports: {},
    currentReportSection: '',
    activeTicker: { code: '', name: '' },
    history: [],
    historyFilter: 'all',
    historyQuery: '',
    historyActiveId: null,
    historyReport: null,
    historyActiveSection: '',
    tickerSearchTimer: null,
    adminPane: 'models',
    adminModels: [],
    adminWhitelist: [],
    selectedModelId: null,
    selectedWhitelistEmail: ''
  };

  // Pipeline order for the Agent team board (analysts first, then managers).
  const PIPELINE_ORDER = [
    'market', 'news', 'fundamentals', 'social',
    'research_manager', 'trader', 'portfolio_manager'
  ];

  // Map a free-text decision / final report to a Buy / Sell / Hold badge.
  function classifyDecision(text) {
    const value = String(text || '').toLowerCase();
    if (/\b(buy|long|overweight)\b|买入|看多|增持|加仓/.test(value)) return { kind: 'buy', label: '买入' };
    if (/\b(sell|short|underweight)\b|卖出|看空|减持|清仓|减仓/.test(value)) return { kind: 'sell', label: '卖出' };
    if (/\b(hold|neutral)\b|持有|观望|中性/.test(value)) return { kind: 'hold', label: '持有' };
    return null;
  }

  function decisionBadgeHtml(text) {
    const decision = classifyDecision(text);
    if (!decision) return '';
    return `<span class="decision-badge ${decision.kind}">${decision.label}</span>`;
  }

  // --- Theme -----------------------------------------------------------------
  function applyTheme(theme) {
    state.theme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
    localStorage.setItem('ta_theme', state.theme);
  }

  function toggleTheme() {
    applyTheme(state.theme === 'dark' ? 'light' : 'dark');
  }

  // --- Topbar ticker info bar ------------------------------------------------
  function updateTickerBar() {
    const bar = qs('#ticker-bar');
    if (!bar) return;
    const { code, name } = state.activeTicker;
    if (!code) {
      bar.classList.remove('show');
      bar.innerHTML = '';
      return;
    }
    bar.classList.add('show');
    bar.innerHTML = `
      <span class="tk-code"></span>
      <span class="tk-name"></span>
    `;
    bar.querySelector('.tk-code').textContent = code;
    bar.querySelector('.tk-name').textContent = name || '';
    updateTopbarContext();
  }

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function setText(selector, text) {
    const node = qs(selector);
    if (node) node.textContent = text;
  }

  function updateIdentitySummary() {
    const isAdmin = Boolean(state.adminToken);
    const summary = isAdmin ? '本地管理员' : state.identityEmail || '未设置邮箱';
    setText('#identity-summary', summary);
    const summaryNode = qs('#identity-summary');
    if (summaryNode) summaryNode.title = summary;
    const stateNodes = [qs('#identity-state'), qs('#identity-modal-state')].filter(Boolean);
    stateNodes.forEach(node => {
      node.className = `dot-badge ${isAdmin || state.identityEmail ? 'active' : 'pending'}`;
      node.textContent = isAdmin ? '已授权' : state.identityEmail ? '已设置' : '未设置';
    });
    setText(
      '#identity-modal-status',
      isAdmin ? '管理员模式下无需邮箱白名单' : state.identityEmail ? `当前使用 ${state.identityEmail}` : '尚未保存访问邮箱'
    );
    updateTopbarContext();
  }

  function updateTopbarContext() {
    const context = qs('#topbar-context');
    const runState = qs('#global-run-state');
    const ticker = qs('#ticker-bar');
    if (!context || !runState || !ticker) return;

    context.hidden = false;
    runState.hidden = state.view !== 'analysis';
    ticker.hidden = state.view !== 'analysis';
    if (state.view === 'analysis') {
      context.hidden = true;
    } else if (state.view === 'reports') {
      const owner = state.adminToken ? '管理员视图' : state.identityEmail || '当前用户';
      context.textContent = `${owner} · ${state.history.length} 份报告`;
    } else {
      context.textContent = state.adminToken ? '本地开发 · 管理员已授权' : '需要管理员登录';
    }
  }

  function todayChinaDate() {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  }

  function identityQuery() {
    const params = new URLSearchParams();
    if (state.identityEmail) params.set('access_email', state.identityEmail);
    return params;
  }

  function adminHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (state.adminToken) headers.Authorization = `Bearer ${state.adminToken}`;
    return headers;
  }

  function persistAdminSession(token) {
    state.adminToken = token || '';
    if (state.adminToken) {
      localStorage.setItem('ta_admin_token', state.adminToken);
      document.cookie = `ta_admin=${encodeURIComponent(state.adminToken)}; Path=/; SameSite=Lax`;
    } else {
      localStorage.removeItem('ta_admin_token');
      document.cookie = 'ta_admin=; Path=/; Max-Age=0; SameSite=Lax';
    }
    setAdminAvailable(Boolean(state.adminToken));
    updateIdentitySummary();
  }

  async function apiJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const message = typeof detail === 'object' && detail
        ? detail.message || detail.error_type || `HTTP ${response.status}`
        : detail || `HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.detail = detail;
      throw error;
    }
    return data;
  }

  function withIdentity(url) {
    const query = identityQuery().toString();
    if (!query) return url;
    return `${url}${url.includes('?') ? '&' : '?'}${query}`;
  }

  function setAdminAvailable(available) {
    document.querySelectorAll('.nav-admin').forEach(button => { button.hidden = false; });
    updateIdentitySummary();
  }

  async function refreshAdminStatus() {
    const status = await apiJson('/api/admin/status', { headers: adminHeaders() });
    state.adminPasswordConfigured = Boolean(status.password_configured);
    if (state.adminToken && status.session_valid) {
      persistAdminSession(state.adminToken);
    } else if (state.adminToken) {
      persistAdminSession('');
    } else {
      setAdminAvailable(false);
    }
    renderAdminAuth();
  }

  function showView(view) {
    const target = qs(`#view-${view}`) ? view : 'analysis';
    state.view = target;
    document.querySelectorAll('.view').forEach(section => {
      section.classList.toggle('active', section.id === `view-${target}`);
    });
    document.querySelectorAll('.nav-item').forEach(button => {
      button.classList.toggle('active', button.dataset.view === target);
    });
    const active = qs(`#view-${target}`);
    setText('#view-title', active?.dataset.title || '工作台');
    updateTopbarContext();
    if (target === 'admin') renderAdminWorkspace();
    if (target === 'reports') loadReportHistory();
    const query = new URLSearchParams();
    query.set('view', target);
    if (target === 'admin') query.set('adminPane', state.adminPane);
    history.replaceState(null, '', `?${query.toString()}`);
  }

  function openAdminModal() {
    const modal = qs('#admin-modal');
    if (modal) modal.hidden = false;
    renderAdminAuth();
  }

  function openAdminEntry() {
    if (state.adminToken) {
      showView('admin');
      return;
    }
    openAdminModal();
  }

  function closeAdminModal() {
    const modal = qs('#admin-modal');
    if (modal) modal.hidden = true;
  }

  function openIdentityModal() {
    const modal = qs('#identity-modal');
    const input = qs('#identity-email');
    if (input) input.value = state.identityEmail;
    if (modal) modal.hidden = false;
    updateIdentitySummary();
    setTimeout(() => input?.focus(), 0);
  }

  function closeIdentityModal() {
    const modal = qs('#identity-modal');
    if (modal) modal.hidden = true;
  }

  async function saveIdentity() {
    const input = qs('#identity-email');
    if (input && !input.reportValidity()) return;
    const email = input?.value.trim() || '';
    state.identityEmail = email;
    if (email) localStorage.setItem('ta_identity_email', email);
    else localStorage.removeItem('ta_identity_email');
    updateIdentitySummary();
    closeIdentityModal();
    await restoreActiveRun();
    if (state.view === 'reports') await loadReportHistory();
  }

  function renderAdminAuth() {
    const root = qs('#admin-auth-root');
    if (!root) return;
    const isSetup = !state.adminPasswordConfigured;
    root.innerHTML = `
      <div class="modal-body">
        <p class="helper-text">${isSetup ? '首次使用请设置管理员密码。' : '输入管理员密码进入后台管理。'}</p>
        <label for="admin-password">${isSetup ? '设置管理员密码' : '管理员密码'}</label>
        <input id="admin-password" type="password" autocomplete="${isSetup ? 'new-password' : 'current-password'}">
        <div class="form-status" id="admin-auth-status" role="alert"></div>
        <button type="button" id="admin-auth-submit">${isSetup ? '设置并登录' : '登录'}</button>
      </div>
    `;
    qs('#admin-auth-submit')?.addEventListener('click', submitAdminAuth);
    qs('#admin-password')?.addEventListener('keydown', event => {
      if (event.key === 'Enter') submitAdminAuth();
    });
  }

  async function submitAdminAuth() {
    const password = qs('#admin-password')?.value || '';
    const status = qs('#admin-auth-status');
    if (status) status.textContent = '';
    try {
      if (!state.adminPasswordConfigured) {
        await apiJson('/api/admin/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password })
        });
        state.adminPasswordConfigured = true;
      }
      const login = await apiJson('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });
      persistAdminSession(login.token || '');
      closeAdminModal();
      showView('admin');
      renderAdminWorkspace();
    } catch (err) {
      if (status) status.textContent = `登录失败：${err.message}`;
    }
  }

  // ---------------------------------------------------------------------------
  // Single-stock analysis workspace
  // ---------------------------------------------------------------------------
  function renderAnalysisWorkspace() {
    const root = qs('#analysis-root');
    if (!root) return;
    root.innerHTML = `
      <div class="analysis-layout">
        <section class="analysis-launchbar" aria-label="分析参数">
          <div class="launch-field">
            <label for="ticker">股票代码</label>
            <div class="combo ticker-combo" id="ticker-combo">
              <input id="ticker" value="600519.SH" autocomplete="off" role="combobox"
                aria-expanded="false" aria-autocomplete="list" aria-controls="ticker-suggest"
                placeholder="输入代码或名称，如 600519 / 茅台">
              <span class="ticker-name-chip" id="ticker-name-chip" aria-hidden="true" hidden></span>
              <div class="combo-menu" id="ticker-suggest" role="listbox" hidden></div>
            </div>
          </div>
          <div class="launch-field">
            <label for="trade-date">分析日期</label>
            <input id="trade-date" type="date">
          </div>
          <div class="launch-modules">
            <span class="launch-label">分析模块</span>
            <div class="module-fieldset" role="group" aria-label="分析模块">
              <label class="check-option"><input type="checkbox" name="analyst" value="market" checked>市场</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="news" checked>新闻</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="fundamentals" checked>基本面</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="social">社交情绪</label>
            </div>
          </div>
          <button class="launch-action" type="button" id="run-analysis">开始分析</button>
        </section>
        <div class="analysis-notice" id="analysis-status" role="status" hidden></div>

        <section class="panel agent-flow-panel" aria-label="Agent 协作进度">
          <div class="panel-header">
            <h3>Agent 协作进度</h3>
            <div class="flow-summary">
              <span class="flow-current" id="current-agent">未开始</span>
              <span class="flow-count mono" id="team-count">0 / 0</span>
            </div>
          </div>
          <div class="agent-flow-scroll"><div class="agent-flow" id="team-board"></div></div>
        </section>

        <div class="workspace-grid analysis-grid">
          <section class="panel report-current-panel">
            <div class="report-toolbar" id="current-report-toolbar">
              <div class="report-identity">
                <div class="report-identity-main">
                  <span class="report-code" id="current-report-code">等待分析</span>
                  <span class="report-name" id="current-report-name"></span>
                </div>
                <span class="report-meta" id="current-report-meta">报告会在 Agent 交付后显示</span>
              </div>
              <button class="secondary-button compact-button" type="button" data-open-reports>历史报告</button>
            </div>
            <div class="report-viewer" id="report-preview"><div class="empty-state">等待报告生成</div></div>
          </section>

          <div class="analysis-side">
            <section class="panel timeline-panel">
              <div class="panel-header">
                <h3>过程</h3>
                <p><span class="event-count" id="event-count">0</span> 个事件</p>
              </div>
              <div class="timeline" id="log"><div class="empty-state">等待过程事件</div></div>
            </section>
          </div>
        </div>
      </div>
    `;
    const dateInput = qs('#trade-date');
    if (dateInput && !dateInput.value) dateInput.value = todayChinaDate();
    dateInput?.addEventListener('change', updateCurrentReportToolbar);
    qs('#run-analysis')?.addEventListener('click', startAnalysis);
    qs('[data-open-reports]', root)?.addEventListener('click', () => showView('reports'));
    setupTickerAutocomplete();
    resetTeamBoard(selectedAnalystList());
    renderReportPreview();
    const seed = qs('#ticker')?.value.trim();
    if (seed && !state.activeTicker.code) state.activeTicker = { code: seed, name: '' };
    setAnalysisTicker(state.activeTicker.code || seed, state.activeTicker.name || '');
    updateTickerBar();
  }

  // --- Ticker autocomplete ---------------------------------------------------
  function setupTickerAutocomplete() {
    const input = qs('#ticker');
    const menu = qs('#ticker-suggest');
    if (!input || !menu) return;

    input.addEventListener('input', () => {
      const value = input.value.trim();
      state.activeTicker = { code: value, name: '' };
      updateTickerIdentity();
      if (state.tickerSearchTimer) clearTimeout(state.tickerSearchTimer);
      if (!value) {
        hideTickerSuggestions();
        return;
      }
      state.tickerSearchTimer = setTimeout(() => fetchTickerSuggestions(value), 180);
    });

    input.addEventListener('keydown', event => {
      if (event.key === 'Escape') hideTickerSuggestions();
      if (event.key === 'ArrowDown' && !menu.hidden) {
        event.preventDefault();
        menu.querySelector('.combo-item')?.focus();
      }
    });

    document.addEventListener('click', event => {
      if (!qs('#ticker-combo')?.contains(event.target)) hideTickerSuggestions();
    });
    document.querySelectorAll('input[name="analyst"]').forEach(option => {
      option.addEventListener('change', () => {
        if (!state.activeRunId) resetTeamBoard(selectedAnalystList());
        updateCurrentReportToolbar();
      });
    });
  }

  async function fetchTickerSuggestions(query) {
    try {
      const data = await apiJson(`/api/stocks/search?q=${encodeURIComponent(query)}&limit=8`);
      renderTickerSuggestions(data.items || []);
    } catch {
      hideTickerSuggestions();
    }
  }

  function renderTickerSuggestions(items) {
    const input = qs('#ticker');
    const menu = qs('#ticker-suggest');
    if (!menu || !input) return;
    if (!items.length) {
      hideTickerSuggestions();
      return;
    }
    menu.textContent = '';
    items.forEach(item => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'combo-item';
      option.role = 'option';
      option.innerHTML = '<strong></strong><span></span>';
      option.querySelector('strong').textContent = item.name || item.code;
      option.querySelector('span').textContent = item.code;
      option.addEventListener('click', () => {
        setAnalysisTicker(item.code, item.name);
        hideTickerSuggestions();
        input.focus();
      });
      menu.appendChild(option);
    });
    menu.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function hideTickerSuggestions() {
    const menu = qs('#ticker-suggest');
    const input = qs('#ticker');
    if (menu) {
      menu.hidden = true;
      menu.textContent = '';
    }
    if (input) input.setAttribute('aria-expanded', 'false');
  }

  function selectedAnalystList() {
    const selected = [...document.querySelectorAll('input[name="analyst"]:checked')].map(
      input => input.value
    );
    return selected.length ? selected : ['market', 'news', 'fundamentals'];
  }

  function getSelectedAnalysts() {
    return selectedAnalystList().join(',');
  }

  function setAnalysisTicker(ticker, name) {
    const input = qs('#ticker');
    if (input) input.value = ticker;
    state.activeTicker = { code: ticker || '', name: name || '' };
    updateTickerIdentity();
    updateTickerBar();
  }

  function updateTickerIdentity() {
    const chip = qs('#ticker-name-chip');
    if (chip) {
      chip.textContent = state.activeTicker.name || '';
      chip.hidden = !state.activeTicker.name;
    }
    updateCurrentReportToolbar();
  }

  function updateCurrentReportToolbar() {
    setText('#current-report-code', state.activeTicker.code || '等待分析');
    setText('#current-report-name', state.activeTicker.name || '');
    const date = qs('#trade-date')?.value || state.currentRun?.trade_date || todayChinaDate();
    const count = selectedAnalystList().length;
    setText(
      '#current-report-meta',
      state.activeTicker.code ? `分析日 ${date} · ${count} 个分析模块` : '报告会在 Agent 交付后显示'
    );
  }

  function resetTeamBoard(analysts) {
    state.roleStates = {};
    [...analysts, 'research_manager', 'trader', 'portfolio_manager'].forEach(key => {
      state.roleStates[key] = 'pending';
    });
    renderTeamBoard();
  }

  function renderTeamBoard() {
    const board = qs('#team-board');
    if (!board) return;
    board.textContent = '';
    const visibleRoles = PIPELINE_ORDER.filter(key => state.roleStates[key]);
    const doneCount = visibleRoles.filter(key => state.roleStates[key] === 'done').length;
    setText('#team-count', `${doneCount} / ${visibleRoles.length}`);
    visibleRoles.forEach((key, index) => {
      const role = TEAM_ROLES[key];
      if (!role) return;
      const roleState = state.roleStates[key];
      const node = document.createElement('div');
      node.className = `flow-node ${roleState}`;
      node.innerHTML = '<span class="flow-marker" aria-hidden="true"></span>'
        + '<strong class="flow-role"></strong><span class="flow-kind"></span>'
        + '<span class="flow-status"></span>';
      node.querySelector('.flow-marker').textContent = roleState === 'done' ? '✓' : String(index + 1);
      node.querySelector('.flow-role').textContent = role.label;
      node.querySelector('.flow-kind').textContent = role.kind.replaceAll('/', ' / ');
      node.querySelector('.flow-status').textContent =
        roleState === 'active' ? '进行中' : roleState === 'done' ? '已完成' : '待处理';
      board.appendChild(node);
    });
    const activeLabels = visibleRoles
      .filter(key => state.roleStates[key] === 'active')
      .map(key => TEAM_ROLES[key].label);
    if (activeLabels.length) {
      setText('#current-agent', activeLabels.length > 1 ? `${activeLabels.length} 位分析师并行处理中` : `${activeLabels[0]}处理中`);
    } else if (visibleRoles.length && doneCount === visibleRoles.length) {
      setText('#current-agent', '团队已结束');
    }
    requestAnimationFrame(focusCurrentAgent);
  }

  function focusCurrentAgent() {
    const scroller = qs('.agent-flow-scroll');
    const current = scroller?.querySelector('.flow-node.active');
    if (!scroller || !current || scroller.scrollWidth <= scroller.clientWidth) return;
    scroller.scrollLeft = Math.max(
      0,
      current.offsetLeft - (scroller.clientWidth - current.offsetWidth) / 2
    );
  }

  function markRole(key, nextState) {
    if (!state.roleStates[key]) return;
    state.roleStates[key] = nextState;
    advancePipelineStage();
    renderTeamBoard();
  }

  function advancePipelineStage() {
    const analystKeys = Object.keys(state.roleStates).filter(key => !['research_manager', 'trader', 'portfolio_manager'].includes(key));
    if (analystKeys.length && analystKeys.every(key => state.roleStates[key] === 'done')) {
      if (state.roleStates.research_manager === 'pending') state.roleStates.research_manager = 'active';
    }
    if (state.roleStates.research_manager === 'done' && state.roleStates.trader === 'pending') {
      state.roleStates.trader = 'active';
    }
    if (state.roleStates.trader === 'done' && state.roleStates.portfolio_manager === 'pending') {
      state.roleStates.portfolio_manager = 'active';
    }
  }

  function setRunState(text, mode) {
    state.runState = text;
    const pill = qs('#global-run-state');
    if (pill) {
      pill.textContent = text;
      pill.className = `state-pill ${mode || ''}`.trim();
    }
    updateTopbarContext();
  }

  function tickEvent() {
    state.eventTotal += 1;
    setText('#event-count', String(state.eventTotal));
  }

  function formatEventTime(value) {
    const date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    }).format(date);
  }

  function addCollapsibleLog(title, detail, meta = '', kind = '', createdAt = '') {
    tickEvent();
    const log = qs('#log');
    if (!log) return;
    log.querySelector('.empty-state')?.remove();
    const item = document.createElement('article');
    item.className = `event-item ${kind}`.trim();
    const safeDetail = String(detail || '');
    item.innerHTML = '<div class="event-title"><strong></strong><div class="event-meta"><span></span><time></time></div></div><p></p>';
    item.querySelector('strong').textContent = title;
    item.querySelector('.event-meta span').textContent = meta;
    item.querySelector('time').textContent = formatEventTime(createdAt);
    if (createdAt) item.querySelector('time').dateTime = createdAt;
    item.querySelector('p').textContent =
      safeDetail.length > COLLAPSE_LIMIT ? `${safeDetail.slice(0, COLLAPSE_LIMIT)}...` : safeDetail;
    if (safeDetail.length > COLLAPSE_LIMIT) {
      const details = document.createElement('details');
      details.innerHTML = `<summary>展开完整输出（${safeDetail.length} 字）</summary><pre></pre>`;
      details.querySelector('pre').textContent = safeDetail;
      item.appendChild(details);
    }
    log.prepend(item);
  }

  function resetRunView() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
    state.reports = {};
    state.currentReportSection = '';
    state.streamDone = false;
    state.eventTotal = 0;
    state.lastEventSeq = 0;
    setText('#event-count', '0');
    setText('#current-agent', '连接中');
    const log = qs('#log');
    if (log) log.innerHTML = '<div class="empty-state">等待过程事件</div>';
    resetTeamBoard(selectedAnalystList());
    renderReportPreview();
  }

  function setAnalysisStatus(text, mode = '') {
    const node = qs('#analysis-status');
    if (!node) return;
    node.textContent = text || '';
    node.hidden = !text;
    node.className = `analysis-notice ${mode}`.trim();
  }

  async function startAnalysis() {
    resetRunView();
    const button = qs('#run-analysis');
    if (button) button.disabled = true;
    setRunState('连接中', 'running');
    setAnalysisStatus('正在创建后台分析任务…', 'running');
    if (state.adminToken) {
      try {
        await refreshAdminStatus();
      } catch (err) {
        setRunState('会话同步失败', 'failed');
        setAnalysisStatus(`管理员会话同步失败：${err.message}`, 'error');
        if (button) button.disabled = false;
        return;
      }
    }
    const payload = {
      ticker: qs('#ticker')?.value.trim() || '',
      stock_name: state.activeTicker.name || '',
      trade_date: qs('#trade-date')?.value || todayChinaDate(),
      asset_type: 'stock',
      analysts: selectedAnalystList()
    };
    try {
      const result = await apiJson(withIdentity('/api/runs'), {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify(payload)
      });
      await attachToRun(result.run);
    } catch (err) {
      if (err.status === 409 && err.detail?.run) {
        await attachToRun(err.detail.run);
        return;
      }
      setRunState('启动失败', 'failed');
      setAnalysisStatus(`分析启动失败：${err.message}`, 'error');
      if (button) button.disabled = false;
    }
  }

  async function restoreActiveRun() {
    try {
      const data = await apiJson(withIdentity('/api/runs/active'), {
        headers: adminHeaders()
      });
      if (data.run) {
        await attachToRun(data.run);
        return data.run;
      }
      state.activeRunId = '';
      state.currentRun = null;
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
      return null;
    } catch (err) {
      if (err.status !== 401) setAnalysisStatus(`任务状态读取失败：${err.message}`, 'error');
      return null;
    }
  }

  async function attachToRun(run) {
    if (!run?.id) return;
    resetRunView();
    state.activeRunId = run.id;
    state.currentRun = run;
    const button = qs('#run-analysis');
    if (button) button.disabled = true;
    setAnalysisTicker(run.ticker || '', run.stock_name || '');
    const tickerInput = qs('#ticker');
    const dateInput = qs('#trade-date');
    if (tickerInput) tickerInput.value = run.ticker || '';
    if (dateInput) dateInput.value = run.trade_date || todayChinaDate();
    document.querySelectorAll('input[name="analyst"]').forEach(input => {
      input.checked = (run.analysts || []).includes(input.value);
    });
    updateCurrentReportToolbar();
    resetTeamBoard(run.analysts || []);
    setRunState(run.status === 'queued' ? '排队中' : '分析中', 'running');
    setAnalysisStatus(run.status === 'queued' ? '任务已提交，等待执行' : '后台任务正在执行，关闭页面不会中断', 'running');
    await loadPersistedRunEvents(run.id, 0);
    if (!state.streamDone && state.activeRunId === run.id) pollActiveRun(run.id);
  }

  async function loadPersistedRunEvents(runId, after) {
    const result = await apiJson(withIdentity(`/api/runs/${encodeURIComponent(runId)}/events?after=${after}`), {
      headers: adminHeaders()
    });
    (result.items || []).forEach(item => {
      handleAnalysisEvent(item.event, {
        ...(item.data || {}), seq: item.seq, run_id: runId, created_at: item.created_at
      });
    });
  }

  function scheduleRunPoll(runId, delay = 700) {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(() => pollActiveRun(runId), delay);
  }

  async function pollActiveRun(runId) {
    if (!runId || state.activeRunId !== runId || state.streamDone) return;
    try {
      await loadPersistedRunEvents(runId, state.lastEventSeq || 0);
      if (state.streamDone) return;
      const result = await apiJson(withIdentity(`/api/runs/${encodeURIComponent(runId)}`), {
        headers: adminHeaders()
      });
      state.currentRun = result.run;
      if (result.run.status === 'completed') {
        handleAnalysisEvent('run_completed', { run_id: runId });
        return;
      }
      if (result.run.status === 'failed') {
        handleAnalysisEvent('run_failed', {
          run_id: runId,
          error_type: result.run.error_type,
          message: result.run.error_message
        });
        return;
      }
      setRunState(result.run.status === 'queued' ? '排队中' : '分析中', 'running');
      setAnalysisStatus(result.run.status === 'queued'
        ? '任务已提交，等待执行'
        : '后台任务正在执行，进度已同步', 'running');
      scheduleRunPoll(runId);
    } catch (err) {
      setAnalysisStatus(`实时同步暂时中断，后台任务仍在执行，正在重试：${err.message}`, 'warning');
      scheduleRunPoll(runId, 2000);
    }
  }

  function handleAnalysisEvent(event, data) {
    if (Number(data.seq) > state.lastEventSeq) state.lastEventSeq = Number(data.seq);
    if (event === 'run_started') {
      resetTeamBoard(data.analysts || []);
      (data.analysts || []).forEach(key => {
        if (state.roleStates[key]) state.roleStates[key] = 'active';
      });
      renderTeamBoard();
      if (data.ticker) {
        state.activeTicker = { code: data.ticker, name: state.activeTicker.name };
        updateTickerBar();
      }
      setRunState('分析中', 'running');
      setAnalysisStatus(`后台分析中：${data.ticker} · ${data.trade_date}`, 'running');
      addCollapsibleLog('任务启动', `${data.ticker} ${(data.analysts || []).join('、')}`, '团队', 'active', data.created_at);
    } else if (event === 'tool_called') {
      setRunState('分析中', 'running');
      addCollapsibleLog('工具调用', `${data.tool} ${JSON.stringify(data.args)}`, '工具', 'active', data.created_at);
    } else if (event === 'agent_message') {
      setRunState('分析中', 'running');
      addCollapsibleLog('Agent 输出', data.content, data.message_type || 'Agent', 'active', data.created_at);
    } else if (event === 'report_section_updated') {
      const roleKey = SECTION_TO_ROLE[data.section];
      if (roleKey) markRole(roleKey, 'done');
      state.reports[data.section] = data.content || '';
      if (!state.currentReportSection) state.currentReportSection = data.section;
      renderReportPreview();
      addCollapsibleLog('报告更新', `${TEAM_ROLES[roleKey]?.label || data.section} 交付了 ${data.section}`, '协作轨迹', 'done', data.created_at);
    } else if (event === 'run_completed') {
      state.streamDone = true;
      state.activeRunId = '';
      state.currentRun = null;
      if (state.pollTimer) clearTimeout(state.pollTimer);
      Object.keys(state.roleStates).forEach(key => { state.roleStates[key] = 'done'; });
      renderTeamBoard();
      setRunState('分析完成', 'done');
      setText('#current-agent', '已结束');
      setAnalysisStatus('分析完成，报告已归档到报告中心', 'success');
      addCollapsibleLog('完成', '最终状态已生成，团队分析已结束。', '系统', 'done', data.created_at);
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
      loadReportHistory();
    } else if (event === 'run_failed') {
      state.streamDone = true;
      state.activeRunId = '';
      state.currentRun = null;
      if (state.pollTimer) clearTimeout(state.pollTimer);
      const detail = `${data.error_type || 'Error'}: ${data.message || '未知错误'}`;
      setRunState('分析失败', 'failed');
      setText('#current-agent', '已停止');
      setAnalysisStatus(`分析失败：${detail}`, 'error');
      addCollapsibleLog('错误', detail, '系统', 'error', data.created_at);
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Markdown rendering
  // ---------------------------------------------------------------------------
  function escapeHtml(value) {
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function renderInline(value) {
    let text = escapeHtml(value);
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return text;
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || '').replace(/\r\n/g, '\n').split('\n');
    const output = [];
    let i = 0;
    while (i < lines.length) {
      const trimmed = lines[i].trim();
      if (!trimmed) {
        i += 1;
        continue;
      }
      if (trimmed.startsWith('```')) {
        const code = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          code.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        output.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
        continue;
      }
      const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        i += 1;
        continue;
      }
      if (/^---+$/.test(trimmed)) {
        output.push('<hr>');
        i += 1;
        continue;
      }
      if (/^>\s?/.test(trimmed)) {
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
          quote.push(lines[i].trim().replace(/^>\s?/, ''));
          i += 1;
        }
        output.push(`<blockquote>${renderInline(quote.join(' '))}</blockquote>`);
        continue;
      }
      if (trimmed.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
        const headers = splitTableRow(trimmed);
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim().includes('|')) {
          rows.push(splitTableRow(lines[i]));
          i += 1;
        }
        output.push('<table>');
        output.push(`<thead><tr>${headers.map(cell => `<th>${renderInline(cell)}</th>`).join('')}</tr></thead>`);
        output.push('<tbody>');
        rows.forEach(row => {
          output.push(`<tr>${row.map(cell => `<td>${renderInline(cell)}</td>`).join('')}</tr>`);
        });
        output.push('</tbody></table>');
        continue;
      }
      if (/^[-*]\s+/.test(trimmed)) {
        const items = [];
        while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
          items.push(lines[i].trim().replace(/^[-*]\s+/, ''));
          i += 1;
        }
        output.push(`<ul>${items.map(item => `<li>${renderInline(item)}</li>`).join('')}</ul>`);
        continue;
      }
      if (/^\d+\.\s+/.test(trimmed)) {
        const items = [];
        while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
          items.push(lines[i].trim().replace(/^\d+\.\s+/, ''));
          i += 1;
        }
        output.push(`<ol>${items.map(item => `<li>${renderInline(item)}</li>`).join('')}</ol>`);
        continue;
      }
      const paragraph = [trimmed];
      i += 1;
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,4})\s+/.test(lines[i].trim()) &&
        !/^[-*]\s+/.test(lines[i].trim()) &&
        !/^\d+\.\s+/.test(lines[i].trim()) &&
        !lines[i].trim().startsWith('```') &&
        !(lines[i].trim().includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
      ) {
        paragraph.push(lines[i].trim());
        i += 1;
      }
      output.push(`<p>${renderInline(paragraph.join(' '))}</p>`);
    }
    return output.join('');
  }

  function reportTitle(section) {
    const roleKey = SECTION_TO_ROLE[section];
    if (roleKey && TEAM_ROLES[roleKey]) return TEAM_ROLES[roleKey].label;
    return section.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
  }

  function orderedReportSections() {
    return [
      'market_report',
      'news_report',
      'fundamentals_report',
      'sentiment_report',
      'investment_plan',
      'trader_investment_plan',
      'final_trade_decision'
    ];
  }

  // Shared tabbed report viewer used by both the live analysis panel and the
  // report-center history detail.
  function buildReportTabs(container, sectionsMap, activeSection, onSelect) {
    if (!container) return;
    const available = orderedReportSections().filter(section => sectionsMap[section]);
    if (!available.length) {
      container.innerHTML = '<div class="empty-state">暂无报告内容</div>';
      return;
    }
    const active = available.includes(activeSection) ? activeSection : available[0];
    container.textContent = '';
    const tabs = document.createElement('div');
    tabs.className = 'report-tabs';
    tabs.setAttribute('role', 'tablist');
    const panel = document.createElement('article');
    panel.className = 'report-article markdown';

    const paint = section => {
      tabs.querySelectorAll('.report-tab').forEach(tab => {
        const on = tab.dataset.section === section;
        tab.classList.toggle('active', on);
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
      });
      const body = sectionsMap[section] || '';
      // The final decision gets a Buy/Sell/Hold badge pinned above its report.
      const badge = section === 'final_trade_decision' ? decisionBadgeHtml(body) : '';
      panel.innerHTML = (badge ? `<div class="report-summary-head">${badge}</div>` : '')
        + renderMarkdown(body);
      if (typeof onSelect === 'function') onSelect(section);
    };

    available.forEach(section => {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = `report-tab ${section === active ? 'active' : ''}`.trim();
      tab.dataset.section = section;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', section === active ? 'true' : 'false');
      tab.tabIndex = section === active ? 0 : -1;
      tab.textContent = reportTitle(section);
      tab.addEventListener('click', () => paint(section));
      tabs.appendChild(tab);
    });

    container.appendChild(tabs);
    container.appendChild(panel);
    paint(active);
  }

  // Live current report inside the analysis view.
  function renderReportPreview() {
    const root = qs('#report-preview');
    if (!root) return;
    updateCurrentReportToolbar();
    const identity = qs('#current-report-toolbar .report-identity-main');
    identity?.querySelector('.decision-badge')?.remove();
    const finalDecision = state.reports.final_trade_decision;
    if (identity && finalDecision) identity.insertAdjacentHTML('beforeend', decisionBadgeHtml(finalDecision));
    if (!Object.keys(state.reports).length) {
      root.innerHTML = '<div class="empty-state">等待报告生成</div>';
      return;
    }
    buildReportTabs(root, state.reports, state.currentReportSection, section => {
      state.currentReportSection = section;
    });
  }

  // ---------------------------------------------------------------------------
  // Report center (history)
  // ---------------------------------------------------------------------------
  async function loadReportHistory() {
    const root = qs('#reports-root');
    if (!root) return;
    try {
      const data = await apiJson(withIdentity('/api/reports'), { headers: adminHeaders() });
      state.history = data.items || [];
      updateTopbarContext();
      if (state.historyActiveId && !state.history.some(item => item.id === state.historyActiveId)) {
        state.historyActiveId = null;
        state.historyReport = null;
      }
      renderReportCenter();
    } catch (err) {
      root.innerHTML = `<section class="panel"><div class="panel-body"><div class="empty-state">历史报告加载失败：${escapeHtml(err.message)}</div></div></section>`;
    }
  }

  function reportInstrumentLabel(report) {
    if (report?.stock_name) return `${report.stock_name}（${report.ticker}）`;
    return report?.ticker || '未知';
  }

  function historyLabel(item) {
    const date = item.trade_date || (item.created_at || '').slice(0, 10);
    return `${reportInstrumentLabel(item)} · ${date}`;
  }

  function renderReportCenter() {
    const root = qs('#reports-root');
    if (!root) return;
    root.innerHTML = `
      <div class="workspace-grid reports-grid">
        <section class="panel history-panel">
          <div class="panel-header">
            <h3>历史报告</h3>
            <button class="icon-button history-refresh" type="button" id="reload-history" aria-label="刷新历史报告" title="刷新">↻</button>
          </div>
          <div class="history-filter">
            <input id="history-search" placeholder="搜索代码或名称" autocomplete="off">
            <div class="filter-chips" role="group" aria-label="按决策筛选">
              <button type="button" class="filter-chip" data-filter="all">全部</button>
              <button type="button" class="filter-chip" data-filter="buy">买入</button>
              <button type="button" class="filter-chip" data-filter="sell">卖出</button>
              <button type="button" class="filter-chip" data-filter="hold">持有</button>
            </div>
          </div>
          <div class="history-list" id="history-list"></div>
        </section>
        <section class="panel report-detail-panel">
          <div class="report-toolbar" id="history-summary">
            <div class="report-identity">
              <div class="report-identity-main">
                <span class="report-code" id="history-detail-title">报告详情</span>
                <span class="report-name" id="history-detail-name"></span>
              </div>
              <span class="report-meta" id="history-detail-meta">从左侧选择一份历史报告</span>
            </div>
          </div>
          <div class="report-viewer" id="history-detail"><div class="empty-state">选择左侧报告后查看完整内容</div></div>
        </section>
      </div>
    `;
    qs('#reload-history')?.addEventListener('click', loadReportHistory);
    const search = qs('#history-search');
    if (search) {
      search.value = state.historyQuery;
      search.addEventListener('input', () => {
        state.historyQuery = search.value.trim();
        renderHistoryList();
      });
    }
    root.querySelectorAll('.filter-chip').forEach(chip => {
      chip.classList.toggle('active', chip.dataset.filter === state.historyFilter);
      chip.addEventListener('click', () => {
        state.historyFilter = chip.dataset.filter;
        root.querySelectorAll('.filter-chip').forEach(c =>
          c.classList.toggle('active', c.dataset.filter === state.historyFilter));
        renderHistoryList();
      });
    });
    renderHistoryList();
    if (state.historyReport) renderHistoryDetail();
  }

  // Client-side filter over the already-loaded history list.
  function filteredHistory() {
    const query = state.historyQuery.toLowerCase();
    return state.history.filter(item => {
      if (state.historyFilter !== 'all') {
        const decision = classifyDecision(item.decision);
        if (!decision || decision.kind !== state.historyFilter) return false;
      }
      if (!query) return true;
      return `${item.ticker || ''} ${item.stock_name || ''}`.toLowerCase().includes(query);
    });
  }

  function renderHistoryList() {
    const list = qs('#history-list');
    if (!list) return;
    if (!state.history.length) {
      list.innerHTML = '<div class="empty-state">暂无历史报告，完成一次分析后自动归档</div>';
      return;
    }
    const items = filteredHistory();
    if (!items.length) {
      list.innerHTML = '<div class="empty-state">没有符合筛选条件的报告</div>';
      return;
    }
    list.textContent = '';
    items.forEach(item => {
      const row = document.createElement('div');
      row.className = `history-item ${item.id === state.historyActiveId ? 'active' : ''}`.trim();

      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'history-open';
      open.innerHTML = '<div class="ho-top"><strong><span class="ho-code"></span><span class="ho-name"></span></strong></div>'
        + '<div class="ho-meta"><span class="ho-trade-date"></span><span class="ho-created"></span><span class="ho-owner"></span></div>';
      open.querySelector('.ho-code').textContent = item.ticker || '未知代码';
      open.querySelector('.ho-name').textContent = item.stock_name ? ` · ${item.stock_name}` : '';
      const badge = decisionBadgeHtml(item.decision);
      open.querySelector('.ho-top').insertAdjacentHTML('beforeend', badge || '<span class="tag">已归档</span>');
      const owner = state.adminToken
        ? item.owner_email || item.owner_uid || '历史未归属'
        : '';
      const tradeDate = item.trade_date || (item.created_at || '').slice(0, 10) || '未知';
      open.querySelector('.ho-trade-date').textContent = `分析日 ${tradeDate}`;
      open.querySelector('.ho-created').textContent = `${formatEventTime(item.created_at) || '--:--'} 归档`;
      const ownerNode = open.querySelector('.ho-owner');
      ownerNode.textContent = owner;
      ownerNode.hidden = !owner;
      open.addEventListener('click', () => openHistoryReport(item.id));

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'history-delete';
      del.setAttribute('aria-label', '删除报告');
      del.textContent = '删除';
      del.addEventListener('click', () => deleteHistoryReport(item.id));

      row.appendChild(open);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  async function openHistoryReport(id) {
    try {
      const data = await apiJson(withIdentity(`/api/reports/${id}`), { headers: adminHeaders() });
      state.historyActiveId = id;
      state.historyReport = data.item || null;
      state.historyActiveSection = '';
      renderHistoryList();
      renderHistoryDetail();
    } catch (err) {
      setText('#history-detail-meta', `加载失败：${err.message}`);
    }
  }

  function renderHistoryDetail() {
    const detail = qs('#history-detail');
    const report = state.historyReport;
    if (!detail || !report) return;
    setText('#history-detail-title', report.ticker || '未知代码');
    setText('#history-detail-name', report.stock_name || '');
    const moduleCount = (report.analysts || []).length;
    const created = formatEventTime(report.created_at);
    const owner = state.adminToken ? report.owner_email || report.owner_uid || '' : '';
    setText('#history-detail-meta', [
      `分析日 ${report.trade_date || '未知'}`,
      created ? `${created} 归档` : '',
      `${moduleCount} 个分析模块`,
      owner
    ].filter(Boolean).join(' · '));
    const summary = qs('#history-summary');
    if (summary) {
      summary.querySelector('.decision-badge')?.remove();
      const badge = decisionBadgeHtml(report.decision);
      if (badge) summary.insertAdjacentHTML('beforeend', badge);
    }
    buildReportTabs(detail, report.sections || {}, state.historyActiveSection, section => {
      state.historyActiveSection = section;
    });
  }

  async function deleteHistoryReport(id) {
    if (typeof confirm === 'function' && !confirm('确认删除这份历史报告？')) return;
    try {
      await apiJson(withIdentity(`/api/reports/${id}`), {
        method: 'DELETE',
        headers: adminHeaders()
      });
      if (state.historyActiveId === id) {
        state.historyActiveId = null;
        state.historyReport = null;
      }
      await loadReportHistory();
    } catch (err) {
      setText('#history-detail-meta', `删除失败：${err.message}`);
    }
  }

  // ---------------------------------------------------------------------------
  // Admin workspace
  // ---------------------------------------------------------------------------
  function renderAdminWorkspace() {
    const root = qs('#admin-root');
    if (!root) return;
    if (!state.adminToken) {
      root.innerHTML = `
        <section class="panel admin-login-panel">
          <div class="panel-header">
            <h3>后台管理</h3>
            <p>需要管理员登录</p>
          </div>
          <div class="panel-body">
            <p class="helper-text">登录后可管理模型配置与访问白名单。本地管理员会话保存在当前浏览器。</p>
            <button type="button" id="admin-login-from-view">管理员登录</button>
          </div>
        </section>
      `;
      qs('#admin-login-from-view')?.addEventListener('click', openAdminModal);
      return;
    }
    const providerOptions = Object.entries(PROVIDER_PRESETS)
      .map(([value, preset]) => `<option value="${value}">${preset.label}</option>`)
      .join('');
    root.innerHTML = `
      <div class="workspace-grid admin-grid">
        <div class="admin-commandbar">
          <div class="admin-tabs" role="tablist" aria-label="后台配置类型">
            <button class="admin-tab ${state.adminPane === 'models' ? 'active' : ''}" type="button" data-admin-pane="models">模型管理</button>
            <button class="admin-tab ${state.adminPane === 'whitelist' ? 'active' : ''}" type="button" data-admin-pane="whitelist">白名单</button>
          </div>
          <div class="admin-session-inline">
            <span class="state-pill done">管理员已登录</span>
            <button class="secondary-button compact-button" type="button" id="admin-logout">退出</button>
          </div>
        </div>

        <div class="admin-pane ${state.adminPane === 'models' ? 'active' : ''}" data-admin-content="models">
          <section class="panel admin-list-panel">
            <div class="panel-header">
              <div><h3>模型配置</h3><span class="admin-count" id="model-count">加载中</span></div>
              <div class="admin-header-actions">
                <button class="icon-button history-refresh" type="button" data-reload-admin aria-label="刷新模型配置" title="刷新">↻</button>
                <button class="secondary-button compact-button" type="button" id="new-model-config">新增模型</button>
              </div>
            </div>
            <div class="table-wrap" id="model-list"><div class="empty-state">加载中…</div></div>
          </section>
          <section class="panel admin-editor-panel">
            <div class="panel-header"><h3 id="model-editor-title">新增模型</h3><span class="tag" id="model-editor-tag">新配置</span></div>
            <div class="admin-editor-form">
              <div class="admin-field"><label for="model-provider">供应商</label><select id="model-provider">${providerOptions}</select></div>
              <div class="admin-field"><label for="model-name">显示名称</label><input id="model-name" autocomplete="off"></div>
              <div class="admin-field full">
                <label for="model-base-url">Base URL</label><input id="model-base-url" readonly>
                <p class="helper-text" id="model-base-url-hint">官方供应商地址已自动填充。</p>
              </div>
              <div class="admin-field"><label for="model-quick">快速模型</label><input id="model-quick" list="model-options" autocomplete="off" placeholder="选择或输入模型 ID"></div>
              <div class="admin-field"><label for="model-deep">深度模型</label><input id="model-deep" list="model-options" autocomplete="off" placeholder="可与快速模型相同"></div>
              <datalist id="model-options"></datalist>
              <div class="admin-field full">
                <label for="model-api-key">API Key</label><input id="model-api-key" type="password" autocomplete="new-password" placeholder="编辑时留空即保留现有 Key">
              </div>
              <div class="admin-field full admin-toggle-row">
                <label class="toggle-option"><input id="model-enabled" type="checkbox" checked>启用此配置</label>
              </div>
              <div class="status-box admin-field full" id="model-status"></div>
              <div class="admin-editor-actions">
                <button class="secondary-button danger-button" type="button" id="delete-model-config" hidden>删除</button>
                <button class="secondary-button" type="button" id="set-default-model" hidden>设为默认</button>
                <button class="secondary-button" type="button" id="fetch-model-catalog">获取模型列表</button>
                <button type="button" id="save-model-config">保存模型</button>
              </div>
            </div>
          </section>
        </div>

        <div class="admin-pane ${state.adminPane === 'whitelist' ? 'active' : ''}" data-admin-content="whitelist">
          <section class="panel admin-list-panel">
            <div class="panel-header">
              <div><h3>白名单用户</h3><span class="admin-count" id="whitelist-count">加载中</span></div>
              <div class="admin-header-actions">
                <button class="icon-button history-refresh" type="button" data-reload-admin aria-label="刷新白名单" title="刷新">↻</button>
                <button class="secondary-button compact-button" type="button" id="new-whitelist">新增用户</button>
              </div>
            </div>
            <div class="table-wrap" id="whitelist-list"><div class="empty-state">加载中…</div></div>
          </section>
          <section class="panel admin-editor-panel">
            <div class="panel-header"><h3 id="whitelist-editor-title">新增白名单</h3><span class="dot-badge pending" id="whitelist-editor-tag">待录入</span></div>
            <div class="admin-editor-form">
              <div class="admin-field full">
                <label for="wl-email">邮箱</label><input id="wl-email" type="email" autocomplete="off">
                <p class="helper-text" id="wl-email-hint">邮箱是访问匹配键，保存既有用户时不可修改。</p>
              </div>
              <div class="admin-field"><label for="wl-uid">UID</label><input id="wl-uid" autocomplete="off"></div>
              <div class="admin-field"><label for="wl-status">状态</label><select id="wl-status"><option value="active">启用</option><option value="pending">待确认</option><option value="blocked">禁用</option></select></div>
              <div class="admin-field"><label for="wl-limit">每日次数</label><input id="wl-limit" type="number" min="0" value="5"></div>
              <div class="admin-field full"><label for="wl-note">备注</label><textarea id="wl-note"></textarea></div>
              <div class="status-box admin-field full" id="whitelist-status"></div>
              <div class="admin-editor-actions">
                <button class="secondary-button danger-button" type="button" id="delete-whitelist" hidden>移除用户</button>
                <button type="button" id="save-whitelist">保存白名单</button>
              </div>
            </div>
          </section>
        </div>
      </div>
    `;
    qs('#admin-logout')?.addEventListener('click', logoutAdmin);
    root.querySelectorAll('.admin-tab').forEach(tab => tab.addEventListener('click', () => switchAdminPane(tab.dataset.adminPane)));
    root.querySelectorAll('[data-reload-admin]').forEach(button => button.addEventListener('click', loadAdminData));
    qs('#new-model-config')?.addEventListener('click', clearModelEditor);
    qs('#new-whitelist')?.addEventListener('click', clearWhitelistEditor);
    qs('#fetch-model-catalog')?.addEventListener('click', fetchModelCatalog);
    qs('#save-model-config')?.addEventListener('click', saveModelConfig);
    qs('#set-default-model')?.addEventListener('click', setDefaultModel);
    qs('#delete-model-config')?.addEventListener('click', deleteModelConfig);
    qs('#save-whitelist')?.addEventListener('click', saveWhitelist);
    qs('#delete-whitelist')?.addEventListener('click', deleteWhitelist);
    qs('#model-provider')?.addEventListener('change', () => applyProviderPreset(true));
    applyProviderPreset();
    loadAdminData().catch(err => {
      setStatus(state.adminPane === 'models' ? '#model-status' : '#whitelist-status', `加载失败：${err.message}`, false);
    });
  }

  function switchAdminPane(pane) {
    state.adminPane = pane === 'whitelist' ? 'whitelist' : 'models';
    document.querySelectorAll('.admin-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.adminPane === state.adminPane));
    document.querySelectorAll('.admin-pane').forEach(panel => panel.classList.toggle('active', panel.dataset.adminContent === state.adminPane));
    if (state.view === 'admin') {
      const query = new URLSearchParams();
      query.set('view', 'admin');
      query.set('adminPane', state.adminPane);
      history.replaceState(null, '', `?${query.toString()}`);
    }
  }

  function fillModelOptions(models) {
    const list = qs('#model-options');
    if (!list) return;
    list.textContent = '';
    models.forEach(model => {
      const option = document.createElement('option');
      option.value = model.id;
      option.label = model.display_name || model.id;
      list.appendChild(option);
    });
  }

  function applyProviderPreset(clearModels = true) {
    const providerSelect = qs('#model-provider');
    if (!providerSelect) return;
    const preset = PROVIDER_PRESETS[providerSelect.value] || PROVIDER_PRESETS.deepseek;
    const baseUrl = qs('#model-base-url');
    const hint = qs('#model-base-url-hint');
    if (baseUrl) {
      baseUrl.value = preset.base_url || '';
      baseUrl.readOnly = !preset.editable_base_url;
      baseUrl.placeholder = preset.editable_base_url
        ? '请填写自建/兼容服务地址，例如 http://localhost:8000/v1'
        : '';
    }
    if (hint) {
      hint.textContent = preset.editable_base_url
        ? '自建或兼容服务需手动填写完整 Base URL。'
        : preset.base_url
          ? '官方供应商地址已自动填充，不可修改。'
          : '该供应商使用官方默认地址，无需填写。';
    }
    if (clearModels) {
      fillModelOptions([]);
      if (qs('#model-quick')) qs('#model-quick').value = '';
      if (qs('#model-deep')) qs('#model-deep').value = '';
    }
    setStatus(
      '#model-status',
      preset.catalog_supported ? '填写 API Key 后获取实时模型列表' : '该供应商请手动填写模型 ID',
      undefined
    );
    const nameInput = qs('#model-name');
    if (clearModels && nameInput && !nameInput.value) nameInput.value = `${preset.label} 默认`;
  }

  async function fetchModelCatalog() {
    const provider = qs('#model-provider')?.value || 'deepseek';
    const apiKey = qs('#model-api-key')?.value || '';
    const baseUrl = qs('#model-base-url')?.value || '';
    setStatus('#model-status', '正在获取模型列表', undefined);
    try {
      const result = await apiJson('/api/admin/model-catalog', {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({
          provider, api_key: apiKey, base_url: baseUrl,
          config_id: state.selectedModelId || undefined
        })
      });
      fillModelOptions(result.models || []);
      if (result.source === 'manual') {
        setStatus('#model-status', result.message || '请手动填写模型 ID', undefined);
        return;
      }
      const count = (result.models || []).length;
      setStatus('#model-status', `已获取 ${count} 个可用模型，两个模型可以选择相同值`, true);
    } catch (err) {
      setStatus('#model-status', `获取失败：${err.message}`, false);
    }
  }

  function logoutAdmin() {
    persistAdminSession('');
    renderAdminWorkspace();
  }

  // Small helper: set a status box's text and success/error styling.
  function setStatus(selector, text, ok) {
    const node = qs(selector);
    if (!node) return;
    node.textContent = text;
    node.classList.remove('ok', 'err');
    if (ok === true) node.classList.add('ok');
    else if (ok === false) node.classList.add('err');
  }

  async function saveModelConfig() {
    try {
      const result = await apiJson('/api/admin/model-configs', {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({
          id: state.selectedModelId || undefined,
          display_name: qs('#model-name')?.value || '',
          provider: qs('#model-provider')?.value || 'deepseek',
          base_url: qs('#model-base-url')?.value || '',
          quick_model: qs('#model-quick')?.value || '',
          deep_model: qs('#model-deep')?.value || '',
          api_key: qs('#model-api-key')?.value || '',
          enabled: Boolean(qs('#model-enabled')?.checked),
          is_default: state.adminModels.find(item => item.id === state.selectedModelId)?.is_default || false
        })
      });
      state.selectedModelId = result.item?.id || state.selectedModelId;
      await loadAdminData();
      setStatus('#model-status', '模型配置已保存', true);
    } catch (err) {
      setStatus('#model-status', `保存失败：${err.message}`, false);
    }
  }

  async function saveWhitelist() {
    try {
      const result = await apiJson('/api/admin/whitelist', {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({
          email: qs('#wl-email')?.value || '',
          uid: qs('#wl-uid')?.value || '',
          status: qs('#wl-status')?.value || 'active',
          daily_limit: Number(qs('#wl-limit')?.value || 5),
          note: qs('#wl-note')?.value || ''
        })
      });
      state.selectedWhitelistEmail = result.item?.email || qs('#wl-email')?.value || '';
      await loadAdminData();
      setStatus('#whitelist-status', '白名单已保存', true);
    } catch (err) {
      setStatus('#whitelist-status', `保存失败：${err.message}`, false);
    }
  }

  async function loadAdminData() {
    if (!state.adminToken) return;
    const [models, whitelist] = await Promise.all([
      apiJson('/api/admin/model-configs', { headers: adminHeaders() }),
      apiJson('/api/admin/whitelist', { headers: adminHeaders() })
    ]);
    state.adminModels = models.items || [];
    state.adminWhitelist = whitelist.items || [];
    if (state.selectedModelId && !state.adminModels.some(item => item.id === state.selectedModelId)) state.selectedModelId = null;
    if (state.selectedWhitelistEmail && !state.adminWhitelist.some(item => item.email === state.selectedWhitelistEmail)) state.selectedWhitelistEmail = '';
    if (!state.selectedModelId && state.adminModels.length) state.selectedModelId = state.adminModels[0].id;
    if (!state.selectedWhitelistEmail && state.adminWhitelist.length) state.selectedWhitelistEmail = state.adminWhitelist[0].email;
    renderAdminLists();
    if (state.selectedModelId) fillModelEditor(state.adminModels.find(item => item.id === state.selectedModelId));
    else clearModelEditor();
    if (state.selectedWhitelistEmail) fillWhitelistEditor(state.adminWhitelist.find(item => item.email === state.selectedWhitelistEmail));
    else clearWhitelistEditor();
  }

  function renderAdminLists() {
    const modelList = qs('#model-list');
    const whitelistList = qs('#whitelist-list');
    if (modelList) modelList.innerHTML = renderAdminModels(state.adminModels);
    if (whitelistList) whitelistList.innerHTML = renderWhitelist(state.adminWhitelist);
    setText('#model-count', `${state.adminModels.filter(item => item.enabled).length} 个可用配置`);
    const activeCount = state.adminWhitelist.filter(item => item.status === 'active').length;
    const pendingCount = state.adminWhitelist.filter(item => item.status === 'pending').length;
    setText('#whitelist-count', `${activeCount} 个启用 · ${pendingCount} 个待确认`);
    qs('#model-list')?.querySelectorAll('[data-model-id]').forEach(row => row.addEventListener('click', () => selectModel(Number(row.dataset.modelId))));
    qs('#whitelist-list')?.querySelectorAll('[data-whitelist-email]').forEach(row => row.addEventListener('click', () => selectWhitelist(row.dataset.whitelistEmail)));
  }

  function renderAdminModels(items) {
    if (!items.length) return '<div class="empty-state">暂无模型配置</div>';
    return `<table class="data-table admin-model-table"><thead><tr><th>名称</th><th>供应商</th><th>快速模型</th><th>深度模型</th><th>Key</th><th>状态</th></tr></thead><tbody>${
      items.map(item => `<tr class="selectable ${item.id === state.selectedModelId ? 'selected' : ''}" data-model-id="${item.id}"><td>${escapeHtml(item.display_name)}</td><td><span class="tag">${escapeHtml(item.provider)}</span></td><td class="mono">${escapeHtml(item.quick_model)}</td><td class="mono">${escapeHtml(item.deep_model)}</td><td class="mono">${escapeHtml(item.api_key_masked)}</td><td>${item.is_default ? '<span class="tag accent">默认</span>' : item.enabled ? '<span class="tag">可用</span>' : '<span class="tag">停用</span>'}</td></tr>`).join('')
    }</tbody></table>`;
  }

  const WL_STATUS_LABEL = { active: '启用', pending: '待确认', blocked: '禁用' };

  function renderWhitelist(items) {
    if (!items.length) return '<div class="empty-state">暂无白名单用户</div>';
    return `<table class="data-table admin-whitelist-table"><thead><tr><th>邮箱</th><th>UID</th><th>状态</th><th>每日次数</th><th>备注</th></tr></thead><tbody>${
      items.map(item => `<tr class="selectable ${item.email === state.selectedWhitelistEmail ? 'selected' : ''}" data-whitelist-email="${escapeHtml(item.email)}"><td>${escapeHtml(item.email)}</td><td class="mono">${escapeHtml(item.uid || '')}</td><td><span class="dot-badge ${escapeHtml(item.status)}">${escapeHtml(WL_STATUS_LABEL[item.status] || item.status)}</span></td><td class="num">${escapeHtml(item.daily_limit)}</td><td>${escapeHtml(item.note || '')}</td></tr>`).join('')
    }</tbody></table>`;
  }

  function selectModel(id) {
    state.selectedModelId = id;
    renderAdminLists();
    fillModelEditor(state.adminModels.find(item => item.id === id));
  }

  function clearModelEditor() {
    state.selectedModelId = null;
    renderAdminLists();
    setText('#model-editor-title', '新增模型');
    setText('#model-editor-tag', '新配置');
    qs('#model-editor-tag')?.classList.remove('accent');
    if (qs('#model-provider')) qs('#model-provider').value = 'deepseek';
    if (qs('#model-name')) qs('#model-name').value = '';
    if (qs('#model-api-key')) qs('#model-api-key').value = '';
    if (qs('#model-enabled')) qs('#model-enabled').checked = true;
    qs('#delete-model-config')?.setAttribute('hidden', '');
    qs('#set-default-model')?.setAttribute('hidden', '');
    applyProviderPreset(true);
    setStatus('#model-status', '', undefined);
  }

  function fillModelEditor(item) {
    if (!item) return clearModelEditor();
    state.selectedModelId = item.id;
    setText('#model-editor-title', '编辑模型');
    setText('#model-editor-tag', item.is_default ? '默认配置' : item.enabled ? '已启用' : '已停用');
    qs('#model-editor-tag')?.classList.toggle('accent', item.is_default);
    qs('#model-provider').value = item.provider;
    applyProviderPreset(false);
    qs('#model-name').value = item.display_name || '';
    qs('#model-base-url').value = item.base_url || PROVIDER_PRESETS[item.provider]?.base_url || '';
    qs('#model-quick').value = item.quick_model || '';
    qs('#model-deep').value = item.deep_model || '';
    qs('#model-api-key').value = '';
    qs('#model-api-key').placeholder = `留空保留 ${item.api_key_masked || '现有 Key'}`;
    qs('#model-enabled').checked = Boolean(item.enabled);
    qs('#delete-model-config').hidden = false;
    qs('#set-default-model').hidden = Boolean(item.is_default || !item.enabled);
    setStatus('#model-status', '', undefined);
  }

  async function setDefaultModel() {
    if (!state.selectedModelId) return;
    try {
      await apiJson(`/api/admin/model-configs/${state.selectedModelId}/set-default`, { method: 'POST', headers: adminHeaders() });
      await loadAdminData();
    } catch (err) { setStatus('#model-status', `设置失败：${err.message}`, false); }
  }

  async function deleteModelConfig() {
    if (!state.selectedModelId || (typeof confirm === 'function' && !confirm('确认删除这个模型配置？'))) return;
    try {
      await apiJson(`/api/admin/model-configs/${state.selectedModelId}`, { method: 'DELETE', headers: adminHeaders() });
      state.selectedModelId = null;
      await loadAdminData();
    } catch (err) { setStatus('#model-status', `删除失败：${err.message}`, false); }
  }

  function selectWhitelist(email) {
    state.selectedWhitelistEmail = email;
    renderAdminLists();
    fillWhitelistEditor(state.adminWhitelist.find(item => item.email === email));
  }

  function clearWhitelistEditor() {
    state.selectedWhitelistEmail = '';
    renderAdminLists();
    setText('#whitelist-editor-title', '新增白名单');
    const tag = qs('#whitelist-editor-tag');
    if (tag) { tag.textContent = '待录入'; tag.className = 'dot-badge pending'; }
    if (qs('#wl-email')) { qs('#wl-email').value = ''; qs('#wl-email').readOnly = false; }
    if (qs('#wl-uid')) qs('#wl-uid').value = '';
    if (qs('#wl-status')) qs('#wl-status').value = 'active';
    if (qs('#wl-limit')) qs('#wl-limit').value = '5';
    if (qs('#wl-note')) qs('#wl-note').value = '';
    qs('#delete-whitelist')?.setAttribute('hidden', '');
    setStatus('#whitelist-status', '', undefined);
  }

  function fillWhitelistEditor(item) {
    if (!item) return clearWhitelistEditor();
    state.selectedWhitelistEmail = item.email;
    setText('#whitelist-editor-title', '编辑白名单');
    const tag = qs('#whitelist-editor-tag');
    if (tag) { tag.textContent = WL_STATUS_LABEL[item.status] || item.status; tag.className = `dot-badge ${item.status}`; }
    qs('#wl-email').value = item.email || '';
    qs('#wl-email').readOnly = true;
    qs('#wl-uid').value = item.uid || '';
    qs('#wl-status').value = item.status || 'active';
    qs('#wl-limit').value = String(item.daily_limit ?? 5);
    qs('#wl-note').value = item.note || '';
    qs('#delete-whitelist').hidden = false;
    setStatus('#whitelist-status', '', undefined);
  }

  async function deleteWhitelist() {
    const item = state.adminWhitelist.find(row => row.email === state.selectedWhitelistEmail);
    if (!item || (typeof confirm === 'function' && !confirm('确认从白名单移除这个用户？'))) return;
    try {
      await apiJson(`/api/admin/whitelist/${item.id}`, { method: 'DELETE', headers: adminHeaders() });
      state.selectedWhitelistEmail = '';
      await loadAdminData();
    } catch (err) { setStatus('#whitelist-status', `移除失败：${err.message}`, false); }
  }

  function boot() {
    const params = new URLSearchParams(location.search);
    const requestedView = params.get('view') || state.view;
    const requestedAdminPane = params.get('adminPane');
    if (requestedAdminPane === 'models' || requestedAdminPane === 'whitelist') {
      state.adminPane = requestedAdminPane;
    }
    applyTheme(state.theme);
    if (state.identityEmail && qs('#identity-email')) qs('#identity-email').value = state.identityEmail;
    updateIdentitySummary();
    document.querySelectorAll('.nav-item').forEach(button => {
      button.addEventListener('click', () => showView(button.dataset.view));
    });
    qs('#theme-toggle')?.addEventListener('click', toggleTheme);
    qs('#open-identity-modal')?.addEventListener('click', openIdentityModal);
    qs('#close-identity-modal')?.addEventListener('click', closeIdentityModal);
    qs('#cancel-identity-modal')?.addEventListener('click', closeIdentityModal);
    qs('#save-identity')?.addEventListener('click', saveIdentity);
    qs('#identity-email')?.addEventListener('keydown', event => {
      if (event.key === 'Enter') saveIdentity();
    });
    qs('#close-admin-modal')?.addEventListener('click', closeAdminModal);
    document.querySelectorAll('.modal-backdrop').forEach(modal => {
      modal.addEventListener('click', event => {
        if (event.target !== modal) return;
        if (modal.id === 'identity-modal') closeIdentityModal();
        if (modal.id === 'admin-modal') closeAdminModal();
      });
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      if (!qs('#identity-modal')?.hidden) closeIdentityModal();
      if (!qs('#admin-modal')?.hidden) closeAdminModal();
    });
    window.addEventListener('resize', focusCurrentAgent);

    renderAnalysisWorkspace();
    renderReportCenter();
    renderAdminWorkspace();
    refreshAdminStatus()
      .catch(err => {
        setText('#identity-summary', `后台状态读取失败：${err.message}`);
      })
      .finally(() => restoreActiveRun());
    showView(requestedView);
    if (params.get('identity') === 'edit') openIdentityModal();
  }

  window.TradingAgentsWorkbench = {
    state,
    showView,
    identityQuery,
    adminHeaders,
    persistAdminSession,
    refreshAdminStatus,
    logoutAdmin,
    setAnalysisTicker,
    renderAnalysisWorkspace,
    renderMarkdown,
    renderReportCenter,
    renderReportPreview,
    renderAdminWorkspace,
    loadAdminData,
    loadReportHistory
  };

  document.addEventListener('DOMContentLoaded', boot);
})();
