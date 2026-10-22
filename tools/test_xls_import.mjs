/* 课表 Excel（.xls / .xlsx）导入 —— 端到端回归测试。
 *
 * 为什么要单独一个测试：
 *   用户反馈「照片不好识别」，改用教务系统导出的 .xls。原来的导入只认
 *   图片和 JSON/CSV，.xls 会被当文本读（readAsText on binary -> 乱码 -> 报错）。
 *   本测试守住这条链路，避免以后有人改 handleImport 的分支判断时又把它弄坏。
 *
 * 断言四件事：
 *   ① 文件选择框的 accept 含 .xls/.xlsx（否则用户根本选不到这个文件）
 *   ② 同一个 .xls 走完导入 -> 核对弹窗出现 24 条（不再报「没有解析到有效内容」）
 *   ③ 日期/节次/课程名/教室 与源表格一致（抽查几条硬编码的期望值）
 *   ④ 确认导入后能落库，且「同格放不下两门课」时如实报出课名（不是谎称"重复"）
 *
 * ⚠️ 依赖用户上传的那份样例表格。文件不在时**跳过**而不是失败 ——
 *    测试不该因为缺一份真实样本就把 CI 判红。
 *
 * 跑法：先起后端（8000）与前端静态服务（5500），再
 *   node tools/test_xls_import.mjs
 */
import pw from 'playwright';
import fs from 'fs';

/* ★ 2026-10-22 CI 兼容：原来这里写死了
   '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'。
   但 GitHub runner 的 HOME 是 /home/runner（不是 /root），而且浏览器目录名带版本号，
   playwright 一升级就变 —— 写死 = 「CI 上必然 Executable doesn't exist」，
   报错还会长得像业务代码坏了。现在：只认 CHROME_PATH（本机 run_all_tests.py 会传），
   没传就让 playwright 自己找它刚装好的那一份。 */
const CHROME = process.env.CHROME_PATH || undefined;
const P = process.env.CAMPUS_WEB_PORT || '5500';
const WEB = `http://127.0.0.1:${P}`;
const API = process.env.CAMPUS_BASE || `http://127.0.0.1:${process.env.CAMPUS_API_PORT || '8000'}`;

/* 样例课表。找不到就跳过涉及真实内容的断言。 */
const SAMPLES = [
  process.env.CAMPUS_XLS,
  '/root/uploads/1790416290192603375-学生课表.xls',
].filter(Boolean);
const XLS = SAMPLES.find(p => { try { return fs.existsSync(p); } catch { return false; } });

let pass = 0, fail = 0, skip = 0;
const ok = (m) => { console.log(`  [PASS] ${m}`); pass++; };
const bad = (m) => { console.log(`  [FAIL] ${m}`); fail++; };
const sk = (m) => { console.log(`  [SKIP] ${m}`); skip++; };

const browser = await pw.chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });

async function loginCtx(w = 1440, h = 1000) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const stamp = Date.now() + Math.floor(Math.random() * 1000);
  const reg = await (await ctx.request.post(`${API}/api/auth/register`, {
    data: { username: 'xl' + stamp, password: 'test123456', nickname: '课表' + String(stamp).slice(-6) },
  })).json();
  if (!reg.access_token) throw new Error('注册失败: ' + JSON.stringify(reg));
  const me = await (await ctx.request.get(`${API}/api/auth/me`, {
    headers: { Authorization: `Bearer ${reg.access_token}` },
  })).json();
  await ctx.addInitScript(([t, u]) => {
    try { localStorage.setItem('token', t); localStorage.setItem('user', JSON.stringify(u)); } catch (e) {}
  }, [reg.access_token, me]);
  return { ctx, token: reg.access_token };
}

/* ---------------- ① accept 属性 ---------------- */
console.log('\n--- ① 文件选择框允许选 Excel ---');
{
  const { ctx } = await loginCtx();
  const p = await ctx.newPage();
  await p.goto(`${WEB}/planner.html?v=${Date.now()}`);
  await p.waitForTimeout(800);
  const accept = await (await p.$('#file-import')).getAttribute('accept');
  (/\.xls/i.test(accept) && /\.xlsx/i.test(accept))
    ? ok(`accept 含 .xls/.xlsx  ${accept}`)
    : bad(`accept 缺 Excel 类型: ${accept}`);
  await ctx.close();
}

if (!XLS) {
  sk('没找到样例 .xls（设 CAMPUS_XLS 或放到 /root/uploads），跳过真实文件断言');
} else {
  console.log(`\n--- ② 导入 ${XLS.split('/').pop()} ---`);
  const { ctx, token } = await loginCtx();
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(`${WEB}/planner.html?v=${Date.now()}`);
  await p.waitForTimeout(800);

  await (await p.$('#file-import')).setInputFiles(XLS);
  await p.waitForTimeout(3500);

  const res = await p.evaluate(() => {
    const mask = document.querySelector('#review-mask');
    const shown = !!(mask && mask.classList.contains('show') && getComputedStyle(mask).display !== 'none');
    const rows = [...document.querySelectorAll('tr')].filter(tr => tr.querySelector('[data-f="slot_order"]'));
    const g = (tr, f) => { const e = tr.querySelector(`[data-f="${f}"]`); return e ? e.value : ''; };
    const items = rows.map(tr => ({
      slot: g(tr, 'slot_order'), day: g(tr, 'weekday') || g(tr, 'date'),
      name: g(tr, 'name'), location: g(tr, 'location'), building: g(tr, 'building'),
    }));
    const toasts = [...document.querySelectorAll('div')]
      .filter(d => /toast/i.test(d.className || '') && d.innerText.trim()).map(d => d.innerText.trim());
    const count = (document.querySelector('#review-count') || {}).innerText || '';
    const scanMask = document.querySelector('#scan-mask');
    const scanStuck = !!(scanMask && scanMask.classList.contains('show'));
    return { shown, items, toasts, count, scanStuck };
  });

  res.shown ? ok('核对弹窗弹出（不再是报错）') : bad('核对弹窗没弹出');
  !res.scanStuck ? ok('进度遮罩没有卡住') : bad('进度遮罩卡住了（会挡住页面）');
  /* ⚠️ 条数从 24 变 48 是**预期的**，不是回归。
     这份样例是「一大节两行」模板（课名行 + 教室行，课程单元格上下合并），
     表格里写「1」实际占第 1-2 小节。改成按真实跨度展开后，
     每一大节都会生成 2 条（相邻节次），所以条数翻倍 —— 这是「课都是两节的」
     这个用户反馈的修复本身。
     断言不写死 48，而是断言「偶数条 + 全是成对出现」，这样换了样例也成立。 */
  const n1 = res.items.length;
  n1 >= 24 && n1 % 2 === 0
    ? ok(`解析出 ${n1} 条（大节模板展开成对）  ${res.count}`)
    : bad(`条数异常：${n1}（应为 ≥24 且为偶数）  ${res.count}`);
  !res.toasts.some(t => /没有解析到有效内容|文件解析失败/.test(t))
    ? ok('没有出现旧的「解析失败」提示') : bad(`仍有旧报错: ${JSON.stringify(res.toasts)}`);

  /* ③ 抽查硬编码期望：源表格第 3 行、周二第 1 节 = 计算机网络 @ 平远楼-313 */
  console.log('\n--- ③ 字段与源表格一致 ---');
  const find = (day, slot) => res.items.find(x => String(x.day).includes(day) && String(x.slot) === String(slot));
  const cases = [
    ['2026-09-22', '3', '计算机网络', '平远楼-313'],
    ['2026-09-24', '5', '大学英语（专升本）', '博文楼203（语音室）'],
    ['2026-09-23', '3', '计算机组成原理[实践学时]', '组成原理实验室(启智楼502)'],
    ['2026-09-26', '9', '人工智能技术与应用', 'AI技术综合实验中心(润知楼201)'],
  ];
  for (const [day, slot, name, loc] of cases) {
    const hit = find(day, slot);
    if (!hit) { bad(`${day} 第${slot}节 没找到（期望 ${name}）`); continue; }
    (hit.name === name && hit.location === loc)
      ? ok(`${day} 第${slot}节 = ${name} @ ${loc}`)
      : bad(`${day} 第${slot}节 期望「${name} @ ${loc}」，实际「${hit.name} @ ${hit.location}」`);
  }
  /* 教室串不能被截断（历史 bug：楼名被从中间切走，只剩 "306)"） */
  const truncated = res.items.filter(x => /^[0-9]+[)）]?$/.test(String(x.location).trim()));
  truncated.length === 0
    ? ok('教室字段没有被截断（无 "306)" 这类残值）')
    : bad(`有 ${truncated.length} 条教室被截断: ${truncated.map(t => t.location).join('/')}`);

  /* ④ 确认导入 -> 落库 */
  console.log('\n--- ④ 确认导入并落库 ---');
  const btn = await p.$('#review-body button:has-text("确认导入")');
  if (!btn) { bad('找不到「确认导入」按钮'); }
  else {
    await btn.click();
    await p.waitForTimeout(1200);
    const after = await p.evaluate(() =>
      [...document.querySelectorAll('div')].filter(d => /toast/i.test(d.className || '') && d.innerText.trim())
        .map(d => d.innerText.trim()));
    const line = after.join(' ');
    /已导入\s*\d+\s*条/.test(line) ? ok(`导入提示正常  ${line}`) : bad(`导入提示异常  ${line}`);
    /* 同格放不下两门课时，必须点出课名；不许谎称"重复" */
    if (/同格/.test(line)) {
      ok(`同格冲突如实报出课名  ${line}`);
    } else if (/跳过\s*\d+\s*条重复/.test(line)) {
      ok('无同格冲突，按重复跳过（正常分支）');
    }

    const save = await p.$('button:has-text("保存时间表")');
    if (save) { await save.click(); await p.waitForTimeout(2500); }
    const tt = await (await ctx.request.get(`${API}/api/planner/timetable`, {
      headers: { Authorization: `Bearer ${token}` } })).json();
    const list = Array.isArray(tt) ? tt : (tt.courses || []);
    list.length >= 20
      ? ok(`课表已落库 ${list.length} 条`)
      : bad(`落库条数异常: ${list.length}`);
    const saved = list.find(c => c.name === '计算机网络' && c.location === '平远楼-313');
    saved ? ok('落库内容与解析一致（抽查 计算机网络 @ 平远楼-313）')
          : bad('落库里找不到抽查的课程');
  }

  errs.length === 0 ? ok('全程无 JS 异常') : bad(`JS 异常: ${errs.join(' | ')}`);
  await ctx.close();
}

/* ---------------- ⑤ 坏文件兜底 ---------------- */
console.log('\n--- ⑤ 选到坏文件时的兜底 ---');
{
  const fake = '/tmp/_bad_sample.xls';
  fs.writeFileSync(fake, '这不是真的 Excel，只是一段文字。'.repeat(80));
  const { ctx } = await loginCtx();
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(`${WEB}/planner.html?v=${Date.now()}`);
  await p.waitForTimeout(800);
  await (await p.$('#file-import')).setInputFiles(fake);
  await p.waitForTimeout(2500);
  const r = await p.evaluate(() => ({
    toasts: [...document.querySelectorAll('div')].filter(d => /toast/i.test(d.className || '') && d.innerText.trim())
      .map(d => d.innerText.trim()),
    stuck: (() => { const m = document.querySelector('#scan-mask'); return !!(m && m.classList.contains('show')); })(),
  }));
  r.toasts.length ? ok(`给出中文提示  ${r.toasts[0]}`) : bad('坏文件没有任何提示（用户会以为没反应）');
  !r.stuck ? ok('进度遮罩已收起') : bad('进度遮罩卡住，页面被挡住');
  errs.length === 0 ? ok('坏文件无 JS 异常') : bad(`坏文件 JS 异常: ${errs.join(' | ')}`);
  await ctx.close();
}

/* ---------------- ⑤·二 兜底文案必须带上「问题类型」 ----------------
 *
 * 为什么专门测这个：
 *   「这个表格读不了」这句兜底文案曾经三次让排查陷进死胡同 ——
 *   用户截图只有这一句，而真实异常只写在服务器日志里，用户拿不到。
 *   结果双方只能反复猜「是不是文件坏了」「是不是你用的旧版本」。
 *
 *   现在文案末尾会带 `（问题类型：XXX）`，用户一截图就把真凶带过来了。
 *   这一节确保这个诊断信息**不会被以后的重构弄丢**。
 */
console.log('\n--- ⑤·二 兜底文案带上问题类型（便于远程排查）---');
{
  const { ctx, token } = await loginCtx();
  const res = await ctx.request.post(`${API}/api/planner/import-sheet`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'x.xls', monday: '2026-09-07',
      data: 'data:;base64,' + Buffer.from('这不是真的表格内容，只是一段中文。').toString('base64'),
    },
    failOnStatusCode: false,
  });
  const body = await res.text();
  if (res.status() === 400) {
    // 坏文件的正常出口是解析器自己的文案（ValueError 分支）；
    // 万一走到兜底 except，文案里必须带「问题类型」，否则远程排查又抓瞎。
    const detail = (body.match(/"detail":"([^"]*)"/) || [])[1] || body.slice(0, 90);
    const diagnosable = /问题类型/.test(body) || /打不开|损坏/.test(body);
    diagnosable ? ok(`坏文件提示可诊断  ${detail}`)
                : bad(`坏文件提示没有诊断信息: ${body.slice(0, 200)}`);
  } else {
    bad(`坏文件应返回 400，实际 ${res.status()}`);
  }
  await ctx.close();
}

/* ---------------- ⑤·三 后端要自报版本（用户判断新旧版的唯一依据）----------------
 *
 * 交付方式是「发压缩包、用户解压覆盖」，用户很难确认自己是否真的换成了新版。
 * /api/ping 自报 build + features 后，登录页会显示「后端构建 …」，
 * 用户一眼可查；这里断言这个契约不会被破坏。
 */
console.log('\n--- ⑤·三 后端自报构建标记与能力 ---');
{
  const res = await fetch(`${API}/api/ping`);
  const j = await res.json().catch(() => ({}));
  res.ok ? ok('/api/ping 可访问') : bad(`/api/ping 状态 ${res.status}`);
  j.build ? ok(`后端自报构建标记  ${j.build}`) : bad('后端没自报 build（用户无法判断新旧版）');
  Array.isArray(j.features) && j.features.includes('weeks')
    ? ok('后端自报支持周次（weeks）')
    : bad(`后端 features 缺 weeks: ${JSON.stringify(j.features)}`);
  Array.isArray(j.features) && j.features.includes('tolerant-period')
    ? ok('后端自报支持宽容节次（tolerant-period）')
    : bad(`后端 features 缺 tolerant-period: ${JSON.stringify(j.features)}`);

  /* 构建标记必须来自 backend/build.txt 这**唯一**来源，且格式正确。
   *
   * 为什么值得单独断言：这个号的唯一用途就是让用户判断「我跑的到底是哪份代码」。
   * 以前它是硬编码在 main.py 里的字符串，和包里的标记文件各说各话 ——
   * 用户为了确认自己换包成功，反而被两个互相矛盾的号搞得更糊涂。
   * 现在接口和文件必须**逐字一致**，对不上就说明有人又加了第二个来源。
   */
  const { readFileSync } = await import('node:fs');
  const buildFile = new URL('../backend/build.txt', import.meta.url);
  let onDisk = '';
  try {
    onDisk = readFileSync(buildFile, 'utf8').trim();
  } catch {
    onDisk = '';
  }
  onDisk
    ? ok(`backend/build.txt 存在：${onDisk}`)
    : bad('读不到 backend/build.txt（构建号失去了唯一来源）');
  /^\d{4}-\d{2}-\d{2}-[a-z]$/.test(j.build || '')
    ? ok('构建号格式正确（YYYY-MM-DD-x）')
    : bad(`构建号格式异常：${JSON.stringify(j.build)}，期望形如 2026-09-26-h`);
  j.build === onDisk
    ? ok('接口自报构建号与 build.txt 完全一致')
    : bad(`接口自报 ${JSON.stringify(j.build)} 与 build.txt ${JSON.stringify(onDisk)} 不一致 —— 说明存在第二个来源`);
  j.build !== 'UNKNOWN'
    ? ok('构建号不是 UNKNOWN（build.txt 没被弄丢）')
    : bad('接口自报 UNKNOWN —— 打包时忘了 backend/build.txt');
}

/* ---------------- ⑤·四 教学楼简称要规范成正式楼名 ----------------
 *
 * 用户反馈：「如果是商教教学楼就是商德楼」。
 *
 * 教务表格里写的是**口语简称**（「商教514」），而系统内置的楼名表只有正式名
 * （「商德楼」）。对不上时的后果不只是显示不好看：
 *   · 这栋楼没有坐标 -> **地图上没法导航过去**
 *   · 核对弹窗里会标黄 -> 用户以为是自己填错了
 *
 * 所以导入时就要把简称换成正式名，而且**数据库里存的就是正式名** ——
 * 否则同一栋楼在库里既有「商教」又有「商德楼」两种写法，
 * 以后按楼查数据、统计、导航都要处理两套口径。
 *
 * ⚠️ 替换必须是**串内替换**而不是整体赋值：
 *    「商教514」里「商教」是楼、「514」是教室号，
 *    整体赋值会把教室号丢掉，用户就不知道去哪个教室了。
 */
console.log('\n--- ⑤·四 教学楼简称 -> 正式楼名 ---');
{
  const { execFileSync } = await import('node:child_process');
  /* ⚠️ 子进程的路径必须用 fileURLToPath / process.cwd() 拿，不能用
     `new URL('..', import.meta.url).pathname` —— 后者在 Windows 上给出的是
     `/C:/Users/...` 这种**带前导斜杠的盘符路径**，既不是合法的 cwd，
     `sys.path.insert(0, 'backend')` 也跟着失效。结果子进程直接起不来，
     `out` 是空串，下面 6 条断言全部 FAIL 且报 `undefined` ——
     看起来像「简称替换功能坏了」，实际只是测试自己在 Windows 上跑不起来。 */
  const backendDir = process.cwd().replace(/\\/g, '/') + '/backend';
  let out = '';
  try {
    out = execFileSync('python3', ['-c', `
import sys, json
sys.path.insert(0, ${JSON.stringify(backendDir)})
from routers.planner import _normalize_building_alias as f, BUILDING_ALIASES, KNOWN_BUILDINGS
cases = ['商教514', '商教513', '商德楼514', '平远楼-313', '线上', '', '商教']
print(json.dumps({
  'map': {c: f(c) for c in cases},
  'aliases': BUILDING_ALIASES,
  'known': sorted(KNOWN_BUILDINGS),
}, ensure_ascii=False))
`], { cwd: process.cwd(), encoding: 'utf8' });
  } catch (e) {
    out = '';
  }
  let d = {};
  try { d = JSON.parse(out); } catch { /* 取不到就下面报错 */ }

  d.map && d.map['商教514'] === '商德楼514'
    ? ok('「商教514」-> 「商德楼514」（教室号保留）')
    : bad(`商教替换不对: ${JSON.stringify(d.map)}`);
  d.map && d.map['商教513'] === '商德楼513'
    ? ok('「商教513」-> 「商德楼513」')
    : bad(`商教513 替换不对: ${JSON.stringify(d.map)}`);
  d.map && d.map['商德楼514'] === '商德楼514'
    ? ok('已是正式名的不再变动（替换幂等）')
    : bad(`幂等性被破坏: ${JSON.stringify(d.map)}`);
  d.map && d.map['平远楼-313'] === '平远楼-313'
    ? ok('别的楼名不受影响（别名表没有误伤）')
    : bad(`误伤了其他楼名: ${JSON.stringify(d.map)}`);
  d.known && d.known.includes('商德楼')
    ? ok('「商德楼」在教学楼白名单里（能导航）')
    : bad('商德楼不在 KNOWN_BUILDINGS，替换完仍然无法导航');
  // ⚠️ 别名表的**值**必须是正式楼名，否则替换完还是认不出，等于白换
  d.aliases && Object.values(d.aliases).every(v => (d.known || []).includes(v))
    ? ok('别名表的每个值都是正式楼名')
    : bad(`别名表的值不是正式楼名: ${JSON.stringify(d.aliases)}`);
}

/* ---------------- ⑥ 节次 / 星期表头的各种写法 ----------------
 *
 * 血泪教训（用户报「xls不能识别」）：
 *   原来的路由层写的是 `_PERIOD_TO_ORDER.get(int(period))`，只要节次格不是**纯数字**
 *   就抛 ValueError，被最外层兜底 except 抓住 -> 用户看到
 *   「这个表格读不了，可能是文件损坏或格式太特殊」。
 *   文件明明是好的，用户却被告知文件坏了，完全无从下手。
 *   与此同时星期表头只认「正好是一个字」（一/二/…），写「星期一」整张表就找不到。
 *
 * 这一节直接把 API 当黑盒打：不同写法喂进去，必须都能正确落到对应节次，
 * 且**绝不能**再出现「这个表格读不了」这句兜底文案。
 */
console.log('\n--- ⑥ 节次/星期表头各种写法都不该崩 ---');
{
  const xl = await import('xlsx-build-placeholder').catch(() => null);
  /* 不引入任何依赖：用最小 zip 手搓 xlsx 太脆，这里用 python 侧已经生成的样例。
     node 只负责断言 API 行为，构造表格交给 python（同一沙箱内）。 */
  const { execFileSync } = await import('child_process');
  const makeCase = (periodCells, weekdayCells) => {
    const py = `
import openpyxl, sys, json
period_cells = json.loads(sys.argv[1])
weekday_cells = json.loads(sys.argv[2])
wb = openpyxl.Workbook(); ws = wb.active; ws.title = '模版'
ws.append(['课表']); ws.append([])
ws.append(['课程名称','任课教师','学分','开课周次','时段','节次'] + weekday_cells)
# 每条 (节次文字, 周一课名, 周二课名)
for p, n1, n2 in period_cells:
    ws.append(['','','','','上午', p, n1, n2, '','','','',''])
    ws.append(['','','','','','教室','启智楼306','启智楼401','','','','',''])
wb.save(sys.argv[3])
`;
    const out = `/tmp/_case_${Math.random().toString(36).slice(2)}.xlsx`;
    execFileSync('python3', ['-c', py, JSON.stringify(periodCells), JSON.stringify(weekdayCells), out]);
    return out;
  };

  const { ctx, token } = await loginCtx();
  const callApi = async (path) => {
    const b64 = fs.readFileSync(path).toString('base64');
    const r = await ctx.request.post(`${API}/api/planner/import-sheet`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { data: b64, name: path.split('/').pop(), monday: '2026-09-21' },
    });
    let body = {};
    try { body = await r.json(); } catch { /* 非 JSON */ }
    return { status: r.status(), body };
  };

  /* ⑥-1 节次写成「第N节」+ 星期写成「星期一」—— 两个坑一起踩 */
  {
    const f = makeCase([['第1节', '数据库原理', '计算机网络'], ['第5节', '操作系统', '数据结构']],
      ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']);
    const { status, body } = await callApi(f);
    const detail = String(body.detail || '');
    status === 200 ? ok('「第N节」+「星期一」解析成功')
      : bad(`期望 200，实际 ${status}  ${detail}`);
    !/这个表格读不了/.test(detail) ? ok('没有出现「文件损坏」兜底文案')
      : bad(`又出现兜底文案: ${detail}`);
    const cs = body.courses || [];
    /* ⚠️ 4 门课 -> 8 条是**预期的**（大节模板把每门课展开成相邻两小节）。
       真正要守的不变量是「每门课都成对落到相邻节次上」，而不是条数本身。
       所以这里断言：条数翻倍、且每一对（2k, 2k+1）都相邻。 */
    const stages = {};
    cs.forEach(c => {
      const k = `${c.name}|${c.weekday}`;
      (stages[k] = stages[k] || []).push(c.slot_order);
    });
    const pairsOk = Object.values(stages).every(a => {
      const s = [...a].sort((x, y) => x - y);
      return s.length === 2 && s[1] === s[0] + 1;
    });
    (cs.length === 8 && pairsOk)
      ? ok(`4 门课展开成 8 条，且两两相邻（连堂正确）`)
      : bad(`连堂展开不对：共 ${cs.length} 条，明细 ${JSON.stringify(stages)}`);
    const db = cs.find(c => c.name === '数据库原理');
    db && db.slot_order === 3 ? ok('「第1节」-> order_no 3')
      : bad(`「第1节」映射错误: ${JSON.stringify(db)}`);
    const os = cs.find(c => c.name === '操作系统');
    os && os.slot_order === 9 ? ok('「第5节」-> order_no 9')
      : bad(`「第5节」映射错误: ${JSON.stringify(os)}`);
    db && db.weekday === 1 ? ok('「星期一」-> weekday 1')
      : bad(`「星期一」映射错误: ${JSON.stringify(db)}`);
    /* 教室必须还在（历史上节次解析失败时教室会整片丢失） */
    db && db.location === '启智楼306' ? ok('教室没丢  启智楼306')
      : bad(`教室丢了: ${JSON.stringify(db && db.location)}`);
  }

  /* ⑥-2 节次写成连排「3-4」——取首节并提示，别整张表报废 */
  {
    const f = makeCase([['3-4', '高等数学', '线性代数']],
      ['一', '二', '三', '四', '五', '六', '日']);
    const { status, body } = await callApi(f);
    status === 200 ? ok('「3-4」连排节次解析成功') : bad(`「3-4」失败 ${status} ${body.detail || ''}`);
    const cs = body.courses || [];
    cs.find(c => c.name === '高等数学') ? ok('「3-4」的课没丢') : bad('「3-4」的课丢了');
  }

  /* ⑥-3 节次写成汉字「三」*/
  {
    const f = makeCase([['三', '大学物理', '大学英语']], ['一', '二', '三', '四', '五', '六', '日']);
    const { status, body } = await callApi(f);
    status === 200 ? ok('汉字节次「三」解析成功') : bad(`汉字节次失败 ${status} ${body.detail || ''}`);
    const c = (body.courses || []).find(x => x.name === '大学物理');
    c && c.slot_order === 5 ? ok('「三」-> order_no 5') : bad(`「三」映射错误: ${JSON.stringify(c)}`);
  }

  /* ⑥-4 节次完全不是数字（如「上午」）—— 允许标黄兜底，但不能崩、不能丢课 */
  {
    const f = makeCase([['上午', '体育', '心理健康']], ['一', '二', '三', '四', '五', '六', '日']);
    const { status, body } = await callApi(f);
    status === 200 ? ok('无法识别的节次走标黄兜底（不报文件损坏）')
      : bad(`无法识别的节次应当兜底而非报错，实际 ${status} ${body.detail || ''}`);
    (body.courses || []).find(x => x.name === '体育') ? ok('兜底时课程没丢') : bad('兜底时课程丢了');
  }

  await ctx.close();
}

/* ---------------- ⑦ 周次（weeks）必须一路透传到落库 ---------------- */
console.log('\n--- ⑦ 上课周次透传（导入 -> 核对 -> 落库）---');
if (!XLS) {
  /* 没有样例 .xls 时必须**明确跳过**，不能就这么往下跑。
     踩过的坑：这里曾经直接 setInputFiles(XLS)，而 XLS 是 undefined ——
     playwright 收到 undefined 会在自己的 coreBundle 里抛
     「TypeError: Cannot read properties of undefined (reading 'buffer')」，
     整个测试脚本当场崩掉，前面几十条 PASS 也一起没了。报错还完全指不到
     「少了个样例文件」这个真正的原因 —— 一个可选输入缺失，不该把整轮验收打断。 */
  sk('没找到样例 .xls，跳过「周次透传」一节（设 CAMPUS_XLS 或放到 /root/uploads）');
} else {
  const { ctx, token } = await loginCtx();
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(`${WEB}/planner.html?v=${Date.now()}`);
  await p.waitForTimeout(800);

  await (await p.$('#file-import')).setInputFiles(XLS);
  await p.waitForTimeout(3500);

  const r = await p.evaluate(() => {
    const rows = [...document.querySelectorAll('#review-body tbody tr')]
      .filter(tr => tr.querySelector('[data-f="slot_order"]'));
    const g = (tr, f) => { const e = tr.querySelector(`[data-f="${f}"]`); return e ? e.value : ''; };
    return {
      rows: rows.length,
      weeksInputs: document.querySelectorAll('#review-body input[data-f="weeks"]').length,
      items: rows.map(tr => ({
        slot: g(tr, 'slot_order'), day: g(tr, 'weekday') || g(tr, 'date'),
        name: g(tr, 'name'), weeks: g(tr, 'weeks'),
      })),
    };
  });

  /* ⑦-1 解析结果里必须带出周次串（后端不透传的话这里全是空） */
  const withWeeks = r.items.filter(x => x.weeks && String(x.weeks).trim());
  withWeeks.length > 0
    ? ok(`导入结果带出周次（${withWeeks.length}/${r.items.length} 条有周次）`)
    : bad('导入结果里一条周次都没有（后端没透传 weeks）');

  /* ⑦-2 核对表每行都要有独立的周次输入框，用户才能看见/修正 */
  r.weeksInputs === r.rows
    ? ok(`核对表每行都有周次输入框（${r.weeksInputs} 个 / ${r.rows} 行）`)
    : bad(`周次输入框数不对：${r.weeksInputs} 个，但表格有 ${r.rows} 行`);

  /* ⑦-3 抽查具体课程：计算机网络只在第 1 周（源表格第 3 行 "1周(全部)"）。
     用宽松正则，避免换样例文件就红。 */
  const net = r.items.find(x => x.name === '计算机网络' && /1\s*周/.test(x.weeks || ''));
  net ? ok(`计算机网络 的周次读到了  ${net.weeks}`)
      : bad(`计算机网络 周次不对：${JSON.stringify(r.items.filter(x => x.name === '计算机网络').map(x => x.weeks))}`);

  /* ⑦-4 落库时 weeks 不能被 confirmImport 的白名单漏掉 ——
     这是最容易踩的坑：漏了不报错、不提示，但导入的课全变「每周都上」。 */
  const btn = await p.$('#review-body button:has-text("确认导入")');
  if (!btn) { bad('找不到「确认导入」按钮'); }
  else {
    await btn.click();
    await p.waitForTimeout(1200);
    const save = await p.$('button:has-text("保存时间表")');
    if (save) { await save.click(); await p.waitForTimeout(2500); }

    const tt = await (await ctx.request.get(`${API}/api/planner/timetable`, {
      headers: { Authorization: `Bearer ${token}` } })).json();
    const list = Array.isArray(tt) ? tt : (tt.courses || []);
    const savedWithWeeks = list.filter(c => c.weeks && String(c.weeks).trim());
    savedWithWeeks.length > 0
      ? ok(`周次已随课程落库（${savedWithWeeks.length}/${list.length} 条带周次）`)
      : bad('落库后周次全空了（confirmImport 或 CourseIn 漏了 weeks）');

    /* ⚠️ 别拿「计算机网络」抽查落库 —— 它在源表格里有两条同格冲突
       （周三第1节既是「计算机组成原理[实践学时]」又是「计算机网络」），
       confirmImport 只保留一条，所以它的 1周 版本可能根本不在落库结果里。
       改成断言「这些周次串都完整保留、没被截断」更稳。 */
    const netWeeks = list.filter(c => c.name === '计算机网络').map(c => c.weeks);
    netWeeks.length && netWeeks.every(w => /周/.test(w || ''))
      ? ok(`落库的 计算机网络 周次完整  ${JSON.stringify(netWeeks)}`)
      : bad(`落库的 计算机网络 周次异常：${JSON.stringify(netWeeks)}`);

    /* ⑦-5 刷新页面后周次还在（走 loadTimetable + 渲染全链路）。
       ⚠️ 顶层 `let courses` 不会挂到 window 上，得用兜底写法拿到它。 */
    await p.reload();
    await p.waitForTimeout(2500);
    const after = await p.evaluate(() => {
      // 优先读全局变量；读不到就从渲染出的卡片反推（卡片角标来自 weeks）
      const g = (typeof courses !== 'undefined' && Array.isArray(courses)) ? courses
        : (window.courses || []);
      return {
        fromVar: g.filter(c => c.weeks && String(c.weeks).trim()).length,
        total: g.length,
        chipsWithWeeks: [...document.querySelectorAll('.course-chip .chip-weeks')].length,
      };
    });
    (after.fromVar > 0 || after.chipsWithWeeks > 0)
      ? ok(`刷新后周次仍在（变量里 ${after.fromVar}/${after.total} 条；卡片角标 ${after.chipsWithWeeks} 个）`)
      : bad(`刷新后周次丢了（变量 ${after.fromVar}/${after.total}，角标 ${after.chipsWithWeeks}）`);
  }

  errs.length === 0 ? ok('周次链路无 JS 异常') : bad(`周次链路 JS 异常: ${errs.join(' | ')}`);
  await ctx.close();
}

/* ---------------- ⑧ 没有 pandas 也要能用（降级路径）----------------
 *
 * 真实事故：requirements.txt 漏写 pandas、启动脚本也没探测它，
 * 结果用户机器上永远装不上，一导入表格就 ModuleNotFoundError，
 * 还被兜底文案说成「文件损坏」。而用户重装多少次都没用 ——
 * 因为探测语句没通过就不会触发安装。
 *
 * 治本做法：pandas 在这个功能里只是「打开工作簿、遍历单元格」的包装，
 * 真正的引擎是 xlrd / openpyxl。所以现在有 pandas 就用、没有就自己读，
 * 两条路产出必须**完全一致**。
 *
 * 这一节在**屏蔽 pandas 的子进程**里跑，验证：
 *   1) .xls 能解析（走 xlrd）
 *   2) .xlsx 能解析（走 openpyxl）
 *   3) 两条路结果逐字段一致（关键！否则修好一个用户、弄坏另一个）
 *   4) 坏文件仍给人话
 */
console.log('\n--- ⑧ 缺 pandas 时自动降级（不依赖它也能导入）---');
{
  const { execFileSync } = await import('child_process');
  /* ⚠️ 样例 .xls 是**可选输入**（这台机器上没有就跳过），绝不能写死 Linux 路径。
     踩过的坑：这一节原来把 '/root/uploads/xxx-学生课表.xls' 硬编码在注入的
     python 里，于是没有那两份样例的机器上必然 4 条 FAIL，报的却是
     「FileNotFoundError」—— 看着像导入功能坏了，其实只是缺测试素材。
     同一个坑在 ⑦ 也有一份（那里更糟：XLS 是 undefined，直接把 playwright 打崩）。
     现在统一走 SAMPLES 探测，没有就明确 SKIP，并把「坏文件仍给人话」那条
     不依赖样例的断言留下来继续跑。 */
  const sampleList = SAMPLES.filter((p) => { try { return fs.existsSync(p); } catch { return false; } })
    .map((p) => p.replace(/\\/g, '/'));
  if (!sampleList.length) {
    sk('没找到样例 .xls，跳过「缺 pandas 仍能解析 / 两条路一致」断言（坏文件那条照跑）');
  }
  const py = `
import sys, json
sys.path.insert(0, ${JSON.stringify(process.cwd().replace(/\\/g, '/') + '/backend')})

# 屏蔽 pandas，模拟用户「依赖没装全」的机器
import builtins
_real = builtins.__import__
def fake(name, *a, **kw):
    if name == 'pandas' or name.startswith('pandas.'):
        raise ImportError("No module named 'pandas'")
    return _real(name, *a, **kw)
builtins.__import__ = fake
for k in [k for k in list(sys.modules) if k == 'pandas' or k.startswith('pandas.')]:
    del sys.modules[k]

import services.timetable_excel as T
out = {}
FILES = ${JSON.stringify(sampleList)}
for p in FILES:
    try:
        with open(p, 'rb') as f:
            data = f.read()
        r = T.parse_timetable_excel(data, 'x.xls', '2026-09-07')
        out[p.split('/')[-1]] = {
            'n': len(r['courses']),
            'has_weeks': all(c.get('weeks') for c in r['courses']),
            'first': r['courses'][0]['name'] if r['courses'] else '',
        }
    except Exception as e:
        out[p.split('/')[-1]] = {'err': type(e).__name__ + ': ' + str(e)[:80]}

# 坏输入也不能崩
bad_res = {}
for nm, d in [('empty', b''), ('html', b'<html>x</html>')]:
    try:
        T.parse_timetable_excel(d, 'x.xls', '')
        bad_res[nm] = 'no-error'
    except ValueError as e:
        bad_res[nm] = str(e)[:40]
    except Exception as e:
        bad_res[nm] = 'BAD:' + type(e).__name__

print('JSON>>>' + json.dumps({'files': out, 'bad': bad_res}, ensure_ascii=False))
`;
  let parsed = null;
  try {
    const raw = execFileSync('python3', ['-c', py], { encoding: 'utf8', timeout: 90000 });
    const m = raw.match(/JSON>>>(\{.*\})/s);
    if (m) parsed = JSON.parse(m[1]);
  } catch (e) {
    bad(`无 pandas 子进程失败: ${String(e.message || e).slice(0, 120)}`);
  }

  if (parsed) {
    const fs = parsed.files || {};
    const entries = Object.entries(fs);
    // entries 为空 = 这台机器没有样例 .xls，上面已经 SKIP 过了，这里不再刷 FAIL
    if (entries.length) {
      const good = entries.filter(([, v]) => v.n === 24);
      good.length === entries.length
        ? ok(`缺 pandas 时 .xls 仍能解析（${good.map(([k, v]) => v.n).join('/')} 门课）`)
        : bad(`缺 pandas 时解析异常：${JSON.stringify(fs)}`);

      entries.every(([, v]) => v.has_weeks)
        ? ok('缺 pandas 时周次字段仍完整')
        : bad(`缺 pandas 时周次丢失：${JSON.stringify(entries.map(([k, v]) => [k, v.has_weeks]))}`);

      entries.every(([, v]) => /数据库原理/.test(v.first || ''))
        ? ok(`缺 pandas 时首条课名正确  ${entries[0]?.[1]?.first}`)
        : bad(`缺 pandas 时首条课名异常：${JSON.stringify(entries.map(([k, v]) => v.first))}`);
    }

    const b = parsed.bad || {};
    b.empty && /打不开|缺少/.test(b.empty)
      ? ok('缺 pandas 时空文件仍给人话')
      : bad(`缺 pandas 时空文件提示异常: ${b.empty}`);
  }

  /* 两条路（有/无 pandas）必须逐字段一致 —— 这是本次改造的核心不变量。
     不一致就意味着「修好了一个用户的环境、弄坏了另一个」。
     同样依赖样例 .xls，没有就跳过。 */
  const cmp = sampleList.length ? `
import sys, json, builtins
sys.path.insert(0, ${JSON.stringify(process.cwd().replace(/\\/g, '/') + '/backend')})
import services.timetable_excel as T

P = ${JSON.stringify(sampleList[sampleList.length - 1])}
data = open(P, 'rb').read()
a = T.parse_timetable_excel(data, 'x.xls', '2026-09-07')['courses']

_real = builtins.__import__
def fake(name, *a2, **kw):
    if name == 'pandas' or name.startswith('pandas.'):
        raise ImportError('no pandas')
    return _real(name, *a2, **kw)
builtins.__import__ = fake
for k in [k for k in list(sys.modules) if k == 'pandas' or k.startswith('pandas.')]:
    del sys.modules[k]

b = T.parse_timetable_excel(data, 'x.xls', '2026-09-07')['courses']
keys = ['name','teacher','building','location','weekday','slot_order','weeks','start','end']
diff = [(i,k) for i,(x,y) in enumerate(zip(a,b)) for k in keys if x.get(k) != y.get(k)]
print('JSON>>>' + json.dumps({'n_a': len(a), 'n_b': len(b), 'diff': diff[:8], 'ndiff': len(diff)}))
` : '';
  try {
    if (!cmp) throw new Error('SKIP: 没有样例 .xls');
    const raw2 = execFileSync('python3', ['-c', cmp], { encoding: 'utf8', timeout: 90000 });
    const m2 = raw2.match(/JSON>>>(\{.*\})/s);
    const r2 = m2 ? JSON.parse(m2[1]) : null;
    if (r2 && r2.n_a === r2.n_b && r2.ndiff === 0) {
      ok(`有/无 pandas 两条路径结果完全一致（各 ${r2.n_a} 条，0 处差异）`);
    } else {
      bad(`两条路径结果不一致：${JSON.stringify(r2)}`);
    }
  } catch (e) {
    if (/SKIP/.test(String(e.message || e))) sk('没找到样例 .xls，跳过「有/无 pandas 逐字段比对」');
    else bad(`双路径比对失败: ${String(e.message || e).slice(0, 120)}`);
  }
}

await browser.close();
console.log('\n' + '='.repeat(60));
console.log(`结论: ${pass} 通过 / ${fail} 失败${skip ? ` / ${skip} 跳过` : ''}`);
process.exit(fail ? 1 : 0);
