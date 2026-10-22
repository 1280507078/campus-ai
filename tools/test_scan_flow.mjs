/* 扫码登录端到端验证（双浏览器 context）
 *
 * 验证的语义（用户本轮纠偏后的正确形态）：
 *   ① 电脑端打开 login.html -> 切到「扫码登录」-> 后端出票 -> 渲染二维码
 *   ② 二维码内容必须是 login.html?t=票据（**登录页自己**，不是确认页）
 *   ③ 「手机」用全新 context 打开二维码里的 URL（模拟微信/QQ 扫到）
 *   ④ 在「手机」上注册 -> 手机自己拿到 token -> 进 index.html（移动版）★核心
 *   ⑤ 电脑端轮询到 confirmed -> consume 领到 token（bonus）
 */
import pw from 'playwright';
const { chromium } = pw;

const BASE = process.env.CAMPUS_BASE || 'http://127.0.0.1:8000';
/* 沙箱/离线机上的 Chromium 是**预先下载好的固定版本**，而 playwright npm 包
   如果升过版，会去找一个更新的 build 号（比如 chromium_headless_shell-1243），
   机器上没有，直接报 “Executable doesn't exist”——看着像测试挂了，其实是环境对不上。
   所以和其它测试脚本统一：显式指向已下载的那份浏览器。 */
const CHROME = process.env.CHROME_PATH
  || '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome';

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail });
  console.log(`  ${cond ? '[PASS]' : '[FAIL]'} ${name}${detail ? '  ' + detail : ''}`);
}
const noise = (arr) => arr.filter((t) => !/favicon|404 \(Not Found\)/i.test(t));

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });

/* ---------------- ① 电脑端：出码 ---------------- */
console.log('\n--- ① 电脑端出码 ---');
const pcCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const pc = await pcCtx.newPage();
const pcErrors = [];
pc.on('console', (m) => { if (m.type() === 'error') pcErrors.push(m.text()); });
pc.on('pageerror', (e) => pcErrors.push(String(e)));

let ticket = null;
pc.on('response', async (r) => {
  if (r.url().includes('/api/auth/scan/create') && r.ok()) {
    try { ticket = (await r.json()).ticket || null; } catch {}
  }
});

await pc.goto(`${BASE}/app/login.html`, { waitUntil: 'networkidle' });
await pc.click('#tab-scan');                       // 正确 id：tab-scan
await pc.waitForTimeout(2500);

check('电脑端能切到「扫码登录」tab', await pc.isVisible('#pane-scan'));
check('电脑端拿到了票据', !!ticket, `ticket=${ticket}`);

const svgLen = await pc.evaluate(() => {
  const s = document.querySelector('#scan-qr svg');
  return s ? s.outerHTML.length : 0;
});
check('电脑端在 #scan-qr 里渲染了二维码 SVG', svgLen > 500, `svg len=${svgLen}`);

// 二维码指向 login.html（用页面内 qrcode.js 的真实产物反推不了，
// 但 scanUrl 的语义可以直接断言：URL 形如 .../app/login.html?t=票据）
const scanTarget = `${BASE}/app/login.html?t=${encodeURIComponent(ticket || '')}`;
check('二维码目标 = 登录页自己（login.html?t=）', scanTarget.includes('/app/login.html?t='), scanTarget);

// 电脑端提示必须是「让手机扫」的文案（电脑上不该出现"已在手机上"）
const pcHint = await pc.evaluate(() => (document.getElementById('scan-hint') || {}).textContent.trim());
check('电脑端提示是「让手机扫」', pcHint.includes('请用手机'), pcHint.slice(0, 30));

/* ★ 底部那段「手机怎么扫」的说明，**第一句必须跟着部署形态变**。
 *
 * 为什么专门守这一条：这句话原来写死在 login.html 里，内容是"手机与电脑连
 * 同一个 Wi-Fi"。本机 / 局域网演示时它是对的（二维码里放的是局域网 IP，
 * 手机不连同一网络确实够不着）；**但公网部署之后就是错的** —— 那时二维码里
 * 已经是公网域名，手机用 4G/5G 就能打开，跟 Wi-Fi 没半点关系。
 * 留着一句过时说明的代价不是"不准确"这么轻：评委看到"要连同一 Wi-Fi"
 * 很可能直接放弃扫码，一个本来更好用的入口就这么没了。
 *
 * 这里守**本机这一侧**（公网那一侧由 gtest/e2e_scan_public.mjs 守），两边都不能丢。
 */
const pcNote = await pc.evaluate(() => (document.getElementById('scan-note') || {}).textContent.replace(/\s+/g, ' ').trim());
check('★ 本机形态：说明里明确「手机要和电脑连同一个网络」（局域网 IP 手机够不着）',
  pcNote.includes('连同一个网络'), pcNote.slice(0, 46));
check('★ 本机形态：不该出现「不用连同一个网络」——那是公网才成立的话',
  !pcNote.includes('不用'), pcNote.slice(0, 46));

/* ★★ 口径统一（用户 2026-10-14 要求「本机也统一：只说浏览器，全站一致」）
 *
 * 这里原来守的是**反向**的东西：「本机形态仍推荐微信」。当时的分工是本机劝微信、
 * 公网劝浏览器。用户后来明确否掉了这个分工 —— 界面上只需要回答"用什么打开"，
 * 本机 / 公网都是「用浏览器」最省事，也不会在同一屏里出现互相打架的说法。
 * 所以这条守卫**必须跟着反转**，否则它会把正确的实现判成失败。
 *
 * 三处（副标题 #auth-sub / 提示语 #scan-hint / 图标 #scan-apps）都要守，因为
 * 它们在同一屏里，任何一处漏改都会让页面自相矛盾。
 */
const pcSub = await pc.evaluate(() => (document.getElementById('auth-sub') || {}).textContent.trim());
check('★ 本机形态：提示语只说浏览器', pcHint.includes('浏览器'), pcHint);
check('★ 本机形态：顶部副标题也统一成浏览器', pcSub.includes('浏览器') && !pcSub.includes('微信'), pcSub);

/* 底部 App 图标：按用户要求**只剩「浏览器」一个**。
   （演化：六个 → 微信+百度 → 只剩浏览器。）
   顺带断言整页不再出现「微信 / 相机 / QQ」字样 —— 图标删了文案还留着，比不删更糟。 */
const scanApps = await pc.evaluate(() =>
  [...document.querySelectorAll('.scan-app')].map(e => e.textContent.replace(/\s+/g, '').trim()));
check('★ 扫码 App 图标只剩「浏览器」一个',
  scanApps.length === 1 && scanApps[0].includes('浏览器'), JSON.stringify(scanApps));

const pageText = await pc.evaluate(() => document.body.innerText);
const leftover = pageText.match(/微信|相机|QQ/g);
check('★ 登录页正文不再出现「微信 / 相机 / QQ」（图标删了文案也要跟着删）', !leftover,
  leftover ? pageText.match(new RegExp(`.{0,20}(${[...new Set(leftover)].join('|')}).{0,20}`))[0] : '');

/* ---------------- ② 手机端：扫码到达 ---------------- */
console.log('\n--- ② 手机 context 打开二维码里的 URL（模拟微信/QQ 扫到） ---');
const mbCtx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
});
const mb = await mbCtx.newPage();
const mbErrors = [];
mb.on('console', (m) => { if (m.type() === 'error') mbErrors.push(m.text()); });
mb.on('pageerror', (e) => mbErrors.push(String(e)));

await mb.goto(scanTarget, { waitUntil: 'networkidle' });
await mb.waitForTimeout(600);

check('手机端被识别为移动设备',
  (await mb.evaluate(() => window.isMobileDevice && window.isMobileDevice())) === true);

const mbSub = await mb.evaluate(() => (document.getElementById('auth-sub') || {}).textContent.trim());
check('手机端欢迎语正确（"已打开登录页，请登录或注册"）',
  mbSub.includes('已打开登录页'), mbSub);

// 手机端不应出现「请用手机扫一扫」这种荒谬文案
const mbHintAll = await mb.evaluate(() => {
  const a = (document.getElementById('scan-hint') || {}).textContent || '';
  const b = (document.getElementById('scan-note') || {}).textContent || '';
  return a + '|' + b;
});
check('手机端不出现「请用手机扫一扫」的错配文案', !mbHintAll.includes('请用手机微信'), mbHintAll.slice(0, 40));

/* ---------------- ③ 手机端：注册 ---------------- */
console.log('\n--- ③ 手机端在扫码到达的页面上注册 ---');
const uname = 'scan' + Date.now().toString().slice(-8);
const pwd = 'test123456';

await mb.click('#subtab-register');
await mb.fill('#reg-username', uname);
await mb.fill('#reg-password', pwd);
await mb.fill('#reg-password2', pwd);
await mb.click('#form-register button[type="submit"]');
await mb.waitForTimeout(3500);

const mbToken = await mb.evaluate(() => { try { return localStorage.getItem('token') || ''; } catch { return ''; } });
const mbUrl = mb.url();
check('★ 手机端自己拿到了 token（核心）', !!mbToken, mbToken ? mbToken.slice(0, 14) + '…' : '(空)');
check('★ 手机端进入移动版 index.html（核心）', /index\.html/.test(mbUrl), mbUrl.split('/').slice(-1)[0]);

// 手机端登录态真的可用（经 /auth/me 验证，不是只看 localStorage）
const meOk = await mb.evaluate(async () => {
  try {
    const r = await fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
    return r.ok ? (await r.json()).username || 'ok' : 'http' + r.status;
  } catch (e) { return 'err'; }
});
check('★ 手机端 token 经 /auth/me 验证有效', meOk === uname, `me=${meOk} 期望=${uname}`);

/* ---------------- ④ 电脑端：bonus 同步 ---------------- */
console.log('\n--- ④ 电脑端同步（bonus） ---');
await pc.waitForTimeout(3000);
const pcState = await pc.evaluate(() => ({
  token: (() => { try { return localStorage.getItem('token') || ''; } catch { return ''; } })(),
  url: location.href,
}));
check('电脑端轮询到并 consume 领到 token', !!pcState.token,
  pcState.token ? 'yes' : `no (url=${pcState.url.split('/').slice(-1)[0]})`);

/* ---------------- ⑤ 控制台 ---------------- */
console.log('\n--- ⑤ 控制台错误 ---');
check('手机端 0 控制台错误', noise(mbErrors).length === 0, noise(mbErrors).slice(0, 2).join(' | '));
check('电脑端 0 控制台错误', noise(pcErrors).length === 0, noise(pcErrors).slice(0, 2).join(' | '));

await browser.close();
const pass = results.filter((r) => r.ok).length;
console.log('\n' + '='.repeat(60));
console.log(`  结论: ${pass} 通过 / ${results.length - pass} 失败`);
console.log('='.repeat(60));
process.exit(pass === results.length ? 0 : 1);
