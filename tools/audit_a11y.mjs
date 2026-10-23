/* 无障碍（a11y）审计：用 axe-core 把全站页面扫一遍，按 WCAG 2.1 A/AA 规则判。

   跑法（先起后端，本树前端挂在 /app）：
     cd backend && PORT=8011 python main.py
     node tools/audit_a11y.mjs                # 默认扫 http://127.0.0.1:8011/app
     CAMPUS_BASE=http://127.0.0.1:8000 node tools/audit_a11y.mjs

   ## 为什么单独做一个审计脚本，而不是塞进 test_site_smoke.mjs

   smoke 判的是「页面有没有报错/白屏」，是**能跑**；axe 判的是「看不看得见、
   读屏读不读得出来」，是**能用**。两者失败的后果完全不同：
   白屏一眼就发现，而「浅灰字放在白底上」这种事，视力正常的人在好显示器上
   根本看不出来 —— 本项目就真踩过：`--ink-faint` 原本是 #8b94a8，
   对比度 3.04:1，低于 WCAG AA 正文要求的 4.5:1，修到 #646e85 才够。
   这种问题靠人眼看着改是改不干净的，必须有机器逐条算。

   ## 两轮扫描：浅色 + 黑夜

   主题是**令牌覆盖**实现的（同一个类名换个 CSS 变量），所以浅色跑通不代表
   黑夜也通 —— 深色底上再压一层 40% 透明的字，对比度很容易掉下去。
   只挑三页跑黑夜：它们承载了全站 90% 的文字（登录 / 首页 / AI 伴学）。

   ## 结果怎么读

   - `critical` / `serious`：必须修，**脚本会以非 0 退出**（CI 里能挡住回归）。
   - `moderate` / `minor`：打印出来但不判失败。典型是「表单缺少 autocomplete」
     这类，修了更好但不影响能不能用 —— 如果连它一起判死，这个脚本会长期飘红，
     然后所有人开始无视它（比不跑更糟）。
*/
import pw from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';

const { chromium } = pw;

const API = process.env.CAMPUS_BASE
  || `http://127.0.0.1:${process.env.CAMPUS_API_PORT || '8011'}`;
const BASE = API + '/app';

/* 沙箱/离线机上的 Chromium 是预先下载的固定版本，playwright 包升过版会去找
   另一个 build 号，报 "Executable doesn't exist" —— 看着像页面坏了，其实是
   环境对不上。所以显式指定，找不到再退回让 playwright 自己找。 */
const CHROME = process.env.CHROME_PATH || (() => {
  const p = `${process.env.LOCALAPPDATA || ''}\\ms-playwright\\chromium-1248\\chrome-win64\\chrome.exe`;
  return process.platform === 'win32' ? p : undefined;
})();

const PAGES = [
  'index.html', 'plan.html', 'planner.html', 'reminder.html', 'account.html',
  'emotion-chat.html', 'emotion-quiz.html', 'emotion-result.html',
  'login.html', 'scan-confirm.html', 'feedback.html',
  'admin.html', 'admin-feedback.html',
];
/* 黑夜主题只扫这三页（承载全站绝大部分文字）。其余页面的文字样式与它们共用令牌。 */
const DARK_PAGES = ['login.html', 'index.html', 'planner.html'];

const RULE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

// ---------------------------------------------------------------- 拿一个 token
// 受保护页面在未登录时会重定向到登录页，扫到的就不是目标页面了。
const uname = 'ax' + Date.now().toString().slice(-8);
let token = '';
try {
  const r = await fetch(`${API}/api/auth/register`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: uname, password: 'test123456' }),
  });
  token = (await r.json()).access_token || '';
} catch (e) {
  console.warn(`[WARN] 注册临时账号失败，受保护页面可能被重定向：${String(e).slice(0, 80)}`);
}
console.log(token ? `临时账号 ${uname} 已就绪\n` : '');

const browser = await chromium.launch({
  executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

const all = [];   // {page, theme, id, impact, help, nodes:[selector]}

async function runGroup(ctx, pages, theme) {
  for (const name of pages) {
    const page = await ctx.newPage();
    try {
      const resp = await page.goto(`${BASE}/${name}`, { waitUntil: 'networkidle', timeout: 25000 });
      if (!resp || !resp.ok()) {
        console.log(`  [SKIP] ${name.padEnd(22)} HTTP ${resp ? resp.status() : '?'}`);
        continue;
      }
      await page.waitForTimeout(500);   // 让首屏动画/异步渲染落定，免得扫到半成品
      const res = await new AxeBuilder({ page }).withTags(RULE_TAGS).analyze();
      const v = res.violations || [];
      const bad = v.filter(x => x.impact === 'critical' || x.impact === 'serious');
      const mark = bad.length ? '[FAIL]' : (v.length ? '[WARN]' : '[PASS]');
      console.log(`  ${mark} ${(theme === 'dark' ? '黑夜 ' : '') + name}`.padEnd(34)
        + `严重=${bad.length} 全部=${v.length}`);
      for (const x of v) {
        const nodes = (x.nodes || []).map(n => n.target && n.target.join(' ')).filter(Boolean);
        all.push({ page: name, theme, id: x.id, impact: x.impact,
                   help: x.help, nodes: nodes.slice(0, 4), count: nodes.length });
        if (x.impact === 'critical' || x.impact === 'serious') {
          console.log(`         · ${x.id} (${x.impact}) ${x.help}`);
          // 对比度这类问题，**必须有具体数字才修得动**：axe 的 failureSummary 里带着
          // 「2.9:1，要求 4.5:1」这样的原文，只给选择器等于让人去猜。
          for (const n of (x.nodes || []).slice(0, 3)) {
            console.log(`           ${(n.target || []).join(' ')}`);
            const fs = String(n.failureSummary || '').replace(/\s+/g, ' ').trim();
            if (fs) console.log(`             ${fs.slice(0, 160)}`);
          }
        }
      }
    } catch (e) {
      console.log(`  [ERR ] ${name.padEnd(22)} ${String(e).slice(0, 70)}`);
      all.push({ page: name, theme, id: 'harness-error', impact: 'serious',
                 help: String(e).slice(0, 200), nodes: [], count: 0 });
    }
    await page.close();
  }
}

const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
if (token) {
  await ctx.addInitScript((t) => { try { localStorage.setItem('token', t); } catch {} }, token);
}

console.log('--- 浅色主题 ---');
await runGroup(ctx, PAGES, 'light');

console.log('\n--- 黑夜主题（令牌覆盖后重新算对比度）---');
const darkCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await darkCtx.addInitScript((t) => {
  try {
    localStorage.setItem('theme', 'dark');
    if (t) localStorage.setItem('token', t);
  } catch {}
}, token);
await runGroup(darkCtx, DARK_PAGES, 'dark');

await browser.close();

// ------------------------------------------------------------------ 汇总
const bad = all.filter(x => x.impact === 'critical' || x.impact === 'serious');
console.log('\n' + '='.repeat(64));
console.log(`  规则集合: ${RULE_TAGS.join(' / ')}（WCAG 2.1 A + AA）`);
console.log(`  扫描页面: 浅色 ${PAGES.length} 页 + 黑夜 ${DARK_PAGES.length} 页`);
if (all.length) {
  const byRule = {};
  for (const x of all) {
    const k = `${x.id} (${x.impact})`;
    byRule[k] = byRule[k] || { n: 0, pages: new Set(), help: x.help };
    byRule[k].n += 1;
    byRule[k].pages.add(x.page);
  }
  console.log('\n  命中的规则:');
  for (const [k, v] of Object.entries(byRule).sort((a, b) => b[1].n - a[1].n)) {
    console.log(`    ${k}  出现 ${v.n} 次 / ${v.pages.size} 页  ${v.help}`);
  }
}
console.log('\n' + '='.repeat(64));
console.log(bad.length
  ? `  结论: ${bad.length} 处 serious/critical 违规，必须修`
  : '  结论: 无 serious/critical 违规（moderate/minor 见上）');
console.log('='.repeat(64) + '\n');

process.exit(bad.length ? 1 : 0);
