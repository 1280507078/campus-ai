/* 校园点位数据契约测试（纯 Node，不开浏览器）
 *
 * 跑法：node tools/test_campus_places.mjs
 *
 * 为什么要有这个文件：
 *   1. `frontend/js/campus.js` 的点位表是**三处联动**的 —— 它自己、后端注释里
 *      写的点位数、以及 `tools/lookup_buildings.py` 的查证清单。改一处忘一处
 *      就会出现"文档说 45 个、实际 42 个"这种对不上的情况。
 *   2. 用户 2026-10-14 明确要求的几条改动（操场入其他类 / 新增东西区宿舍 /
 *      食堂餐饮只留 4 个 / 删掉 4 个档口），**都是很容易被"顺手改回去"的** ——
 *      数据文件没有类型保护，删一行、改个 type 都不会报错。
 *   3. 坐标是**逐条查证**过的，手抖多写或少写一位（119.44 变 119.4）不会报错，
 *      但地图上就是几十公里。这里用校园包围盒兜住。
 *
 * 这个测试**不需要起服务、不需要浏览器**，秒级完成，适合放进每次改动的自检链。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = fs.readFileSync(path.join(ROOT, "frontend/js/campus.js"), "utf8");

// campus.js 是浏览器用的全局脚本，这里在 Node 里 eval 出它导出的常量。
const sandbox = {};
// eslint-disable-next-line no-eval
eval(SRC + ";sandbox.CAMPUS_PLACES = CAMPUS_PLACES; sandbox.PLACE_TYPES = PLACE_TYPES;");
const PLACES = sandbox.CAMPUS_PLACES;
const TYPES = sandbox.PLACE_TYPES;

let pass = 0, fail = 0;
function check(title, cond, detail = "") {
  if (cond) { pass++; console.log(`  [PASS] ${title}${detail ? "  " + detail : ""}`); }
  else { fail++; console.log(`  [FAIL] ${title}  ${detail}`); }
}

const byName = (n) => PLACES.find(p => p.name === n);
const namesOf = (t) => PLACES.filter(p => p.type === t).map(p => p.name);

console.log("\n--- ① 本轮用户指定的改动（改回去就红）---");
check("★ 新增「操场」且归入「其他」类",
  byName("操场") && byName("操场").type === "other",
  byName("操场") ? `type=${byName("操场").type} ${byName("操场").lng},${byName("操场").lat}` : "不存在");
check("★ 新增「东区宿舍」且归入「宿舍」类",
  byName("东区宿舍") && byName("东区宿舍").type === "dorm",
  byName("东区宿舍") ? `${byName("东区宿舍").lng},${byName("东区宿舍").lat}` : "不存在");
check("★ 「五期餐厅」归入「餐饮」类且坐标未被改动（用户给的点）",
  byName("五期餐厅") && byName("五期餐厅").type === "food"
  && byName("五期餐厅").lng === 119.452159 && byName("五期餐厅").lat === 26.004726,
  byName("五期餐厅") ? `${byName("五期餐厅").lng},${byName("五期餐厅").lat}` : "不存在");
check("★ 旧名「五期食堂」已不存在（2026-10-15 要求统一成「XX餐厅」口径）",
  !byName("五期食堂"), byName("五期食堂") ? "旧名还在！" : "已改名");
check("★ 新增「五期宿舍」且归入「宿舍」类（用户给的坐标）",
  byName("五期宿舍") && byName("五期宿舍").type === "dorm"
  && byName("五期宿舍").lng === 119.45204 && byName("五期宿舍").lat === 26.005447,
  byName("五期宿舍") ? `${byName("五期宿舍").lng},${byName("五期宿舍").lat}` : "不存在");
check("★ 原「西区」已改名为「西区宿舍」（旧名不该再存在）", !byName("西区"));
check("★ 「西区宿舍」坐标已换成用户给的点",
  byName("西区宿舍") && byName("西区宿舍").lng === 119.446697 && byName("西区宿舍").lat === 26.005108,
  byName("西区宿舍") ? `${byName("西区宿舍").lng},${byName("西区宿舍").lat}` : "不存在");
check("★ 「六期」已改名为「六期宿舍」（旧名不该再存在）", !byName("六期") && byName("六期宿舍"));
check("「六期餐厅」**没有**被误改（food 类要跟宿舍类区分开）",
  byName("六期餐厅") && byName("六期餐厅").type === "food");

console.log("\n--- ② 食堂餐饮：只留 5 个 ---");
const FOOD_KEEP = ["东区餐厅", "西区餐厅", "六期餐厅", "创四小镇", "五期餐厅"];
const food = namesOf("food");
check("★ 食堂餐饮恰好是这 5 个（多一个少一个都算错）",
  food.length === FOOD_KEEP.length && FOOD_KEEP.every(n => food.includes(n)),
  JSON.stringify(food));
for (const n of ["陈家生煎", "1点点", "益禾堂", "0090汉堡工厂"]) {
  check(`★ 已删除「${n}」（整条不在点位表里，不只是换个类）`, !byName(n));
}
check("★ 7-ELEVEn 挪到「其他」类（是便利店不是食堂）",
  byName("7-ELEVEn") && byName("7-ELEVEn").type === "other",
  byName("7-ELEVEn") ? `type=${byName("7-ELEVEn").type}` : "不存在");

console.log("\n--- ③ 分类计数（改数据时这里会提醒你去同步注释）---");
const counts = {};
for (const p of PLACES) counts[p.type] = (counts[p.type] || 0) + 1;
check("dorm = 5（学生公寓 / 西区宿舍 / 东区宿舍 / 五期宿舍 / 六期宿舍）",
  counts.dorm === 5, `实际 ${counts.dorm}：${namesOf("dorm").join("、")}`);
check("food = 5（东区/西区/六期/五期餐厅 + 创四小镇）",
  counts.food === 5, `实际 ${counts.food}：${namesOf("food").join("、")}`);
check("other = 4（图书馆 / 游泳馆 / 操场 / 7-ELEVEn）",
  counts.other === 4, `实际 ${counts.other}：${namesOf("other").join("、")}`);
check("teaching = 10（就是 BUILDINGS 那 10 栋，一栋不多一栋不少）",
  counts.teaching === 10, `实际 ${counts.teaching}`);
check("总点数 = 44（与后端 planner.py 注释、lookup_buildings.py 清单同步）",
  PLACES.length === 44, `实际 ${PLACES.length}`);

console.log("\n--- ④ 坐标必须是「校内」且是有限数 ---");
// 校本部外围包围盒：比校园大一圈，但足以抓住"少写一位小数"（0.01° ≈ 1.1km）
const BOX = { lngMin: 119.44, lngMax: 119.455, latMin: 25.998, latMax: 26.011 };
const bad = PLACES.filter(p =>
  !Number.isFinite(p.lng) || !Number.isFinite(p.lat) ||
  p.lng < BOX.lngMin || p.lng > BOX.lngMax || p.lat < BOX.latMin || p.lat > BOX.latMax);
check("★ 所有点位坐标都在校园包围盒内（抓「少写一位小数」这类手抖）",
  bad.length === 0,
  bad.map(p => `${p.name}(${p.lng},${p.lat})`).join(" ") || "全部正常");

console.log("\n--- ⑤ 类型表与数据要自洽 ---");
const unknown = PLACES.filter(p => !TYPES[p.type]);
check("每个点位的 type 都在 PLACE_TYPES 里（否则渲染会掉色）",
  unknown.length === 0, unknown.map(p => `${p.name}:${p.type}`).join(" "));
check("「其他」类在导航页是可见的（没有 off:true）", !TYPES.other.off);

console.log("\n--- ⑥ 路网地标表要和点位表一致 ---");
// campus-graph.js 的 CAMPUS_LANDMARKS 用来把路线翻译成文字指引，
// 坐标一旦和 campus.js 分叉，就会出现"路线说途经西区宿舍、但指在旧位置"。
const GRAPH = fs.readFileSync(path.join(ROOT, "frontend/js/campus-graph.js"), "utf8");
const lmBlock = GRAPH.slice(GRAPH.indexOf("const CAMPUS_LANDMARKS = ["),
                           GRAPH.indexOf("];", GRAPH.indexOf("const CAMPUS_LANDMARKS = [")));
for (const n of ["西区宿舍", "东区餐厅", "图书馆", "游泳馆"]) {
  const inGraph = new RegExp(`name:\\s*"${n}",\\s*lng:\\s*([\\d.]+),\\s*lat:\\s*([\\d.]+)`).exec(lmBlock);
  const inPlaces = byName(n) || PLACES.find(p => p.name === n);
  check(`★ 地标「${n}」的坐标与点位表一致`,
    !!inGraph && !!inPlaces &&
    Number(inGraph[1]) === inPlaces.lng && Number(inGraph[2]) === inPlaces.lat,
    inGraph ? `地标 ${inGraph[1]},${inGraph[2]} vs 点位 ${inPlaces ? inPlaces.lng + "," + inPlaces.lat : "?"}` : "地标表里没有");
}

console.log("\n" + "=".repeat(60));
console.log(`  结论: ${pass} 通过 / ${fail} 失败`);
console.log("=".repeat(60) + "\n");
process.exit(fail ? 1 : 0);
