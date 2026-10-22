// @ts-check
/* 学业规划 · AI伴学子页（**对话式**）
   ------------------------------------------------------------------
   这一页现在就是**一个对话框**：你在下面说，Agent 在上面答。

   改造原因（用户 2026-10-15 原话）：
     「这里使用 AI agent，对话框方式，其他样式去掉」

   之前的样子是一张「生成设置」表单 —— 学习目标、计划天数、年级、专业四个控件，
   加一个「生成计划」按钮，下面再挂一个结果框和一块独立的「Agent 调用记录」。
   问题是：**它看起来像一个表单工具，而不是一个 Agent。**
   哪怕后台真的在调工具，用户感受到的也只是"填表 → 等 → 出结果"，
   跟"和一个会自己查资料的人对话"完全是两种心理模型。

   现在改成对话之后，几件事跟着变了：
     · 说目标就是打字，不再是"填输入框"；
     · **天数从话里认**（「排 3 天」「排两周」都认，没说就沿用上一轮，默认 7 天）
       —— 因为下拉框已经被撤掉了，见 detectDays()；
     · Agent 的工具调用记录**挂在它自己那条回答里**，而不是飘在页面别处 ——
       "谁答的就贴在谁下面"，这一句比任何说明都直白；
     · 支持多轮：前面说过的话会一起发上去，所以「改成两周」「再细一点」接得上。

   依赖 planner-core.js（courses 状态 / api / onEl / stripLocal）。

   ⚠️ 这里有一个必须记住的坑（更早一轮修的，别改回去）：
      生成计划时**必须把当前课表一并传给后端**。
      早期这里只发了 `{goal, days}`，后端只能去数据库读 Course 表 ——
      而用户「刚导入还没点保存」时库里是空的，AI 于是回答
      「你还没有添加任何课程，目前你的课表是空的」，看起来像是白填了。
      现在 body 里带上 courses，所见即所得。
   ================================================================== */

/* ===========================================================================
   状态与常量
   =========================================================================== */

/** 对话记录存本地：换页回来还能看到上一次排的计划。 */
const PLAN_CHAT_KEY = "plan_chat_history";
/** 上一轮用的天数。用户不说天数时沿用 —— 下拉框没了，只能靠这个记住。 */
const PLAN_DAYS_KEY = "plan_days";
/** 本地最多留几条消息。计划正文很长，不设上限会把 localStorage 撑爆。 */
const PLAN_CHAT_KEEP = 12;

/* ===========================================================================
   纯函数区（工具 tools/test_plan_chat.mjs 会把这段单独抠出来 eval，
   所以**这里不许引用 DOM / localStorage / 任何浏览器全局**，
   常量也要放在这一段里面 —— 抠出去之后外面那些声明是不存在的）
   =========================================================================== */

/** 天数默认值 / 上下限（与后端 max(1, min(30, days)) 对齐，别一边 30 一边 14）。 */
const DAYS_DEFAULT = 7;
const DAYS_MAX = 30;

/**
 * 从用户说的话里认「要排几天」。
 *
 * 为什么需要它：下拉框被撤掉了（用户要求"其他样式去掉"），
 * 但天数是个**真参数**（决定 Agent 排几天、也决定发给后端的天数），不能丢。
 * 于是改成从自然语言里认 —— 这本来也更符合"对话"这件事。
 *
 * 认的顺序是**从具体到笼统**，先数字后汉字：
 *   「3 天」/「三天」/「2周」/「两周」/「半个月」/「一个月」
 * 都认不出来就**沿用上一轮**（比如追问「再细一点」时不该跳回 7 天）。
 *
 * @param {string} text 用户这一轮说的话
 * @param {number} [prev] 上一轮用的天数
 * @returns {number} 1~30 之间的天数
 */
function detectDays(text, prev) {
  const s = String(text || "");
  const CN = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
  const clamp = (n) => Math.max(1, Math.min(DAYS_MAX, Math.round(n)));
  let m;

  if ((m = s.match(/(\d+)\s*天/))) return clamp(Number(m[1]));
  // 「周」单独出现时不算（「周末」「周一会很忙」都不是在说排几天），所以要求前面有数字或汉字数词
  if ((m = s.match(/(\d+)\s*个?\s*周/))) return clamp(Number(m[1]) * 7);
  if ((m = s.match(/([一两二三四五六七八九十])\s*天/))) return clamp(CN[m[1]]);
  if ((m = s.match(/([一两二三四五六七八九十])\s*个?\s*周/))) return clamp(CN[m[1]] * 7);
  if (/半个月/.test(s)) return 15;
  if (/一个月/.test(s)) return 30;

  const p = Number(prev);
  return clamp(p >= 1 && p <= DAYS_MAX ? p : DAYS_DEFAULT);
}

/* ==================== 纯函数区结束 ==================== */

/* ---------------------------------------------------------------------------
   ⚠️ 这两个 `let` 必须放在纯函数区**后面**。
      第一版写在前面（`let planDays = DAYS_DEFAULT;` 在 `const DAYS_DEFAULT = 7;` 之前），
      结果是**整个页面全哑** —— `const`/`let` 有"暂时性死区"，声明语句执行到之前
      访问就抛 `ReferenceError: Cannot access 'DAYS_DEFAULT' before initialization`，
      而它发生在脚本顶层，于是后面所有函数都没定义、按钮点了没反应。
      静态检查（甚至只读代码）都看不出来，是真浏览器 e2e 抓到的。
   =========================================================================== */

/** 对话记录：[{role:"user"|"assistant", content:string, trace?:Array}] */
let planMessages = [];
let planDays = DAYS_DEFAULT;

/* ===========================================================================
   渲染
   =========================================================================== */

/** 开场白：把"能聊什么"一次说清楚，省得用户对着空框发呆。 */
const PLAN_GREETING =
  "你好，我是你的学业规划 Agent。说说你的目标就行 —— 比如"
  + "「期末数据结构冲 90 分，顺便把六级过了」，或者「这周想把高数补一补」。\n"
  + "我会先读你的时间表、算出这期间的空档，再排出每天几点到几点做什么。\n"
  + "想排几天直接说（「排 3 天」「排两周」都行），不说就按 7 天来。";

/** 工具名 -> 给人看的说法。后端返回的是函数名，直接摆给用户看太技术了。 */
const TOOL_LABELS = {
  get_timetable: "读取我的时间表",
  get_class_schedule: "查询节次作息",
  find_free_slots: "计算空闲时段",
  get_building_info: "识别上课地点",
  get_weather: "查询天气",
  // 2026-10-20：往时间表里写一条固定安排（不是排计划）
  add_schedule: "写入我的时间表",
};

/**
 * 渲染 **Agent 的工具调用记录**（一条回答一份，贴在回答上方）。
 *
 * 这一块是「AI伴学」和普通「AI 问答」最直观的区别：用户能看见它不是凭空生成，
 * 而是**先查了课表、又算了空档，才动笔**。对答辩/演示来说，这一屏比计划本身更有说服力
 * —— 它把「function calling / tool use」这件本来藏在后端的事摊开了。
 *
 * @param {Array<{tool:string,args?:object,ok?:boolean,summary?:string}>|null} trace
 *        后端返回的调用轨迹。**空/null 时整块不渲染** —— AI 不可用降级成本地计划时
 *        就是空数组，这时候留一个空框只会让人以为出错了。
 * @returns {string} HTML，可能是空串
 */
function traceHtml(trace) {
  const steps = (Array.isArray(trace) ? trace : []).filter(s => s && s.tool);
  if (!steps.length) return "";

  const items = steps.map(s => {
    const label = TOOL_LABELS[s.tool] || s.tool;
    const summary = escapeHtml(s.summary || "");
    // 失败的一步要显出来（红点），否则「Agent 说它查了」就成了无法证伪的话
    const bad = s.ok === false;
    return `<li class="at-step${bad ? " at-step-bad" : ""}">`
      + `<span class="at-ico">${bad ? "⚠️" : "✓"}</span>`
      + `<span class="at-name">${escapeHtml(label)}</span>`
      + (summary ? `<span class="at-sum">${summary}</span>` : "")
      + `</li>`;
  }).join("");

  return `<div class="agent-trace at-inline">`
    + `<div class="at-head">Agent 调用记录`
    + `<span class="at-count">${steps.length} 步</span></div>`
    + `<ol class="at-list">${items}</ol></div>`;
}

/**
 * 计划正文 -> 带一点排版的 HTML。
 *
 * 模型输出的计划是 **Markdown 味的纯文本**：「**10月15日（周四）：**」这样的
 * 小标题、`- ` 开头的条目。以前它躺在一个纯文本框里，星号就是星号；
 * 现在进了对话气泡（字号更大、更显眼），再让 `**` 裸着就很像"没渲染出来"。
 *
 * ⚠️ 安全边界：**先 `escapeHtml` 再替换**。顺序反了就是自己给自己开一个 XSS 口子
 *    （计划正文里可以出现任意用户输入的内容）。这里只认 `**加粗**` 一种记号，
 *    不引第三方 Markdown 库 —— 计划长、要逐字渲染，库进来只会更慢更不确定。
 *
 * @param {string} text
 * @returns {string}
 */
function planTextHtml(text) {
  return escapeHtml(String(text || ""))
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/^###?\s+(.+)$/gm, "<b>$1</b>")
    // 出处那行（`来源：《休学手续办理流程》`）单独成块、灰一档：
    // 它是**证据**不是答案，和正文糊在一起学生会以为"这句话也是结论"。
    // 连前面的换行一起吃掉 —— .chat-box 是 pre-wrap，留着会多一个空行。
    .replace(/\n*来源：([^\n]+)/g, '<span class="doc-src">来源：$1</span>');
}

/**
 * 一条消息 -> HTML。
 *
 * ⚠️ 注意这里**不写跨行的模板字符串**：外层 `.chat-box` 上有 `white-space: pre-wrap`
 *    （那是给"计划正文"用的），模板里的换行和缩进会被原样渲染成空行，
 *    消息之间就会莫名其妙多出一片空白。所以一律用单行拼接。
 */
function planMsgHtml(m) {
  if (m.role === "user") {
    return `<div class="msg me"><div class="bubble">${escapeHtml(m.content)}</div></div>`;
  }
  // AI 这条：调用记录在上、正文在下，一起包在 .stack 里（宽度比普通气泡宽）
  return `<div class="msg ai"><div class="stack">${traceHtml(m.trace)}`
    + `<div class="bubble plan-text">${planTextHtml(m.content)}</div></div></div>`;
}

/** 整屏重绘。开场白**永远在最上面**，清空对话也留着 —— 它是这页的说明书。 */
function renderPlanChat() {
  const box = onEl("plan-chat");
  if (!box) return;
  // 开场白额外挂一个 `greeting` 类：它和"AI 的回答"长得一样（都是 .bubble.plan-text），
  // 验收脚本要靠这个类把两者区分开（数"AI 回了几条"时不能把开场白算进去）。
  box.innerHTML = `<div class="msg ai"><div class="stack">`
    + `<div class="bubble plan-text greeting">${escapeHtml(PLAN_GREETING)}</div></div></div>`
    + planMessages.map(planMsgHtml).join("");
  box.scrollTop = box.scrollHeight;
}

/** 追加一条并落盘（只留最近 PLAN_CHAT_KEEP 条，计划正文太占地方）。 */
function pushPlanMsg(msg) {
  planMessages.push(msg);
  if (planMessages.length > PLAN_CHAT_KEEP) {
    planMessages = planMessages.slice(-PLAN_CHAT_KEEP);
  }
  try { localStorage.setItem(PLAN_CHAT_KEY, JSON.stringify(planMessages)); } catch (e) {
    // 存不下（隐私模式 / 配额满）不该影响这一轮对话，静默跳过
    console.warn("[AI伴学] 对话记录写本地失败：", e);
  }
  renderPlanChat();
}

/**
 * 页脚那句提示：说清「AI 这次能看到几门课」，并提醒**天数可以直接说**。
 * 天数输入框撤掉之后，这句是用户唯一能知道"还能改天数"的地方。
 */
function updatePlanTip() {
  const tip = onEl("plan-tip");
  if (!tip) return;
  const n = (typeof courses !== "undefined" ? courses : []).filter(c => c.name).length;
  const how = "想改天数直接说「排 3 天」或「排两周」。";
  tip.textContent = n
    ? `已读到你的 ${n} 条时间表安排 —— Agent 会避开上课时间，只往空档里排。${how}`
    : `当前时间表为空也没关系 —— 直接说「周六 18-19 点上钢琴课」这类话，`
      + `我会写进时间表；想要更贴合就先回时间表页导入课程。${how}`;
}

/* ===========================================================================
   等待体验
   ---------------------------------------------------------------------------
   与「AI 倾听对话」页同一套口径（用户 2026-10-14 定的，这里是照搬不是新发明）：
     ① **不要省略号** —— 一串点点看着像卡死，也像敷衍；
     ② 要给出**大概还要多久**，让人心里有数；
     ③ 措辞要有**温度**。

   这里的等待比情绪页长得多（要跑好几轮工具 + 写一份 7 天计划，实测 1 分钟起步），
   所以预估的兜底值按 60 秒起，并且换了"Agent 在干活"的说法。
   =========================================================================== */

const ETA_KEY = "plan_reply_ms";
const ETA_DEFAULT = 60000;
/** 低于这个耗时就不记 —— 一次极快的回复会把预估拉得很低，下次真慢下来用户会觉得被骗。 */
const ETA_MIN_RECORD = 8000;
const ETA_MAX_RECORD = 300000;

const WAIT_LINES = [
  "我在查你的时间表和空闲时段，先看清楚再动笔。",
  "正在翻你这几天的时间表，看看哪些时段是空的。",
  "先把你的时间表和你说的目标对一遍，马上开始排。",
  "在算未来几天的空档，算完就给你排每天的安排。",
];

/** 超出预估之后的话：**不报负数秒数**，换成安抚。 */
const OVERRUN_LINES = [
  "计划要写得细一点，还在写，再等我一会儿。",
  "正在把每天的时间段一条条排出来，马上就好。",
  "就快好了，我想把每个时段都写具体些，别急。",
];

/** @param {string[]} arr */
function pickOne(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** 预估本轮耗时（毫秒）。 */
function estimatedMs() {
  const v = Number(localStorage.getItem(ETA_KEY) || "");
  return (v >= ETA_MIN_RECORD && v <= ETA_MAX_RECORD) ? v : ETA_DEFAULT;
}

/** 记下本轮真实耗时，做指数平滑（新值占 4 成）。 */
function rememberMs(ms) {
  if (!(ms >= ETA_MIN_RECORD && ms <= ETA_MAX_RECORD)) return;
  const prev = Number(localStorage.getItem(ETA_KEY) || "");
  const next = prev >= ETA_MIN_RECORD ? Math.round(prev * 0.6 + ms * 0.4) : Math.round(ms);
  try { localStorage.setItem(ETA_KEY, String(next)); } catch (e) { /* 存不下就算了 */ }
}

/**
 * 在对话流末尾挂一个「正在做」的气泡，每秒更新剩余时间。
 *
 * 这个气泡**不进 planMessages**（那会污染记录、还会被写进 localStorage）——
 * 它只是 DOM 上的临时节点，回复到了就摘掉。
 *
 * @param {number} estMs 预估耗时
 * @returns {{stop: () => void}}
 */
function showPlanWaiting(estMs) {
  const box = onEl("plan-chat");
  if (!box) return { stop() {} };

  const wrap = document.createElement("div");
  wrap.className = "msg ai";
  const stack = document.createElement("div");
  stack.className = "stack";
  const bubble = document.createElement("div");
  bubble.className = "bubble wait-bubble";
  stack.appendChild(bubble);
  wrap.appendChild(stack);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;

  const head = pickOne(WAIT_LINES);
  const t0 = Date.now();

  const paint = () => {
    const left = Math.ceil((estMs - (Date.now() - t0)) / 1000);
    // 还有 1 秒以上就报数；否则换成安抚语 —— 报「还要 -3 秒」比不报更伤信任
    const tail = left > 1 ? `大概还要 ${left} 秒。` : pickOne(OVERRUN_LINES);
    bubble.innerHTML = `${escapeHtml(head)}`
      + `<span class="wait-eta">${escapeHtml(tail)}</span>`;
    box.scrollTop = box.scrollHeight;
  };
  paint();
  const timer = setInterval(paint, 1000);

  return {
    stop() { clearInterval(timer); if (wrap.parentNode) wrap.remove(); },
  };
}

/* ===========================================================================
   输入框：随内容长高（1~3 行）
   =========================================================================== */

/**
 * 输入框按内容自己长高，最多 3 行，再长就在框里滚。
 *
 * 为什么不用纯 CSS 的 `field-sizing: content`：只有很新的 Chrome 支持，
 * 微信内置浏览器 / iOS Safari 基本没有 —— 手机上等于没写。
 * 为什么必须先 `height = "auto"`：不归零的话删字时高度只涨不缩，
 * 删完一段话输入框还支棱着三行。
 * ⚠️ 3 行的高度要**现算**，不能写死 108px：手机端字号 16px（行高 24）、
 *    桌面端 14px（行高 21），再加上下 padding 各 10 —— 写死一个数就会
 *    「说好 3 行却露出 4 行」或者「只显示 2 行半」。实测就是露了 4 行。
 */
function autoGrowPlanInput(el) {
  const box = el || onEl("plan-msg");
  if (!box) return;
  const cs = getComputedStyle(box);
  const lh = parseFloat(cs.lineHeight) || 21;
  const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const max = Math.round(lh * 3 + pad);
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight, max) + "px";
}

/**
 * 回车键：Enter 发送，Shift+Enter 换行。
 *
 * ⚠️ 两个坑：
 *   1. <textarea> 里 Enter 默认是**换行**，不 preventDefault 就是「按了没反应」；
 *   2. 中文输入法选字时按的回车不能当发送（isComposing / keyCode 229），
 *      否则「打拼音按回车选第一个词」会直接把半句话发出去。
 */
function onPlanInputKey(e) {
  if (e.key !== "Enter" || e.shiftKey) return;
  if (e.isComposing || e.keyCode === 229) return;
  e.preventDefault();
  sendPlan();
}

/* ===========================================================================
   发送
   =========================================================================== */

/**
 * 发一轮：把用户的话交给后端 Agent，把计划贴回对话里。
 * @returns {Promise<void>}
 */
async function sendPlan() {
  const input = onEl("plan-msg");
  const btn = onEl("btn-plan-send");
  if (!input || !btn) return;

  const text = input.value.trim();
  if (!text) { toast("先说点什么吧，比如「期末数据结构冲 90 分」", "info"); return; }

  // ★ 天数从这句话里认（认不出来沿用上一轮）。必须在发请求**之前**算好。
  planDays = detectDays(text, planDays);
  try { localStorage.setItem(PLAN_DAYS_KEY, String(planDays)); } catch (e) { /* 忽略 */ }

  // ⚠️ 上下文要取「**这一条之前**」的消息。取完再 push 当前这条，
  //    否则同一句话会既出现在 history 里、又作为 goal 发一次。
  const history = planMessages.slice(-8).map(m => ({ role: m.role, content: m.content }));

  input.value = "";
  autoGrowPlanInput(input);      // 清空后把高度收回一行，不然会留着一块空白
  pushPlanMsg({ role: "user", content: text });

  // 发送期间禁止重复点击，避免连点产生多轮请求
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = "正在排";              // ★ 不带省略号

  const wait = showPlanWaiting(estimatedMs());
  const t0 = Date.now();
  let plan = "";
  let trace = [];
  try {
    const data = await api("/planner/plan", {
      method: "POST",
      body: {
        goal: text,
        days: planDays,
        // 年级 / 专业不用再单独填了 —— 用户想说的话自己会说（「我是大二软件工程的」），
        // 后端也只需要一个字符串，模型能从 goal 里读出来。这里保留字段是为了接口不变。
        grade: "",
        major: "",
        // ★ 把课表带上 —— 这是「AI 看到空课表」那个 bug 的根治点。
        //   只传后端认的字段，_id/_uncertain 这些前端标记会被 stripLocal 剥掉。
        courses: (typeof courses !== "undefined" ? courses : [])
          .filter(c => c.name || c.building)
          .map(stripLocal),
        // ★ 多轮上下文：后端只取其中的 user 原话，用来读懂「改成两周」这类短追问
        history,
      },
    });
    plan = data.plan || "（这次没有拿到内容，换个说法再问一次试试）";
    trace = data.trace || [];
  } catch (e) {
    plan = "生成失败：" + (e && e.message ? e.message
      : "请确认后端已启动，且 backend/.env 里填了 GLM_API_KEY");
  } finally {
    rememberMs(Date.now() - t0);
    wait.stop();
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }

  pushPlanMsg({ role: "assistant", content: plan, trace });

  // ★ 刚往时间表里写了一条，就把本地那份课表读回来（2026-10-20）。
  //   不读的话页面内存里的 courses 还是旧的：页脚还说「当前时间表为空」，
  //   下一句问「周六有没有课」也会拿旧课表去答。
  if ((trace || []).some(t => t && t.tool === "add_schedule")) {
    try { await loadTimetable(true); }
    catch (e) { console.warn("[AI伴学] 写入后重读时间表失败：", e); }
  }
  if (input.focus) input.focus();
}

/** 清空对话：记录、天数、估算全归零（估算留着会让下一轮的第一个提示秒数很怪）。 */
function clearPlanChat() {
  planMessages = [];
  planDays = DAYS_DEFAULT;
  try {
    localStorage.removeItem(PLAN_CHAT_KEY);
    localStorage.removeItem(PLAN_DAYS_KEY);
    localStorage.removeItem(ETA_KEY);
  } catch (e) { /* 忽略 */ }
  renderPlanChat();
  toast("对话已清空", "ok");
}

/* ---------- 启动 ---------- */
(async function initPlanPage() {
  try {
    const saved = JSON.parse(localStorage.getItem(PLAN_CHAT_KEY) || "[]");
    if (Array.isArray(saved)) {
      planMessages = saved.filter(m => m && typeof m.content === "string");
    }
  } catch (e) { planMessages = []; }
  planDays = detectDays("", Number(localStorage.getItem(PLAN_DAYS_KEY) || ""));

  // 先画一次：即使后面读课表失败，用户至少能看到开场白和一个可用的输入框
  renderPlanChat();

  // bootPlanner 里会先注入顶栏、再读课表；读完课表后那句提示才知道"能看到几门课"
  await bootPlanner("plan", ["updatePlanTip"]);
})();
