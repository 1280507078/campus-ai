/* 鉴权 token 传递的契约测试。
 *
 * ============================================================
 * 为什么单独立这条测试（它不是"锦上添花"，是出现过问题之后的防线）
 * ============================================================
 * ：公网部署之后问题记录：「一直登录不了」，前端一直弹
 * 「登录状态已过期，请重新登录」。
 *
 * 排查结论：**托管平台的网关会改写 / 注入 `Authorization` 头**。
 * 证据（curl 打线上域名）：
 *   · 客户端**什么都不发**          -> 后端却收到一个解不开的 token；
 *   · 客户端发 `Authorization: Basic abcdef` -> 后端照样按「解不开 token」处理，
 *     而按后端代码，Basic 开头本该落到「你还没有登录」。
 * 两件事都说明客户端发的那一版头根本没到。后果是所有需要登录的接口一律 401，
 * 前端清 token 跳登录页，重新登录也还是 401 —— 永远进不去。
 *
 * 修法：另起一个网关不会碰的头 `X-Campus-Token` 并优先读它，`Authorization`
 * 降级为回退（本机 / 局域网 / 第三方客户端照旧）。
 *
 * 这条测试守的就是这个契约。★ 核心是那条「坏 Authorization + 好自定义头」——
 * 它精确复现了线上那个死循环场景，是本文件存在的理由。
 *
 * 分两半：
 *   A. 源码守卫（不需要服务）：改了实现没同步改测试就会失败的静态断言；
 *   B. 活服务契约（需要后端在跑）：真的发请求，验两个头都能认证、且优先级正确。
 *
 * 用法：
 *   node tools/test_auth_token.mjs
 *   CAMPUS_BASE=http://127.0.0.1:8210 node tools/test_auth_token.mjs
 *
 * ⚠️ B 段要打**直连的后端**，**不要**拿公网域名跑。原因是那道网关本身就是被测
 *    环境的一部分：它会改写 Authorization，所以「Authorization 仍能认证」那几条
 *    在网关后面必然失败 —— 那是环境特性，不是被测代码坏了。一个"注定失败"的
 *    测试比没有测试更坏（它会让人对真实失败脱敏）。公网那一侧由
 *    gtest/e2e_live_auth.mjs 负责，它只断言"用自定义头能通"。
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..');
const BASE = (process.env.CAMPUS_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '');

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? '[PASS]' : '[FAIL]'} ${name}${detail ? '  ' + detail : ''}`);
}
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/* ============================================================
   A. 源码守卫
   ============================================================ */
console.log('\n--- A. 源码守卫（不需要服务） ---');

const authPy = read('backend/routers/auth.py');
check('后端鉴权读自定义头 x-campus-token', /x-campus-token/i.test(authPy));
check('后端仍然兼容标准 Authorization: Bearer（本机 / 第三方客户端别被砍掉）',
  /authorization/i.test(authPy) && /bearer/i.test(authPy));
check('★ 后端**不再**用 Header(None) 直接取 authorization（那正是被网关污染的入口）',
  !/authorization:\s*str\s*=\s*Header\(/.test(authPy) && !/Header\(None\).*authorization/.test(authPy));

const commonJs = read('frontend/js/common.js');
check('前端 api() 会带上 X-Campus-Token', /X-Campus-Token/.test(commonJs));
check('前端仍然同时带 Authorization（两个头都发，哪条通都能认证）',
  /headers\["Authorization"\]\s*=\s*"Bearer "/.test(commonJs));

const dbPy = read('backend/database.py');
check('★ 换包时不再无条件删库（把 db_file.unlink() 限制在显式开关里）',
  !/if old and old != cur:[\s\S]{0,200}?unlink\(\)/.test(dbPy));
check('换包检测提供 CAMPUS_RESET_DB 显式开关', /CAMPUS_RESET_DB/.test(dbPy));
check('换包检测会另存备份（不静默丢数据）', /campus_ai\.db\.bak-/.test(dbPy) && /copy2?\(/.test(dbPy));

const loginJs = read('frontend/js/login.js');

/* ★ 剥掉注释再看界面文案。
 *
 * 为什么不能直接对整份源码正则「不许出现微信 / 相机」：源码注释里出现这些词是
 * **故意的**（那是写给维护者的演化说明与背景记录，比如"原来是六个图标，需求
 * 只剩浏览器"）。整份源码一起断言，会把注释里的正常说明也判成违规，逼着人把有用的
 * 说明删掉 —— 那是测试在制造坏结果。这里只对**真正的界面字符串**负责。
 *
 * 同理，断言也不能只挑「公网形态」—— 2026-10-14 build r 起本机 / 公网已经统一成
 * 一套话术了，还按两套去断言，就会把正确的实现判成失败（本条守卫上一版正是如此）。 */
const loginJsUi = loginJs
  .replace(/\/\*[\s\S]*?\*\//g, '')    // 块注释
  .replace(/^[ \t]*\/\/.*$/gm, '');    // 行注释

check('★ 扫码提示语统一为「请用手机浏览器扫码打开」（本机 / 公网同一套）',
  /用手机浏览器扫码打开/.test(loginJsUi));
check('★ 界面文案里不出现「微信 / 相机」（用户要求：只说浏览器，不解释谁不行）',
  !/微信|相机/.test(loginJsUi));
check('★ 底部扫码 App 只剩「浏览器」一个，且不再按部署形态分叉',
  !/SCAN_APPS_LOCAL|SCAN_APPS_PUBLIC/.test(loginJs) &&
  /const SCAN_APPS = \[\s*\{\s*name:\s*"浏览器"/.test(loginJs));

const loginHtml = read('frontend/login.html');
check('★ 登录页 HTML 里也不出现「微信 / 相机」（静态兜底文案同样要干净）',
  !/微信|相机/.test(loginHtml));

/* ============================================================
   B. 活服务契约
   ============================================================ */
console.log('\n--- B. 活服务契约 ---');

let alive = false;
try {
  const r = await fetch(`${BASE}/api/ping`, { signal: AbortSignal.timeout(6000) });
  alive = r.ok;
} catch { alive = false; }

if (!alive) {
  console.log(`  [SKIP] ${BASE} 上没有后端在跑，跳过 B 段（A 段结论仍然有效）`);
} else {
  const uname = 'tk' + Date.now().toString().slice(-8);
  const reg = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: uname, password: 'test123456' }),
  });
  const regBody = await reg.json().catch(() => ({}));
  const token = regBody.access_token || '';
  check('能注册并拿到 token', reg.status === 200 && !!token, `HTTP ${reg.status}`);

  const me = async (headers) => {
    const r = await fetch(`${BASE}/api/auth/me`, { headers });
    let body = {};
    try { body = await r.json(); } catch { /* 忽略 */ }
    return { status: r.status, detail: body.detail || '', username: body.username || '' };
  };

  const a = await me({ 'X-Campus-Token': token });
  check('X-Campus-Token 能认证（新的主路径）',
    a.status === 200 && a.username === uname, `HTTP ${a.status}`);

  const b = await me({ Authorization: 'Bearer ' + token });
  check('Authorization: Bearer 仍能认证（老路径没被砍）',
    b.status === 200 && b.username === uname, `HTTP ${b.status}`);

  const c = await me({ 'X-Campus-Token': token, Authorization: 'Bearer ' + token });
  check('两个头都带时能认证（前端实际行为）', c.status === 200, `HTTP ${c.status}`);

  /* ★★★ 本文件的核心断言：精确复现线上死循环的场景 —— */
  const d = await me({ 'X-Campus-Token': token, Authorization: 'Bearer GATEWAY-INJECTED-GARBAGE' });
  check('★ 坏 Authorization + 好 X-Campus-Token 仍然认证通过（线上死循环的复现与防线）',
    d.status === 200 && d.username === uname, `HTTP ${d.status} ${d.detail}`);

  const e = await me({ Authorization: 'Bearer GATEWAY-INJECTED-GARBAGE' });
  check('只有坏 token 时报「登录状态已过期」（而不是放行）',
    e.status === 401 && e.detail.includes('已过期'), `HTTP ${e.status} ${e.detail}`);

  const f = await me({});
  check('完全不带 token 时报「你还没有登录」（两种 401 语义不能混）',
    f.status === 401 && f.detail.includes('你还没有登录'), `HTTP ${f.status} ${f.detail}`);

  const g = await me({ Authorization: 'Basic abcdef' });
  check('Authorization 不是 Bearer 时报「你还没有登录」',
    g.status === 401 && g.detail.includes('你还没有登录'), `HTTP ${g.status} ${g.detail}`);

  /* 空的自定义头不能让请求"短路"掉 Authorization 回退 */
  const h = await me({ 'X-Campus-Token': '   ', Authorization: 'Bearer ' + token });
  check('自定义头是空白字符时回退到 Authorization（别把空串当有效值）',
    h.status === 200, `HTTP ${h.status} ${h.detail}`);
}

const pass = results.filter((r) => r.ok).length;
console.log('\n' + '='.repeat(60));
console.log(`  结论: ${pass} 通过 / ${results.length - pass} 失败`);
console.log('='.repeat(60));
process.exit(pass === results.length ? 0 : 1);
