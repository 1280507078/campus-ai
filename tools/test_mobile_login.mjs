/* 移动端登录 · 最终验收
 *
 * 走「修复后脚本会打开的地址」（局域网 IP + 前端端口），完整模拟：
 *   ① 电脑端出码（页面地址 = 局域网 IP，所以码里也是局域网 IP）
 *   ② 「手机」打开码里的 URL（模拟微信/QQ 扫到）
 *   ③ 手机上注册 / 登录 -> 拿到 token -> 进移动版
 * 两种模式都测：
 *   A. 双端口（前端 5500）：脚本当前用的方式
 *   B. 单端口（后端 8000 /app）：更稳的推荐方式
 */
import pw from 'playwright';
const { chromium } = pw;
/* 沙箱/离线机上的 Chromium 是**预先下载好的固定版本**，而 playwright npm 包
   如果升过版，会去找一个更新的 build 号（比如 chromium_headless_shell-1243），
   机器上没有，直接报 “Executable doesn't exist”。这里显式指向已下载的那份。 */
/* ★ CI 兼容：原来这里写死了
   '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'。
   但 GitHub runner 的 HOME 是 /home/runner（不是 /root），而且浏览器目录名带版本号，
   playwright 一升级就变 —— 写死 = 「CI 上必然 Executable doesn't exist」，
   报错还会长得像业务代码坏了。现在：只认 CHROME_PATH（本机 run_all_tests.py 会传），
   没传就让 playwright 自己找它刚装好的那一份。 */
const CHROME = process.env.CHROME_PATH || undefined;
// 用法: node tools/test_mobile_login.mjs <局域网IP>   或设 CAMPUS_LAN
const LAN = process.argv[2] || process.env.CAMPUS_LAN;

const results = [];
const check = (n, c, d = '') => {
  results.push(!!c);
  console.log(`  ${c ? '[PASS]' : '[FAIL]'} ${n}${d ? '  ' + d : ''}`);
};

if (!LAN) { console.error('请给出局域网 IP：node tools/test_mobile_login.mjs 192.168.1.5'); process.exit(1); }

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });

async function runMode(tag, pcUrl) {
  console.log(`\n--- ${tag} ---`);
  console.log(`    电脑打开: ${pcUrl}`);

  // ① 电脑端出码
  const pcCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const pc = await pcCtx.newPage();
  let ticket = null;
  pc.on('response', async (r) => {
    if (r.url().includes('/api/auth/scan/create') && r.ok()) {
      try { ticket = (await r.json()).ticket || null; } catch {}
    }
  });
  await pc.goto(pcUrl, { waitUntil: 'networkidle' });
  await pc.click('#tab-scan');
  await pc.waitForTimeout(2600);
  check(`${tag} 电脑端出码`, !!ticket, `ticket=${ticket ? ticket.slice(0, 10) + '…' : 'null'}`);
  if (!ticket) { await pcCtx.close(); return; }

  // 二维码目标（复刻 scanUrl 语义）
  const dir = new URL(pcUrl).pathname.replace(/[^/]*$/, '');
  const target = `${new URL(pcUrl).origin}${dir}login.html?t=${encodeURIComponent(ticket)}`;
  check(`${tag} 二维码是局域网地址（非 127.0.0.1）`,
    !/127\.0\.0\.1|localhost/.test(target) && target.includes(LAN), target);

  // ② 手机打开码里的 URL
  const mbCtx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  });
  const mb = await mbCtx.newPage();
  const mbErrs = [];
  mb.on('pageerror', (e) => mbErrs.push(String(e)));
  await mb.goto(target, { waitUntil: 'networkidle' });
  await mb.waitForTimeout(500);

  check(`${tag} 手机端识别为移动设备`,
    (await mb.evaluate(() => window.isMobileDevice && window.isMobileDevice())) === true);

  // ③ 手机上注册
  const uname = 'm' + Math.random().toString(36).slice(2, 10);
  await mb.click('#subtab-register');
  await mb.fill('#reg-username', uname);
  await mb.fill('#reg-password', 'test123456');
  await mb.fill('#reg-password2', 'test123456');
  await mb.click('#form-register button[type="submit"]');
  await mb.waitForTimeout(3500);

  const tok = await mb.evaluate(() => { try { return localStorage.getItem('token') || ''; } catch { return ''; } });
  const url = mb.url();
  check(`${tag} ★ 手机拿到 token`, !!tok, tok ? tok.slice(0, 14) + '…' : '(空)');
  check(`${tag} ★ 手机进入移动版 index.html`, /index\.html/.test(url), url.split('/').slice(-1)[0]);

  const me = await mb.evaluate(async () => {
    try {
      const r = await fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
      if (r.ok) return (await r.json()).username;
      // 双端口模式下 /api 不在同源，退回 CONFIG
      const base = (typeof CONFIG !== 'undefined') ? CONFIG.API_BASE : '';
      const r2 = await fetch(base + '/auth/me', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
      return r2.ok ? (await r2.json()).username : 'http' + r2.status;
    } catch (e) { return 'err'; }
  });
  check(`${tag} ★ token 经 /auth/me 验证有效`, me === uname, `me=${me} 期望=${uname}`);
  check(`${tag} 手机端无 JS 异常`, mbErrs.length === 0, mbErrs.slice(0, 1).join(''));

  await pcCtx.close();
  await mbCtx.close();
}

await runMode('A. 双端口(5500)', `http://${LAN}:5500/login.html`);
await runMode('B. 单端口(8000/app)', `http://${LAN}:8000/app/login.html`);

await browser.close();
const pass = results.filter(Boolean).length;
console.log('\n' + '='.repeat(62));
console.log(`  结论: ${pass} 通过 / ${results.length - pass} 失败`);
console.log('='.repeat(62));
process.exit(pass === results.length ? 0 : 1);
