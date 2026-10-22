/* 校内步行路网图的纯逻辑守卫测试（不需要浏览器 / 不需要联网）。
 *
 * ## 为什么要有它
 *
 * 这套路网经历过四轮返工，每个坑都很隐蔽、改坏了在页面上只表现为
 * 「路线看着怪」，不报错：
 *
 *   ① 手估节点坐标        -> 路线不贴地图上的白色道路（穿楼、走空地）
 *   ② 只有地标当节点      -> 两条路在真实路口交叉时不共享节点，
 *                            去正德楼被算成「先南下博文楼再折返」
 *   ③ 只覆盖主干路        -> 知行楼到平远楼没有路可走，图里补一条
 *                            114 米直线边（用户一眼看出「没从路走」）
 *   ④ ★ 折线几何被丢掉    -> 建图时把折线吸附到网格后**只留节点对**，
 *                            两节点之间永远是一条直线，弯路全被"抄近道"抹平。
 *                            实测 48% 节点离真实道路 >4 m、**56% 里程离路 >4 m**
 *                            （最差 174 m）。用户原话：「根本没在路上，要根据道路走」。
 *
 * 所以这份测试的**核心**是第 2 节：证明「每段道路的折线几何都还在」。
 * 第 9 节把这件事的最终裁判（折线投到底图白路像素上量距离）指到
 * `tools/qa_campus_graph.py`，那是唯一非循环论证的"贴路"度量。
 *
 * 用法：node tools/test_campus_graph.mjs
 */

import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FILE = path.join(HERE, '..', 'frontend', 'js', 'campus-graph.js');
const code = fs.readFileSync(FILE, 'utf8');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  code + ';this.G=CAMPUS_GRAPH;this.LM=CAMPUS_LANDMARKS;this.plan=planCampusRoute;'
  + 'this.TOL=LANDMARK_TOL;this.EP=edgePoints;this.EL=edgeLength;',
  sandbox,
);
const { G, LM, plan, TOL, EP, EL } = sandbox;

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  cond ? pass++ : fail++;
  console.log(`  ${cond ? '[PASS]' : '[FAIL]'} ${name}${extra ? '  <- ' + extra : ''}`);
};
const R = 6371000, rad = Math.PI / 180;
const hav = (a, b) => {
  const dLat = (b[1] - a[1]) * rad, dLng = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};
const node = id => [G.nodes[id].lng, G.nodes[id].lat];
const lm = name => { const x = LM.find(y => y.name === name); return [x.lng, x.lat]; };
const plen = pts => { let s = 0; for (let i = 1; i < pts.length; i++) s += hav(pts[i - 1], pts[i]); return s; };
const qtl = (arr, p) => {
  const a = arr.slice().sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.floor(a.length * p))];
};

console.log('='.repeat(62));
console.log('  校园路网图 · 纯逻辑守卫测试');
console.log('='.repeat(62));

// ── 1. 规模 ──────────────────────────────────────────────────
console.log('\n-- 1. 路网规模 --');
const ids = Object.keys(G.nodes);
ok('节点足够密（>=150）', ids.length >= 150, `节点 ${ids.length}`);
ok('边数合理（>=190）', G.edges.length >= 190, `边 ${G.edges.length}`);
// ★ 防返工：旧版「只抓地标两两之间的高德路线」只有 3.7 km / 106 节点，
//   校园里大半步道都不在图里。总量掉回 9 km 以下就说明有人把数据源退回旧口径。
//   （注意：11.5 km 是**带折线几何的真实长度**；旧口径按两点直线量能虚报到 31 km，
//     所以这个阈值只能和「同样带几何」的版本比。）
const edgePl = G.edges.map(e => EP(e));
const totalM = edgePl.reduce((s, p) => s + plen(p), 0);
ok('★ 路网总长 >= 9 km（按折线量，覆盖校园全部路段而非只连地标）',
  totalM >= 9000, `${(totalM / 1000).toFixed(2)} km`);

// ── 2. ★ 几何保真：边必须带折线，且弯路不能被抹平 ──────────────
// 这一节是整个文件的核心。旧版丢掉了折线中间点，导致用户在手机上看到
// 蓝线"根本没在路上"。下面每一条都是当时那个 bug 的探针。
console.log('\n-- 2. ★ 几何保真（防返工的关键）--');
const withVia = G.edges.filter(e => e[2] !== undefined);
ok('★ 带折线几何的边占绝大多数（>=80%）',
  withVia.length / G.edges.length >= 0.8,
  `${withVia.length}/${G.edges.length} = ${(100 * withVia.length / G.edges.length).toFixed(1)}%`);

const ratios = [], chords = [], lens = [];
let nVert = 0, badEnds = 0;
for (const e of G.edges) {
  const p = EP(e);
  nVert += p.length;
  const a = node(e[0]), b = node(e[1]);
  if (hav(p[0], a) > 0.01 || hav(p[p.length - 1], b) > 0.01) badEnds++;
  const c = hav(a, b), L = plen(p);
  chords.push(c); lens.push(L);
  ratios.push(L / Math.max(c, 0.01));
}
const nBend = ratios.filter(r => r > 1.02).length;      // 折线明显比直线长的边 = 弯路
ok('edgePoints() 的首尾就是边的两个端点',
  badEnds === 0, `不吻合 ${badEnds} 条`);
ok('★ 折线明显长于直线的边 >= 55 条（弯路几何确实被保留）',
  nBend >= 55, `${nBend} 条（比值 >1.02）`);
ok('顶点总数 >= 800（折线被完整保存，不是只剩两端点）',
  nVert >= 800, `${nVert} 个顶点 / ${G.edges.length} 条边`);
ok('折线/弦长 比值中位数 <= 1.05（大多数是直路）',
  qtl(ratios, 0.5) <= 1.05, `p50 = ${qtl(ratios, 0.5).toFixed(3)}`);
ok('折线/弦长 比值上界 <= 4.0（没有绕圈/回头边）',
  Math.max(...ratios) <= 4.0, `max = ${Math.max(...ratios).toFixed(2)}`);
// ★ 这条最锋利：长边必须是直路。如果有人再把折线抽稀成两点，
//   一条 200 m 的弯道会变成「弦长 200 m、比值 1.0」的假直路 —— 而弯路的
//   弦长本来就短，比值会掉到 1.0，于是"折线明显长于直线的边"数量骤减（上面第 3 条拦住）。
//   反过来，长边若带弯，就说明它真是一段长弯道（如环山道），允许比值到 2.0。
const longBad = G.edges.filter(e => hav(node(e[0]), node(e[1])) > 100 && plen(EP(e)) / hav(node(e[0]), node(e[1])) > 2.0);
ok('★ 弦长 >100 m 的边，折线/弦长 都 <= 2.0（长边只能是直路或缓弯）',
  longBad.length === 0, `违规 ${longBad.length} 条${longBad.length ? '：' + longBad.slice(0, 3).map(e => e[0] + '-' + e[1]).join(',') : ''}`);
ok('edgeLength() 与折线长度一致',
  Math.abs(EL(G.edges[0]) - plen(EP(G.edges[0]))) < 0.01);

ok('★ 没有自环边（两端塌成同一节点的退化边会让比值爆表）',
  G.edges.filter(e => e[0] === e[1]).length === 0);
ok('节点 id 是几何顶点（rNN），不是地标缩写',
  ids.filter(i => !/^r\d+$/.test(i)).length === 0,
  ids.filter(i => !/^r\d+$/.test(i)).slice(0, 5).join(','));
ok('地标与拓扑解耦（LANDMARKS 是独立清单）', Array.isArray(LM) && LM.length >= 15,
  `${LM.length} 个地标`);
ok('「途经地标」的判定半径已定义', typeof TOL === 'number' && TOL > 0, `${TOL} 米`);
ok('文件头记录了「折线几何不能丢」这条教训与数据来源',
  /折线几何绝对不能丢|只留节点对|根本没在路上/.test(code)
  && /底图栅格|白色道路|OpenStreetMap/.test(code));

// ── 3. 连通与重复 ────────────────────────────────────────────
console.log('\n-- 3. 连通性 --');
const adj = {}; ids.forEach(i => adj[i] = []);
G.edges.forEach(([a, b]) => { adj[a].push(b); adj[b].push(a); });
const seen = new Set([ids[0]]), q = [ids[0]];
while (q.length) for (const n of adj[q.pop()]) if (!seen.has(n)) { seen.add(n); q.push(n); }
ok('★ 全连通（任意两点可达）', seen.size === ids.length, `${seen.size}/${ids.length}`);
ok('没有悬空节点（度数 0）',
  ids.filter(i => adj[i].length === 0).length === 0);

const seenEdge = new Set(), dup = [];
for (const [a, b] of G.edges) {
  const k = a < b ? `${a}|${b}` : `${b}|${a}`;
  if (seenEdge.has(k)) dup.push(k); else seenEdge.add(k);
}
ok('没有重复边', dup.length === 0, dup.slice(0, 4).join(','));

// ── 4. 边长分布：每段都应是「一小截真实道路」 ────────────────
console.log('\n-- 4. 边长分布（按折线长度量）--');
ok('边长中位数 <= 40m（说明是逐段真实道路）', qtl(lens, 0.5) <= 40,
  `p50 = ${qtl(lens, 0.5).toFixed(1)} m`);
ok('最长边 <= 600m', Math.max(...lens) <= 600,
  `max = ${Math.max(...lens).toFixed(0)} m`);
// 长直路（如环山道、主干道）在图里不插中间节点，所以允许有一定比例的长边；
// 但它们必须已经由上一节的「长边 ratio <= 2.0」保证是直的。
ok('超长边（弦长 >130m）<= 25 条',
  chords.filter(c => c > 130).length <= 25,
  `${chords.filter(c => c > 130).length} 条`);

// ── 5. 地标覆盖 ──────────────────────────────────────────────
console.log('\n-- 5. 地标覆盖（每个地标附近都要有路）--');
let worst = 0, worstName = '';
for (const L of LM) {
  const d = Math.min(...ids.map(i => hav([L.lng, L.lat], node(i))));
  if (d > worst) { worst = d; worstName = L.name; }
}
ok('每个地标离最近路网节点 <= 60m', worst <= 60, `最远 ${worstName} ${worst.toFixed(1)}m`);

// ── 6. 关键路线 ──────────────────────────────────────────────
console.log('\n-- 6. 关键路线（用户报过的场景）--');
const cases = [
  ['益禾堂', '正德楼'], ['学生公寓', '正德楼'], ['图书馆', '正德楼'],
  ['博文楼', '知行楼'], ['游泳馆', '知行楼'], ['商德楼', '正德楼'],
  ['西区宿舍', '致远楼'], ['菜鸟驿站', '平远楼'], ['知行楼', '正德楼'],
  // ↓ 用户报过的那条：知行楼到平远楼曾经是一条 114 米的直线（没从路走）
  ['知行楼', '平远楼'], ['菜鸟驿站', '致远楼'], ['知行楼', '致远楼'],
  ['知行楼', '菜鸟驿站'],
];

// 高德步行规划在同一对点上给的里程（米，2026-10 实测，高德 v3 walking）。
// 两类点要分开看：
//   A 类「高德绕出校门」——知行楼去东片区，高德要 1.6~1.8 km。自建路网存在的
//     理由就是它，我们必须**比高德短**。
//   B 类「高德给的是校内短途」——它常常直接穿广场抄近道（如图书馆→正德楼
//     直线才 190 m，它给 181 m）。我们**必须沿道路走**，所以绕一点是应该的，
//     拿「不长于高德」当标准是错的，改成 "<= 高德 × 1.6" 的合理上界。
const AMAP_WALK_BASE = {
  '知行楼→平远楼': 1675, '知行楼→致远楼': 1767, '知行楼→菜鸟驿站': 1758,
  '图书馆→致远楼': 1769, '平远楼→西区餐厅': 1873, '雅颂楼→平远楼': 1786,
  '西区宿舍→致远楼': 2152,
  '菜鸟驿站→平远楼': 155, '菜鸟驿站→致远楼': 175, '益禾堂→正德楼': 538,
  '学生公寓→正德楼': 362, '图书馆→正德楼': 181, '商德楼→正德楼': 163,
  '游泳馆→知行楼': 362, '博文楼→知行楼': 700, '知行楼→正德楼': 639,
};
const AMAP_DETOUR = 800;    // 高德里程超过它就算「绕出校门」，我们必须更短

for (const [a, b] of cases) {
  const from = lm(a), to = lm(b);
  const r = plan(from, to);
  const name = `${a} → ${b}`;
  if (!r) { ok(`${name} 可规划`, false); continue; }
  const straight = hav(from, to);
  // 「没有折返」：路线上任何一点离**终点**的距离，都不该远超过起点离终点的距离。
  // 绕山、绕楼会鼓出去一点，所以给 30% + 120m 余量；再多就说明它真往反方向
  // 跑了一趟又折回来（历史上「先南下博文楼再折返」就是这么坏的）。
  const maxToDest = Math.max(...r.points.map(p => hav(p, to)));
  const okFold = maxToDest <= straight * 1.3 + 120;
  const base = AMAP_WALK_BASE[`${a}→${b}`];   // 表里的 key 不带空格，别写成 `${a} → ${b}`
  const detour = base > AMAP_DETOUR;
  const okLen = base
    ? (detour ? r.meters < base : r.meters <= base * 1.6)
    : r.meters <= straight * 3.5 + 80;
  console.log(`     ${name}: ${r.meters}m（直线 ${straight.toFixed(0)}m，`
    + `${(r.meters / straight).toFixed(2)}×`
    + (base ? `，高德步行 ${base}m${detour ? '【绕校门外】' : ''}` : '')
    + `），途经 ${r.nodeNames.join(' → ') || '（无）'}`);
  ok(`${name} 没有折返`, okFold,
    `离终点最远 ${maxToDest.toFixed(0)}m（起点 ${straight.toFixed(0)}m）`);
  ok(`${name} 里程合理（${base ? (detour ? `优于高德 ${base}m` : `<= 高德×1.6 = ${Math.round(base * 1.6)}m`) : '<= 直线×3.5+80'}）`,
    okLen, `${r.meters}m`);
  ok(`${name} 文字指引非空（渲染层要用）`, r.nodeNames.length >= 1, r.nodeNames.join(' → '));
}

// ★ 里程回归守卫：下面这些是当前实测值，留 8% 余量。
//   将来若有人删掉某段路导致绕远，这里立刻失败。
//   （旧版「只连地标」的路网跑知行楼→菜鸟驿站是 571 m，现在 118 m。）
console.log('\n-- 6b. ★ 里程回归守卫（防绕远）--');
const GUARD = [
  ['益禾堂', '正德楼', 630], ['图书馆', '正德楼', 290], ['商德楼', '正德楼', 225],
  ['知行楼', '平远楼', 735], ['知行楼', '菜鸟驿站', 130], ['知行楼', '致远楼', 560],
  ['菜鸟驿站', '平远楼', 172], ['菜鸟驿站', '致远楼', 196], ['博文楼', '知行楼', 715],
  ['图书馆', '致远楼', 1105],
];
for (const [a, b, cap] of GUARD) {
  const r = plan(lm(a), lm(b));
  ok(`★ ${a} → ${b} <= ${cap}m`, !!r && r.meters <= cap, r ? `${r.meters}m` : '规划失败');
}

// ── 7. 双向一致 ──────────────────────────────────────────────
console.log('\n-- 7. 双向一致 --');
for (const [a, b] of [['益禾堂', '正德楼'], ['图书馆', '游泳馆'], ['博文楼', '商德楼'],
  ['知行楼', '平远楼'], ['西区餐厅', '东区餐厅']]) {
  const f = plan(lm(a), lm(b)), t = plan(lm(b), lm(a));
  ok(`${a} ↔ ${b} 里程一致`, !!f && !!t && Math.abs(f.meters - t.meters) <= 15,
    f && t ? `${f.meters}m / ${t.meters}m` : '规划失败');
}

// ── 8. 校外坐标必须被拒 ──────────────────────────────────────
console.log('\n-- 8. 校外坐标 --');
// ⚠️ 原来这里还有两条用 distanceToNetwork 量的「离路网多远」断言。
//    那个函数是给已删除的定位功能做可信度校验的，随功能一起删了 ——
//    现在起点只能来自校内地名，不需要「一个任意坐标离路网多远」这个度量。
//    保留下面两条，因为它们测的是 planCampusRoute 的边界行为，与定位无关。
ok('★ 校外起点不走路网（交回高德 / 兜底），避免绕出校门',
  plan([119.4420, 26.0010], lm('知行楼')) === null);
ok('校内点位照常规划成功（对照组，防止上面那条断言恒真）',
  !!plan(lm('东区餐厅'), lm('知行楼')));

// ── 9. ★ 终极贴路守卫（在另一个脚本里）────────────────────────
console.log('\n-- 9. ★ 贴路守卫（唯一非循环论证的度量）--');
// 本文件只能证明「折线几何没丢」。真正回答「线在不在底图那条白路上」的，
// 是把折线投到**高德底图的路像素**上量距离 —— 见 tools/qa_campus_graph.py。
// 这里只做一条轻量自检，确保那条链路的数据前提还在。
ok('每条边都能被 edgePoints() 展开成 >= 2 个点（渲染层与质检共用）',
  G.edges.every(e => EP(e).length >= 2));
ok('tools/qa_campus_graph.py 及其参考底图都在（贴路硬指标可复跑）',
  fs.existsSync(path.join(HERE, 'qa_campus_graph.py'))
  && fs.existsSync(path.join(HERE, 'refdata', 'campus_shot.png'))
  && fs.existsSync(path.join(HERE, 'refdata', 'campus_shot.json')),
  '跑 python tools/qa_campus_graph.py 看 p90 与离路里程占比');

console.log('\n' + '='.repeat(62));
console.log(`  结论: ${pass} 通过 / ${fail} 失败`);
console.log('='.repeat(62));
process.exit(fail ? 1 : 0);
