/* 「时间表」术语契约测试（纯 Node，不开浏览器）
 *
 * 跑法：node tools/test_term_timetable.mjs
 *
 * 为什么要有这个文件：
 *   2026-10-15 需求：学习规划三个子页里，**界面上这个功能一律叫「时间表」**，
 *   不再叫「课表」。这个改动天生容易被"顺手改回去"——它只是文案，
 *   没有类型保护，改错了不会报错，只会让页面之间口径打架。
 *
 * 更要紧的是**边界**：只改「给人看的字」，**不许碰代码里的名字**。
 *   变量 / 函数 / localStorage key / DOM id / 后端接口 / Agent 工具名，
 *   继续叫 timetable / courses —— 这些是链条，改一个就断一片。
 *   所以本文件既做正向断言（新词在），也做反向守卫（旧词没了、内部名没动）。
 *
 * ⚠️ 源码注释里出现「课表」是**故意的**（它们在讲历史与内部机制），
 *    所以 HTML 一律先剥掉注释再断言；JS 用「精确旧文案」而不是全文扫，
 *    否则会把正确实现判成失败（这个问题在 test_plan_chat.mjs 上已经踩过一次）。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = p => fs.readFileSync(path.join(ROOT, p), "utf8");
/** HTML 注释里写着"以前叫课表"，断言前必须剥掉。 */
const stripHtmlComments = s => s.replace(/<!--[\s\S]*?-->/g, "");

const PAGE_TIMETABLE = "frontend/planner.html";
const PAGE_REMINDER = "frontend/reminder.html";
const PAGE_PLAN = "frontend/plan.html";
const JS_CORE = "frontend/js/planner-core.js";
const JS_TIMETABLE = "frontend/js/planner-timetable.js";
const JS_REMINDER = "frontend/js/planner-reminder.js";
const JS_PLAN = "frontend/js/planner-plan.js";

const PAGES = [PAGE_TIMETABLE, PAGE_REMINDER, PAGE_PLAN];
const JSS = [JS_CORE, JS_TIMETABLE, JS_REMINDER, JS_PLAN];

const html = Object.fromEntries(PAGES.map(p => [p, read(p)]));
const htmlVisible = Object.fromEntries(PAGES.map(p => [p, stripHtmlComments(read(p))]));
const js = Object.fromEntries(JSS.map(p => [p, read(p)]));
const backendAgent = read("backend/services/study_agent.py");
const backendPlanner = read("backend/routers/planner.py");
const backendExcel = read("backend/services/timetable_excel.py");

let pass = 0, fail = 0;
function check(title, cond, detail = "") {
  if (cond) { pass++; console.log(`  [PASS] ${title}${detail ? "  " + detail : ""}`); }
  else { fail++; console.log(`  [FAIL] ${title}  ${detail}`); }
}
/** 统计子串出现次数（负向守卫用 `=== 0`）。 */
const countIn = (text, sub) => text.split(sub).length - 1;

console.log("\n--- ① 三个子页的胶囊标签都叫「时间表」---");
for (const p of PAGES) {
  check(`★ ${path.basename(p)} 的胶囊标签是「时间表」`,
    html[p].includes(">时间表</a>"),
    html[p].includes(">课表</a>") ? "还是「课表」！" : "");
}

console.log("\n--- ② 页面可见区域里不许再出现「课表」（注释已剥）---");
for (const p of PAGES) {
  const n = countIn(htmlVisible[p], "课表");
  check(`★ ${path.basename(p)} 可见文案里「课表」出现 0 次`, n === 0, `实际 ${n} 次`);
}

console.log("\n--- ③ 标题 / 按钮 / 提示 ---");
check("★ 标题是「我的时间表」", html[PAGE_TIMETABLE].includes("我的时间表"));
check("★ 按钮是「导入时间表」", html[PAGE_TIMETABLE].includes("导入时间表"));
check("★ 按钮是「保存时间表」", html[PAGE_TIMETABLE].includes("保存时间表"));
check("★ 导入按钮的 title 提示也同步了", html[PAGE_TIMETABLE].includes("支持时间表截图"));
check("★ 识别进度弹窗标题已同步", html[PAGE_TIMETABLE].includes("正在识别时间表截图"));
check("★ 日程提醒页加载文案已同步", html[PAGE_REMINDER].includes("正在读取你的时间表"));
check("★ AI伴学页副标题已同步", html[PAGE_PLAN].includes("我会先去查你的时间表和空档"));

console.log("\n--- ④ JS 里给人看的文案 ---");
check("planner-core 三条 toast 已改",
  js[JS_CORE].includes('"已从账号读取你的时间表"')
  && js[JS_CORE].includes('"已读取本地时间表（未连接后端）"')
  && js[JS_CORE].includes('"时间表已保存到你的账号"'));
// 2026-10-18：删掉「沿用上周」后先降到 4 处（改/新增/删/导入），
// 同一天又补了第 5 处：同格已有「本周不上的课」时，问一句要不要让它本周也上。
// ⚠️ 断言跟着功能一起改，不是为了凑数：5 处必须一个不少。
check("★ 「保存时间表」在 planner-timetable 里出现 5 次（改/新增/删/导入/让老课本周也上）",
  countIn(js[JS_TIMETABLE], "「保存时间表」") === 5,
  `实际 ${countIn(js[JS_TIMETABLE], "「保存时间表」")} 次`);
check("planner-reminder 空态文案已改",
  js[JS_REMINDER].includes('"还没有时间表数据"')
  && js[JS_REMINDER].includes("先去「时间表」页导入或手动添加课程")
  && js[JS_REMINDER].includes('"去添加时间表"')
  && js[JS_REMINDER].includes("你的时间表里还没有安排"));
check("plan 页 Agent 工具名（给人看的说法）已改",
  js[JS_PLAN].includes('get_timetable: "读取我的时间表"'));
check("plan 页开场白 / 页脚提示已改",
  js[JS_PLAN].includes("我会先读你的时间表、算出这期间的空档")
  && js[JS_PLAN].includes("条时间表安排")
  && js[JS_PLAN].includes("当前时间表为空也没关系")
  && js[JS_PLAN].includes("先回时间表页导入"));
check("plan 页三条等待语已改",
  js[JS_PLAN].includes('"我在查你的时间表和空闲时段，先看清楚再动笔。"')
  && js[JS_PLAN].includes('"正在翻你这几天的时间表，看看哪些时段是空的。"')
  && js[JS_PLAN].includes('"先把你的时间表和你说的目标对一遍，马上开始排。"'));

console.log("\n--- ⑤ 负向守卫：旧文案一个字都不许回来 ---");
const OLD = [
  ["planner-core", JS_CORE, '"已从账号读取你的课表"'],
  ["planner-core", JS_CORE, '"已读取本地课表（未连接后端）"'],
  ["planner-core", JS_CORE, '"课表已保存到你的账号"'],
  ["planner-timetable", JS_TIMETABLE, "「保存课表」"],
  ["planner-timetable", JS_TIMETABLE, "课表一般不会这么大"],
  ["planner-timetable", JS_TIMETABLE, "确认一下选的是课表文件吗"],
  ["planner-timetable", JS_TIMETABLE, "个时间点课表里已经有了"],
  ["planner-reminder", JS_REMINDER, '"还没有课表数据"'],
  ["planner-reminder", JS_REMINDER, '"去添加课表"'],
  ["planner-reminder", JS_REMINDER, "你的课表里还没有安排"],
  ["planner-plan", JS_PLAN, 'get_timetable: "读取我的课表"'],
  ["planner-plan", JS_PLAN, "条课表安排"],
  ["planner-plan", JS_PLAN, "当前课表为空也没关系"],
  ["planner-plan", JS_PLAN, "先回课表页导入"],
  ["planner-plan", JS_PLAN, "我会先读你的课表"],
  ["planner-plan", JS_PLAN, '"我在查你的课表和空闲时段'],
  ["planner-plan", JS_PLAN, '"正在翻你这几天的课表'],
  ["planner-plan", JS_PLAN, '"先把你的课表和你说的目标对一遍'],
];
for (const [label, file, lit] of OLD) {
  const n = countIn(js[file], lit);
  check(`旧文案已消失：${label} · ${lit}`, n === 0, `实际 ${n} 次`);
}

console.log("\n--- ⑥ 反向守卫：代码里的名字一个都不许动 ---");
check("★ localStorage key 仍是 timetable_local",
  js[JS_CORE].includes('const LOCAL_KEY = "timetable_local"'));
check("★ 函数名 loadTimetable / saveTimetable 未改",
  js[JS_CORE].includes("async function loadTimetable(")
  && js[JS_CORE].includes("async function saveTimetable("));
check("★ 页面脚本函数名未改（importTimetable / handleImport）",
  js[JS_TIMETABLE].includes("function importTimetable(")
  && js[JS_TIMETABLE].includes("function handleImport("));
check("★ 后端接口路径未改：/planner/timetable · /planner/import-sheet · /planner/plan",
  js[JS_CORE].includes('api("/planner/timetable"')
  && js[JS_TIMETABLE].includes('api("/planner/import-sheet"')
  && js[JS_PLAN].includes('api("/planner/plan"'));
check("★ DOM 锚点未改：#tt / #tt-body / #file-import / #plan-chat",
  html[PAGE_TIMETABLE].includes('id="tt"')
  && html[PAGE_TIMETABLE].includes('id="tt-body"')
  && html[PAGE_TIMETABLE].includes('id="file-import"')
  && html[PAGE_PLAN].includes('id="plan-chat"'));
check("★ Agent 工具名仍是 get_timetable（这是接口契约，不是文案）",
  backendAgent.includes('"name": "get_timetable"'));
check("★ Agent 五个工具的 key 未增未减",
  ["get_timetable", "get_class_schedule", "find_free_slots", "get_building_info", "get_weather"]
    .every(k => backendAgent.includes(`"name": "${k}"`)),
  ["get_timetable", "get_class_schedule", "find_free_slots", "get_building_info", "get_weather"]
    .filter(k => !backendAgent.includes(`"name": "${k}"`)).join(",") || "全在");
check("★ Excel 导入接口路径未改", backendPlanner.includes('@router.post("/import-sheet"'));
check("★ Excel 解析里识别工作表名的关键词未改（要匹配真实表名，属于数据不是文案）",
  backendExcel.includes('for kw in ("模版", "模板", "课表", "课程表")'));

console.log("\n--- ⑦ 后端展示给用户的文案 ---");
check("★ 沿用上周的失败提示已改",
  backendPlanner.includes("如果上周时间表还没点过「保存时间表」"));
check("★ 兜底计划的说明已改",
  backendPlanner.includes("以下为按你的时间表空档自动排出的计划框架")
  && backendPlanner.includes("你的时间表还是空的"));
check("★ 表格导入的失败提示已改",
  backendExcel.includes("没在这张表里找到「星期一~日」的时间表格子")
  && backendExcel.includes("如果时间表在别的工作表里"));
check("★ 截图导入的失败提示已改",
  backendPlanner.includes("请换一张更清晰的时间表截图重试"));
check("★ Agent 提示词里的用词已改（模型会照着措辞写正文）",
  backendAgent.includes("基于这名学生的**真实时间表**")
  && backendAgent.includes("不要凭印象编造学生的时间表"));

console.log("\n" + "=".repeat(60));
console.log(`  结论: ${pass} 通过 / ${fail} 失败`);
console.log("=".repeat(60) + "\n");
process.exit(fail ? 1 : 0);
