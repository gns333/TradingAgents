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

async function fixture(t) {
  const page = await browser.newPage();
  page.setDefaultTimeout(7000);
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const registrations = [];
  let syncFailures = 0;
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
  await page.goto('http://registration.test/');
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
