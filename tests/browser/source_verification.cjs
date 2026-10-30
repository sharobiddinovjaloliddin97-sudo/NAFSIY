const { chromium } = require(process.env.NAFSIY_PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [], reads = [], sends = [];
    page.on('pageerror', error => errors.push(error.message));
    const surah = { number: 1, name: 'Fixture', englishName: 'Fixture surah', numberOfAyahs: 7 };
    await context.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/v1/quran/surahs') return route.fulfill({ json: [surah] });
      if (url.pathname === '/api/v1/quran/surahs/1') return route.fulfill({ json: { ...surah,
        ayahs: Array.from({ length: 7 }, (_, i) => ({ numberInSurah: i + 1, text: `Synthetic source ${i + 1}` })) } });
      if (url.pathname === '/api/v1/quran/surahs/1/read') {
        reads.push(url.searchParams.get('edition'));
        assert.equal(url.searchParams.get('edition'), 'en.itani');
        return route.fulfill({ json: { ...surah, ayahs: Array.from({ length: 7 }, (_, i) => ({ number: i + 1, translation: `Synthetic translation ${i + 1}` })) } });
      }
      if (url.pathname.endsWith('/send')) sends.push(route.request().headers());
      const response = await route.fetch({ url: `http://127.0.0.1:8102${url.pathname}${url.search}` });
      await route.fulfill({ response });
    });
    await page.goto('http://localhost:3000');
    await page.locator('.chat-login-notice').waitFor();
    await page.locator('.auth-trigger').click();
    await page.locator('.auth-switch').click();
    await page.locator('.auth-modal input[type=email]').fill(`sources-${Date.now()}@example.com`);
    await page.locator('.auth-modal input[type=password]').fill('Browser fixture password 123!');
    await page.locator('.auth-submit').click();
    await page.locator('.auth-notice').waitFor();
    await page.locator('.auth-modal input[type=password]').fill('Browser fixture password 123!');
    await page.locator('.auth-submit').click();
    const ready = () => page.waitForFunction(() => document.querySelector('.chat-sync-status')?.textContent.includes('bulutiga'));
    await ready();
    const send = async text => {
      const response = page.waitForResponse(r => r.url().endsWith('/send'));
      await page.locator('textarea').fill(text);
      await page.locator('.composer button').click();
      assert.equal((await response).status(), 200);
      await page.waitForFunction(() => !localStorage.getItem('nafsiy.pending-ai.v1'));
    };
    await send('Source metadata fixture');
    assert.match(await page.locator('.source-chip').innerText(), /en\.itani/);
    assert.match(await page.locator('.source-chip').innerText(), /ClearQuran/);
    assert.match(await page.locator('.message-ai .message-body').last().innerText(), /unverified/);
    await page.reload();
    await ready();
    assert.match(await page.locator('.source-chip').innerText(), /en\.itani/);
    await send('Unsupported quotation fixture');
    const last = page.locator('.message-ai .message-body').last();
    assert.match(await last.innerText(), /withheld/);
    assert.equal(await last.locator('.source-chip').count(), 0);
    assert(!await last.innerText().then(value => value.includes('Invented quotation')));
    await send('Source quotation fixture');
    const quotation = page.locator('.message-ai .message-body').last();
    assert.match(await quotation.locator('.source-chip').innerText(), /Iqtibos manba matniga mos/);
    assert.match(await quotation.locator('pre code').innerText(), /Synthetic browser source text\./);
    const registry = await context.request.get('http://127.0.0.1:8102/api/v1/quran/sources');
    const entries = (await registry.json()).sources;
    assert.equal(entries.find(x => x.id === 'tafsiri-hilol').license_state, 'restricted');
    assert.equal(entries.find(x => x.id === 'uz.sodik').license_state, 'permission_pending');
    assert.equal((await context.request.get('http://127.0.0.1:8102/api/v1/quran/surahs/1/read?language=uz&edition=uz.sodik')).status(), 503);
    await page.locator('.nav-link').filter({ hasText: "Qur'on" }).click();
    await page.getByText('Fixture surah', { exact: true }).click();
    const language = page.locator('.quran-toolbar select').first();
    await language.waitFor();
    assert(await language.locator('option[value=uz]').evaluate(option => option.disabled));
    assert(await language.locator('option[value=ru]').evaluate(option => option.disabled));
    await language.selectOption('en');
    await page.getByText('Synthetic translation 1', { exact: true }).waitFor();
    assert.deepEqual(reads, ['en.itani']);
    assert(await page.getByRole('link', { name: /Translation by Talal Itani/ }).count() > 0);
    assert(sends.every(headers => headers['idempotency-key'] && headers['x-csrf-token']));
    assert.deepEqual(errors, []);
    console.log('PASS: Source attribution/status persists after refresh; unsupported quotations withheld; restricted editions blocked; reviewed English reader attribution; CSRF/idempotency preserved.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
