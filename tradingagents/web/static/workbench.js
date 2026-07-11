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
      quick_models: ['deepseek-chat', 'deepseek-v4-flash'],
      deep_models: ['deepseek-reasoner', 'deepseek-v4-pro']
    },
    openai: {
      label: 'OpenAI',
      base_url: '',
      editable_base_url: false,
      quick_models: ['gpt-4.1-mini', 'gpt-4o-mini', 'gpt-5.4-mini'],
      deep_models: ['gpt-4.1', 'gpt-4o', 'gpt-5.5']
    },
    anthropic: {
      label: 'Anthropic',
      base_url: '',
      editable_base_url: false,
      quick_models: ['claude-3-5-haiku-latest'],
      deep_models: ['claude-sonnet-4-latest', 'claude-opus-4-latest']
    },
    google: {
      label: 'Google Gemini',
      base_url: '',
      editable_base_url: false,
      quick_models: ['gemini-2.5-flash'],
      deep_models: ['gemini-2.5-pro']
    },
    'qwen-cn': {
      label: '通义千问（国内）',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      editable_base_url: false,
      quick_models: ['qwen-turbo', 'qwen-plus'],
      deep_models: ['qwen-max', 'qwen-plus']
    },
    'glm-cn': {
      label: '智谱 GLM（国内）',
      base_url: 'https://open.bigmodel.cn/api/paas/v4/',
      editable_base_url: false,
      quick_models: ['glm-4-flash', 'glm-4-air'],
      deep_models: ['glm-4-plus']
    },
    kimi: {
      label: 'Kimi（月之暗面）',
      base_url: 'https://api.moonshot.ai/v1',
      editable_base_url: false,
      quick_models: ['moonshot-v1-8k', 'moonshot-v1-32k'],
      deep_models: ['moonshot-v1-128k']
    },
    openai_compatible: {
      label: 'OpenAI 兼容 / 自建',
      base_url: '',
      editable_base_url: true,
      quick_models: [],
      deep_models: []
    }
  };

  const state = {
    view: 'analysis',
    adminToken: localStorage.getItem('ta_admin_token') || '',
    adminPasswordConfigured: false,
    identityEmail: localStorage.getItem('ta_identity_email') || '',
    runState: '空闲',
    source: null,
    streamDone: false,
    eventTotal: 0,
    roleStates: {},
    reports: {},
    currentReportSection: '',
    history: [],
    historyActiveId: null,
    historyReport: null,
    historyActiveSection: '',
    tickerSearchTimer: null
  };

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function setText(selector, text) {
    const node = qs(selector);
    if (node) node.textContent = text;
  }

  function updateIdentitySummary() {
    const summary = state.adminToken
      ? '管理员模式：本地开发已授权'
      : state.identityEmail || '普通访问：未设置邮箱';
    setText('#identity-summary', summary);
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
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function setAdminAvailable(available) {
    document.querySelectorAll('.nav-admin').forEach(button => {
      button.hidden = !available;
    });
    const adminEntry = qs('#open-admin-login');
    if (adminEntry) {
      adminEntry.textContent = available ? '后台' : '管理员';
      adminEntry.setAttribute('aria-label', available ? '进入后台管理' : '管理员登录');
    }
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
    setText('#view-eyebrow', active?.dataset.eyebrow || 'TradingAgents');
    if (target === 'admin') renderAdminWorkspace();
    if (target === 'reports') loadReportHistory();
    history.replaceState(null, '', `?view=${encodeURIComponent(target)}`);
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
      <div class="workspace-grid analysis-grid">
        <section class="panel form-panel">
          <div class="panel-header">
            <h3>分析参数</h3>
            <p>选择股票和分析团队</p>
          </div>
          <div class="panel-body">
            <label for="ticker">股票代码</label>
            <div class="combo" id="ticker-combo">
              <input id="ticker" value="600519.SH" autocomplete="off" role="combobox"
                aria-expanded="false" aria-autocomplete="list" aria-controls="ticker-suggest"
                placeholder="输入代码或名称，如 600519 / 茅台">
              <div class="combo-menu" id="ticker-suggest" role="listbox" hidden></div>
            </div>
            <label for="trade-date">分析日期</label>
            <input id="trade-date" type="date">
            <fieldset class="module-fieldset">
              <legend>分析模块</legend>
              <label class="check-option"><input type="checkbox" name="analyst" value="market" checked>市场</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="news" checked>新闻</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="fundamentals" checked>基本面</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="social">社交情绪</label>
            </fieldset>
            <button type="button" id="run-analysis">开始分析</button>
            <div class="status-box" id="analysis-status">等待开始</div>
          </div>
        </section>
        <section class="panel run-panel">
          <div class="panel-header">
            <h3>Agent 团队</h3>
            <p id="current-agent">未开始</p>
          </div>
          <div class="team-board" id="team-board"></div>
        </section>
        <section class="panel timeline-panel">
          <div class="panel-header">
            <h3>过程</h3>
            <p><span id="event-count">0</span> 个事件</p>
          </div>
          <div class="timeline" id="log"><div class="empty-state">等待过程事件</div></div>
        </section>
        <section class="panel report-current-panel">
          <div class="panel-header">
            <h3>当前报告</h3>
            <button class="secondary-button compact-button" type="button" data-open-reports>历史报告</button>
          </div>
          <div class="report-viewer" id="report-preview"><div class="empty-state">等待报告生成</div></div>
        </section>
      </div>
    `;
    const dateInput = qs('#trade-date');
    if (dateInput && !dateInput.value) dateInput.value = todayChinaDate();
    qs('#run-analysis')?.addEventListener('click', startAnalysis);
    qs('[data-open-reports]', root)?.addEventListener('click', () => showView('reports'));
    setupTickerAutocomplete();
    resetTeamBoard(selectedAnalystList());
    renderReportPreview();
  }

  // --- Ticker autocomplete ---------------------------------------------------
  function setupTickerAutocomplete() {
    const input = qs('#ticker');
    const menu = qs('#ticker-suggest');
    if (!input || !menu) return;

    input.addEventListener('input', () => {
      const value = input.value.trim();
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
        setAnalysisTicker(item.code);
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

  function setAnalysisTicker(ticker) {
    const input = qs('#ticker');
    if (input) input.value = ticker;
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
    Object.entries(state.roleStates).forEach(([key, roleState]) => {
      const role = TEAM_ROLES[key];
      if (!role) return;
      const card = document.createElement('div');
      card.className = `role-card ${roleState}`;
      card.innerHTML = '<div><strong></strong><span></span></div><p></p>';
      card.querySelector('strong').textContent = role.label;
      card.querySelector('span').textContent =
        roleState === 'active' ? '进行中' : roleState === 'done' ? '已完成' : '待处理';
      card.querySelector('p').textContent = role.kind;
      board.appendChild(card);
    });
  }

  function markRole(key, nextState) {
    if (!state.roleStates[key]) return;
    if (nextState === 'active') {
      Object.keys(state.roleStates).forEach(name => {
        if (state.roleStates[name] === 'active') state.roleStates[name] = 'done';
      });
    }
    state.roleStates[key] = nextState;
    setText('#current-agent', TEAM_ROLES[key]?.label || '团队协作');
    renderTeamBoard();
  }

  function setRunState(text, mode) {
    state.runState = text;
    const pill = qs('#global-run-state');
    if (pill) {
      pill.textContent = text;
      pill.className = `state-pill ${mode || ''}`.trim();
    }
  }

  function tickEvent() {
    state.eventTotal += 1;
    setText('#event-count', String(state.eventTotal));
  }

  function addCollapsibleLog(title, detail, meta = '', kind = '') {
    tickEvent();
    const log = qs('#log');
    if (!log) return;
    log.querySelector('.empty-state')?.remove();
    const item = document.createElement('article');
    item.className = `event-item ${kind}`.trim();
    const safeDetail = String(detail || '');
    item.innerHTML = '<div class="event-title"><strong></strong><span></span></div><p></p>';
    item.querySelector('strong').textContent = title;
    item.querySelector('span').textContent = meta;
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
    if (state.source) state.source.close();
    state.reports = {};
    state.currentReportSection = '';
    state.streamDone = false;
    state.eventTotal = 0;
    setText('#event-count', '0');
    setText('#current-agent', '连接中');
    const log = qs('#log');
    if (log) log.innerHTML = '<div class="empty-state">等待过程事件</div>';
    resetTeamBoard(selectedAnalystList());
    renderReportPreview();
  }

  async function startAnalysis() {
    resetRunView();
    const button = qs('#run-analysis');
    if (button) button.disabled = true;
    setRunState('连接中', 'running');
    setText('#analysis-status', '连接中');
    if (state.adminToken) {
      try {
        await refreshAdminStatus();
      } catch (err) {
        setRunState('会话同步失败', 'failed');
        setText('#analysis-status', `管理员会话同步失败：${err.message}`);
        if (button) button.disabled = false;
        return;
      }
    }
    const params = new URLSearchParams({
      ticker: qs('#ticker')?.value.trim() || '',
      trade_date: qs('#trade-date')?.value || todayChinaDate(),
      analysts: getSelectedAnalysts()
    });
    identityQuery().forEach((value, key) => params.set(key, value));
    state.source = new EventSource(`/api/events?${params}`);
    ['run_started', 'tool_called', 'agent_message', 'report_section_updated', 'run_completed', 'run_failed'].forEach(name => {
      state.source.addEventListener(name, event => handleAnalysisEvent(name, parseAnalysisEventData(event.data)));
    });
    state.source.onerror = () => {
      if (state.streamDone) return;
      setRunState('连接中断', 'failed');
      setText('#analysis-status', '连接中断或服务端报错');
      if (button) button.disabled = false;
      if (state.source) state.source.close();
    };
  }

  function parseAnalysisEventData(raw) {
    try {
      const parsed = JSON.parse(raw || '{}');
      if (parsed && typeof parsed === 'object' && 'data' in parsed) {
        return parsed.data || {};
      }
      return parsed || {};
    } catch (err) {
      return {
        error_type: 'ParseError',
        message: `无法解析服务端事件：${err.message}`
      };
    }
  }

  function handleAnalysisEvent(event, data) {
    if (event === 'run_started') {
      resetTeamBoard(data.analysts || []);
      setRunState('分析中', 'running');
      setText('#current-agent', '团队启动');
      setText('#analysis-status', `分析中：${data.ticker} / ${data.trade_date}`);
      addCollapsibleLog('任务启动', `${data.ticker} ${(data.analysts || []).join('、')}`, '团队', 'active');
    } else if (event === 'tool_called') {
      setRunState('分析中', 'running');
      addCollapsibleLog('工具调用', `${data.tool} ${JSON.stringify(data.args)}`, '工具', 'active');
    } else if (event === 'agent_message') {
      setRunState('分析中', 'running');
      addCollapsibleLog('Agent 输出', data.content, data.message_type || 'Agent', 'active');
    } else if (event === 'report_section_updated') {
      const roleKey = SECTION_TO_ROLE[data.section];
      if (roleKey) markRole(roleKey, 'done');
      state.reports[data.section] = data.content || '';
      if (!state.currentReportSection) state.currentReportSection = data.section;
      renderReportPreview();
      addCollapsibleLog('报告更新', `${TEAM_ROLES[roleKey]?.label || data.section} 交付了 ${data.section}`, '协作轨迹', 'done');
    } else if (event === 'run_completed') {
      state.streamDone = true;
      Object.keys(state.roleStates).forEach(key => { state.roleStates[key] = 'done'; });
      renderTeamBoard();
      setRunState('分析完成', 'done');
      setText('#current-agent', '已结束');
      setText('#analysis-status', '分析完成，已归档到报告中心');
      addCollapsibleLog('完成', '最终状态已生成，团队分析已结束。', '系统', 'done');
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
      if (state.source) state.source.close();
      loadReportHistory();
    } else if (event === 'run_failed') {
      state.streamDone = true;
      const detail = `${data.error_type || 'Error'}: ${data.message || '未知错误'}`;
      setRunState('分析失败', 'failed');
      setText('#current-agent', '已停止');
      setText('#analysis-status', `分析失败：${detail}`);
      addCollapsibleLog('错误', detail, '系统', 'error');
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
      if (state.source) state.source.close();
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
      panel.innerHTML = renderMarkdown(sectionsMap[section] || '');
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
      const data = await apiJson('/api/reports');
      state.history = data.items || [];
      if (state.historyActiveId && !state.history.some(item => item.id === state.historyActiveId)) {
        state.historyActiveId = null;
        state.historyReport = null;
      }
      renderReportCenter();
    } catch (err) {
      root.innerHTML = `<section class="panel"><div class="panel-body"><div class="empty-state">历史报告加载失败：${escapeHtml(err.message)}</div></div></section>`;
    }
  }

  function historyLabel(item) {
    const date = item.trade_date || (item.created_at || '').slice(0, 10);
    return `${item.ticker || '未知'} · ${date}`;
  }

  function renderReportCenter() {
    const root = qs('#reports-root');
    if (!root) return;
    root.innerHTML = `
      <div class="workspace-grid reports-grid">
        <section class="panel history-panel">
          <div class="panel-header">
            <h3>历史报告</h3>
            <button class="secondary-button compact-button" type="button" id="reload-history">刷新</button>
          </div>
          <div class="history-list" id="history-list"></div>
        </section>
        <section class="panel report-detail-panel">
          <div class="panel-header">
            <h3 id="history-detail-title">报告详情</h3>
            <p id="history-detail-meta">从左侧选择一份历史报告</p>
          </div>
          <div class="report-viewer" id="history-detail"><div class="empty-state">选择左侧报告后查看完整内容</div></div>
        </section>
      </div>
    `;
    qs('#reload-history')?.addEventListener('click', loadReportHistory);
    renderHistoryList();
    if (state.historyReport) renderHistoryDetail();
  }

  function renderHistoryList() {
    const list = qs('#history-list');
    if (!list) return;
    if (!state.history.length) {
      list.innerHTML = '<div class="empty-state">暂无历史报告，完成一次分析后自动归档</div>';
      return;
    }
    list.textContent = '';
    state.history.forEach(item => {
      const row = document.createElement('div');
      row.className = `history-item ${item.id === state.historyActiveId ? 'active' : ''}`.trim();
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'history-open';
      open.innerHTML = '<strong></strong><span></span><small></small>';
      open.querySelector('strong').textContent = historyLabel(item);
      open.querySelector('span').textContent = item.decision || '已归档报告';
      open.querySelector('small').textContent = (item.created_at || '').replace('T', ' ').slice(0, 16);
      open.addEventListener('click', () => openHistoryReport(item.id));

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'icon-button history-delete';
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
      const data = await apiJson(`/api/reports/${id}`);
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
    setText('#history-detail-title', historyLabel(report));
    const analysts = (report.analysts || []).map(key => TEAM_ROLES[key]?.label || key).join('、');
    setText('#history-detail-meta', analysts ? `分析模块：${analysts}` : '完整团队报告');
    buildReportTabs(detail, report.sections || {}, state.historyActiveSection, section => {
      state.historyActiveSection = section;
    });
  }

  async function deleteHistoryReport(id) {
    if (typeof confirm === 'function' && !confirm('确认删除这份历史报告？')) return;
    try {
      await apiJson(`/api/reports/${id}`, { method: 'DELETE' });
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
        <section class="panel">
          <div class="panel-header">
            <h3>后台管理</h3>
            <p>需要管理员登录</p>
          </div>
          <div class="panel-body">
            <p class="helper-text">登录后可配置白名单和模型 API Key。</p>
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
        <section class="panel admin-session-panel">
          <div class="panel-header">
            <h3>后台会话</h3>
            <p>当前浏览器已通过管理员验证</p>
          </div>
          <div class="panel-body admin-session-body">
            <div>
              <span class="state-pill done">已登录</span>
              <p class="helper-text">退出后会清除本地管理员 token，不影响已保存的模型和白名单配置。</p>
            </div>
            <button class="secondary-button" type="button" id="admin-logout">退出登录</button>
          </div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h3>模型配置</h3>
            <p>选择供应商自动带出接口地址，模型下拉选择</p>
          </div>
          <div class="panel-body admin-form">
            <label for="model-provider">供应商</label>
            <select id="model-provider">${providerOptions}</select>
            <label for="model-name">显示名称</label>
            <input id="model-name" value="DeepSeek 默认">
            <label for="model-base-url">Base URL</label>
            <input id="model-base-url" readonly>
            <p class="helper-text" id="model-base-url-hint">官方供应商地址已自动填充。</p>
            <label for="model-quick">快速模型</label>
            <select id="model-quick"></select>
            <label for="model-deep">深度模型</label>
            <select id="model-deep"></select>
            <label for="model-api-key">API Key</label>
            <input id="model-api-key" type="password" autocomplete="off">
            <button type="button" id="save-model-config">保存模型</button>
            <div class="status-box" id="model-status">等待保存</div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h3>白名单</h3>
            <p>控制可访问用户</p>
          </div>
          <div class="panel-body admin-form">
            <label for="wl-email">邮箱</label>
            <input id="wl-email" autocomplete="off">
            <label for="wl-uid">UID</label>
            <input id="wl-uid" autocomplete="off">
            <label for="wl-status">状态</label>
            <select id="wl-status">
              <option value="active">启用</option>
              <option value="pending">待确认</option>
              <option value="blocked">禁用</option>
            </select>
            <label for="wl-limit">每日次数</label>
            <input id="wl-limit" type="number" min="0" value="5">
            <label for="wl-note">备注</label>
            <textarea id="wl-note"></textarea>
            <button type="button" id="save-whitelist">保存白名单</button>
            <div class="status-box" id="whitelist-status">等待保存</div>
          </div>
        </section>
        <section class="panel admin-table-panel">
          <div class="panel-header">
            <h3>当前配置</h3>
            <button class="secondary-button compact-button" type="button" id="reload-admin-data">刷新</button>
          </div>
          <div class="table-wrap" id="admin-data">等待加载</div>
        </section>
      </div>
    `;
    qs('#admin-logout')?.addEventListener('click', logoutAdmin);
    qs('#save-model-config')?.addEventListener('click', saveModelConfig);
    qs('#save-whitelist')?.addEventListener('click', saveWhitelist);
    qs('#reload-admin-data')?.addEventListener('click', loadAdminData);
    qs('#model-provider')?.addEventListener('change', applyProviderPreset);
    applyProviderPreset();
    loadAdminData().catch(err => {
      const dataRoot = qs('#admin-data');
      if (dataRoot) dataRoot.textContent = `加载失败：${err.message}`;
    });
  }

  function fillModelSelect(select, models, allowCustom) {
    if (!select) return;
    select.innerHTML = '';
    models.forEach(model => {
      const option = document.createElement('option');
      option.value = model;
      option.textContent = model;
      select.appendChild(option);
    });
    if (allowCustom || !models.length) {
      // Generic/self-hosted endpoints accept arbitrary model IDs.
      select.innerHTML = '';
      const editable = document.createElement('input');
      editable.id = select.id;
      editable.autocomplete = 'off';
      editable.placeholder = '输入模型名称';
      select.replaceWith(editable);
    }
  }

  function applyProviderPreset() {
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
    fillModelSelect(qs('#model-quick'), preset.quick_models, preset.editable_base_url);
    fillModelSelect(qs('#model-deep'), preset.deep_models, preset.editable_base_url);
    const nameInput = qs('#model-name');
    if (nameInput && !nameInput.dataset.touched) nameInput.value = `${preset.label} 默认`;
    nameInput?.addEventListener('input', () => { nameInput.dataset.touched = 'true'; }, { once: true });
  }

  function logoutAdmin() {
    persistAdminSession('');
    renderAdminWorkspace();
  }

  async function saveModelConfig() {
    try {
      await apiJson('/api/admin/model-configs', {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({
          display_name: qs('#model-name')?.value || '',
          provider: qs('#model-provider')?.value || 'deepseek',
          base_url: qs('#model-base-url')?.value || '',
          quick_model: qs('#model-quick')?.value || '',
          deep_model: qs('#model-deep')?.value || '',
          api_key: qs('#model-api-key')?.value || ''
        })
      });
      setText('#model-status', '模型配置已保存');
      await loadAdminData();
    } catch (err) {
      setText('#model-status', `保存失败：${err.message}`);
    }
  }

  async function saveWhitelist() {
    try {
      await apiJson('/api/admin/whitelist', {
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
      setText('#whitelist-status', '白名单已保存');
      await loadAdminData();
    } catch (err) {
      setText('#whitelist-status', `保存失败：${err.message}`);
    }
  }

  async function loadAdminData() {
    if (!state.adminToken) return;
    const [models, whitelist] = await Promise.all([
      apiJson('/api/admin/model-configs', { headers: adminHeaders() }),
      apiJson('/api/admin/whitelist', { headers: adminHeaders() })
    ]);
    const root = qs('#admin-data');
    if (!root) return;
    root.innerHTML = `
      <h4>模型</h4>
      ${renderAdminModels(models.items || [])}
      <h4>白名单</h4>
      ${renderWhitelist(whitelist.items || [])}
    `;
  }

  function renderAdminModels(items) {
    if (!items.length) return '<div class="empty-state">暂无模型配置</div>';
    return `<table class="data-table"><thead><tr><th>名称</th><th>供应商</th><th>快速模型</th><th>深度模型</th><th>Key</th><th>状态</th></tr></thead><tbody>${
      items.map(item => `<tr><td>${escapeHtml(item.display_name)}</td><td>${escapeHtml(item.provider)}</td><td>${escapeHtml(item.quick_model)}</td><td>${escapeHtml(item.deep_model)}</td><td>${escapeHtml(item.api_key_masked)}</td><td>${item.is_default ? '默认' : '可用'}</td></tr>`).join('')
    }</tbody></table>`;
  }

  function renderWhitelist(items) {
    if (!items.length) return '<div class="empty-state">暂无白名单用户</div>';
    return `<table class="data-table"><thead><tr><th>邮箱</th><th>UID</th><th>状态</th><th>每日次数</th><th>备注</th></tr></thead><tbody>${
      items.map(item => `<tr><td>${escapeHtml(item.email)}</td><td>${escapeHtml(item.uid || '')}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.daily_limit)}</td><td>${escapeHtml(item.note || '')}</td></tr>`).join('')
    }</tbody></table>`;
  }

  function boot() {
    const params = new URLSearchParams(location.search);
    const requestedView = params.get('view') || state.view;
    if (state.identityEmail) {
      const input = qs('#identity-email');
      if (input) input.value = state.identityEmail;
    }
    updateIdentitySummary();
    document.querySelectorAll('.nav-item').forEach(button => {
      button.addEventListener('click', () => showView(button.dataset.view));
    });
    qs('#save-identity')?.addEventListener('click', () => {
      const email = qs('#identity-email')?.value.trim() || '';
      state.identityEmail = email;
      localStorage.setItem('ta_identity_email', email);
      updateIdentitySummary();
    });
    qs('#open-admin-login')?.addEventListener('click', openAdminEntry);
    qs('#close-admin-modal')?.addEventListener('click', closeAdminModal);

    renderAnalysisWorkspace();
    renderReportCenter();
    renderAdminWorkspace();
    refreshAdminStatus().catch(err => {
      setText('#identity-summary', `后台状态读取失败：${err.message}`);
    });
    showView(requestedView);
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
