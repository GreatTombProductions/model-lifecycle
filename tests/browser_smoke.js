'use strict';
// Drives the page as a reader would: search and filters, then reads the cards
// and the capture receipts that back them.
const assert = require('assert');
const { chromium } = require('playwright');

(async () => {
  const base = process.env.SMOKE_BASE;
  const origin = new URL(base).origin;
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const foreign = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (new URL(r.url()).origin !== origin) foreign.push(r.url()); });
  const card = id => page.locator('.card', { has: page.locator('h3', { hasText: new RegExp(`^${id.replace(/\./g, '\\.')}$`) }) });
  try {
    const response = await page.goto(base + `?v=${Date.now()}`, { waitUntil: 'networkidle' });
    assert(response.ok(), `index returned ${response.status()}`);
    await page.waitForSelector('.card');
    const captures = await page.locator('#captures li').allTextContents();
    assert.strictEqual(captures.length, 3, `captures: ${captures.length}`);
    for (const c of captures) assert(/[0-9a-f]{64}/.test(c), `no receipt in: ${c}`);
    assert((await page.textContent('#asof')).includes('DeepSeek (no deprecation page — docs statements only)'));

    await page.fill('#search', 'sonnet-4-5');
    const s45 = card('claude-sonnet-4-5-20250929');
    await s45.waitFor();
    const s45text = await s45.textContent();
    assert(/Deprecated/i.test(s45text) && s45text.includes('claude-sonnet-5-5'), s45text);
    assert(/Nov(ember)? 30, 2026/.test(s45text), s45text);

    await page.fill('#search', 'deepseek-v4-flash');
    const ds = card('deepseek-v4-flash');
    await ds.waitFor();
    const dstext = await ds.textContent();
    assert(dstext.includes('no date published') && dstext.includes('deepseek-flash'), dstext);

    await page.fill('#search', 'mythos-preview');
    const mp = await card('claude-mythos-preview').textContent();
    assert(mp.includes('date to be announced'), mp);

    await page.fill('#search', '');
    await page.click('.filter[data-status="active"]');
    const active = await page.locator('.card h3').allTextContents();
    assert(active.includes('claude-opus-5-5') && !active.includes('claude-sonnet-4-5-20250929'), active.join(','));

    assert.deepStrictEqual(errors, []);
    assert.deepStrictEqual(foreign, [], `third-party requests: ${foreign}`);
    console.log(`Browser smoke passed (${base}): 3 capture receipts, Sonnet 4.5 deprecated with date + replacement, DeepSeek retired without date, Mythos Preview TBA, active filter, no errors, no third-party requests.`);
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
