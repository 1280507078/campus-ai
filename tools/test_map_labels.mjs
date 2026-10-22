/* 地图标签显示规则 + 类型筛选交互的验收测试。
 *
 * 规则（需求）：
 *   · 教学楼  —— 标签**常驻**，地图上永远能看到名字
 *   · 其它类型 —— 默认**只有鼠标悬停时**才显示名字，移开立刻消失；
 *                 但**筛选到只剩少数几类时**（原句：「如果单选的话
 *                 这类的 POI 就开启显示」），把选中类型的标签全部亮出来。
 *                 屏幕上本来就被过滤掉一大半，点少了不挤，直接看名字
 *                 比一个个悬停方便得多 —— 这也是用户点筛选的本来目的。
 *                 为防「选两类 = 十几个标签」又糊掉，加了数量闸
 *                 LABEL_AUTO_MAX = 14：选中点总数超过就退回悬停显示。
 *
 * 类型筛选的交互语义（第 7 轮改过，实测问题：「无法取消全选模式」）：
 *   · 每个类型按钮都是**独立开/关**（标准多选），不是"单选/全选"两态开关
 *   · 点某类：没选中 -> 加入；已选中 -> 取消（**只取消这一个**）
 *   · 全部取消 = 回到全选（兜底，防地图一片空白）
 *   · 点「全部」= 清空筛选回全选
 *   ⚠️ 旧逻辑在单选态再点同一类会**整个弹回全选 45 个**，
 *      用户想"取消这一个"却得到"全部恢复"，所以觉得取消不了。
 *
 * 额外覆盖一个真实发生过的现象：
 *   在地图上按住拖动 / 滚轮缩放时，指针会从标记上「跳过」，
 *   mouseout 被吞掉，悬停标签会永久留在地图上。代码用
 *   movestart / zoomstart / mouseout 三个时机兜底清理。
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
   就不该写死 —— 否则测试自己连错地址，看着像功能坏了。 */
const API = process.env.CAMPUS_BASE || `http://127.0.0.1:${process.env.CAMPUS_API_PORT || '8000'}`;

let pass = 0, fail = 0;
const ok = (m) => { console.log(`  [PASS] ${m}`); pass++; };
const bad = (m) => { console.log(`  [FAIL] ${m}`); fail++; };

const browser = await pw.chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
const r = await ctx.request.post(`${API}/api/auth/register`, {
  data: { username: 'lbl' + Date.now(), password: 'test123456' },
});
await ctx.addInitScript((t) => { try { localStorage.setItem('token', t); } catch (e) {} }, (await r.json()).access_token);

const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error' && !/404/.test(m.text())) errs.push(m.text()); });

console.log('\n--- 加载地图 ---');
await page.goto(`${BASE}/nav.html`, { waitUntil: 'networkidle' });
await page.waitForTimeout(6500);   // 等高德 SDK + 45 个标记就绪

/** 统计当前渲染出来的标签 */
async function labels() {
  return page.$$eval('.amap-marker-label', els => {
    const out = [];
    for (const e of els) {
      const inner = e.querySelector('.poi-label');
      if (!inner) continue;
      out.push({ txt: inner.textContent.trim(), teaching: inner.classList.contains('teaching') });
    }
    return out;
  });
}

/* ---------- 1. 初始：只有教学楼带标签 ---------- */
console.log('\n--- 1. 初始状态 ---');
{
  const L = await labels();
  const t = L.filter(x => x.teaching), nt = L.filter(x => !x.teaching);
  console.log(`    标签总数 ${L.length}：教学楼 ${t.length}，非教学楼 ${nt.length}`);
  if (t.length > 0) ok(`教学楼标签常驻（${t.length} 个）`);
  else bad('教学楼标签没有常驻');
  if (nt.length === 0) ok('非教学楼初始不带标签（符合「只悬停显示」）');
  else bad(`非教学楼初始就有 ${nt.length} 个标签: ${JSON.stringify(nt.map(x => x.txt))}`);
}

/* ---------- 2. 悬停一个非教学楼 -> 出现；移开 -> 消失 ---------- */
console.log('\n--- 2. 悬停显示 / 移开消失 ---');
{
  // 挑一个非教学楼点位，算出它在屏幕上的坐标。
  // 不用 window.CAMPUS_PLACES —— 它是脚本作用域的 const，不挂在 window 上，
  // page.evaluate 里取不到。改从地图上的 marker 反查（markerPlace 是 Map，同样
  // 不挂 window），所以这里用 getAllOverlays 遍历 marker 的 position，
  // 再对照 POIS 的坐标找出非教学楼的那些。
  const target = await page.evaluate(() => {
    // ⚠️ 不能用 window.map —— `let map` 不挂在 window 上（已实测 undefined）。
    // 高德会把地图实例挂到容器元素上：document.getElementById('map').amap
    const m = document.getElementById('map') && document.getElementById('map').amap;
    if (!m || !m.getAllOverlays) return null;
    const overlays = m.getAllOverlays('marker');
    for (const mk of overlays) {
      // 「有没有常驻标签」就是「是不是教学楼」（教学楼的标签在建标记时就挂上了）
      const el = mk.getLabel && mk.getLabel();
      if (el && (el.dom || el.domElement)) continue;
      const dom = mk.dom;
      if (!dom) continue;
      const r = dom.getBoundingClientRect();
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      if (cx < 10 || cy < 10 || cx > innerWidth - 10 || cy > innerHeight - 10) continue;
      // ★ 必须确认这个点上真能收到鼠标事件（最顶层元素就是它自己）。
      //   实测有标记被教学楼常驻标签压住，鼠标移过去事件落在标签上，
      //   测出来的「悬停没反应」是假象。只挑未被遮挡的点来测。
      const top = document.elementFromPoint(cx, cy);
      if (top && (top === dom || dom.contains(top) || top.contains(dom)))
        return { x: Math.round(cx), y: Math.round(cy) };
    }
    return null;
  });

  if (!target) {
    bad('找不到视口内的非教学楼点位（地图可能没加载完）');
  } else {
    console.log(`    悬停目标 @ ${Math.round(target.x)},${Math.round(target.y)}`);

    const before = (await labels()).filter(x => !x.teaching).length;
    await page.mouse.move(target.x, target.y);
    await page.waitForTimeout(450);
    const during = (await labels()).filter(x => !x.teaching);
    if (during.length > before) ok(`悬停后标签出现（非教学标签 ${before} -> ${during.length}）`);
    else bad(`悬停后标签没出现（仍为 ${during.length}）`);
    if (during.length > 0) ok(`出现的标签内容: ${JSON.stringify(during.map(x => x.txt))}`);
    else bad('悬停处没有渲染出任何非教学标签');

    // 鼠标移到空白处
    await page.mouse.move(15, 900);
    await page.waitForTimeout(700);
    const after = (await labels()).filter(x => !x.teaching);
    if (after.length === 0) ok('移开鼠标后标签消失（非教学 0 个）');
    else bad(`移开后仍有 ${after.length} 个标签残留: ${JSON.stringify(after.map(x => x.txt))}`);
  }
}

/* ---------- 3. 筛选态：选中类型的标签自动亮出来 ---------- */
console.log('\n--- 3. 单选一类 -> 该类 POI 自动显示名字 ---');

/** 点某个类型按钮（按文字匹配） */
async function clickChip(re) {
  const hit = await page.evaluate((rx) => {
    const b = [...document.querySelectorAll('#type-filter button, #type-filter .type-chip')]
      .find(x => new RegExp(rx).test(x.textContent));
    if (b) { b.click(); return b.textContent.trim(); }
    return null;
  }, re.source);
  await page.waitForTimeout(900);
  return hit;
}

/** 当前可见点位与带标签数量，以及按钮选中态 */
async function chipState() {
  return page.evaluate(() => {
    const m = document.getElementById('map') && document.getElementById('map').amap;
    const vis = m && m.getAllOverlays ? m.getAllOverlays('marker').filter(x => x.getVisible()) : [];
    const withLabel = vis.filter(x => {
      const l = x.getLabel && x.getLabel();
      const c = l && l.content;
      return typeof c === 'string' ? c.trim().length > 0 : !!c;
    }).length;
    const on = [...document.querySelectorAll('#type-filter .type-chip.on')]
      .map(e => e.textContent.trim());
    return { visible: vis.length, withLabel, on };
  });
}

{
  // 先确保回到全选
  await clickChip(/^全部/);
  const base = await chipState();
  console.log('    全选态:', JSON.stringify(base));

  // 单选「宿舍」——点数少，标签应全亮
  const dorm = await clickChip(/宿舍/);
  const s1 = await chipState();
  console.log(`    单选「${dorm}」:`, JSON.stringify(s1));
  if (s1.visible > 0 && s1.withLabel === s1.visible) {
    ok(`单选「${dorm}」后该类 ${s1.withLabel}/${s1.visible} 个点全部亮出名字`);
  } else {
    bad(`单选「${dorm}」后只有 ${s1.withLabel}/${s1.visible} 个点带标签，应全部亮出`);
  }

  // 单选「快递服务」——更少
  const svc = await clickChip(/快递/);
  const s1b = await chipState();
  console.log(`    单选「${svc}」:`, JSON.stringify(s1b));
  s1b.visible > 0 && s1b.withLabel === s1b.visible
    ? ok(`单选「${svc}」后该类 ${s1b.withLabel}/${s1b.visible} 个点全部亮出名字`)
    : bad(`单选「${svc}」后只有 ${s1b.withLabel}/${s1b.visible} 个点带标签`);

  // 回到全选：标签应收起（只剩教学楼常驻）
  await clickChip(/^全部/);
  const back = await chipState();
  console.log('    回到全选:', JSON.stringify(back));
  back.withLabel < back.visible
    ? ok(`回到全选后标签收起（${back.withLabel}/${back.visible} 带标签，只有教学楼常驻）`)
    : bad(`回到全选后标签没收起（${back.withLabel}/${back.visible}）`);
}

/* ---------- 3b. 多选语义：能"取消某一个"而不是弹回全选 ---------- */
console.log('\n--- 3b. 多选语义（用户反馈「无法取消全选模式」） ---');
{
  await clickChip(/^全部/);
  const n0 = (await chipState()).visible;

  // 点 A -> 只看 A
  await clickChip(/停车场/);
  const a = await chipState();

  // 再点 B -> A+B（旧逻辑这一步没问题，但一起守住）
  await clickChip(/快递/);
  const b = await chipState();
  b.on.length === 2
    ? ok(`点两类 -> 两个按钮都点亮（${JSON.stringify(b.on)}），可见 ${b.visible} 个`)
    : bad(`点两类后选中态应为 2 个，实际 ${JSON.stringify(b.on)}`);

  b.visible > a.visible
    ? ok(`两类叠加后可见数增加（${a.visible} -> ${b.visible}）`)
    : bad(`两类叠加后可见数没增加（${a.visible} -> ${b.visible}）`);

  /* 关键：把 A 取消掉，应该**只剩 B**，而不是弹回全选。
     这正是输入里说的「无法取消全选模式」。 */
  await clickChip(/停车场/);
  const c = await chipState();
  console.log(`    取消「停车场」后:`, JSON.stringify(c));
  c.visible < n0 && c.visible !== n0
    ? ok(`取消「停车场」后只去掉它（可见 ${c.visible}），**没有弹回全选 ${n0}**`)
    : bad(`取消「停车场」后可见 ${c.visible}，疑似弹回了全选 ${n0}`);

  c.on.length === 1 && /快递/.test(c.on[0])
    ? ok(`取消后只剩「快递」被选中（${JSON.stringify(c.on)}）`)
    : bad(`取消后选中态应为只剩「快递」，实际 ${JSON.stringify(c.on)}`);

  // 再取消最后一个 -> 兜底回全选（地图不能一片空白）
  await clickChip(/快递/);
  const d = await chipState();
  console.log('    全部取消后:', JSON.stringify(d));
  d.visible === n0
    ? ok(`全部取消后兜底回到全选（可见 ${d.visible}），地图不会空掉`)
    : bad(`全部取消后可见 ${d.visible}，应兜底回全选 ${n0}`);
}

/* ---------- 4. 拖动地图后不残留悬停标签（真实踩过的坑） ---------- */
console.log('\n--- 4. 拖地图 / 缩放后不残留 ---');
{
  const target = await page.evaluate(() => {
    // ⚠️ 不能用 window.map —— `let map` 不挂在 window 上（已实测 undefined）。
    // 高德会把地图实例挂到容器元素上：document.getElementById('map').amap
    const m = document.getElementById('map') && document.getElementById('map').amap;
    if (!m || !m.getAllOverlays) return null;
    const overlays = m.getAllOverlays('marker');
    for (const mk of overlays) {
      const el = mk.getLabel && mk.getLabel();
      if (el && (el.dom || el.domElement)) continue;
      const dom = mk.dom;
      if (!dom) continue;
      const r = dom.getBoundingClientRect();
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      if (cx < 60 || cy < 60 || cx > innerWidth - 60 || cy > innerHeight - 60) continue;
      const top = document.elementFromPoint(cx, cy);
      if (top && (top === dom || dom.contains(top) || top.contains(dom)))
        return { x: Math.round(cx), y: Math.round(cy) };
    }
    return null;
  });
  if (!target) {
    bad('找不到可用的拖动起点');
  } else {
    // 悬停上去让标签出现
    await page.mouse.move(target.x, target.y);
    await page.waitForTimeout(400);
    // 然后**不经过 mouseout**直接按住拖动（这正是吞掉 mouseout 的真实操作）
    await page.mouse.down();
    await page.mouse.move(target.x - 80, target.y - 60, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(900);

    const nt = (await labels()).filter(x => !x.teaching);
    if (nt.length === 0) ok('拖动地图后悬停标签被清理，无残留');
    else bad(`拖动后残留 ${nt.length} 个标签: ${JSON.stringify(nt.map(x => x.txt))}`);

    // 再测滚轮缩放
    await page.mouse.move(target.x, target.y);
    await page.waitForTimeout(350);
    await page.mouse.wheel(0, -240);
    await page.waitForTimeout(900);
    const nt2 = (await labels()).filter(x => !x.teaching);
    if (nt2.length === 0) ok('滚轮缩放后悬停标签被清理，无残留');
    else bad(`缩放后残留 ${nt2.length} 个标签: ${JSON.stringify(nt2.map(x => x.txt))}`);

    // 教学楼标签不能被误伤
    const t = (await labels()).filter(x => x.teaching);
    if (t.length > 0) ok(`教学楼标签未受清理影响（仍有 ${t.length} 个）`);
    else bad('教学楼标签被清理动作误删了');
  }
}

/* ---------- 5. 缓存版本号 ---------- */
console.log('\n--- 5. 静态资源缓存版本 ---');
{
  const html = await (await ctx.request.get(`${BASE}/nav.html`)).text();
  const vs = [...html.matchAll(/\?v=([0-9a-z]+)/g)].map(m => m[1]);
  const uniq = [...new Set(vs)];
  console.log(`    nav.html 里的版本号: ${JSON.stringify(uniq)}`);
  if (uniq.length === 1 && uniq[0] !== '20260921t') ok(`版本号已更新为 ${uniq[0]}（不再是旧的 20260921t）`);
  else bad(`版本号异常: ${JSON.stringify(uniq)}`);
}

/* 哪些控制台错误算「真 bug」。
   ERR_CONNECTION_REFUSED / 404 这类是**环境问题**（后端没起、端口不对、
   离线时外部 SDK 拉不到），不代表页面脚本有缺陷 —— 把它们算进来的话，
   真正的前端异常会被淹掉，测试也就失去意义了。
   （和其它测试脚本保持同一口径。） */
const isRealError = (t) => !/ERR_CONNECTION_REFUSED|ERR_NAME_NOT_RESOLVED|Failed to load resource|404 \(Not Found\)/i.test(t);
const realErrs = errs.filter(isRealError);
if (realErrs.length === 0) ok(`无 JS 异常 / 控制台错误（已滤掉 ${errs.length - realErrs.length} 条环境噪声）`);
else bad(`有 ${realErrs.length} 条错误: ${realErrs.slice(0, 3).join(' | ')}`);

await browser.close();
console.log('\n' + '='.repeat(56));
console.log(`结论: ${pass} 通过 / ${fail} 失败` + (fail === 0 ? '  —— 全部通过' : ''));
process.exit(fail === 0 ? 0 : 1);
