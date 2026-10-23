/* AI伴学「对话框式」页面的契约测试（纯 Node，不开浏览器、不起服务）
 *
 * 跑法：node tools/test_plan_chat.mjs
 *
 * 为什么要有这个文件：
 *   需求：「这里使用 AI agent，对话框方式，其他样式去掉」。
 *   这条改动最容易被**顺手改回去**：只要有人觉得"表单更好用"，
 *   把 plan.html 里的输入框加回来、或者把天数做成下拉框，页面照样能跑、
 *   也不会报任何错 —— 但那就不是用户要的那个东西了。
 *
 *   所以这里把三件事钉死：
 *     ① **旧样式必须真的没了**（生成设置表单 / 生成按钮 / 结果框 / 独立调用记录块）；
 *     ② **天数只能从话里认**（下拉框撤掉之后，这是天数的唯一来源）；
 *     ③ 那条被真金白银换来的不变量 —— 发送时**必须带上课表**，别改回去。
 *
 *   本文件不需要浏览器，秒级完成，适合放进每次改动的自检链。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HTML = fs.readFileSync(path.join(ROOT, "frontend/plan.html"), "utf8");
const JS = fs.readFileSync(path.join(ROOT, "frontend/js/planner-plan.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "frontend/css/style.css"), "utf8");
const CORE = fs.readFileSync(path.join(ROOT, "frontend/js/planner-core.js"), "utf8");

let pass = 0, fail = 0;
function check(title, cond, detail = "") {
  if (cond) { pass++; console.log(`  [PASS] ${title}${detail ? "  " + detail : ""}`); }
  else { fail++; console.log(`  [FAIL] ${title}  ${detail}`); }
}

/* ===========================================================================
   ① 天数识别：把 planner-plan.js 的「纯函数区」抠出来在 Node 里 eval
   ---------------------------------------------------------------------------
   这两行标记是**契约**：纯函数区里不许出现 DOM / localStorage，
   否则这里 eval 就会炸。文件里那段注释也写了这条。
   =========================================================================== */
const PURE_START = JS.indexOf("   纯函数区（工具");
const PURE_END = JS.indexOf("/* ==================== 纯函数区结束");
check("planner-plan.js 里能定位到「纯函数区」标记",
  PURE_START > 0 && PURE_END > PURE_START,
  `start=${PURE_START} end=${PURE_END}`);

const PURE_SRC = PURE_START > 0 && PURE_END > PURE_START
  ? JS.slice(JS.lastIndexOf("/*", PURE_START), PURE_END)
  : "";

const sandbox = {};
if (PURE_SRC) {
  // eslint-disable-next-line no-eval
  eval(PURE_SRC + ";sandbox.detectDays = detectDays;"
    + "sandbox.DAYS_DEFAULT = DAYS_DEFAULT; sandbox.DAYS_MAX = DAYS_MAX;");
}
const detectDays = sandbox.detectDays;
const DAYS_DEFAULT = sandbox.DAYS_DEFAULT;
const DAYS_MAX = sandbox.DAYS_MAX;

console.log("\n--- ① 天数从自然语言里认（下拉框撤了，这是天数的唯一来源）---");
check("默认天数是 7（与后端 / 界面文案一致）", DAYS_DEFAULT === 7, `实际 ${DAYS_DEFAULT}`);
check("上限 30（与后端 max(1, min(30, days)) 对齐，别一边 30 一边 14）",
  DAYS_MAX === 30, `实际 ${DAYS_MAX}`);

// ★ 暂时性死区（TDZ）防护：`let planDays = DAYS_DEFAULT;` 若排在
//   `const DAYS_DEFAULT = 7;` **前面**，脚本顶层就抛 ReferenceError，
//   后果是**整页全哑**（后面的函数都没定义、按钮点了没反应），
//   而语法检查和小白读代码都看不出来 —— 这是真浏览器 e2e 抓到的第一版 bug。
const iDaysDefault = JS.indexOf("const DAYS_DEFAULT");
const iPlanDays = JS.indexOf("let planDays");
check("★ DAYS_DEFAULT 的声明排在用到它的 let planDays 之前（否则整页 TDZ 全哑）",
  iDaysDefault > 0 && iPlanDays > iDaysDefault,
  `const DAYS_DEFAULT@${iDaysDefault}  let planDays@${iPlanDays}`);

if (typeof detectDays === "function") {
  const cases = [
    ["帮我排 3 天", undefined, 3],
    ["排三天", undefined, 3],
    ["我要两周的", undefined, 14],
    ["2周", undefined, 14],
    ["从明天开始排 14 天", undefined, 14],
    ["来个半个月的", undefined, 15],
    ["排一个月", undefined, 30],
    // 上限/下限：说 60 天不能真的排 60 天（后端也只有 30 天数据）
    ["排 60 天", undefined, 30],
    ["排 0 天", undefined, 1],
    // ★ 认不出来的必须**沿用此前**：追问「再细一点」时跳回 7 天是最烦人的
    ["再细一点", 3, 3],
    ["期末数据结构冲 90 分，顺便把六级过了", undefined, 7],
    // 「周末」「周一」不是天数，不能被当成 14 天
    ["这周末想放松一下", 5, 5],
  ];
  for (const [text, prev, want] of cases) {
    const got = detectDays(text, prev);
    check(`「${text}」${prev ? `（上一轮 ${prev} 天）` : ""} -> ${want} 天`,
      got === want, `实际 ${got}`);
  }
} else {
  fail++; console.log("  [FAIL] 纯函数区没抠出 detectDays —— 后面 12 条断言全部跳过");
}

/* ===========================================================================
   ② HTML：该有的在、该没的没了
   =========================================================================== */
console.log("\n--- ② plan.html：对话框在，旧表单没了 ---");
const has = (s) => HTML.includes(s);

check("有对话区容器 #plan-chat", has('id="plan-chat"'));
check("有输入框 #plan-msg", has('id="plan-msg"'));
check("有发送按钮 #btn-plan-send", has('id="btn-plan-send"'));
// 回车发送：2026-10-18 输入框从 <input> 换成 <textarea>（长目标要能换行、超出能滚），
// 键位改由 onPlanInputKey() 统一处理 —— Enter 发送、Shift+Enter 换行、
// 中文输入法选字时的回车**不**发送。断言跟着验这一对，别再去找旧的 inline 写法。
check("回车即可发送（onkeydown → onPlanInputKey，最终调 sendPlan）",
  has('onkeydown="onPlanInputKey(event)"')
  && /function onPlanInputKey\(e\) \{[\s\S]{0,400}?e\.key !== "Enter"[\s\S]{0,300}?sendPlan\(\)/.test(JS));
check("Shift+Enter 换行 / 输入法选字不误发",
  /e\.shiftKey/.test(JS) && /isComposing/.test(JS));
check("发送按钮绑的是 sendPlan()", has('onclick="sendPlan()"'));
check("有清空对话的入口", has('onclick="clearPlanChat()"'));
check("页脚提示位 #plan-tip 在（天数怎么改全靠它说）", has('id="plan-tip"'));

// ★ 这几条是「其他样式去掉」的实质：撤掉的东西不许回来。
//   ⚠️ 必须先**剥掉 HTML 注释**再断言：plan.html 里那段注释正是在说明
//      "这些控件已经被撤掉了"，直接匹配源码会把**正确的新实现判成失败**。
//      （同一条教训在这个项目里已经出现过：断言"界面不含某词"之前要先剥注释。）
const HTML_NO_COMMENT = HTML.replace(/<!--[\s\S]*?-->/g, "");
const hasVisible = (s) => HTML_NO_COMMENT.includes(s);

const GONE = [
  ["生成设置表单标题", "生成设置"],
  ["学习目标输入框", 'id="goal"'],
  ["计划天数下拉框", 'id="days"'],
  ["年级输入框", 'id="grade"'],
  ["专业输入框", 'id="major"'],
  ["生成计划按钮", 'id="btn-plan"'],
  ["单独的结果框", 'id="plan"'],
  ["单独的 Agent 调用记录块", 'id="agent-trace"'],
];
for (const [what, needle] of GONE) {
  check(`★ 旧样式已撤掉：${what}（${needle}）`, !hasVisible(needle));
}
check("★ 没有裸的 <select>（天数不再是下拉框选的）", !/<select/i.test(HTML_NO_COMMENT));

check("引入了 planner-plan.js", /src="js\/planner-plan\.js\?v=/.test(HTML));

/* ===========================================================================
   ③ JS：对话逻辑的关键点
   =========================================================================== */
console.log("\n--- ③ planner-plan.js：对话逻辑 ---");
for (const fn of ["sendPlan", "clearPlanChat", "renderPlanChat", "pushPlanMsg",
                  "traceHtml", "planMsgHtml", "showPlanWaiting", "updatePlanTip"]) {
  check(`定义了 ${fn}()`, new RegExp(`function ${fn}\\s*\\(`).test(JS));
}

// ★ 调用记录要挂在 **AI 自己那条消息**里。旧版是单独一块 div，飘在表单下面 ——
//   那正是"看起来不像 Agent"的根源，所以这条要钉住。
check("★ 工具调用记录由 traceHtml() 渲染，且挂在 AI 消息内部（不是独立区块）",
  /function planMsgHtml/.test(JS)
  && /traceHtml\(m\.trace\)/.test(JS)
  && /class="msg ai"><div class="stack">/.test(JS));

// 开场白和"AI 的回答"长得一模一样（都是 .bubble.plan-text），验收脚本靠 greeting 类区分。
// 少了这个类，「AI 回了几条」会把开场白也算进去，导致 e2e 在第一轮就误判成功。
check("★ 开场白带 greeting 类（e2e 靠它把开场白和真回答分开）",
  /class="bubble plan-text greeting"/.test(JS));

// ★ 计划正文带一点点排版（**小标题**），但**必须先转义再替换** ——
//   顺序反了就是给自己开一个 XSS 口子（正文里可能有任意用户输入）。
{
  const seg = JS.slice(JS.indexOf("function planTextHtml"), JS.indexOf("function planMsgHtml"));
  const iEsc = seg.indexOf("escapeHtml");
  const iRep = seg.indexOf(".replace(");
  check("★ 计划正文先转义、再套加粗（顺序反了就是 XSS 口子）",
    iEsc > 0 && iRep > iEsc, `escapeHtml@${iEsc} replace@${iRep}`);
}

// ★ core 里那句 `callIfDefined("...")` 是**按名字**找函数的：找不到就静默跳过。
//   所以页面里的函数一改名，这里不改**不会报任何错**，只是"课表变了页脚提示不刷新"。
//   这类静默失效只能靠断言钉住。
check("★ planner-core 刷新时叫的是页面上真实存在的函数名（updatePlanTip）",
  /callIfDefined\("updatePlanTip"\)/.test(CORE) && !/callIfDefined\("renderPlanHint"\)/.test(CORE));

// ★ 防回归：「AI 看到空课表」是出现过的真问题（用户刚导入还没保存时库里是空的）
check("★ 发送时带上当前课表（别改回只发 goal/days）",
  /courses:\s*\(typeof courses/.test(JS) && /\.map\(stripLocal\)/.test(JS));
check("★ 发送时带上对话上下文 history（多轮追问才接得住）",
  /history,/.test(JS) || /history:/.test(JS));

// ★ 顺序守卫：history 必须在 push 当前这条**之前**取。
//   反了的话，同一句话会既在 history 里、又作为 goal 发一次（提示词里说两遍）。
const iHistory = JS.indexOf("const history = planMessages");
const iPush = JS.indexOf('pushPlanMsg({ role: "user"');
check("★ history 在 push 用户消息之前取（否则同一句话会发两遍）",
  iHistory > 0 && iPush > 0 && iHistory < iPush,
  `history@${iHistory} push@${iPush}`);

// 等待体验：这三条是用户 2026-10-14 定的口径，AI伴学页照搬
const WAIT_BLOCK = JS.slice(JS.indexOf("const WAIT_LINES"), JS.indexOf("function pickOne"));
check("★ 等待文案**不含省略号**（用户明确否掉的写法）",
  !/[…]{1}|\.\.\.|。。。/.test(WAIT_BLOCK));
check("★ 超出预估后不报负数秒数（改用安抚语）", /left > 1\s*\?/.test(JS));
check("★ 等待气泡不进对话记录（只挂 DOM 节点）",
  /stack\.appendChild\(bubble\)/.test(JS) && !/pushPlanMsg\(\{\s*role:\s*"assistant",\s*content:\s*pickOne/.test(JS));

// CSS：计划正文必须保留换行、AI 气泡必须比普通气泡宽
console.log("\n--- ④ 样式：长计划的观感 ---");
check("★ AI 气泡保留换行（计划是分天分条的，塌成一段没法照做）",
  /\.msg \.bubble\.plan-text \{ white-space: pre-wrap;/.test(CSS));
check("★ 对话容器显式写回 white-space: normal（否则模板里的换行会渲染成空行）",
  /\.plan-chat-box \{[^}]*white-space: normal/.test(CSS));
check("★ AI 气泡比普通气泡宽（计划很长，挤在 78% 里会拖得过分）",
  /\.msg\.ai \.stack \{[^}]*max-width: 92%/.test(CSS));
check("调用记录进气泡后不再要下外边距", /\.at-inline \{ margin: 0; \}/.test(CSS));

console.log("\n" + "=".repeat(60));
console.log(`  结论: ${pass} 通过 / ${fail} 失败`);
console.log("=".repeat(60) + "\n");
process.exit(fail ? 1 : 0);
