/* 全站回归：登录后逐页打开，断言无控制台错误 / 无白屏。
 * 用 addInitScript 预置 token，保证受保护页面能正常渲染。
 */
import pw from 'playwright';
const { chromium } = pw;

/* 后端地址：默认 8000，但解包后用别的端口验证时（CAMPUS_API_PORT=8200）
   就不该写死 —— 否则测试自己连错地址、报 ECONNREFUSED，
   看着像"页面坏了"，其实是环境对不上。 */
const API = process.env.CAMPUS_BASE || `http://127.0.0.1:${process.env.CAMPUS_API_PORT || '8000'}`;
const BASE = API + '/app';
/* 沙箱/离线机上的 Chromium 是**预先下载好的固定版本**，而 playwright npm 包
   如果升过版，会去找一个更新的 build 号（比如 chromium_headless_shell-1243），
   机器上没有，直接报 “Executable doesn't exist”——看着像测试挂了，其实是环境对不上。
   所以这里和其它测试脚本统一：显式指向已下载的那份浏览器，
   找不到就退回让 playwright 自己找（用户本机 npx playwright install 过也能跑）。 */
/* ★ 2026-10-22 CI 兼容：原来这里写死了
   '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'。
   但 GitHub runner 的 HOME 是 /home/runner（不是 /root），而且浏览器目录名带版本号，
   playwright 一升级就变 —— 写死 = 「CI 上必然 Executable doesn't exist」，
   报错还会长得像业务代码坏了。现在：只认 CHROME_PATH（本机 run_all_tests.py 会传），
   没传就让 playwright 自己找它刚装好的那一份。 */
const CHROME = process.env.CHROME_PATH || undefined;
const PAGES = ['index.html','plan.html','planner.html','reminder.html','account.html',
               'emotion-chat.html','emotion-quiz.html','emotion-result.html',
               'login.html','scan-confirm.html','feedback.html'];

const noise = (t) => !/favicon|404 \(Not Found\)|Failed to load resource.*favicon/i.test(t);

// 先注册一个临时账号拿 token
const uname = 'reg' + Date.now().toString().slice(-8);
const reg = await fetch(`${BASE.replace('/app','')}/api/auth/register`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: uname, password: 'test123456' }),
});
const { access_token: token } = await reg.json();
console.log(`临时账号 ${uname} 注册成功，token 已获取\n`);

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
// ★ 必须在任何 newPage 之前注入，页面脚本才读得到
await ctx.addInitScript((t) => { try { localStorage.setItem('token', t); } catch {} }, token);

const results = [];
for (const p of PAGES) {
  const page = await ctx.newPage();
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', (e) => errs.push(String(e)));
  let status = 'ok';
  try {
    const resp = await page.goto(`${BASE}/${p}`, { waitUntil: 'networkidle', timeout: 20000 });
    if (!resp || !resp.ok()) status = 'http' + (resp ? resp.status() : '?');
    await page.waitForTimeout(700);
    // 白屏判定：body 文本几乎为空
    const txt = (await page.evaluate(() => document.body.innerText || '')).trim();
    if (txt.length < 10) status = 'blank';
  } catch (e) { status = 'err:' + String(e).slice(0, 50); }

  const real = errs.filter(noise);
  const ok = status === 'ok' && real.length === 0;
  results.push(ok);
  console.log(`  ${ok ? '[PASS]' : '[FAIL]'} ${p.padEnd(22)} status=${status} 控制台错误=${real.length}${real.length ? ' -> ' + real[0].slice(0, 70) : ''}`);
  await page.close();
}

await browser.close();
const pass = results.filter(Boolean).length;
console.log('\n' + '='.repeat(60));
console.log(`  结论: ${pass} 通过 / ${results.length - pass} 失败  （共 ${PAGES.length} 页）`);
console.log('='.repeat(60));
process.exit(pass === results.length ? 0 : 1);
