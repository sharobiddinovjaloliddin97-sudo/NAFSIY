const { chromium } = require(process.env.NAFSIY_PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  let browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    let context = await browser.newContext();
    let page = await context.newPage();
    const sends = [], errors = [];
    let lose = '';
    const key = 'nafsiy.pending-ai.v1';
    const rawState = async () => (await context.request.get('http://127.0.0.1:8102/api/v1/__test/state')).json();
    const baseline = await rawState();
    const state = async () => {
      const value = await rawState();
      return { model_calls: value.model_calls - baseline.model_calls, messages: value.messages - baseline.messages };
    };
    const listen = target => target.on('pageerror', error => errors.push(error.message));
    listen(page);
    async function routeApi(route) {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname.endsWith('/send')) {
        sends.push({ key: request.headers()['idempotency-key'], csrf: request.headers()['x-csrf-token'] });
        const mode = lose;
        lose = '';
        if (mode === 'before') return route.abort('failed');
        if (mode === 'after') {
          await route.fetch({ url: `http://127.0.0.1:8102${url.pathname}` });
          return route.abort('failed');
        }
      }
      const response = await route.fetch({ url: `http://127.0.0.1:8102${url.pathname}${url.search}` });
      await route.fulfill({ response });
    }
    await context.route('**/api/v1/**', routeApi);
    const ready = async () => page.waitForFunction(() => document.querySelector('.chat-sync-status')?.textContent.includes('bulutiga'));
    const pending = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), key);
    const submit = async text => { await page.locator('textarea').fill(text); await page.locator('.composer button').click(); };
    async function register(email) {
      await page.locator('.auth-trigger').click();
      await page.locator('.auth-switch').click();
      await page.locator('.auth-modal input[type=email]').fill(email);
      await page.locator('.auth-modal input[type=password]').fill('Browser fixture password 123!');
      await page.locator('.auth-submit').click();
      await page.locator('.auth-notice').waitFor();
      await page.locator('.auth-modal input[type=password]').fill('Browser fixture password 123!');
      await page.locator('.auth-submit').click();
      await ready();
    }
    await page.goto('http://localhost:3000');
    await page.locator('.chat-login-notice').waitFor();
    await register(`durable-${Date.now()}@example.com`);
    lose = 'after';
    await submit('First private fixture question');
    await page.waitForFunction(() => document.querySelector('textarea').value === 'First private fixture question');
    const saved = await pending();
    assert.deepEqual(Object.keys(saved).sort(), ['createdAt', 'digest', 'key', 'scope', 'sessionId']);
    assert(!JSON.stringify(saved).includes('private fixture'));
    assert(sends[0].csrf && saved.key === sends[0].key);
    await page.reload();
    await ready();
    await page.getByRole('button', { name: 'Oldingi so‘rovni tekshirish', exact: true }).waitFor();
    await page.waitForTimeout(200);
    assert.equal(sends.length, 1);
    await page.getByRole('button', { name: 'Oldingi so‘rovni tekshirish', exact: true }).click();
    await page.waitForFunction(() => !localStorage.getItem('nafsiy.pending-ai.v1'));
    assert.equal((await state()).model_calls, 1);
    assert.equal(sends.length, 1);
    lose = 'before';
    await submit('Second private fixture question');
    await page.waitForFunction(() => document.querySelector('textarea').value === 'Second private fixture question');
    const second = await pending();
    const restoredState = await context.storageState();
    await browser.close();
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    context = await browser.newContext({ storageState: restoredState });
    await context.route('**/api/v1/**', routeApi);
    page = await context.newPage();
    listen(page);
    await page.goto('http://localhost:3000');
    await ready();
    await page.getByRole('button', { name: 'Oldingi so‘rovni tekshirish', exact: true }).waitFor();
    await page.waitForTimeout(200);
    assert.equal(sends.length, 2);
    await page.getByRole('button', { name: 'Oldingi so‘rovni tekshirish', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.error-banner')?.textContent.includes('aynan qayta yozing'));
    const next = page.waitForResponse(response => response.url().endsWith('/send'));
    await submit('Second private fixture question');
    assert.equal((await next).status(), 200);
    await page.waitForFunction(() => !localStorage.getItem('nafsiy.pending-ai.v1'));
    assert.equal(sends.at(-1).key, second.key);
    assert.equal((await state()).model_calls, 2);
    lose = 'before';
    await submit('Third private fixture question');
    await page.waitForFunction(() => document.querySelector('textarea').value === 'Third private fixture question');
    assert(await pending());
    await page.locator('.auth-trigger').click();
    await page.locator('.auth-submit').click();
    await page.waitForFunction(() => !localStorage.getItem('nafsiy.pending-ai.v1'));
    await page.locator('.chat-login-notice').waitFor();
    await register(`durable-other-${Date.now()}@example.com`);
    assert.equal(await pending(), null);
    await page.evaluate(({ key, saved }) => localStorage.setItem(key, JSON.stringify(saved)), { key, saved: second });
    const countBeforeSwitch = sends.length;
    await page.reload();
    await ready();
    await page.waitForFunction(() => !localStorage.getItem('nafsiy.pending-ai.v1'));
    assert.equal(sends.length, countBeforeSwitch);
    assert.equal((await state()).model_calls, 2);
    assert.deepEqual(errors, []);
    console.log('PASS: Durable metadata only; refresh and page restart never auto-send; manual status recovery; same-key manual retry; success/logout/account change clears pending state.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
