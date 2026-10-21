// Run with NODE_PATH pointing to an installation of playwright:
// node --test tests/browser/cloudbase_registration.cjs
// No real accounts, passwords, email messages or cloud mutations are involved.
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

let browser;
before(async () => {
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined, headless: true });
});
after(async () => { await browser?.close(); });

async function fixture(t, options = {}) {
  const page = await browser.newPage({ viewport: options.viewport || { width: 1280, height: 720 } });
  page.setDefaultTimeout(7000);
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const registrations = [];
  let syncFailures = 0;
  // Use the application's system-font fallback deterministically. Remote font
  // availability must not decide whether this isolated UI regression runs.
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, route => route.abort());
  await page.route('http://registration.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/runtime-config') {
      return route.fulfill({ json: { runtime: 'cloudbase', auth: 'cloudbase', env_id: 'test', region: 'ap-shanghai' } });
    }
    if (url.pathname === '/api/register') {
      registrations.push(route.request().headers());
      if (syncFailures-- > 0) return route.fulfill({ status: 503, json: { detail: 'try again' } });
      return route.fulfill({ json: { approval_status: 'pending' } });
    }
    if (options.rich && url.pathname === '/api/reports') {
      return route.fulfill({ json: { items: [{
        id: 1, ticker: '600519.SH', stock_name: '贵州茅台', decision: 'buy',
        trade_date: '2026-10-21', created_at: '2026-10-21T07:00:00Z',
        owner_email: 'a-very-long-research-account@example.com'
      }] } });
    }
    if (options.rich && url.pathname === '/api/reports/1') {
      return route.fulfill({ json: { item: {
        id: 1, ticker: '600519.SH', stock_name: '贵州茅台股份有限公司', decision: 'buy',
        trade_date: '2026-10-21', created_at: '2026-10-21T07:00:00Z', analysts: ['market', 'fundamentals'],
        owner_email: 'a-very-long-research-account@example.com',
        sections: {
          market_report: '## 市场分析\n\n报告正文与表格。\n\n| 指标 | 2025 年 | 2026 年 | 变化 |\n| --- | --- | --- | --- |\n| 现金流 | 1.2 亿元 | 1.3 亿元 | +8.3% |',
          fundamentals_report: '## 基本面分析\n\n测试报告内容。'
        }
      } } });
    }
    if (url.pathname.startsWith('/api/')) return route.fulfill({ json: { items: [] } });
    const file = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/assets\//, '');
    const target = path.join(__dirname, '../../tradingagents/web/static', file);
    if (!fs.existsSync(target)) return route.fulfill({ status: 404, body: '' });
    const contentType = file.endsWith('.js') ? 'application/javascript'
      : file.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({ path: target, contentType });
  });
  await page.addInitScript(() => {
    window.authCalls = [];
    window.existingUser = false;
    window.sdkSignedIn = false;
    window.authErrors = {};
    window.cloudbase = { init: () => ({ auth: () => ({
      getSession: async () => ({ data: {} }),
      getAccessToken: async () => ({ accessToken: window.sdkSignedIn ? 'test-session-token' : '' }),
      getVerification: async params => {
        window.authCalls.push(['send', params]);
        if (window.authErrors.send) return { error: { message: 'mail unavailable' } };
        return { verification_id: 'verification-id', is_user: window.existingUser };
      },
      verify: async params => {
        window.authCalls.push(['verify', params]);
        if (params.verification_code !== '123456') throw new Error('invalid code');
        return { verification_token: 'verified-email-token' };
      },
      signUp: async params => {
        window.authCalls.push(['signup', params]);
        if (window.authErrors.signup) return { error: { message: 'expired verification' } };
        window.sdkSignedIn = true;
        return { user: { uid: 'test-user' } };
      },
      signOut: async () => { window.sdkSignedIn = false; return { error: null }; }
    }) }) };
  });
  await page.goto('http://registration.test/', { waitUntil: 'domcontentloaded' });
  try {
    await page.locator('#cloudbase-auth-modal').waitFor({ state: 'visible' });
  } catch (error) {
    t.diagnostic(JSON.stringify(await page.evaluate(() => ({
      status: document.querySelector('#identity-summary').textContent,
      runtime: window.TradingAgentsWorkbench?.state.runtime
    }))));
    throw error;
  }
  await page.locator('#show-cloudbase-register').click();
  await page.locator('#cloudbase-register-email').fill('new@example.com');
  return { page, registrations, failNextSync: () => { syncFailures = 1; } };
}

async function verifyEmail(page) {
  await page.locator('#cloudbase-send-code').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('验证码已发送'));
  await page.locator('#cloudbase-register-code').fill('123456');
  await page.locator('#cloudbase-verify-email').click();
  await page.locator('#cloudbase-register-password').waitFor({ state: 'visible' });
}

test('send requires only email; verify then set password and submit for review', async t => {
  const { page, registrations } = await fixture(t);
  assert.equal(await page.locator('#cloudbase-register-password').isVisible(), false);
  assert.equal(await page.locator('#cloudbase-sign-up').isVisible(), false);
  await verifyEmail(page);
  assert.equal(await page.locator('#cloudbase-register-code').getAttribute('readonly'), '');
  await page.locator('#cloudbase-register-password').fill('Example123');
  await page.locator('#cloudbase-register-confirm').fill('Example123');
  assert.equal(await page.evaluate(() => Boolean(window.TradingAgentsWorkbench.state.cloudbaseVerification.verificationToken)), true);
  await page.locator('#cloudbase-sign-up').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('已提交管理员审核'));
  const calls = await page.evaluate(() => window.authCalls);
  assert.deepEqual(calls.map(call => call[0]), ['send', 'verify', 'signup']);
  assert.deepEqual(calls[0][1], { email: 'new@example.com' });
  assert.deepEqual(calls[2][1], {
    email: 'new@example.com', password: 'Example123',
    verification_token: 'verified-email-token', verification_code: '123456'
  });
  assert.equal(registrations[0].authorization, 'Bearer test-session-token');
  assert.equal(await page.locator('#cloudbase-register-password').inputValue(), '');
});

test('invalid code cannot reveal password fields or create an account', async t => {
  const { page } = await fixture(t);
  await page.locator('#cloudbase-send-code').click();
  await page.locator('#cloudbase-register-code').fill('000000');
  await page.locator('#cloudbase-verify-email').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('invalid code'));
  assert.equal(await page.locator('#cloudbase-register-password').isVisible(), false);
  assert.equal(await page.evaluate(() => window.authCalls.some(call => call[0] === 'signup')), false);
});

test('changing email invalidates verification, changing passwords does not', async t => {
  const { page } = await fixture(t);
  await verifyEmail(page);
  await page.locator('#cloudbase-register-password').fill('Changed123');
  assert.equal(await page.evaluate(() => Boolean(window.TradingAgentsWorkbench.state.cloudbaseVerification.verificationToken)), true);
  await page.locator('#cloudbase-change-email').click();
  await page.locator('#cloudbase-register-email').fill('other@example.com');
  assert.equal(await page.locator('#cloudbase-register-password').isVisible(), false);
  assert.equal(await page.locator('#cloudbase-register-code').inputValue(), '');
  assert.equal(await page.evaluate(() => window.TradingAgentsWorkbench.state.cloudbaseVerification), null);
});

test('existing accounts are directed to login/recovery, never overwritten', async t => {
  const { page } = await fixture(t);
  await page.evaluate(() => { window.existingUser = true; });
  await page.locator('#cloudbase-send-code').click();
  await page.locator('#cloudbase-register-code').fill('123456');
  await page.locator('#cloudbase-verify-email').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('忘记密码'));
  assert.equal(await page.locator('#cloudbase-register-password').isVisible(), false);
  assert.equal(await page.evaluate(() => window.authCalls.some(call => call[0] === 'signup')), false);
});

test('failed sync retries without registering the account twice', async t => {
  const { page, failNextSync } = await fixture(t);
  await verifyEmail(page);
  await page.locator('#cloudbase-register-password').fill('Example123');
  await page.locator('#cloudbase-register-confirm').fill('Example123');
  failNextSync();
  await page.locator('#cloudbase-sign-up').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('try again'));
  await page.locator('#cloudbase-sign-up').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('已提交管理员审核'));
  assert.equal(await page.evaluate(() => window.authCalls.filter(call => call[0] === 'signup').length), 1);
});

test('weak passwords are validated only in the second step', async t => {
  const { page } = await fixture(t);
  await verifyEmail(page);
  await page.locator('#cloudbase-register-password').fill('abcdefgh');
  await page.locator('#cloudbase-register-confirm').fill('abcdefgh');
  await page.locator('#cloudbase-sign-up').click();
  assert.match(await page.locator('#cloudbase-register-password-error').textContent(), /字母和数字/);
  assert.equal(await page.evaluate(() => window.authCalls.some(call => call[0] === 'signup')), false);
});

test('delivery errors are visible and allow retry', async t => {
  const { page } = await fixture(t);
  await page.evaluate(() => { window.authErrors.send = true; });
  await page.locator('#cloudbase-send-code').click();
  await page.waitForFunction(() => document.querySelector('#cloudbase-auth-status').textContent.includes('mail unavailable'));
  assert.equal(await page.locator('#cloudbase-send-code').isEnabled(), true);
  await page.evaluate(() => { window.authErrors.send = false; });
  await verifyEmail(page);
});

test('late delivery responses cannot restore verification after an email change', async t => {
  const { page } = await fixture(t);
  const result = await page.evaluate(async () => {
    const app = window.TradingAgentsWorkbench;
    let complete;
    app.state.cloudbaseAuth.getVerification = () => new Promise(resolve => { complete = resolve; });
    const sending = app.requestCloudBaseRegistrationCode('new@example.com');
    document.querySelector('#cloudbase-register-email').value = 'other@example.com';
    document.querySelector('#cloudbase-register-email').dispatchEvent(new Event('input'));
    complete({ verification_id: 'stale-id' });
    try { await sending; } catch (error) { return [app.state.cloudbaseVerification, error.message]; }
  });
  assert.equal(result[0], null);
  assert.match(result[1], /邮箱已更改/);
});

test('double submit creates at most one account', async t => {
  const { page } = await fixture(t);
  await verifyEmail(page);
  await page.locator('#cloudbase-register-password').fill('Example123');
  await page.locator('#cloudbase-register-confirm').fill('Example123');
  await page.evaluate(async () => {
    const app = window.TradingAgentsWorkbench;
    await Promise.all([app.submitCloudBaseRegistration(), app.submitCloudBaseRegistration()]);
  });
  assert.equal(await page.evaluate(() => window.authCalls.filter(call => call[0] === 'signup').length), 1);
});

test('UI registration password step fits a regular laptop without an inner scrollbar', async t => {
  const { page } = await fixture(t, { viewport: { width: 1366, height: 768 } });
  await verifyEmail(page);
  const metrics = await page.locator('#cloudbase-auth-panel').evaluate(node => ({
    scrollHeight: node.scrollHeight, clientHeight: node.clientHeight,
    top: node.getBoundingClientRect().top, bottom: node.getBoundingClientRect().bottom
  }));
  if (process.env.UI_SCREENSHOTS_DIR) {
    await page.screenshot({ path: path.join(process.env.UI_SCREENSHOTS_DIR, 'registration-desktop.png'), animations: 'disabled' });
  }
  t.diagnostic(JSON.stringify(metrics));
  assert.ok(metrics.scrollHeight <= metrics.clientHeight + 1, 'password step should not scroll inside the dialog');
  assert.ok(metrics.top >= 0 && metrics.bottom <= 768, 'dialog must fit the viewport');
});

test('UI dialog traps keyboard focus and restores the opener on close', async t => {
  const { page } = await fixture(t);
  await verifyEmail(page);
  await page.locator('#cloudbase-register-confirm').focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.querySelector('#cloudbase-auth-modal').contains(document.activeElement)), true);
  await page.locator('#close-cloudbase-auth-modal').focus();
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => document.querySelector('#cloudbase-auth-modal').contains(document.activeElement)), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#app-shell').evaluate(node => node.inert), false);
  await page.locator('#open-identity-modal').click();
  await page.locator('#close-cloudbase-auth-modal').click();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'open-identity-modal');
});

for (const viewport of [
  { width: 1440, height: 900 }, { width: 1280, height: 720 },
  { width: 1100, height: 768 }, { width: 768, height: 1024 },
  { width: 390, height: 844 }, { width: 320, height: 568 }
]) {
  test(`UI surfaces fit ${viewport.width}x${viewport.height}`, async t => {
    const { page } = await fixture(t, { viewport, rich: true });
    await verifyEmail(page);
    assert.equal(await page.locator('#cloudbase-register-verification').isVisible(), false);
    assert.equal(await page.locator('#cloudbase-verified-email').textContent(), 'new@example.com');
    const modal = await page.locator('#cloudbase-auth-panel').evaluate(node => ({
      scroll: node.scrollHeight, height: node.clientHeight
    }));
    assert.ok(modal.scroll <= modal.height + 1, 'dialog itself must never be a second scroll container');
    await page.locator('#cloudbase-register-confirm').fill('Example123');
    assert.equal(await page.locator('#cloudbase-sign-up').isVisible(), true);
    await page.locator('#close-cloudbase-auth-modal').click();
    await page.evaluate(() => {
      const app = window.TradingAgentsWorkbench;
      app.state.currentUser = { uid: 'test', email: 'admin@example.com', is_admin: true };
      document.querySelector('[data-view="admin"]').hidden = false;
    });
    const assertNoPageOverflow = async view => {
      const layout = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }));
      assert.ok(layout.scroll <= layout.width + 1, `${view} overflows: ${JSON.stringify(layout)}`);
      if (process.env.UI_SCREENSHOTS_DIR) {
        await page.screenshot({ path: path.join(process.env.UI_SCREENSHOTS_DIR, `${view}-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
      }
    };
    await assertNoPageOverflow('analysis');
    if (viewport.width <= 1280) {
      const widths = await page.evaluate(() => ({
        report: document.querySelector('.report-current-panel').getBoundingClientRect().width,
        grid: document.querySelector('.analysis-grid').getBoundingClientRect().width
      }));
      assert.ok(widths.report >= widths.grid - 2, 'report should fill the grid when the timeline moves below it');
    }
    await page.locator('[data-view="reports"]').click();
    await page.locator('.history-open').waitFor();
    await assertNoPageOverflow('history');
    const badgeFits = await page.locator('.history-item').evaluate(row => {
      const badge = row.querySelector('.decision-badge').getBoundingClientRect();
      const bounds = row.getBoundingClientRect();
      return badge.right <= bounds.right && badge.left >= bounds.left;
    });
    assert.equal(badgeFits, true);
    await page.locator('.history-open').click();
    await page.locator('#history-detail .report-tab').first().waitFor();
    await assertNoPageOverflow('report');
    await page.locator('#history-detail .report-tab').first().focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#history-detail .report-tab[aria-selected="true"]').count(), 1);
    assert.match(await page.locator('#history-detail .report-tab[aria-selected="true"]').textContent(), /基本面/);
    await page.locator('[data-view="admin"]').click();
    await page.locator('#save-model-config').waitFor();
    const navContrast = await page.locator('[data-view="admin"]').evaluate(button => {
      const style = getComputedStyle(button);
      const parse = color => color.match(/[\d.]+/g).map(Number);
      const foreground = parse(style.color);
      const background = parse(style.backgroundColor);
      const surface = parse(getComputedStyle(button.closest('.sidebar')).backgroundColor);
      const alpha = background[3] ?? 1;
      const blended = background.slice(0, 3).map((value, index) => value * alpha + surface[index] * (1 - alpha));
      const luminance = color => color.slice(0, 3).map(value => {
        const channel = value / 255;
        return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
      const a = luminance(foreground);
      const b = luminance(blended);
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05) >= 3;
    });
    assert.equal(navContrast, true, 'navigation hover must not override its selected-state contrast');
    await assertNoPageOverflow('admin');
    const actionFits = await page.locator('.admin-pane.active .admin-editor-actions').evaluate(node => {
      const bounds = node.getBoundingClientRect();
      return [...node.querySelectorAll('button')].filter(button => button.getClientRects().length)
        .every(button => button.getBoundingClientRect().right <= bounds.right + 1);
    });
    assert.equal(actionFits, true, 'admin actions should wrap inside their panel');
    await page.locator('#theme-toggle').click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    await assertNoPageOverflow('admin-light');
  });
}

async function openExportReport(t) {
  const { page } = await fixture(t, { rich: true });
  await page.locator('#close-cloudbase-auth-modal').click();
  await page.evaluate(() => {
    window.sdkSignedIn = true;
    window.TradingAgentsWorkbench.state.accessToken = 'stale-token';
    window.TradingAgentsWorkbench.state.currentUser = { uid: 'test', is_admin: false };
  });
  await page.locator('[data-view="reports"]').click();
  await page.locator('.history-open').waitFor();
  assert.equal(await page.locator('#history-export-pdf').isDisabled(), true);
  await page.locator('.history-open').click();
  await page.waitForFunction(() => !document.querySelector('#history-export-pdf').disabled);
  return page;
}

test('PDF full report and active chapter download with refreshed authentication', async t => {
  const page = await openExportReport(t);
  const requests = [];
  await page.route('**/api/reports/1/pdf*', async route => {
    requests.push({ url: route.request().url(), headers: route.request().headers() });
    await route.fulfill({
      contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF',
      headers: { 'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent('贵州茅台-600519.SH-2026-10-21.pdf')}` }
    });
  });
  let downloading = page.waitForEvent('download');
  await page.locator('#history-export-pdf').click();
  const download = await downloading;
  assert.equal(download.suggestedFilename(), '贵州茅台-600519.SH-2026-10-21.pdf');
  await page.waitForFunction(() => !document.querySelector('#history-export-pdf').disabled);
  assert.equal(new URL(requests[0].url).searchParams.has('section'), false);
  assert.equal(requests[0].headers.authorization, 'Bearer test-session-token');
  await page.locator('#history-export-scope').selectOption('current');
  await page.locator('#history-detail [data-section="fundamentals_report"]').click();
  downloading = page.waitForEvent('download');
  await page.locator('#history-export-pdf').click();
  await downloading;
  assert.equal(new URL(requests[1].url).searchParams.get('section'), 'fundamentals_report');
});

test('PDF generation shows progress, prevents duplicate requests, and recovers from 429', async t => {
  const page = await openExportReport(t);
  let release;
  let count = 0;
  await page.route('**/api/reports/1/pdf*', async route => {
    count += 1;
    await new Promise(resolve => { release = resolve; });
    await route.fulfill({ status: 429, json: { detail: '另一个 PDF 正在生成，请稍后重试' } });
  });
  await page.locator('#history-export-pdf').click();
  await page.waitForFunction(() => document.querySelector('#history-export-pdf').textContent.includes('正在生成'));
  assert.equal(await page.locator('#history-export-scope').isDisabled(), true);
  await page.locator('#history-export-pdf').evaluate(button => button.click());
  assert.equal(count, 1);
  release();
  await page.waitForFunction(() => document.querySelector('#history-export-status').textContent.includes('稍后重试'));
  assert.equal(await page.locator('#history-export-pdf').isEnabled(), true);
  assert.equal(await page.locator('#history-export-scope').isEnabled(), true);
});

test('PDF errors never download an HTML gateway response or unauthenticated JSON', async t => {
  const page = await openExportReport(t);
  const downloads = [];
  page.on('download', download => downloads.push(download));
  let unauthorized = true;
  await page.route('**/api/reports/1/pdf*', route => unauthorized
    ? route.fulfill({ status: 401, json: { detail: { message: '请先登录' } } })
    : route.fulfill({ contentType: 'text/html', body: '<h1>gateway error</h1>' }));
  await page.locator('#history-export-pdf').click();
  await page.waitForFunction(() => document.querySelector('#history-export-status').textContent.includes('请先登录'));
  unauthorized = false;
  await page.locator('#history-export-pdf').click();
  await page.waitForFunction(() => document.querySelector('#history-export-status').textContent.includes('有效的 PDF'));
  assert.equal(downloads.length, 0);
  assert.equal(await page.locator('#history-export-pdf').isEnabled(), true);
});
