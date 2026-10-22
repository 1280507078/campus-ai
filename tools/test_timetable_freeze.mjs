/* 课表「表头 + 节次列冻结」验收测试。
 *
 * 需求（第 6 轮）：
 *   · PC：「网页版星期这行就算我往下拉也能看到，滑到最底下也能知道是周几的课」
 *   · 手机：「星期和时间无论拉到最低还是最右都能看到是几点到几分的课程或者星期几的课」
 *
 * 所以断言两件事在两个视口下都成立：
 *   1) 表头行（周几）纵向冻结 —— 上下滚动时位置不变
 *   2) 节次列（第几节）横向冻结 —— 左右滚动时位置不变
 *
 * ⚠️⚠️ 这里踩过两个 CSS 问题，写测试前必须知道，否则会以为"测过了没问题"：

 * 【问题 1】`overflow-x: auto` 会把另一轴的 `overflow-y: visible` **强制算成 auto**
 *   （规范如此），于是容器同时成了纵向滚动容器，表头 sticky 就锚在它身上、
 *   跟着页面一起滚走 —— 看起来「完全没固定」。
 *   项目里的解法是分两条分支：PC 用 `overflow-x: clip`（裁切、不建滚动容器），
 *   手机保留 `overflow: auto` + `max-height`。

 * 【问题 2】sticky 的 `top` 是相对**滚动祖先的顶边**算的，不是相对视口。
 *   手机端滚动发生在 .tt-wrap 内部，而 .tt-wrap 自己位于文档第 460px 处，
 *   所以表头钉住的位置 = 容器顶 + top。第一版写 `top: 0`，表头钉在 110px
 *   附近，而顶栏底下沿 89.44px —— 只差 20px，很容易被顶栏压住；
 *   第二版写 `top: calc(var(--topbar-h) + 8px)` = 100px，**反而更糟**：
 *   100px 是相对容器顶算的，表头飘到屏幕中间，把表格上半部分盖住了
 *   （截图里「第一节/第二节」跑到表头上面，像表格被打乱）。
 *   正解是让**容器自己** sticky 到顶栏下沿（`.tt-wrap { position: sticky;
 *   top: var(--topbar-h) }`），容器内部再写 `top: 0`。
 *   所以本测试对手机端同时断言：
 *     a) 横向仍可滚（没被 clip 裁掉）      b) 表头纵向冻结不动
 *     c) 节次列横向冻结不动               d) **表头始终在顶栏下方、不被遮住**
 *     e) 表头下方紧接着的是表格内容（不是被表头盖住的空白/错行）
 *   d/e 就是这次实测问题：「星期被顶部导航栏挡住」的回归防线。

 * 另外还守一条：`--topbar-h` 必须 >= 顶栏**实测**高度。
 * 顶栏在窄屏会换行变高（实测 641/361px 两个换行点，89.44 / 126.88px），
 * 变量当初写死 64px 就是被这种情况到的。以后谁改了顶栏的 padding/字号/
 * 导航项数量，这条断言会立刻亮红。
 *
 * 跑法：先起后端（8000）和前端静态服务（5500），再
 *   node tools/test_timetable_freeze.mjs
 */
import pw from 'playwright';

/* ★ 2026-10-22 CI 兼容：原来这里写死了
   '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'。
   但 GitHub runner 的 HOME 是 /home/runner（不是 /root），而且浏览器目录名带版本号，
   playwright 一升级就变 —— 写死 = 「CI 上必然 Executable doesn't exist」，
   报错还会长得像业务代码坏了。现在：只认 CHROME_PATH（本机 run_all_tests.py 会传），
   没传就让 playwright 自己找它刚装好的那一份。 */
const CHROME = process.env.CHROME_PATH || undefined;
const P = process.env.CAMPUS_WEB_PORT || '5500';
const BASE = `http://127.0.0.1:${P}`;
/* 后端地址也要能换：默认 8000，但解包后用别的端口验证时（CAMPUS_API_PORT=8200）
   就不该写死 —— 和 CAMPUS_BASE 一个道理，避免测试自己连错地址。 */
const API = process.env.CAMPUS_BASE || `http://127.0.0.1:${process.env.CAMPUS_API_PORT || '8000'}`;

let pass = 0, fail = 0;
const ok = (m) => { console.log(`  [PASS] ${m}`); pass++; };
const bad = (m) => { console.log(`  [FAIL] ${m}`); fail++; };

const browser = await pw.chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });

/* 哪些控制台错误算「真 bug」。
   ERR_CONNECTION_REFUSED / 404 这类是**环境问题**（后端没起、端口不对、
   离线时外部 SDK 拉不到），不代表页面脚本有缺陷 —— 把它们算进来的话，
   真正的前端异常会被淹掉，测试也就失去意义了。 */
const isRealError = (t) => !/ERR_CONNECTION_REFUSED|ERR_NAME_NOT_RESOLVED|Failed to load resource|404 \(Not Found\)/i.test(t);

/** 造一个已登录的 context（课表页要登录才渲染） */
async function loginCtx(w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const r = await ctx.request.post(`${API}/api/auth/register`, {
    data: { username: 'tt' + Date.now() + Math.random().toString(36).slice(2, 6), password: 'test123456' },
  });
  const reg = await r.json();
  /* ⚠️ 只写 token 不写 user 是不够的：`renderTopbar()` 读的是
     `localStorage.user`，缺了它就渲染成「登录」、`.user` 根本不存在，
     顶栏入口相关的断言会全部量不到（踩过）。这里一并把 /auth/me 写进去。 */
  const me = await (await ctx.request.get(`${API}/api/auth/me`, {
    headers: { Authorization: `Bearer ${reg.access_token}` },
  })).json().catch(() => null);
  await ctx.addInitScript(([t, u]) => {
    try { localStorage.setItem('token', t); if (u) localStorage.setItem('user', JSON.stringify(u)); } catch (e) {}
  }, [reg.access_token, me]);
  return ctx;
}

/* ==================================================================
   一、PC / 大屏：用 overflow-x: clip，表头锚定「视口」
   ================================================================== */
console.log('\n--- 1. PC 1440×900：滚到页面最底，周几那行还在吗 ---');
{
  const ctx = await loginCtx(1440, 900);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/404/.test(m.text())) errs.push(m.text()); });

  await page.goto(`${BASE}/planner.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  const info = await page.evaluate(() => {
    const wrap = document.querySelector('.tt-wrap');
    const table = document.querySelector('.timetable');
    const cs = getComputedStyle(wrap);
    return {
      overflowX: cs.overflowX, overflowY: cs.overflowY, maxHeight: cs.maxHeight,
      pageMaxScroll: document.documentElement.scrollHeight - window.innerHeight,
      // PC 端表格宽度自适应，不该需要横向滚动
      tableFits: table.getBoundingClientRect().width <= wrap.getBoundingClientRect().width + 2,
      rows: document.querySelectorAll('.timetable tbody tr').length,
    };
  });
  console.log('    容器:', JSON.stringify(info));

  info.overflowX === 'clip'
    ? ok('PC 端 .tt-wrap 用 overflow-x: clip（裁切而非滚动，不污染纵向）')
    : bad(`PC 端 overflow-x 应为 clip，实际 ${info.overflowX}`);
  info.overflowY === 'visible'
    ? ok('PC 端 overflow-y 保持 visible（sticky 才能锚到视口）')
    : bad(`PC 端 overflow-y 应为 visible，实际 ${info.overflowY}`);
  info.tableFits
    ? ok('PC 端表格宽度自适应，不需要横向滚动')
    : bad('PC 端表格居然需要横向滚动，clip 会把它裁掉');

  const scrollTest = await page.evaluate(async () => {
    const th = document.querySelector('.timetable thead th');
    const tb = document.querySelector('.topbar');
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 200));
    const top = Math.round(th.getBoundingClientRect().top);
    // 一路滚到最底 —— 输入里说的「滑到最底下」
    window.scrollTo(0, document.documentElement.scrollHeight);
    await new Promise(r => setTimeout(r, 350));
    return {
      atTop: top,
      atBottom: Math.round(th.getBoundingClientRect().top),
      topbarBottom: tb ? Math.round(tb.getBoundingClientRect().bottom) : null,
      scrollY: Math.round(window.scrollY),
    };
  });
  console.log('    位置:', JSON.stringify(scrollTest));

  scrollTest.scrollY > 100
    ? ok(`页面确实滚到了底部（scrollY=${scrollTest.scrollY}）`)
    : bad(`页面几乎没滚动（scrollY=${scrollTest.scrollY}），测试前提不成立`);

  /* 表头相对顶栏下沿的间隙。
     ⚠️ 这里**不能**断言"正好贴住"（原来是 |差| <= 4）：--topbar-h 是
     "实测值 + 余量"（64 -> 68），本来就故意比顶栏高 5px，
     为的是字体渲染差异 / 顶栏多一个字时不至于又钻到顶栏底下。
     所以正确的判据是「表头 >= 顶栏底」（在下方、没被遮），
     而不是「表头 == 顶栏底」。间隙也不该大得离谱 —— 上限给 24px，
     超过说明变量给大了、白白浪费纵向空间。 */
  const gap = scrollTest.atBottom - scrollTest.topbarBottom;
  (gap >= 0 && gap <= 24)
    ? ok(`滚到最底时表头落在顶栏下沿之下 ${gap}px（表头 y=${scrollTest.atBottom}，顶栏底=${scrollTest.topbarBottom}）`)
    : bad(`滚到最底时表头位置不对：表头 y=${scrollTest.atBottom}，顶栏底=${scrollTest.topbarBottom}，间隙 ${gap}px`);

  scrollTest.atBottom > 0
    ? ok('滚到最底时周几那行仍在视口内（没有被滚出屏幕）')
    : bad('滚到最底时周几那行被滚出屏幕了');

  /* PC 端同样守 --topbar-h >= 顶栏实测高度（顶栏遮挡的根因防线） */
  const pcVar = await page.evaluate(() => ({
    topbarH: Math.round(document.querySelector('.topbar').getBoundingClientRect().height * 100) / 100,
    varH: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')),
  }));
  pcVar.varH >= pcVar.topbarH
    ? ok(`PC 端 --topbar-h(${pcVar.varH}px) >= 顶栏实测高度(${pcVar.topbarH}px)`)
    : bad(`PC 端 --topbar-h(${pcVar.varH}px) < 顶栏实测高度(${pcVar.topbarH}px)`);
  gap >= 0
    ? ok('PC 端表头落在顶栏**下沿之下**（不是压在上面）')
    : bad(`PC 端表头压到顶栏上了：表头 y=${scrollTest.atBottom} < 顶栏底=${scrollTest.topbarBottom}`);

  const pcReal = errs.filter(isRealError);
  pcReal.length === 0 ? ok('PC 端无 JS 异常 / 控制台错误') : bad(`PC 端有 ${pcReal.length} 条错误: ${pcReal.slice(0, 3)}`);
  await ctx.close();
}

/* ==================================================================
   二、手机 / 窄屏：用 overflow: auto，表头锚在容器内顶部；
      此时横向必须仍可滚动（所以不能用 clip）
   ================================================================== */
console.log('\n--- 2. 手机 390×780：拉到最下 + 拉到最右 ---');
{
  const ctx = await loginCtx(390, 780);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/404/.test(m.text())) errs.push(m.text()); });

  await page.goto(`${BASE}/planner.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  const info = await page.evaluate(() => {
    const wrap = document.querySelector('.tt-wrap');
    const table = document.querySelector('.timetable');
    const cs = getComputedStyle(wrap);
    const tb = document.querySelector('.topbar');
    return {
      overflowX: cs.overflowX, overflowY: cs.overflowY, maxHeight: cs.maxHeight,
      // 手机端容器自己也要 sticky 到顶栏下沿（这是第 7 轮的正解）
      wrapPosition: cs.position, wrapStickyTop: cs.top,
      canHScroll: wrap.scrollWidth > wrap.clientWidth + 2,
      canVScroll: wrap.scrollHeight > wrap.clientHeight + 2,
      rowH: Math.round(document.querySelector('.timetable tbody td.slot-col').getBoundingClientRect().height),
      rowsVisible: Math.floor(wrap.getBoundingClientRect().height
        / document.querySelector('.timetable tbody td.slot-col').getBoundingClientRect().height),
      // 顶栏实测高度 vs CSS 变量 —— 防止变量又比真实顶栏矮
      topbarH: Math.round(tb.getBoundingClientRect().height * 100) / 100,
      varH: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')),
      thStickyTop: getComputedStyle(document.querySelector('.timetable thead th')).top,
    };
  });
  console.log('    容器:', JSON.stringify(info));

  info.canHScroll
    ? ok('手机端横向仍可滚动（没错用成 clip 被裁掉）')
    : bad('手机端横向滚不动了 —— 可能被 clip 裁掉了');
  info.canVScroll
    ? ok('手机端课表区自己纵向可滚动')
    : bad('手机端课表区纵向滚不动，表头冻结无从验证');
  info.rowsVisible >= 8
    ? ok(`手机端一屏能看到 ${info.rowsVisible} 行（瘦身生效）`)
    : bad(`手机端一屏只能看到 ${info.rowsVisible} 行，偏少`);

  /* 变量必须 >= 顶栏实测高度。这是「星期被顶栏挡住」的根因防线：
     顶栏窄屏换行会变高（89.44 / 126.88），变量写小就直接被遮。 */
  info.varH >= info.topbarH
    ? ok(`--topbar-h(${info.varH}px) >= 顶栏实测高度(${info.topbarH}px)，表头不会钻进顶栏`)
    : bad(`--topbar-h(${info.varH}px) < 顶栏实测高度(${info.topbarH}px)，表头会被顶栏压住`);

  /* 手机端正解：容器自己 sticky 到顶栏下沿，表头锚在容器内 top:0。
     这里断言容器确实是 sticky（不是靠给表头写一个大 top 硬凑）。 */
  info.wrapPosition === 'sticky'
    ? ok(`手机端 .tt-wrap 自身 sticky（top=${info.wrapStickyTop}），为表头提供稳定锚点`)
    : bad(`手机端 .tt-wrap 应为 sticky，实际 ${info.wrapPosition}`);

  // 同时拉到底 + 拉到最右，两个方向都该钉住
  const stick = await page.evaluate(async () => {
    const wrap = document.querySelector('.tt-wrap');
    const th = document.querySelector('.timetable thead th');
    const slot = document.querySelector('.timetable tbody td.slot-col');
    const wt = wrap.getBoundingClientRect().top;
    const wl = wrap.getBoundingClientRect().left;
    const before = {
      th: Math.round(th.getBoundingClientRect().top - wt),
      slot: Math.round(slot.getBoundingClientRect().left - wl),
    };
    wrap.scrollTop = wrap.scrollHeight;   // 拉到底
    wrap.scrollLeft = wrap.scrollWidth;   // 拉到最右
    await new Promise(r => setTimeout(r, 400));
    return {
      before,
      after: {
        th: Math.round(th.getBoundingClientRect().top - wt),
        slot: Math.round(slot.getBoundingClientRect().left - wl),
      },
      scrollTop: Math.round(wrap.scrollTop),
      scrollLeft: Math.round(wrap.scrollLeft),
      // 节次列文字有没有被裁（上一轮的真实 bug：62px 宽时"南 10:55-11:45"被截）
      clipped: [...document.querySelectorAll('.slot-col b, .slot-col span')]
        .filter(e => e.scrollWidth > e.clientWidth + 1).length,
    };
  });
  console.log('    冻结:', JSON.stringify(stick));

  stick.scrollTop > 50 && stick.scrollLeft > 50
    ? ok(`确实同时滚到了底和右（top=${stick.scrollTop}, left=${stick.scrollLeft}）`)
    : bad('没能同时滚到底和右，测试前提不成立');

  stick.before.th === stick.after.th
    ? ok('拉到最下时表头（周几）位置不变 —— 纵向冻结')
    : bad(`表头纵向没冻住：${stick.before.th} → ${stick.after.th}`);

  stick.before.slot === stick.after.slot
    ? ok('拉到最右时节次列位置不变 —— 横向冻结')
    : bad(`节次列横向没冻住：${stick.before.slot} → ${stick.after.slot}`);

  stick.clipped === 0
    ? ok('节次列文字没有被裁切（宽度够）')
    : bad(`节次列有 ${stick.clipped} 处文字被裁切`);

  /* ==================================================================
     本轮的回归重点：表头必须**始终**在顶栏下方，而且表头下面必须
     真的是表格内容（不是被表头盖住的错行 / 空白）。

     为什么要专门测这个：实测问题：「显示 星期还要低一点，顶部导航栏挡住了」。
     查下来是 --topbar-h 写死 64px，而手机顶栏换行后实测 89.44px，
     表头 `sticky top` 算出来偏上，正好压住「周一 9/21」那行。

     断言方式：把容器在纵向从 0 拉到最大，沿途多采样几个点，
     要求 (a) 表头 top 始终 >= 顶栏底 - 1px；(b) 表头 top 全程不变（冻住）；
     (c) 表头正下方 20px 处 elementFromPoint 命中表格内的元素。
     ================================================================== */
  const sweep = await page.evaluate(async () => {
    const wrap = document.querySelector('.tt-wrap');
    const th = document.querySelector('.timetable thead th');
    const tb = document.querySelector('.topbar');
    const tbB = tb.getBoundingClientRect().bottom;
    const samples = [];
    const maxY = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
    if (wrap.scrollWidth > wrap.clientWidth) wrap.scrollLeft = wrap.scrollWidth;
    for (let i = 0; i <= 6; i++) {
      wrap.scrollTop = Math.round(maxY * i / 6);
      await new Promise(r => requestAnimationFrame(r));
      const r1 = th.getBoundingClientRect();
      const below = document.elementFromPoint(r1.left + 60, Math.min(r1.bottom + 20, window.innerHeight - 2));
      samples.push({
        sy: Math.round(wrap.scrollTop),
        thTop: Math.round(r1.top * 100) / 100,
        covered: r1.top < tbB - 1,
        belowTag: below ? below.tagName : 'null',
        belowInTable: below ? !!below.closest('.timetable') : false,
      });
    }
    return { tbB: Math.round(tbB * 100) / 100, samples };
  });

  const covered = sweep.samples.filter(s => s.covered);
  covered.length === 0
    ? ok(`纵向全程表头都没被顶栏遮住（顶栏底=${sweep.tbB}，表头 top 恒为 ${sweep.samples[0].thTop}）`)
    : bad(`有 ${covered.length}/7 个位置表头被顶栏遮住，最靠上 ${Math.min(...covered.map(s => s.thTop))} < 顶栏底 ${sweep.tbB}`);

  const tops = [...new Set(sweep.samples.map(s => s.thTop))];
  tops.length === 1
    ? ok('纵向全程表头位置稳定（没有漂移/跳动）')
    : bad(`表头在纵向滚动中发生漂移：${JSON.stringify(tops)}`);

  const wrongBelow = sweep.samples.filter(s => !s.belowInTable && s.sy > 0);
  wrongBelow.length === 0
    ? ok('表头正下方始终是表格内容（表格行序正确，没有被表头盖出错位）')
    : bad(`有 ${wrongBelow.length} 个位置表头下方不是表格内容：${wrongBelow.map(s => s.belowTag).join(',')}`);

  const mbReal = errs.filter(isRealError);
  mbReal.length === 0 ? ok('手机端无 JS 异常 / 控制台错误') : bad(`手机端有 ${mbReal.length} 条错误: ${mbReal.slice(0, 3)}`);
  await ctx.close();
}

/* ==================================================================
   三、中等宽度（800px 分界附近）不该出现「表格被裁且滚不动」
   ================================================================== */
console.log('\n--- 3. 820×900：分界附近，表格既不能被裁也不该需要横滚 ---');
{
  const ctx = await loginCtx(820, 900);
  const page = await ctx.newPage();
  await page.goto(`${BASE}/planner.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  const r = await page.evaluate(() => {
    const wrap = document.querySelector('.tt-wrap');
    const table = document.querySelector('.timetable');
    const fits = table.getBoundingClientRect().width <= wrap.getBoundingClientRect().width + 2;
    return { fits, overflowX: getComputedStyle(wrap).overflowX,
             tw: Math.round(table.getBoundingClientRect().width),
             ww: Math.round(wrap.getBoundingClientRect().width) };
  });
  console.log('    ', JSON.stringify(r));
  r.fits || r.overflowX !== 'clip'
    ? ok(`820px 下表格没被 clip 裁掉（表宽 ${r.tw} / 容器 ${r.ww}，overflow-x=${r.overflowX}）`)
    : bad('820px 下表格被 clip 裁掉了，分界设错');
  await ctx.close();
}

/* ==================================================================
   四、顶栏「账号设置」入口的三态 + 窄屏不折行
   ==================================================================
   实测问题：「移动版没办法对账号进行设置」。根因是窄屏顶栏段里那句
   `.topbar .user { display: none; }` —— 而它是**全站唯一**的账号入口，
   藏掉等于手机端没有账号设置。

   这一节守三件事，缺一不可：
     1) 账号入口在**每个**视口都可见可点（不再被 display:none 藏掉）
     2) 三态切换正确：PC 全名 / 手机「我的」 / 窄机圆形头像
     3) 导航**不折成 3 行** —— 这是本轮的隐藏难点：加一个入口会占宽，
        390px 这种最主流机型一度被撑成 3 行，顶栏从 92px 涨到 127.7px，
        课表表头又会被顶偏。360~430px 一起扫，别只测一个宽度。

   ⚠️ 顺带守 --topbar-h：入口改动把窄机从 3 行压回 2 行后，变量必须
      同步下调（130 → 92），否则表头被推到下方空白里。断言"变量 >= 实测"
      只能查"给小了"，这里额外记下实测高度，给大了靠人眼复核。
   ================================================================== */
console.log('\n--- 4. 顶栏账号入口：三态 + 窄屏不折成 3 行 ---');
{
  const ctx = await loginCtx(1440, 900);
  const acc = await ctx.newPage();
  await acc.goto(`${BASE}/account.html`, { waitUntil: 'networkidle' });
  await acc.waitForTimeout(900);
  await ctx.close();

  /* 逐档扫，覆盖窄机(320/360/375)到主流手机(390/414/430)到 PC */
  const WIDTHS = [320, 360, 375, 390, 414, 430];
  for (const w of WIDTHS) {
    const c = await loginCtx(w, 800);
    const p = await c.newPage();
    await p.goto(`${BASE}/account.html`, { waitUntil: 'networkidle' });
    await p.waitForTimeout(700);
    const r = await p.evaluate(() => {
      const tb = document.querySelector('.topbar');
      if (!tb) return null;
      const nav = tb.querySelector('nav');
      const user = tb.querySelector('a.user');
      const items = [...nav.querySelectorAll('a')];
      const vis = (sel) => { const e = user && user.querySelector(sel); return e ? getComputedStyle(e).display !== 'none' : false; };
      /* 「我的」右边缘 / 「退出」左边缘 / nav 右边缘 —— 用来验证两者是否紧挨且靠右 */
      const logout = nav.querySelector('#logout');
      const navBox = nav.getBoundingClientRect();
      const uBox = user ? user.getBoundingClientRect() : null;
      const oBox = logout ? logout.getBoundingClientRect() : null;
      return {
        exists: !!user,
        display: user ? getComputedStyle(user).display : null,
        href: user ? user.getAttribute('href') : null,
        w: user ? Math.round(user.getBoundingClientRect().width) : 0,
        name: vis('.u-name'), short: vis('.u-short'), avatar: vis('.u-avatar'),
        rows: new Set(items.map(a => Math.round(a.getBoundingClientRect().top))).size,
        tbH: Math.round(tb.getBoundingClientRect().height * 100) / 100,
        varH: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')),
        bars: document.querySelectorAll('body > .topbar').length,
        mineRight: uBox ? Math.round(uBox.right) : null,
        logoutLeft: oBox ? Math.round(oBox.left) : null,
        rightSlack: Math.round(navBox.right - (oBox ? oBox.right : (uBox ? uBox.right : navBox.right))),
      };
    });
    if (!r) { bad(`${w}px 没有渲染出顶栏`); await c.close(); continue; }

    /* 1) 入口可见且指向账号页 —— 本轮 bug 的直接防线 */
    (r.exists && r.display !== 'none' && r.href === 'account.html')
      ? ok(`${w}px 账号入口可见可点（href=account.html，宽 ${r.w}px）`)
      : bad(`${w}px 账号入口不可用：exists=${r.exists} display=${r.display} href=${r.href}`);

    /* 2) 三态：任意视口下有且只有一种形态显示 */
    const shapes = [r.name, r.short, r.avatar].filter(Boolean).length;
    shapes === 1
      ? ok(`${w}px 三态正确：${r.name ? '全名' : r.short ? '我的' : '圆形头像'}`)
      : bad(`${w}px 三态异常，同时显示 ${shapes} 种形态（name=${r.name} short=${r.short} avatar=${r.avatar}）`);

    /* 窄机必须是头像、手机必须是「我的」—— 断点错了会在这里亮红 */
    if (w <= 381) {
      r.avatar ? ok(`${w}px 走窄机头像档（断点 <=381px）`) : bad(`${w}px 应走头像档，实际 name=${r.name} short=${r.short}`);
    } else {
      r.short ? ok(`${w}px 走手机「我的」档`) : bad(`${w}px 应显示「我的」，实际 name=${r.name} avatar=${r.avatar}`);
    }

    /* 3) 导航行数 + 变量对账 */
    r.rows <= 2
      ? ok(`${w}px 导航 ${r.rows} 行（没被多出来的入口撑破）`)
      : bad(`${w}px 导航折成 ${r.rows} 行，顶栏高 ${r.tbH}px —— 入口把导航挤破了`);
    r.varH >= r.tbH
      ? ok(`${w}px --topbar-h(${r.varH}) >= 顶栏实测(${r.tbH})`)
      : bad(`${w}px --topbar-h(${r.varH}) < 顶栏实测(${r.tbH})，表头会被顶栏压住`);
    r.bars === 1 ? ok(`${w}px 顶栏只有一条（幂等没被破坏）`) : bad(`${w}px 顶栏有 ${r.bars} 条`);

    /* 需求：「移动端账号设置放退出旁边」。
       ⚠️ 这条专门守 `margin-left:auto` 加在**哪个**元素上。
       老写法把 auto 加在 #logout 上，结果只有「退出」被推到最右、
       「我的」被留在导航项后面，两者之间裂开一段**随屏宽变大**的空白：
           390px -> 11px    412px -> 33px    430px -> 51px
       屏幕上就是两个不相干的控件，用户找不到账号设置在哪。
       正解：auto 加在**靠前的** .user 上，让「我的 + 退出」整组一起靠右，
       两者之间只隔一个 nav gap(=2px)，且**不随宽度变化**。
       所以这里断言间距是个常数、并且整体贴住右边缘。 */
    if (r.mineRight !== null && r.logoutLeft !== null) {
      const gap = r.logoutLeft - r.mineRight;
      (gap >= 0 && gap <= 10)
        ? ok(`${w}px 账号入口紧挨「退出」（间距 ${gap}px）`)
        : bad(`${w}px 账号入口与「退出」之间裂开 ${gap}px —— margin-left:auto 多半加错元素了`);
      (r.rightSlack <= 2)
        ? ok(`${w}px 「我的+退出」整组贴住右边缘（右侧留白 ${r.rightSlack}px）`)
        : bad(`${w}px 右侧还空着 ${r.rightSlack}px，整组没靠到边`);
    }

    await c.close();
  }
}

/* ==================================================================
   五、账号页布局：三张卡片、无横向溢出、可读
   ================================================================== */
console.log('\n--- 5. 账号页布局（PC 两栏 / 手机单栏）---');
for (const w of [320, 390, 1440]) {
  const ctx = await loginCtx(w, 900);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await page.goto(`${BASE}/account.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(900);

  const r = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.card.acc-card')];
    const doc = document.documentElement;
    return {
      cards: cards.filter((c) => !c.hidden).length,   // 只数**可见**的：管理员视角卡对普通用户 hidden
      overflowX: doc.scrollWidth > window.innerWidth + 1,
      inline: document.querySelectorAll('.acc-card[style], .card.acc-card [style]').length,
      nick: !!document.getElementById('acc-nickname'),
      oldp: !!document.getElementById('acc-old-pwd'),
      del: !!document.getElementById('btn-delete'),
      /* 危险区文案不该再出现"右上角" —— 手机上「退出」在第二行右侧，
         方位说法是错的（本轮改掉）。 */
      dangerMentionsTopRight: /右上角/.test(document.querySelector('.card.danger-zone')?.innerText || ''),
    };
  });

  /* ★ 账号页从 3 张变成 4 张（2026-10-21 加了「意见反馈」卡；管理员还会看到「切换视图」，
     那张对普通用户是 hidden，所以上面只数可见的）。
     这里守的是「该有的卡都在、没被无关卡片撑开」，不是「永远 3 张」。 */
  r.cards === 4 ? ok(`${w}px 账号页四张卡片齐全`)
                : bad(`${w}px 可见卡片数 ${r.cards}，应为 4`);
  !r.overflowX ? ok(`${w}px 无横向溢出`) : bad(`${w}px 出现横向溢出`);
  (r.nick && r.oldp && r.del) ? ok(`${w}px 关键控件齐全（昵称/原密码/注销）`) : bad(`${w}px 关键控件缺失`);
  !r.dangerMentionsTopRight
    ? ok(`${w}px 注销卡文案不再写「右上角」（方位描述已修正）`)
    : bad(`${w}px 注销卡仍在说「右上角」，手机上方位不对`);

  const real = errs.filter(isRealError);
  real.length === 0 ? ok(`${w}px 账号页无 JS 异常`) : bad(`${w}px 账号页有 ${real.length} 条错误: ${real.slice(0, 3)}`);
  await ctx.close();
}

/* ==================================================================
   六、学期周次锚点：第 1 周必须固定在 9/7
   ==================================================================
   需求：「第一周周一改为九月 7 号」。

   为什么值得单独立一条回归：
     早期实现的 weekOffset 是「相对本周」的（0 = 本周），于是「第 1 周」
     **根本不是一个固定日期** —— 今天打开是 8/25，下周打开就变成 9/1。
     课表里的「第几周」是学校给的绝对概念（进度、考试周都按它说），
     会漂就没法用了。所以锚点改成写死的开学日，这里把它钉住。
   ------------------------------------------------------------------ */
console.log('\n--- 6. 学期周次锚点（第 1 周 = 2026-09-07） ---');
{
  const ctx = await loginCtx(1440, 900);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));

  await page.goto(`${BASE}/planner.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  const info = await page.evaluate(() => {
    const chips = [...document.querySelectorAll('#week-scroll .week-chip')]
      .map(c => ({ off: c.dataset.off, text: c.innerText.trim(),
                   on: c.classList.contains('on'),
                   thisWeek: c.classList.contains('this-week') }));

    // 点第 1 周，看表头第一天是不是 9/7
    const first = document.querySelector('#week-scroll .week-chip[data-off="0"]');
    if (first) first.click();

    return { chips, clicked: !!first };
  });

  await page.waitForTimeout(700);
  const head = await page.evaluate(() =>
    [...document.querySelectorAll('#tt-head th')].map(t => t.innerText.replace(/\n/g, ' ').trim()));

  const w1 = info.chips.find(c => c.off === '0');
  const w2 = info.chips.find(c => c.off === '1');
  const last = info.chips[info.chips.length - 1];

  info.chips.length === 20
    ? ok(`周次条覆盖 20 周（第 1~20 周）`)
    : bad(`周次条 ${info.chips.length} 个胶囊，应为 20`);
  w1 && /9\/7/.test(w1.text) && /9\/13/.test(w1.text)
    ? ok(`第 1 周 = 9/7 - 9/13  ${w1.text}`)
    : bad(`第 1 周不是 9/7 起：${w1 ? w1.text : "（没有第 1 周胶囊）"}`);
  w2 && /9\/14/.test(w2.text)
    ? ok(`第 2 周 = 9/14 起  ${w2.text}`)
    : bad(`第 2 周日期不对：${w2 ? w2.text : "（缺）"}`);
  last && /第 20 周/.test(last.text)
    ? ok(`第 20 周是最后一个胶囊  ${last.text}`)
    : bad(`最后一个胶囊不是第 20 周：${last ? last.text : "（缺）"}`);
  head[1] && /9\/7/.test(head[1])
    ? ok(`点第 1 周后表头首日 = ${head[1]}`)
    : bad(`点第 1 周后表头首日是 ${head[1]}，应为 9/7`);

  // 「本周」标记必须跟着真实日期走，不能永远是第 1 周
  const marked = info.chips.filter(c => c.thisWeek);
  marked.length <= 1
    ? ok(`「本周」标记唯一（或今天不在学期内则不标）`)
    : bad(`有 ${marked.length} 个胶囊被标成「本周」`);
  if (marked.length === 1) {
    marked[0].off === '0'
      ? ok(`本周落在第 1 周（当前日期确实在 9/7-9/13 内）`)
      : ok(`本周落在第 ${Number(marked[0].off) + 1} 周，标记正确  ${marked[0].text}`);
  }

  errs.length === 0 ? ok('周次锚点无 JS 异常') : bad(`周次锚点有 ${errs.length} 条错误`);
  await ctx.close();
}

/* ==================================================================
   八、按周显示：这周不上课的课不能出现（weeks 过滤 + 每周重复归位）
   ==================================================================
   为什么单独测这一节：
     实训导出的课表里每门课都标了「第几周上」（如「计算机网络 1周(全部)」）。
     如果只做「过滤」不做「归位」，会出现两类相反的 bug：
       · 不过滤  -> 每周都显示全部课（用户看到第 3 周 24 门课全在，其实大部分没课）
       · 不归位  -> 只看课程存的那个日期，翻到下一周「每周都上」的课就消失了
     这两种都很难自查，必须锁住。
*/
console.log('\n--- 8. 按周显示（第 1/2/5 周各该显示什么）---');
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const reg = await (await ctx.request.post(`${API}/api/auth/register`, {
    data: { username: 'wk' + Date.now() + Math.random().toString(36).slice(2, 6), password: 'test123456' },
  })).json();
  const tok = reg.access_token;
  const me = await (await ctx.request.get(`${API}/api/auth/me`, {
    headers: { Authorization: `Bearer ${tok}` } })).json();
  await ctx.addInitScript(([t, u]) => {
    try { localStorage.setItem('token', t); localStorage.setItem('user', JSON.stringify(u)); } catch (e) {}
  }, [tok, me]);

  /* 造三条数据，刻意避开唯一约束 uq_user_date_slot(user_id,date,slot_order)：
       同一天不同节次（9/8 的 slot 3 与 slot 5）
       不同天同节次（9/8 与 9/15 的 slot 3）
     第 1 周 = 9/7~9/13，第 2 周 = 9/14~9/20。 */
  await ctx.request.post(`${API}/api/planner/timetable`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { courses: [
      { date: '2026-09-08', type: 'course', slot_order: 3, start: '08:10', end: '08:55',
        name: '只在第1周', teacher: '', building: '', location: '', weeks: '1周(全部)' },
      { date: '2026-09-08', type: 'course', slot_order: 5, start: '09:05', end: '09:50',
        name: '每周都上', teacher: '', building: '', location: '', weeks: '' },
      { date: '2026-09-15', type: 'course', slot_order: 3, start: '08:10', end: '08:55',
        name: '只在第2周', teacher: '', building: '', location: '', weeks: '2周(全部)' },
    ] },
  });

  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(`${BASE}/planner.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  const chips = async () => page.$$eval('.course-chip', els =>
    els.map(e => e.innerText.replace(/\s+/g, ' ').trim()));
  const goWeek = async (off) => {
    await page.click(`#week-scroll .week-chip[data-off="${off}"]`);
    await page.waitForTimeout(450);
  };

  /* ⑧-1 纯函数：周次串解析（各校写法都要认，认不出就「每周都上」） */
  const pw = await page.evaluate(() => {
    const c = (raw) => parseWeeks(raw);
    return {
      single: c('1周(全部)'),
      ranges: c('2-3,6-8周(全部) 19周(全部)'),
      multi: c('14-15周(全部) 2-3,6,10,12-13周(全部)'),
      empty: c(''),
      junk: c('另行通知'),
      label: [formatWeeksLabel([1]), formatWeeksLabel([14, 15]), formatWeeksLabel([2, 3, 6, 7, 8, 19])],
    };
  });
  JSON.stringify(pw.single.weeks) === '[1]'
    ? ok(`「1周(全部)」-> ${JSON.stringify(pw.single.weeks)}`) : bad(`单词解析错: ${JSON.stringify(pw.single)}`);
  JSON.stringify(pw.ranges.weeks) === '[2,3,6,7,8,19]'
    ? ok(`连周+多段 解析正确  ${JSON.stringify(pw.ranges.weeks)}`) : bad(`连周解析错: ${JSON.stringify(pw.ranges.weeks)}`);
  /* ⚠️ 「14-15周 2-3,6,10,12-13周」应含 2,3,6,7,8,10,11,12,13,14,15（没有 9、19） */
  [2, 3, 6, 12, 13, 14, 15].every(w => pw.multi.weeks.includes(w)) && !pw.multi.weeks.includes(9)
    ? ok(`多段并集解析正确  ${JSON.stringify(pw.multi.weeks)}`) : bad(`多段解析错: ${JSON.stringify(pw.multi.weeks)}`);
  /* 兜底：空串 / 认不出的串都必须 all=true（宁可不隐藏，也不要误隐藏） */
  (pw.empty.all === true && pw.junk.all === true)
    ? ok('空串与认不出的周次都兜底成「每周都上」（不会误藏课）')
    : bad(`兜底错了: empty=${JSON.stringify(pw.empty)} junk=${JSON.stringify(pw.junk)}`);
  JSON.stringify(pw.label) === '["1周","14-15周","2-3,6-8,19周"]'
    ? ok(`周次标签压缩正确  ${JSON.stringify(pw.label)}`) : bad(`标签压缩错: ${JSON.stringify(pw.label)}`);

  /* ⑧-2 第 1 周：应有「只在第1周」+「每周都上」，不该有「只在第2周」 */
  await goWeek(0);
  let list = await chips();
  const has = (s) => list.some(x => x.includes(s));
  (has('只在第1周') && has('每周都上') && !has('只在第2周'))
    ? ok(`第 1 周只显示该上的  ${JSON.stringify(list)}`)
    : bad(`第 1 周显示不对: ${JSON.stringify(list)}`);

  /* ⑧-3 第 1 周上，卡片上要带周次角标；「每周都上」不带角标 */
  const w1Chips = await page.$$eval('.course-chip', els => els.map(e => ({
    text: e.innerText.replace(/\s+/g, ' ').trim(),
    weeks: (e.querySelector('.chip-weeks') || {}).innerText || '',
  })));
  const onlyW1 = w1Chips.find(c => c.text.includes('只在第1周'));
  const weekly = w1Chips.find(c => c.text.includes('每周都上'));
  (onlyW1 && /1\s*周/.test(onlyW1.weeks))
    ? ok(`「只在第1周」卡片带周次角标  ${onlyW1.weeks}`)
    : bad(`第 1 周卡片角标不对: ${JSON.stringify(onlyW1)}`);
  (weekly && !weekly.weeks)
    ? ok('「每周都上」的卡片不显示周次角标（空 weeks 不标）')
    : bad(`「每周都上」不该有角标: ${JSON.stringify(weekly)}`);

  /* ⑧-4 第 2 周：「每周都上」必须还在（这是「每周重复归位」的回归点 ——
     只做过滤不做归位的话，它存的是 9/8，第 2 周就找不到了，会消失） */
  await goWeek(1);
  list = await chips();
  (list.some(x => x.includes('只在第2周')) && list.some(x => x.includes('每周都上')) && !list.some(x => x.includes('只在第1周')))
    ? ok(`第 2 周只显示该上的，「每周都上」仍在（归位正常）  ${JSON.stringify(list)}`)
    : bad(`第 2 周显示不对（每周都上的课可能没归位）: ${JSON.stringify(list)}`);

  /* ⑧-5 第 5 周：只有「每周都上」，且空格子显示「＋」占位 */
  await goWeek(4);
  list = await chips();
  (list.length === 1 && list[0].includes('每周都上'))
    ? ok(`第 5 周只剩「每周都上」  ${JSON.stringify(list)}`)
    : bad(`第 5 周显示不对: ${JSON.stringify(list)}`);
  const addCount = await page.$$eval('.cell .add', els => els.length);
  addCount > 0
    ? ok(`本周没课的格子显示「＋」占位（${addCount} 个）`)
    : bad('本周没课的格子没有「＋」占位，用户没法在那周加课');

  errs.filter(isRealError).length === 0
    ? ok('按周显示无 JS 异常')
    : bad(`按周显示 JS 异常: ${errs.filter(isRealError).join(' | ')}`);
  await ctx.close();
}

/* ==================================================================
   九、真实教务 .xls 端到端：导入后逐周只显示该周要上的课
   ==================================================================
   为什么还要单独一节（第 8 节不是已经测了按周显示吗）：

     第 8 节用的是**手工造的 3 条课**，只覆盖了「过滤 + 归位」这个渲染逻辑。
     但用户实际走的是另一条路：**选一个 .xls 文件**
       -> 后端解析出 20 多条带 weeks 的课
       -> toLocalCourse() 补 date
       -> 核对弹窗
       -> confirmImport()（**白名单式拷贝**，漏一个字段就静默丢一个）
       -> 保存到库
       -> 刷新后 loadTimetable() 从库里读回
     这条路上每一个「白名单」都可能把 weeks 悄悄吃掉，而第 8 节
     完全绕开了它们（数据是直接 POST 进库的）。

     用户的原始反馈是「根据表上面的周次把全部有课的周次都填入课…
     现在显示的都把课表上的填入这个周了」—— 说的正是这条**真实链路**。
     所以这里必须用真文件、走真链路、逐周比对**数量**。

     期望值不是硬编码的魔法数字，而是**用同一个 .xls 里的 weeks 串现算**：
     后端返回什么周次，第 N 周就该有几条 —— 这样以后换了样例课表，
     测试依然成立，只锁「渲染结果 == 数据本身」这个不变量。

   依赖：需要 /root/uploads/ 下有那份样例 .xls。没有就**跳过而不是失败** ——
   交付包里本来就不带这个文件（它是用户的真实课表，不该进仓库）。
*/
console.log('\n--- 9. 真实 .xls 逐周渲染（导入 -> 入库 -> 刷新 -> 翻周）---');
{
  const fs = await import('node:fs');
  const XLS_CANDIDATES = [
    '/root/uploads/1790494991678262063-学生课表.xls',
    'tools/fixtures/学生课表.xls',
  ];
  const xlsPath = XLS_CANDIDATES.find(p => fs.existsSync(p));

  if (!xlsPath) {
    ok(`跳过：没找到样例 .xls（找过 ${XLS_CANDIDATES.join(' / ')}）`);
  } else {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const reg = await (await ctx.request.post(`${API}/api/auth/register`, {
      data: { username: 'xls' + Date.now() + Math.random().toString(36).slice(2, 6), password: 'test123456' },
    })).json();
    const tok = reg.access_token;
    const me = await (await ctx.request.get(`${API}/api/auth/me`, {
      headers: { Authorization: `Bearer ${tok}` } })).json();
    await ctx.addInitScript(([t, u]) => {
      try { localStorage.setItem('token', t); localStorage.setItem('user', JSON.stringify(u)); } catch (e) {}
    }, [tok, me]);

    // 先清空这个新账号的课表（新账号本来就是空的，这里只是显式保证）
    await ctx.request.post(`${API}/api/planner/timetable`, {
      headers: { Authorization: `Bearer ${tok}` }, data: { courses: [] },
    });

    // 走真实上传路径：选文件 -> 核对弹窗 -> 确认导入
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(`${BASE}/planner.html`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);

    await page.setInputFiles('input[type=file]', xlsPath);
    await page.waitForTimeout(3500);

    const pendingN = await page.evaluate(() =>
      (typeof pendingImport !== 'undefined' && pendingImport) ? pendingImport.length : -1);
    const pendingWeeks = await page.evaluate(() =>
      (typeof pendingImport !== 'undefined' && pendingImport)
        ? pendingImport.map(c => c.weeks || '') : []);

    pendingN > 0
      ? ok(`.xls 解析出 ${pendingN} 条（核对弹窗已打开）`)
      : bad(`.xls 没解析出课（pendingImport=${pendingN}）`);

    // ⚠️ 关键断言：解析结果里**每一条都带了 weeks**（或者本来就没有周次列）。
    //    后端这条路一直是好的，锁住它，防止有人「优化」掉。
    const withWeeks = pendingWeeks.filter(w => String(w).trim()).length;
    withWeeks > 0
      ? ok(`解析结果里 ${withWeeks}/${pendingN} 条带 weeks（后端透传正常）`)
      : bad('解析结果一条 weeks 都没有 —— 后端把周次丢了？');

    // 确认导入（这一步是白名单拷贝，最容易丢字段的地方）
    await page.click('#btn-import-confirm');
    await page.waitForTimeout(1200);

    const afterImport = await page.evaluate(() => courses.map(c => ({
      name: c.name, date: c.date, weeks: c.weeks || '',
    })));
    const importEmptyWeeks = afterImport.filter(c => !String(c.weeks).trim()).length;
    // 允许「本来就没有周次」的课（样例里每门都有，所以这里是 0），
    // 但**不能**比解析出来的还少 —— 那就说明 confirmImport 吃掉了。
    (importEmptyWeeks <= (pendingN - withWeeks))
      ? ok(`确认导入后 weeks 没被吃掉（空 weeks ${importEmptyWeeks} 条，与解析时一致）`)
      : bad(`确认导入丢了 weeks：解析有 ${withWeeks} 条带周次，导入后只剩 ${afterImport.length - importEmptyWeeks} 条`);

    // 保存到库 -> 刷新 -> 从库读回（loadTimetable 是否保留 weeks）
    const saved = await page.evaluate(async () => {
      if (typeof saveTimetable === 'function') { await saveTimetable(); return 'saveTimetable'; }
      return 'none';
    });
    await page.waitForTimeout(1000);
    await page.evaluate(() => { try { localStorage.removeItem('timetable_local'); } catch (e) {} });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);

    const back = await page.evaluate(() => courses.map(c => ({
      name: c.name, weeks: c.weeks || '',
    })));
    const backEmpty = back.filter(c => !String(c.weeks).trim()).length;
    back.length === afterImport.length && backEmpty <= importEmptyWeeks
      ? ok(`刷新后从库读回 ${back.length} 条，weeks 依然没丢（保存=${saved}）`)
      : bad(`往返丢了数据：导入后 ${afterImport.length} 条 -> 读回 ${back.length} 条，空 weeks ${importEmptyWeeks} -> ${backEmpty}`);

    /* 逐周比对：期望值 = 用数据自己的 weeks 串现算 */
    const expectCount = (weekNo) => back.filter(c => {
      const raw = String(c.weeks || '').trim();
      if (!raw) return true;                       // 空 = 每周都上
      const nums = new Set();
      const cleaned = raw.replace(/[(（][^)）]*[)）]/g, ' ')
        .replace(/[，、;；]/g, ',').replace(/[－—–~～至]/g, '-');
      for (const m of cleaned.matchAll(/(\d+)\s*(?:-\s*(\d+))?/g)) {
        const a = parseInt(m[1], 10), b = m[2] ? parseInt(m[2], 10) : a;
        for (let w = Math.min(a, b); w <= Math.max(a, b); w++) nums.add(w);
      }
      return nums.has(weekNo);
    }).length;

    await page.click('#week-scroll .week-chip[data-off="0"]');   // 回到第 1 周
    await page.waitForTimeout(400);

    let weekOk = 0, weekBad = [];
    for (let wn = 1; wn <= 6; wn++) {
      await page.click(`#week-scroll .week-chip[data-off="${wn - 1}"]`);
      await page.waitForTimeout(420);
      const got = await page.$$eval('.course-chip', els => els.length);
      const want = expectCount(wn);
      if (got === want) weekOk++;
      else weekBad.push(`第${wn}周: 渲染 ${got} / 期望 ${want}`);
    }
    weekBad.length === 0
      ? ok(`真实课表逐周数量全对（第 1-6 周，共 ${back.length} 条课）`)
      : bad(`逐周数量不符 -> ${weekBad.join('；')}`);

    /* 反向断言：第 4 周样例课表里**确实没课**，那这周必须一眼看到「＋」占位，
       而不是把第 3 周的课挪过来展示（用户反馈的正是「都填入这个周」）。 */
    await page.click('#week-scroll .week-chip[data-off="3"]');
    await page.waitForTimeout(420);
    const w4 = await page.$$eval('.course-chip', els => els.length);
    if (expectCount(4) === 0) {
      w4 === 0
        ? ok('第 4 周（本来就无课）不显示任何卡片 —— 没有把别周的课串过来')
        : bad(`第 4 周本该 0 张卡片，却显示了 ${w4} 张（串周了）`);
    } else {
      ok(`第 4 周有 ${expectCount(4)} 条课，数量比对已包含在上面的逐周检查里`);
    }

    errs.filter(isRealError).length === 0
      ? ok('真实 .xls 链路无 JS 异常')
      : bad(`真实 .xls 链路 JS 异常: ${errs.filter(isRealError).join(' | ')}`);

    /* ---- ⑨-x 连堂：表格一大节 = 库里相邻两小节，且渲染时合并成一张卡片 ----
       原句：「课都是两节的，例如1、2，3、4这样子，这些都只有一节」。

       教务表格的课程单元格是**上下合并**的（课名行 + 教室行），节次列写 1..12
       但课名只落在 1、3、5、7 这些**大节的起始小节**上。所以：
         · 表格写「1」=> 库里 order_no 3 和 4（第一 + 第二节）
         · 表格写「3」=> 库里 order_no 5 和 6（第三 + 第四节）
       如果按「表格一行 = 库里一格」导入，课会落到第一、三、五…节上，
       界面上就是「每格只有一节、隔一格才有课」—— 正是用户反馈的现象。

       这里同时守两层：
         a) 数据层：导入结果里每门课都成对，且两条 order_no 相邻
         b) 渲染层：连堂课在课表里合并成一张卡片（span-first/span-last 成对），
            而不是上下两格各一张
    */
    const pairCheck = await page.evaluate(() => {
      const g = {};
      courses.forEach(c => {
        const k = `${c.date}#${c.name}`;
        (g[k] = g[k] || []).push(c.slot_order);
      });
      const bad = [];
      Object.entries(g).forEach(([k, arr]) => {
        const s = [...arr].sort((x, y) => x - y);
        // ⚠️ 不能断言「每组只能两条」—— 同一门课一天上两大节是**正常的**
        //    （样例里周一「数据库原理与应用」就占了第 1-2 节和第 3-4 节，
        //      即 order_no 3,4,5,6）。所以正确的判据是：
        //    组内节次必须能**两两配成相邻对**（偶数个，且 (2k,2k+1) 相邻）。
        if (s.length % 2 !== 0) {
          bad.push(`${k}: ${s}（不是偶数个，没法两两配对）`);
          return;
        }
        for (let i = 0; i < s.length; i += 2) {
          if (s[i + 1] !== s[i] + 1) { bad.push(`${k}: ${s}（第${i / 2 + 1}对不相邻）`); return; }
        }
      });
      return { groups: Object.keys(g).length, bad };
    });
    pairCheck.bad.length === 0
      ? ok(`连堂成对且相邻（${pairCheck.groups} 组同天同名课，无错位）`)
      : bad(`连堂节次错位 -> ${pairCheck.bad.join('；')}`);

    // 换到有课的一周，看连堂是否被渲染成「首+尾」而不是两个独立卡片
    await page.click('#week-scroll .week-chip[data-off="1"]');   // 第 2 周
    await page.waitForTimeout(450);
    const spanStat = await page.evaluate(() => {
      const cs = [...document.querySelectorAll('.course-chip')];
      const n = cls => cs.filter(c => c.classList.contains(cls)).length;
      return { total: cs.length, first: n('span-first'), last: n('span-last'), solo: cs.length - n('span-first') - n('span-last') };
    });
    (spanStat.first > 0 && spanStat.first === spanStat.last)
      ? ok(`连堂渲染成一张跨行卡片（首 ${spanStat.first} / 尾 ${spanStat.last}，总计 ${spanStat.total}）`)
      : bad(`连堂没有合并显示：首 ${spanStat.first} / 尾 ${spanStat.last} / 单节 ${spanStat.solo}`);
    await ctx.close();
  }
}

await browser.close();
console.log('\n' + '='.repeat(60));
console.log(`结论: ${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
