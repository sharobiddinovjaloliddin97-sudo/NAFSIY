const { chromium } = require(process.env.NAFSIY_PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      try {
        const response = await route.fetch({ url: `http://127.0.0.1:8102${url.pathname}${url.search}` });
        await route.fulfill({ response });
      } catch (error) {
        if (!url.pathname.endsWith('/send')) throw error; // Expected cancellation after erasure.
      }
    });
    const state = async () => (await context.request.get('http://127.0.0.1:8102/api/v1/__test/state')).json();
    const baseline = await state();
    await page.goto('http://localhost:3000');
    await page.locator('.chat-login-notice').waitFor();
    await page.locator('.auth-trigger').click();
    await page.locator('.auth-switch').click();
    await page.locator('.auth-modal input[type=email]').fill(`delete-${Date.now()}@example.com`);
    await page.locator('.auth-modal input[type=password]').fill('Browser fixture password 123!');
    await page.locator('.auth-submit').click();
    await page.locator('.auth-notice').waitFor();
    await page.locator('.auth-modal input[type=password]').fill('Browser fixture password 123!');
    await page.locator('.auth-submit').click();
    await page.waitForFunction(() => document.querySelector('.chat-sync-status')?.textContent.includes('bulutiga'));
    const erase = async (confirmation, csrf = true) => page.evaluate(async ({ confirmation, csrf }) => {
      const bootstrap = await fetch('http://localhost:8000/api/v1/auth/csrf', { credentials: 'include', headers: { 'X-CSRF-Request': '1' } });
      const security = await bootstrap.json();
      const response = await fetch('http://localhost:8000/api/v1/user/account', { method: 'DELETE', credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': security.csrf_token } : {}) },
        body: JSON.stringify({ confirmation, password: 'Browser fixture password 123!' }) });
      if (response.status === 204) window.dispatchEvent(new Event('nafsiy-auth-changed'));
      return response.status;
    }, { confirmation, csrf });
    assert.equal(await erase('DELETE MY ACCOUNT', false), 403);
    assert.equal(await erase('yes'), 422);
    await page.locator('textarea').fill('Slow synthetic deletion request');
    await page.locator('.composer button').click();
    await page.waitForFunction(() => !!localStorage.getItem('nafsiy.pending-ai.v1'));
    for (let i = 0; i < 50 && (await state()).model_calls === baseline.model_calls; i++) await page.waitForTimeout(50);
    assert.equal((await state()).model_calls, baseline.model_calls + 1);
    assert.equal(await erase('DELETE MY ACCOUNT'), 204);
    await page.waitForFunction(() => !localStorage.getItem('nafsiy.pending-ai.v1'));
    await page.reload();
    await page.locator('.chat-login-notice').waitFor();
    await page.waitForTimeout(3200);
    const final = await state();
    assert.equal(final.accounts, baseline.accounts);
    assert.equal(final.messages, baseline.messages);
    assert.equal(final.model_calls, baseline.model_calls + 1);
    assert.deepEqual(errors, []);
    console.log('PASS: authenticated erasure requires CSRF and exact confirmation; active send cannot persist; cookie/session and browser pending state cleared.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
