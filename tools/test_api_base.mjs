/**
 * 前端 API 地址推导的回归测试。
 *
 * 为什么需要它：
 *   手机连不上后端，最经典的原因就是前端把后端地址推成了 127.0.0.1
 *   —— 对手机会变成「连它自己」，必然失败。这个推导逻辑藏在
 *   config.js / common.js 里，改一行都可能悄悄破坏它，而**在 PC 上测不出来**
 *   （PC 上 127.0.0.1 恰好是对的）。
 *
 *   所以这里用 Node 桩住 window.location，把各种访问场景跑一遍，
 *   专门断言「手机场景下绝不能出现 127.0.0.1 / localhost」。
 *
 * 用法：
 *   node tools/test_api_base.mjs
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

let ok = 0, bad = 0;
function check(label, cond, extra = '') {
  if (cond) { ok++; console.log(`  [PASS] ${label} ${extra}`); }
  else { bad++; console.log(`  [FAIL] ${label} ${extra}`); }
}

/** 在「像浏览器」的沙箱里跑 config.js，拿到 CONFIG.API_BASE */
function runConfig(href) {
  const src = fs.readFileSync(path.join(ROOT, 'frontend/js/config.js'), 'utf8');
  const u = new URL(href);
  const sandbox = {
    window: {
      location: {
        href, hostname: u.hostname, origin: u.origin, pathname: u.pathname,
        protocol: u.protocol,
      },
    },
  };
  const fn = new Function('window', `${src}\nreturn CONFIG;`);
  return fn(sandbox.window);
}

/** 复刻 common.js 里 resolveApiBase 的候选顺序（不真发请求） */
function candidatesFor(href) {
  const u = new URL(href);
  const out = [];
  if (u.origin && /^https?:/i.test(u.origin)) out.push(u.origin + '/api');
  for (const p of [8000, 8001, 8002, 8003, 8004, 8005, 8010, 8020]) {
    out.push(`http://${u.hostname}:${p}/api`);
  }
  out.push(runConfig(href).API_BASE);
  return [...new Set(out.map(c => String(c).replace(/\/+$/, '')))];
}

const HAS_LOOPBACK = /\b(127\.0\.0\.1|localhost)\b/i;

console.log('='.repeat(62));
console.log('  前端 API 地址推导 · 回归测试');
console.log('='.repeat(62));

// ---- 1) 单端口部署（后端托管前端，前后端同源）----------------------------
{
  const href = 'http://192.168.1.5:8000/app/login.html';
  const c = candidatesFor(href);
  check('同源部署：第一个候选就是同源 /api', c[0] === 'http://192.168.1.5:8000/api', c[0]);
  check('同源部署：候选里不含回环地址', !c.some(x => HAS_LOOPBACK.test(x)));
}

// ---- 2) 双端口部署（前端 5500 单跑）—— 手机场景 ---------------------------
{
  const href = 'http://192.168.1.5:5500/login.html';
  const c = candidatesFor(href);
  check('双端口：config.js 推出局域网 IP', runConfig(href).API_BASE === 'http://192.168.1.5:8000/api',
    runConfig(href).API_BASE);
  // ⚠️ 这是本测试存在的最大理由：手机场景下绝不能出现 127.0.0.1
  check('★ 手机场景候选里【绝不能】出现 127.0.0.1 / localhost',
    !c.some(x => HAS_LOOPBACK.test(x)),
    c.some(x => HAS_LOOPBACK.test(x)) ? c.filter(x => HAS_LOOPBACK.test(x)).join(', ') : '');
  check('手机场景候选里全是局域网 IP', c.every(x => x.includes('192.168.1.5')), c[0]);
}

// ---- 3) PC 本机访问（127.0.0.1 在这里是对的）------------------------------
{
  const href = 'http://127.0.0.1:5500/login.html';
  const c = candidatesFor(href);
  check('PC 本机：同源候选是 127.0.0.1（本机访问时这是正确行为）',
    c[0] === 'http://127.0.0.1:5500/api', c[0]);
}

// ---- 4) 端口确实覆盖到 8020（后端端口被占用后最多升到这里）-----------------
{
  const c = candidatesFor('http://192.168.1.5:5500/login.html');
  check('候选覆盖 8020（后端端口可被占用后上浮到此）',
    c.includes('http://192.168.1.5:8020/api'));
  check('候选覆盖 8010', c.includes('http://192.168.1.5:8010/api'));
  check('候选数量受控（稀疏采样，不会慢到 20 秒+）', c.length <= 11, `实际 ${c.length} 个`);
}

// ---- 5) file:// 兜底 ------------------------------------------------------
{
  // file:// 时 hostname 为空字符串，应落到 localhost 兜底且不抛异常
  const src = fs.readFileSync(path.join(ROOT, 'frontend/js/config.js'), 'utf8');
  const fn = new Function('window', `${src}\nreturn CONFIG;`);
  let base = null, threw = false;
  try {
    base = fn({ location: { href: 'file:///x/login.html', hostname: '', origin: 'null', protocol: 'file:' } }).API_BASE;
  } catch { threw = true; }
  check('file:// 打开不抛异常', !threw);
  check('file:// 兜底到 localhost:8000', base === 'http://localhost:8000/api', String(base));
}

// ---- 6) config.js 不再依赖不存在的注入变量 --------------------------------
{
  const src = fs.readFileSync(path.join(ROOT, 'frontend/js/config.js'), 'utf8');
  // 注释里可以提到，但**代码里不能真的去读** __API_BASE__（那是个死钩子，已删）
  const codeOnly = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  check('config.js 代码里不再读取 __API_BASE__（死钩子已清）',
    !codeOnly.includes('__API_BASE__'));
}

// ---- 7) 前端自带静态服务器必须禁缓存（防「页面被缓存住」）------------------
//
// 真实事故：用户换了新包、旧文件删干净、旧窗口也关了，登录页却仍显示更早的
// 构建号 —— 因为浏览器把旧的 login.html 缓存了，根本没去服务器拿新页面。
// 端口 8000（后端托管）已经发 no-cache，但启动脚本默认打开的是 5500，
// 那个服务以前是 `python -m http.server`，它**不发任何 Cache-Control**。
// 这一节把这个洞钉死：脚本必须存在，且必须对 .html/.js/.css 发 no-store。
{
  const p = path.join(ROOT, 'frontend/serve_nocache.py');
  const exists = fs.existsSync(p);
  check('存在自带静态服务器 frontend/serve_nocache.py', exists);
  if (exists) {
    const src = fs.readFileSync(p, 'utf8');
    check('对代码类资源发 no-store（不是只发 no-cache）',
      src.includes('no-store'));
    check('禁止规则覆盖 .html', src.includes('".html"'));
    check('禁止规则覆盖 .js', src.includes('".js"'));
    check('禁止规则覆盖 .css', src.includes('".css"'));
    // 源码/配置不能被下载（服务器把整个 frontend 目录暴露出去，
    // 脚本自己就躺在里面，不拦的话 /serve_nocache.py 能直接把源码拖走）
    check('拒绝下载 .py（服务器自己就在这个目录里）',
      /FORBIDDEN_SUFFIX\s*=\s*\([^)]*\.py/.test(src));
    check('拒绝下载 .env', src.includes('".env"'));
  }

  // 启动脚本必须改用它（含备用副本），否则等于没修
  for (const bat of ['start-windows.bat', '启动工具/start.bat']) {
    const t = fs.readFileSync(path.join(ROOT, bat), 'utf8');
    check(`${bat} 前端改用它启动（不再裸用 http.server）`,
      t.includes('serve_nocache.py'));
    check(`${bat} 保留 http.server 作为降级兜底`,
      t.includes('-m http.server'));
  }
}

// ---- 8) 登录页要能区分「后端旧」和「页面旧」-------------------------------
//
// 只显示后端版本时，「页面被缓存」这种情况完全看不出来：后端版本是实时请求
// 来的、是对的，错的是这个页面本身。所以必须把前端自己的号也读出来做比对。
{
  const src = fs.readFileSync(path.join(ROOT, 'frontend/js/login.js'), 'utf8');
  check('登录页读取前端自己的构建号（.build-tag）',
    src.includes('.build-tag') || src.includes('build-tag'));
  check('登录页比对前后端构建号并在不一致时告警',
    src.includes('build !== feBuild'));
  check('告警文案告诉用户强刷（Ctrl+F5）',
    /Ctrl\+F5/.test(src));
  check('登录页 HTML 里有 .build-tag 元素可供读取',
    fs.readFileSync(path.join(ROOT, 'frontend/login.html'), 'utf8')
      .includes('build-tag'));
}

// ---- 9) 公网 https 部署（2026-10-15 build o 新增）------------------------
//
// 真实风险，值得单独守一节：
//   config.js 原来一律拼 `http://${host}:8000`。**本机**（http）正好对，
//   但**公网部署后页面是 https**，用 https 页面去连 http 接口属于混合内容，
//   浏览器**直接拦掉**；更糟的是探测一旦超时（后端冷启动那几秒），连兜底
//   都会落到这个 http 地址上 —— 页面就这样白屏了，而且在本机怎么试都复现不出来。
//   修复：https 页面一律返回**同源**地址，协议跟着页面走。
{
  const HOST = 'campus-ai-demo.workbuddy.link';
  const base = runConfig(`https://${HOST}/app/login.html`).API_BASE;
  check('★ https 页面：API 地址必须同源 https（不能是 http，否则被当混合内容拦掉）',
    base === `https://${HOST}/api`, base);
  check('★ https 页面：地址里不带 8000 端口（公网走反代的 443）',
    !/:8000\b/.test(base), base);

  const rootBase = runConfig(`https://${HOST}/`).API_BASE;
  check('★ https 域名根（分享出去的链接点开就是它）也走同源',
    rootBase === `https://${HOST}/api`, rootBase);

  // 反向守卫：别为了 https 把「前端单跑 5500」的老开发习惯改坏
  const devBase = runConfig('http://127.0.0.1:5500/login.html').API_BASE;
  check('反向守卫：http 独立前端仍指向 8000（没被 https 改动带坏）',
    devBase === 'http://127.0.0.1:8000/api', devBase);

  // 反向守卫：不能对「任意 http 页面」都用同源 —— 5500 上的同源是静态服务器，不是后端
  check('反向守卫：http 页面不会误判 5500 为后端',
    !runConfig('http://192.168.1.5:5500/login.html').API_BASE.includes(':5500/'), '');
}

/* ==================================================================
   源码守卫：凡是「新建课程对象」的地方，都必须带上 weeks
   ==================================================================
   为什么守这个：

     planner-timetable.js 里有好几处是**手工新建**一个课程对象
     （不是 spread 已有对象），比如
       · toLocalCourse()   —— 后端解析结果 -> 前端课程
       · importTextFile()  —— JSON/CSV 文本 -> 前端课程
     confirmImport() 也一样，是白名单式挑字段。

     这种写法的问题在于：**少写一个字段既不报错也不警告**，字段就这么没了。
     一旦漏掉 weeks，导入的课全变成「每周都上」——
     在界面上看起来就是「按周显示完全没生效」，而且**一条错误信息都没有**。

     这个 bug 真的发生过一次（importTextFile 漏了 weeks）。
     靠人肉 review 显然靠不住，所以这里做机械检查：
     把函数体抠出来，逐个确认里面出现了 `weeks:` 赋值。

     检查方式刻意用「抠函数体 + 找 weeks:」而不是跑起来测 ——
     因为这是**静态不变量**（"每个新建点都带 weeks"），
     运行时的测试只能证明"我测的那条路是好的"，覆盖不到新增的第三个新建点。
*/
{
  const src = fs.readFileSync(path.join(ROOT, 'frontend/js/planner-timetable.js'), 'utf8');

  /**
   * 抠出一个顶层函数的函数体（按大括号配平）。
   *
   * ⚠️ 不能天真地数字符 —— 第一版就是纯 `src[i] === '{'` 计数，
   *    在 importTextFile() 上直接失败了。原因是函数体里到处是
   *    模板字面量（`${...}`）、正则量词（`\d{4}`）和注释里的花括号，
   *    全被当成了真的结构括号，配平早就偏了。
   *    所以这里必须**先跳过**字符串 / 模板字面量 / 注释再计数。
   */
  function funcBody(name) {
    const start = src.indexOf(`function ${name}(`);
    if (start < 0) return null;
    const open = src.indexOf('{', start);
    if (open < 0) return null;

    let depth = 0;
    for (let i = open; i < src.length; i++) {
      const ch = src[i];

      // 行注释
      if (ch === '/' && src[i + 1] === '/') {
        const nl = src.indexOf('\n', i);
        i = nl < 0 ? src.length : nl;
        continue;
      }
      // 块注释
      if (ch === '/' && src[i + 1] === '*') {
        const end = src.indexOf('*/', i + 2);
        i = end < 0 ? src.length : end + 1;
        continue;
      }
      // 字符串 / 模板字面量：整段跳过（模板里的 ${} 也一并跳过，
      // 因为我们只关心这段代码**是不是写了 weeks:**，不需要展开它）
      if (ch === '"' || ch === "'" || ch === '`') {
        const quote = ch;
        for (let j = i + 1; j < src.length; j++) {
          if (src[j] === '\\') { j++; continue; }
          if (src[j] === quote) { i = j; break; }
        }
        continue;
      }
      if (ch === '{') depth++;
      else if (ch === '}') {
        depth--;
        if (depth === 0) return src.slice(open, i + 1);
      }
    }
    return null;
  }

  // 这几个函数都会「新建课程对象」，必须带 weeks
  const MUST_HAVE_WEEKS = ['toLocalCourse', 'importTextFile', 'confirmImport'];
  MUST_HAVE_WEEKS.forEach(fn => {
    const body = funcBody(fn);
    if (body == null) {
      check(`${fn}() 存在（源码守卫）`, false, '—— 找不到这个函数，是不是改名了？');
      return;
    }
    check(`${fn}() 新建课程对象时带 weeks（防「按周显示」静默失效）`,
      /weeks\s*:/.test(body));
  });
}

console.log();
console.log('='.repeat(62));
console.log(`  结论: ${ok} 通过 / ${bad} 失败` + (bad === 0 ? '  —— 全部通过' : ''));
console.log('='.repeat(62));

process.exit(bad === 0 ? 0 : 1);
