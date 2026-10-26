import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
import { createServer } from 'node:http';
import { present } from './fixtures.mjs';

const { chromium }: typeof import('playwright') = await import(process.env.BIRDVIEW_PLAYWRIGHT_PATH
  ? pathToFileURL(process.env.BIRDVIEW_PLAYWRIGHT_PATH).href : 'playwright');
const browser = await chromium.launch();
// Serve the same /birdview/ layout as GitHub Pages, using the canonical demo.
const server = createServer((request, response) => {
  const route = new URL(request.url ?? '/', 'http://localhost').pathname;
  const file = route === '/birdview/demo/harness-activity.html' ? '../examples/harness-activity.html'
    : route.startsWith('/birdview/') && !route.includes('..') ? `../docs/${route.slice('/birdview/'.length) || 'index.html'}` : '';
  if (!file) { response.writeHead(404).end(); return; }
  try {
    response.setHeader('Content-Type', file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'image/png');
    response.end(fs.readFileSync(new URL(file, import.meta.url)));
  } catch { response.writeHead(404).end(); }
});
await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
const address = server.address();
if (!address || typeof address === 'string') throw new Error('Missing test server port');
try {
  const page = await browser.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`http://127.0.0.1:${address.port}/birdview/`);
    await page.locator('#demo-stage').scrollIntoViewIfNeeded();
    for (const lang of ['en', 'zh']) {
      if (lang === 'zh') await page.locator('#language').click();
      assert.equal(await page.locator('html').getAttribute('lang'), lang === 'zh' ? 'zh-CN' : 'en');
      const frame = page.frameLocator('#demo-stage iframe');
      await frame.locator('#language').waitFor();
      await page.waitForFunction(expected => {
        const iframe = document.querySelector<HTMLIFrameElement>('#demo-stage iframe');
        return iframe?.contentDocument?.querySelector<HTMLSelectElement>('#language')?.value === expected;
      }, lang);
      await frame.locator('#nodes .node').first().click();
      assert.equal(await frame.locator('.workspace').evaluate(el => el.classList.contains('inspector-open')), true);
      const popupReady = page.waitForEvent('popup');
      await page.locator('#demo-open').click();
      const popup = await popupReady;
      await popup.waitForLoadState();
      assert.ok(popup.url().endsWith(`/demo/harness-activity.html?lang=${lang}#lang=${lang}`));
      assert.ok(await popup.locator('#nodes .node').count() > 0);
      await popup.close();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      for (const agent of ['codex', 'claude-code', 'deepseek']) {
        await page.locator('#install-agent').selectOption(agent);
        const root = agent === 'deepseek' ? '$HOME/.dsh/skills/birdview' : agent === 'claude-code' ? '$HOME/.claude/skills/birdview' : '$HOME/.agents/skills/birdview';
        const install = present(await page.locator('#install-command').textContent());
        assert.equal(install, agent === 'deepseek' ? `git clone https://github.com/Qiuner/birdview.git "${root}"` : `npx skills add Qiuner/birdview --skill birdview --agent ${agent} --global --copy --yes`);
        assert.equal(await page.locator('#check-command').textContent(), `npm --prefix "${root}" ci\nnode "${root}/scripts/birdview.mjs" doctor`);
        assert.match(present(await page.locator('#install-guide').getAttribute('href')), lang === 'zh' ? /installation\.zh\.md$/ : /installation\.md$/);
        await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
          writeText: async (text: string) => { document.documentElement.setAttribute('data-copied', text); },
        } }));
        await page.locator('[data-copy="install-command"]').click();
        assert.equal(await page.locator('html').getAttribute('data-copied'), install);
        assert.equal(await page.locator('#copy-status').textContent(), lang === 'zh' ? '已复制。' : 'Copied.');
        await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
          writeText: async () => { throw new Error('Permission denied'); },
        } }));
        await page.locator('[data-copy="check-command"]').click();
        assert.match(present(await page.locator('#copy-status').textContent()), lang === 'zh' ? /复制失败/ : /Could not copy/);
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log('Site: desktop/mobile, bilingual embedded/live demo, three agents and clipboard success/failure passed.');
} finally {
  await browser.close();
  server.close();
}
