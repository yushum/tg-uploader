// Run: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node --test frontend/tests/live-studio.test.mjs
// Uses a local static server and mocked APIs. Never starts a real Telegram stream.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const rows = [
  {message_id: 11, streamer: '测试主播', date: '2026-08-01', label: '2026-08-01 20:00 P1', duration: 3600, available: true},
  {message_id: 12, streamer: '测试主播', date: '2026-08-01', label: '2026-08-01 20:00 P2', duration: 1800, available: true},
  {message_id: 13, streamer: '测试主播', date: '2026-08-02', label: '2026-08-02 20:00 P1', duration: 0, available: false},
];
test('live studio: selection, draft, ordering, status, errors and responsive layout', async () => {
  const server = createServer(async (req, res) => {
    try {
      const path = new URL(req.url, 'http://localhost').pathname;
      const file = path.startsWith('/static/') ? `${root}../web/${path.slice(8)}` : `${root}${path === '/' ? 'index.html' : path.slice(1)}`;
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
      res.end(await readFile(file));
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block', reducedMotion: 'reduce'});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let status = {status: 'IDLE', error: ''}, starts = [], failStop = false, failSearch = false, candidates = rows;
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let data = [];
      if (path === '/api/streamers') data = [{name: '测试主播', session_count: 2, part_count: 3, dates: []}];
      if (path === '/api/live/status') data = status;
      if (path === '/api/live/candidates') {
        if (failSearch) return route.fulfill({status: 503, json: {detail: 'unavailable'}});
        data = candidates;
      }
      if (path === '/api/live/start') {
        starts.push(route.request().postDataJSON());
        status = {status: 'STREAMING', channel: '@test', current: rows[1].label, index: 1, total: 2, error: ''};
        data = {count: 2};
      }
      if (path === '/api/live/stop') {
        if (failStop) return route.fulfill({status: 500, json: {detail: '停止服务暂不可用'}});
        status = {status: 'IDLE', error: ''}; data = {ok: true};
      }
      await route.fulfill({json: data});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.locator('#liveFab').click();
    assert.equal(await page.locator('#liveSheet').evaluate(el => el.open), true);
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent === '准备就绪');
    assert.equal(await page.locator('#liveStartBtn').isDisabled(), true);
    await page.locator('#liveFrom').fill('2026-08-03');
    await page.locator('#liveTo').fill('2026-08-01');
    await page.locator('#liveSearch').click();
    assert.equal(await page.locator('#liveTo').evaluate(el => el.validity.customError), true);
    await page.locator('#liveFrom').fill('');
    await page.locator('#liveTo').fill('');
    await page.locator('#liveSearch').click();
    await page.waitForSelector('.live-result');
    assert.equal(await page.locator('.live-result input:disabled').count(), 1);
    await page.locator('[data-mid="11"]').check();
    assert.equal(await page.locator('.live-day-head input').first().evaluate(el => el.indeterminate), true);
    await page.locator('#liveResultsAll').click();
    assert.equal(await page.locator('#liveCount').textContent(), '2');
    await page.locator('[data-move-mid="12"][data-direction="-1"]').click();
    assert.deepEqual(await page.evaluate(() => [...window.__live.selected.keys()]), [12, 11]);
    await page.locator('#liveChannel').fill('@test');
    await page.locator('#liveChannel').blur();
    await page.screenshot({path: '/tmp/replay-studio-desktop.png'});
    await page.reload();
    await page.locator('#liveFab').click();
    await page.waitForFunction(() => !document.querySelector('#liveStartBtn').disabled);
    assert.deepEqual(await page.evaluate(() => [...window.__live.selected.keys()]), [12, 11]);
    await page.locator('#liveStartBtn').click();
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent.includes('正在推流'));
    assert.deepEqual(starts, [{channel: '@test', message_ids: [12, 11], mode: 'once'}]);
    assert.equal(await page.locator('#liveChannel').isDisabled(), true);
    failStop = true;
    await page.locator('#liveStopBtn').click();
    await page.waitForFunction(() => document.querySelector('#liveFeedback').textContent.includes('停止失败'));
    assert.match(await page.locator('#liveStatus').textContent(), /正在推流/);
    failStop = false;
    await page.locator('#liveStopBtn').click();
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent === '准备就绪');
    status = {status: 'IDLE', error: '目标频道无效'};
    await page.locator('#liveSheetClose').click();
    await page.locator('#liveFab').click();
    await page.waitForFunction(() => document.querySelector('#liveCurrent').textContent.includes('目标频道无效'));
    failSearch = true;
    await page.locator('#liveSearch').click();
    await page.waitForFunction(() => document.querySelector('#liveResultSummary').textContent === '查找失败');
    assert.equal(await page.locator('#liveResultsAll').isDisabled(), true);
    failSearch = false; candidates = [];
    await page.locator('#liveSearch').click();
    await page.waitForFunction(() => document.querySelector('#liveResults').textContent.includes('没有找到录像'));
    assert.equal(await page.locator('#liveCount').textContent(), '2');
    candidates = rows;
    await page.locator('#liveSearch').click();
    await page.waitForSelector('.live-result');
    for (const width of [390, 320, 768, 1440]) {
      await page.setViewportSize({width, height: 844});
      const overflow = await page.locator('#liveSheet').evaluate(el => el.scrollWidth > el.clientWidth);
      assert.equal(overflow, false, `no horizontal overflow at ${width}px`);
      if (width <= 720) {
        await page.locator('[data-live-view="queue"]').click();
        assert.equal(await page.locator('[data-live-view="queue"]').getAttribute('aria-pressed'), 'true');
        assert.equal(await page.locator('[data-live-view="library"]').getAttribute('aria-pressed'), 'false');
        assert.equal(await page.locator('.live-library').isVisible(), false);
        assert.equal(await page.locator('#liveStartBtn').isVisible(), true);
        await page.screenshot({path: `/tmp/replay-studio-mobile-${width}.png`});
        await page.locator('[data-live-view="library"]').click();
      }
    }
    await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
    await page.screenshot({path: '/tmp/replay-studio-dark.png'});
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#liveClear').click();
    assert.equal(await page.locator('#liveCount').textContent(), '2');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#liveClear').click();
    assert.equal(await page.locator('#liveCount').textContent(), '0');
    assert.equal(await page.locator('#liveStartBtn').isDisabled(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#liveSheet').evaluate(el => el.open), false);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'liveFab');
    assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    assert.deepEqual(errors, []);
    await context.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});

test('live studio: cancel search, unverified rows, play modes', async () => {
  const server = createServer(async (req, res) => {
    try {
      const path = new URL(req.url, 'http://localhost').pathname;
      const file = path.startsWith('/static/') ? `${root}../web/${path.slice(8)}` : `${root}${path === '/' ? 'index.html' : path.slice(1)}`;
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
      res.end(await readFile(file));
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block', reducedMotion: 'reduce'});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let status = {status: 'IDLE', error: ''}, starts = [], candidatesDelay = 0;
    let candidates = [{message_id: 21, streamer: '测试主播', date: '2026-08-01', label: '2026-08-01 20:00 P1', duration: 0, available: true, verified: false}];
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let data = [];
      if (path === '/api/streamers') data = [{name: '测试主播', session_count: 1, part_count: 1, dates: []}];
      if (path === '/api/live/status') data = status;
      if (path === '/api/live/candidates') {
        if (candidatesDelay) await new Promise(r => setTimeout(r, candidatesDelay));
        data = candidates;
      }
      if (path === '/api/live/start') {
        const body = route.request().postDataJSON();
        starts.push(body);
        status = {status: 'STREAMING', channel: body.channel, current: '2026-08-01 20:00 P1', index: 1, total: 1, mode: body.mode, round: 3, error: ''};
        data = {count: 1, mode: body.mode};
      }
      if (path === '/api/live/stop') { status = {status: 'IDLE', error: ''}; data = {ok: true}; }
      await route.fulfill({json: data});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.locator('#liveFab').click();
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent === '准备就绪');
    // 播放模式默认单次，选择后持久化，刷新不丢失。
    assert.equal(await page.locator('input[name="liveMode"]:checked').getAttribute('value'), 'once');
    await page.locator('.live-mode label:has(input[value="shuffle"])').click();
    assert.equal(await page.evaluate(() => localStorage.getItem('live-play-mode')), 'shuffle');
    await page.reload();
    await page.locator('#liveFab').click();
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent === '准备就绪');
    assert.equal(await page.locator('input[name="liveMode"]:checked').getAttribute('value'), 'shuffle');
    // 慢请求可取消：按钮变成取消键，点后状态恢复。
    candidatesDelay = 3000;
    await page.locator('#liveSearch').click();
    await page.waitForFunction(() => document.querySelector('#liveSearch').textContent === '取消查找');
    await page.locator('#liveSearch').click();
    await page.waitForFunction(() => document.querySelector('#liveResultSummary').textContent === '已取消查找');
    assert.equal(await page.locator('#liveSearch').textContent(), '查找录像');
    // 未核验行显示待确认但可选。
    candidatesDelay = 0;
    await page.locator('#liveSearch').click();
    await page.waitForSelector('.live-result');
    assert.equal(await page.locator('.live-result small').textContent(), '待确认');
    assert.equal(await page.locator('.live-result input:disabled').count(), 0);
    assert.match(await page.locator('#liveResultSummary').textContent(), /待确认/);
    // 随机模式开播：请求带 mode，状态显示模式与轮次，开播中锁定模式选择。
    await page.locator('[data-mid="21"]').check();
    await page.locator('#liveChannel').fill('@test');
    await page.locator('#liveStartBtn').click();
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent.includes('随机'));
    assert.deepEqual(starts, [{channel: '@test', message_ids: [21], mode: 'shuffle'}]);
    assert.match(await page.locator('#liveProgressText').textContent(), /第3轮 1 \/ 1/);
    assert.equal(await page.locator('input[name="liveMode"]').first().isDisabled(), true);
    await page.locator('#liveStopBtn').click();
    await page.waitForFunction(() => document.querySelector('#liveStatus').textContent === '准备就绪');
    assert.equal(await page.locator('input[name="liveMode"]').first().isDisabled(), false);
    assert.deepEqual(errors, []);
    await context.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
