// @ts-check
/* 学业规划 · 公共核心（课表 / 日程提醒 / AI伴学 三个子页共用）
   ------------------------------------------------------------------
   这里只放**真正跨页共用**的东西：日期换算、时间槽、课程读写、公共状态。

   渲染按页拆开，各自一个文件：
     · planner-timetable.js  课表页（表格渲染 + 导入 + 核对弹窗 + 增删改）
     · planner-reminder.js   日程提醒页（今天/明天清单 + 浏览器通知）
     · planner-plan.js       AI伴学页（生成学习计划）

   三个页脚本都依赖本文件，所以每个 HTML 里都要**先引 core，再引自己的页脚本**。

   ⚠️ 关于跨文件共享：本项目没有构建工具，全是顶层 <script>，
   所以顶层 `function foo(){}` / `let x` 天然共享同一个全局词法环境，
   跨文件直接调用即可，不需要 export，也不要挂到 window 上。
   唯一要小心的是**同名 const/let 重复声明会 SyntaxError** ——
   所以每个文件里的顶层变量名都保持唯一，别在两边都写 `let courses`。

   ★★ 术语（2026-10-15 用户要求）：**界面上这个功能一律叫「时间表」，不叫「课表」**。
   三个子页的胶囊标签 / 标题 / 按钮 / 提示文案 / Agent 工具名 全部已改。
   但下面这些**内部名字一律没动，也不许动**（改了会连锁断链）：
     · 变量/函数名    courses / loadTimetable / saveTimetable / renderGrid …
     · localStorage   `timetable_local`
     · DOM id/class   `#tt` / `#tt-body` / `.timetable` / `#file-import` …
     · 后端接口与字段 `/planner/timetable`、工具名 `get_timetable`、表名 `courses`
   → 一句话：**「时间表」只活在给人看的文案里，代码里继续叫 timetable/courses。**
     源码注释里出现的「课表」是在讲历史与内部机制，不是界面文案，别去批量替换
     （`tools/test_term_timetable.mjs` 会在剥掉注释后校验界面文案）。
   ================================================================== */
requireAuth();

const DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const LOCAL_KEY = "timetable_local";

/* ---------- 防白屏工具（拆页的关键保障，别绕过它们）---------- */

/**
 * 取元素，取不到返回 null（而不是让 `null.onclick = ...` 直接抛错）。
 *
 * 为什么必须有它：拆页之后同一个 core 被三个页面共用，而**不是每个页面都有
 * 全部元素**（reminder.html 没有 #tt-body，plan.html 没有 #review-mask）。
 * 老代码写的是裸 `document.getElementById("x").addEventListener(...)`，
 * 只要缺一个元素，脚本在那一行就**中断**：后面的函数全部没被定义，
 * 页面渲染不出来 —— 用户看到白屏，控制台只有一句
 * 「Cannot read properties of null」，完全看不出是缺元素导致的。
 *
 * 所以凡「可能不在当前页面」的元素，一律走这个函数。
 * @param {string} id
 * @returns {HTMLElement|null}
 */
function onEl(id) { return document.getElementById(id); }

/**
 * 安全绑事件（元素不存在就静默跳过）。
 * @param {string} id
 * @param {string} ev
 * @param {(e: Event) => void} fn
 * @returns {void}
 */
function onElBind(id, ev, fn) {
  const el = onEl(id);
  if (el) el.addEventListener(ev, /** @type {any} */ (fn));
}

/**
 * 调用某个「按页注册」的函数（当前页没有它就跳过，绝不报错）。
 *
 * 为什么需要：`loadTimetable()` 三个页面都调，但它读完后要刷 UI ——
 * 课表页刷 renderGrid、提醒页刷 renderReminders、计划页刷 updatePlanTip。
 * 若在 core 里无条件调用，在提醒页就会 `ReferenceError: renderGrid is not defined`，
 * 同样中断脚本。统一走这里，谁在就调谁。
 * @param {string} name
 * @returns {void}
 */
function callIfDefined(name) {
  // @ts-ignore —— 故意按名字动态查找：找不到就是「当前页不需要它」
  const fn = typeof globalThis[name] === "function" ? globalThis[name] : null;
  if (fn) { try { fn(); } catch (e) { console.error(`[planner] ${name}() 出错：`, e); } }
}


/* 学期周次范围。
   以前是「本周 -1 ~ +1」三个按钮，只能看三周；但课表类应用用户真正需要的是
   「开学第几周」这个概念 —— 要能翻到学期末看结课周、翻回第 1 周看开学安排。
   所以改成横向滚动的周次条，覆盖整学期。

   ⚠️ 学期锚点 = 开学第 1 周的周一，写死在 **common.js** 的 SEMESTER_START。
   早期这里把「本周」当锚点（weekOffset=0 = 本周），代价是**「第 1 周」根本不是
   一个固定日期** —— 同一份课表，今天打开显示「第 1 周 = 8 月 25 号」，下周打开
   就变成「第 1 周 = 9 月 1 号」，一直在漂。课表上的「第几周」是学校给的绝对概念
   （教务系统、教材进度、考试周次都按它说），会漂就没法用了。
   所以改成以真实开学日为准，weekOffset 表示「相对第 1 周的偏移」：
     weekOffset = 0        -> 第 1 周（开学那周）
     weekOffset = 5        -> 第 6 周
   本周落在学期里哪一周，由 mondayOfThisWeek() 现算，不再用作原点。

   ★ 2026-10-18：SEMESTER_START / SEM_WEEKS_BEFORE / SEM_WEEKS_AFTER 与
     parseWeeks / weekNumberByDate / courseShownInWeek / isoOf / todayIso /
     weekdayOf / formatWeeksLabel 已整体搬到 common.js —— 因为首页「今日课程」卡
     和日程提醒页也要按「星期+周次」判断今天上什么课，而它们不引本文件。
     这里**不要再定义一遍**（顶层 const 重复声明会 SyntaxError）。 */
/** 当前浏览的周偏移：0 = 第 1 周；负数往过去，正数往未来 */
let weekOffset = 0;
/** 时间槽：来自 /api/planner/slots 或本地兜底。 @type {TimeSlot[]} */
let slots = [];
/** 当前账号的全部课程 / 活动（含整学期）。 @type {Course[]} */
let courses = [];

/**
 * 「已保存」基线快照：上一次从账号读取或成功保存时的 courses 序列化结果。
 * 与当前 courses 比对即可判断有没有未保存的改动，不用在每个修改点手工打标志。
 * @type {string}
 */
let savedSnapshot = "";

/** 把当前 courses 记为「已保存」基线。 */
function markClean() { savedSnapshot = JSON.stringify(courses); }

/**
 * 上一次时间表是从哪儿读来的：`"server"` / `"local"`（离线兜底）。
 *
 * 为什么要记这个：`loadTimetable(silent)` 在请求失败时会**静默**退到
 * localStorage，界面看起来完全一样。提醒页的「刷新」如果闭着眼报
 * 「已刷新」，用户就会以为看到的是账号里的最新数据 —— 所以刷新按钮
 * 要按这里说真话（「没连上服务器，显示的是本地缓存」）。
 * @type {"server"|"local"|"unknown"}
 */
let lastLoadSource = "unknown";

/** 是否有未保存的改动。 */
function isDirty() { return JSON.stringify(courses) !== savedSnapshot; }

/* 有未保存改动时离开页面 → 弹浏览器原生确认框。
   覆盖关标签页、刷新、站内链接跳转等所有离开方式。 */
window.addEventListener("beforeunload", e => {
  if (!isDirty()) return;
  e.preventDefault();
  e.returnValue = "";   // 触发浏览器默认的「离开此网站？」确认
});

/* ---------- 日期工具 ----------
   isoOf / todayIso / weekdayOf / semesterStart 都在 common.js（那边也定义了），
   本文件直接用，不再重复声明。 */

/** 本周周一（00:00） */
function mondayOfThisWeek() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));  // 周一为一周之首
  return d;
}

/**
 * 本周落在学期里是第几周（1 起算）。
 *
 * 用来让「打开课表先落在真实的那一周」而不是永远停在第 1 周 ——
 * 学期开始前/结束后会越界，所以夹在 [1, 总周数] 内。
 */
function currentWeekOffset() {
  const diffDays = Math.round(
    (mondayOfThisWeek() - semesterStart()) / 86400000);
  const off = Math.floor(diffDays / 7);
  return Math.max(0, Math.min(SEM_WEEKS_BEFORE + SEM_WEEKS_AFTER, off));
}

/** 当前浏览周的 7 个日期 ["YYYY-MM-DD", ...] */
function weekDates() {
  const mon = semesterStart();
  mon.setDate(mon.getDate() + weekOffset * 7);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(mon);
    d.setDate(mon.getDate() + i);
    return isoOf(d);
  });
}

/** 指定周偏移的起止日期，用于后端区间查询 */
function weekRange(offset) {
  const mon = semesterStart();
  mon.setDate(mon.getDate() + offset * 7);
  const sun = new Date(mon);
  sun.setDate(mon.getDate() + 6);
  return { start: isoOf(mon), end: isoOf(sun) };
}

/* 周次解析（parseWeeks / weekNumberByDate / courseShownInWeek / formatWeeksLabel）
   与 todayIso / weekdayOf 一起在 common.js —— 见那边「学期日历」一节。 */

/* ---------- 时间槽加载 ---------- */
async function loadSlots() {
  try {
    slots = await api("/planner/slots");
    if (!slots || !slots.length) throw new Error("empty");
  } catch {
    slots = TIME_SLOTS.slice();  // 离线兜底（campus.js）
  }
  slots.sort((a, b) => a.order_no - b.order_no);
  slots.forEach(s => {
    // 节次统一显示成「第一节」「第二节」。
    // 老库里可能存的是「一节」，这里补齐「第」字，不用等后端迁移。
    if (s.label) {
      const t = String(s.label).trim();
      if (/节$/.test(t) && !/^第/.test(t)) s.label = "第" + t;
    }
  });
}

/**
 * 按 order_no 查时间槽。
 * @param {number} order
 * @returns {TimeSlot|undefined}
 */
function slotByOrder(order) { return slots.find(s => s.order_no === order); }

/* ---------- 加载 / 保存（按账号关联）---------- */

/** 整学期（周次条覆盖的全部周）的起止日期。
 *
 *  ⚠️ 这里是和「周次条」联动的关键：以前数据只取「本周 ±1 周」，
 *  所以翻不到第 10 周 —— 就算界面给了入口，也拿不到那周的课。
 *  现在把范围放开到周次条同款区间，翻到哪周都有数据。
 *
 *  一次拉整学期而不是「翻到哪周就拉哪周」：一个学期的课表也就几百条，
 *  体积比一张图还小；换来的是翻周**零延迟**，且「沿用上周」能跨任意周工作。 */
function semRange() {
  const a = weekRange(-SEM_WEEKS_BEFORE);
  const b = weekRange(SEM_WEEKS_AFTER);
  return { start: a.start, end: b.end };
}

/** 读取整学期的课程
 *  @param {boolean} [silent] 静默模式：不弹「已读取」提示（给沿用上周等场景用）
 */
async function loadTimetable(silent) {
  const sem = semRange();
  try {
    const data = await api(`/planner/timetable?start=${sem.start}&end=${sem.end}`);
    courses = (data.courses || []).map(c => ({
      ...c,
      _id: c.id ? String(c.id) : (crypto.randomUUID ? crypto.randomUUID() : String(Math.random())),
      type: c.type || "course",
      // 显式补 weeks：后端旧版本 / 离线 localStorage 的老数据可能没这个字段，
      // 补成空串 = 「每周都上」，与「读不到就当每周都上」的兜底一致。
      weeks: c.weeks || "",
      _uncertain: [],        // 从库里读回来的都已确认过，不存在存疑标记
    }));
    lastLoadSource = "server";
    markClean();          // 刚从账号读回来 = 没有未保存改动
    refreshAllViews();
    if (!silent) toast("已从账号读取你的时间表", "ok");
  } catch {
    courses = JSON.parse(localStorage.getItem(LOCAL_KEY) || "[]");
    lastLoadSource = "local";
    markClean();
    refreshAllViews();
    if (!silent) toast("已读取本地时间表（未连接后端）", "ok");
  }
}

/**
 * 数据变化后刷新「当前页面上存在的」那些视图。
 *
 * 老代码是两行裸调用 `renderGrid(); renderReminders();`。拆页后必然出问题：
 * 提醒页没有 renderGrid、课表页的 renderReminders 又在另一个文件里，
 * 裸调用会 ReferenceError —— 而且是在 await 之后的回调里抛，
 * 表现为「数据读到了但页面永远不刷新」，极难排查。
 * 统一走 callIfDefined，谁在调谁，都不在也不报错。
 */
function refreshAllViews() {
  callIfDefined("renderGrid");
  callIfDefined("renderReminders");
  // 页面 2026-10-15 改成对话框后，原来是 renderPlanHint（"AI 能看到几门课"那句提示），
  // 现在同一个位置的提示叫 updatePlanTip。**这里必须跟着改名** ——
  // callIfDefined 找不到函数是静默跳过的，写错了不会报错，只是那句提示永远不刷新。
  callIfDefined("updatePlanTip");
}

/**
 * 把内存里的时间表整份提交给后端。
 *
 * @param {{quiet?: boolean}} [opts] quiet = 成功时不弹「已保存」提示
 *   （删课会自己给一句更具体的提示，两句话叠着弹只会让后一句盖掉前一句）。
 * @returns {Promise<boolean>} 有没有真的落到账号上。false = 只写了本地。
 */
async function saveTimetable(opts) {
  const quiet = !!(opts && opts.quiet);
  // 只提交整学期区间内的数据，避免覆盖范围外的记录
  const sem = semRange();
  const mine = courses.filter(c => c.date >= sem.start && c.date <= sem.end);
  localStorage.setItem(LOCAL_KEY, JSON.stringify(courses));
  try {
    await api("/planner/timetable", {
      // _ 开头的是纯前端标记（_id / _uncertain），后端不认识，统一剥掉
      method: "POST",
      body: { courses: mine.map(stripLocal) },
    });
    markClean();                 // 已落库 = 不再有未保存改动
    if (!quiet) toast("时间表已保存到你的账号", "ok");
    return true;
  } catch {
    toast("已保存到本地（未连接后端）", "error");
    return false;
  }
}

/** 去掉前端专用字段（_id / _uncertain / _probable），只留后端认的字段 */
function stripLocal(c) {
  const out = {};
  Object.keys(c).forEach(k => { if (!k.startsWith("_")) out[k] = c[k]; });
  return out;
}

/* ---------- 时间换算（跨页共用）---------- */
/** "HH:MM" -> 分钟数。给不出合法值就当 0。 */
function toMin(hhmm) {
  const [h, m] = String(hhmm || "00:00").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

/* ---------- 弹窗基础 ----------
   showModal / closeModal 各页都可能用到（详情弹窗、核对弹窗都靠它），所以留在 core。
   但**事件绑定必须走 onElBind**：reminder.html / plan.html 里没有 #modal 和
   #review-mask，老式裸绑会在这里直接抛 TypeError，把后面所有代码（包括启动流程）
   一起中断 —— 白屏。 */
function showModal() { const m = onEl("modal"); if (m) m.classList.add("show"); }
function closeModal() { const m = onEl("modal"); if (m) m.classList.remove("show"); }

onElBind("modal", "click", e => {
  const t = /** @type {HTMLElement} */ (e.target);
  if (t.id === "modal") closeModal();
});
// 核对弹窗点遮罩关闭（识别中的进度弹窗刻意关不掉，避免用户以为卡死了乱点）
onElBind("review-mask", "click", e => {
  const t = /** @type {HTMLElement} */ (e.target);
  if (t.id === "review-mask") callIfDefined("closeReview");
});
// ESC 关闭核对弹窗
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  const rm = onEl("review-mask");
  if (rm && rm.classList.contains("show")) callIfDefined("closeReview");
});

/* ============================================================
   跨页共用启动流程
   ------------------------------------------------------------
   三个页面启动时都要做同一件事：
     注入顶栏 -> 加载时间槽 -> 读课表数据 -> 刷新本页视图
   所以抽成 bootPlanner()，各页只传「自己是谁」+「自己的渲染函数」。

   ⚠️ 刻意**不自动执行**：由各页脚本在文件末尾显式调用。
   若放在 core 里自动跑，任何页面引入 core 都会启动一遍，
   而 core 并不知道当前页有哪些元素 —— 「缺元素就抛错」的老问题会立刻回来。
   ============================================================ */
/**
 * 各页统一入口。
 * @param {"timetable"|"reminder"|"plan"} active 当前页（用于点亮顶栏）
 * @param {string[]} [extraViews] 该页额外要刷新的函数名（如 ["renderGrid"]）
 * @param {boolean} [needSlots] 是否需要时间槽（默认要；课表页/提醒页都要它算时间）
 * @returns {Promise<void>}
 */
async function bootPlanner(active, extraViews, needSlots) {
  // 顶栏：必须最先注入，否则页面会先闪一下再出现导航
  renderTopbar(active);

  // 教学楼候选（只有课表页的表单引用这个 datalist，别的页没有就跳过）
  const bl = onEl("building-list");
  if (bl) {
    bl.innerHTML = BUILDINGS
      .map(b => `<option value="${escapeHtml(b.name)}">${b.region === "south" ? "南区" : "北区"}</option>`)
      .join("");
  }

  if (needSlots !== false) {
    try { await loadSlots(); } catch (e) { console.error("[planner] 时间槽加载失败：", e); }
  }

  // 各页自己的初始化钩子（课表页用它定位周次条）
  // @ts-ignore
  const pageInit = globalThis.__plannerPageInit;
  if (typeof pageInit === "function") {
    try { pageInit(); } catch (e) { console.error("[planner] 页面初始化出错：", e); }
  }

  if (typeof hydrateIcons === "function") hydrateIcons();

  // 读课表；读完 core 内部已 refreshAllViews()，再补上本页特有的渲染
  try { await loadTimetable(true); } catch (e) { console.error("[planner] 课表加载失败：", e); }
  (extraViews || []).forEach(n => callIfDefined(n));
}

