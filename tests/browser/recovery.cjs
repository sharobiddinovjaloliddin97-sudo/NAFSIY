const { chromium } = require(process.env.NAFSIY_PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const email = `recovery-${Date.now()}@example.com`;
    const api = 'http://127.0.0.1:8102';
    async function post(path, data) {
      const boot = await context.request.get(api+'/api/v1/auth/csrf', { headers: { 'X-CSRF-Request': '1' } });
      const tokens = await boot.json();
      return context.request.post(api+'/api/v1/auth'+path, {data, headers: {'X-CSRF-Token':tokens.auth_csrf_token}});
    }
    assert.equal((await post('/register',{email,password:'Original browser password 123!'})).status(),202);
    await context.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      const response = await route.fetch({url:api+url.pathname+url.search});
      await route.fulfill({response});
    });
    const outbox = async () => (await (await context.request.get(api+'/api/v1/__test/outbox')).json()).filter(x=>x.recipient===email);
    const verify = (await outbox()).find(x=>x.purpose==='verify');
    assert.ok(verify);
    let confirms=0;
    page.on('request',r=>{if(r.url().includes('/confirm')) confirms++;});
    await page.goto('http://127.0.0.1:3000/account-recovery#verify:'+verify.token);
    await page.getByRole('button',{name:'Confirm',exact:true}).waitFor();
    assert.equal(new URL(page.url()).hash,''); assert.equal(confirms,0);
    await page.getByRole('button',{name:'Confirm',exact:true}).click();
    await page.getByRole('status').filter({hasText:'Completed'}).waitFor();
    await page.goto('http://127.0.0.1:3000/account-recovery');
    await page.getByRole('textbox', {name: 'Email', exact: true}).fill(email);
    await page.getByRole('button',{name:'Send instructions'}).click();
    await page.getByRole('status').filter({hasText:'If eligible'}).waitFor();
    const reset = (await outbox()).filter(x=>x.purpose==='reset').at(-1);
    assert.ok(reset);
    await page.goto('http://127.0.0.1:3000/account-recovery#reset:'+reset.token);
    await page.getByLabel('New password').fill('Changed browser password 456!');
    await page.getByRole('button',{name:'Confirm',exact:true}).click();
    await page.getByRole('status').filter({hasText:'Completed'}).waitFor();
    assert.equal((await post('/login',{email,password:'Original browser password 123!'})).status(),401);
    assert.equal((await post('/login',{email,password:'Changed browser password 456!'})).status(),200);
    assert.equal((await post('/recovery/reset/confirm',{token:reset.token,password:'Third browser password 789!'})).status(),400);
    const local = await page.evaluate(()=>JSON.stringify({...localStorage}));
    assert.ok(!local.includes(reset.token)); assert.ok(!local.includes('Changed browser password'));
    await context.close();
    console.log('PASS: browser verification/reset, explicit confirmation, fragment removal, single use, no token/password storage.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
