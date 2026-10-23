// @ts-check
/* 管理后台：数据大屏（ECharts）+ 用户反馈回复。
 *
 * ## 为什么只有「数据」和「反馈」
 *   需求：管理页只管这两件事，
 *   「主页 / 学习规划 / 情绪陪伴 / 地图导航」那些是**用户界面**的入口，
 *   不挂在管理页上（所以本文件不再调 renderTopbar）。
 *
 * ## 四条口径
 *   1. **权限只在后端**。这里先问一次 /auth/me 的 is_admin，只是为了别把
 *      后台界面摆给普通用户看；真正的门是 /api/admin/* 上的 get_admin_user，
 *      改 localStorage 让界面出现，请求照样 403。
 *   2. **统计数字不在前端算**。后端一次给齐，前端只负责显示 ——
 *      前端自己数会被分页截断（200 个用户只拉到 50 条，统计就永远 50）。
 *   3. **统计不含管理员**（后端就排掉了）。这条是用户明确要的：
 *      「数据不要识别管理员，识别的是用户的数据」——看板上永远是**用户**的数字，
 *      管理员自己进来看看板，不会让"注册用户 +1"。
 *   4. **回复是单向的**：管理员回一条，用户在自己的反馈页看到。
 */
/* 这是「数据」那一页（反馈在 admin-feedback.html，两个页面分开） */
renderTopbar("admin");
hydrateIcons();
if (!getToken()) location.href = "login.html";

/** 最近一次统计（切主题时要用它重画，不能重新请求） */
let lastStats = /** @type {any} */ (null);

const el = (id) => document.getElementById(id);

/** ISO（UTC）-> 本地「10 月 19 日 19:30」 */
function adTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 时分秒，用于「最后刷新 17:42:03」这种实时感更强的时间戳 */
function hms(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/* ============ 1. 权限闸门 ============ */
async function gate() {
  try {
    const me = await api("/auth/me");
    setUser(me);                        // 顺便把 is_admin 写回本地，供「我的」页用
    if (!me || !me.is_admin) {
      el("ad-denied-msg").textContent = `当前登录的是「${(me && (me.nickname || me.username)) || "未知账号"}」，不是管理员账号。`;
      el("ad-denied").hidden = false;
      return false;
    }
    el("ad-body").hidden = false;
    return true;
  } catch (e) {
    el("ad-denied-msg").textContent = "取不到登录状态：" +
      (/** @type {Error} */ (e).message || "请重新登录后再试");
    el("ad-denied").hidden = false;
    return false;
  }
}

/* ============ 2. 主题令牌 -> 图表配色 ============
 * Canvas 画不出 CSS 变量，所以每次画之前把令牌**读成具体颜色**再喂给 ECharts。
 * 这样切主题（含跟随系统切深色）时图表跟着变，不用维护第二套配色表。
 */
function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n, fb) => (cs.getPropertyValue(n) || "").trim() || fb;
  return {
    brand: v("--brand", "#6366f1"),
    brand2: v("--brand-2", "#8b5cf6"),
    planner: v("--planner", "#3b82f6"),
    emotion: v("--emotion", "#ec4899"),
    map: v("--map", "#10b981"),
    warn: v("--warn", "#f59e0b"),
    danger: v("--danger", "#ef4444"),
    ink: v("--ink", "#1a1f2e"),
    soft: v("--ink-soft", "#5b6478"),
    faint: v("--ink-faint", "#646e85"),
    line: v("--line", "#e7eaf3"),
    card: v("--card", "#ffffff"),
  };
}

/* ============ 3. KPI 数字 ============ */
function kpiList(s) {
  const n = (v) => String(v == null ? 0 : v);
  return [
    { label: "注册用户", value: n(s.users.total), sub: `近 7 天 +${n(s.users.new7d)}` },
    // 活跃口径：近 7 天产生过动作（测评 / 情绪对话 / 反馈）的去重用户
    { label: "7 天活跃", value: n(s.users.active7d), sub: "测评 / 情绪对话 / 反馈" },
    { label: "有课表的人", value: n(s.users.withCourses), sub: `课程 ${n(s.courses.total)} 条` },
    { label: "情绪测评", value: n(s.emotion.assessments), sub: `覆盖 ${n(s.emotion.users)} 人` },
    { label: "情绪对话", value: n(s.emotion.chats), sub: "已保存的会话" },
    { label: "反馈总数", value: n(s.feedback.total), sub: `已回复 ${n(s.feedback.replied)} 条` },
    { label: "待回复反馈", value: n(s.feedback.pending), sub: `共 ${n(s.feedback.total)} 条`, hot: Number(s.feedback.pending) > 0 },
  ];
}

function renderKpis(s) {
  el("ad-kpis").innerHTML = kpiList(s).map((c) => `
    <div class="adm-kpi${c.hot ? " is-hot" : ""}">
      <div class="k-label">${escapeHtml(c.label)}</div>
      <div class="k-num">${escapeHtml(c.value)}</div>
      <div class="k-sub">${escapeHtml(c.sub)}</div>
    </div>`).join("");

  // 情绪均值只陈述数字，不做判断（模块红线：不写「好转 / 恶化」）
  const e = s.emotion;
  const emoTxt = e.assessed30d
    ? `近 30 天测评 ${e.assessed30d} 次，抑郁维度均值 ${e.avgDepression30d} 分、焦虑维度均值 ${e.avgAnxiety30d} 分（满分 12）。`
    : "近 30 天没有测评记录。";
  el("ad-generated").textContent =
    `统计时间 ${adTime(s.generatedAt)} · 口径不含管理员账号 · ${emoTxt}`;
}

/* ============ 4. 图表 ============ */
const charts = {};
/** @param {string} id */
function chartOf(id) {
  if (!charts[id]) {
    const dom = el(id);
    if (!dom) return null;
    charts[id] = echarts.init(dom, null, { renderer: "canvas" });
  }
  return charts[id];
}

/** 空数据时的统一提示（不要画一张空白坐标系，看着像坏了） */
const EMPTY = (t) => ({
  graphic: {
    type: "text", left: "center", top: "middle",
    style: { text: t, fill: tokens().faint, fontSize: 12.5 },
  },
});

function axisStyle(t) {
  return {
    axisLine: { lineStyle: { color: t.line } },
    axisTick: { show: false },
    axisLabel: { color: t.faint, fontSize: 11 },
    splitLine: { lineStyle: { color: t.line, type: "dashed" } },
  };
}

/* ---- 4.1 近 7 天走势 ---- */
function drawDaily(daily) {
  const t = tokens();
  const ch = chartOf("ch-daily");
  if (!ch) return;
  const rows = Array.isArray(daily) ? daily : [];
  const flat = rows.flatMap((r) => [r.users, r.assessments, r.chats, r.feedback].map(Number));
  if (!rows.length || Math.max(0, ...flat) === 0) {
    ch.setOption(EMPTY("近 7 天还没有任何记录"), true);
    return;
  }
  // ⚠️ 本树没有「伴学对话」这条线：伴学对话存在浏览器 localStorage，
  //    库里查不到，画上去只能是假的。用「情绪对话」补位 —— 它是真实存在的表。
  const series = [
    { key: "users", label: "新用户", color: t.brand },
    { key: "assessments", label: "测评", color: t.emotion },
    { key: "chats", label: "情绪对话", color: t.planner },
    { key: "feedback", label: "反馈", color: t.danger },
  ];
  ch.setOption({
    color: series.map((s) => s.color),
    grid: { left: 8, right: 12, top: 30, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: t.card, borderColor: t.line,
      textStyle: { color: t.ink, fontSize: 12 },
    },
    legend: {
      top: 0, right: 0, icon: "roundRect", itemWidth: 9, itemHeight: 9,
      textStyle: { color: t.soft, fontSize: 11.5 },
    },
    xAxis: { type: "category", boundaryGap: false, data: rows.map((r) => r.label), ...axisStyle(t) },
    // ★ 计数是整数，纵轴必须留余量：数据最大只有 1 时，max 取 2 才看得出高低差。
    //   不留余量的话所有点都贴在顶边，看着像"掉下来一条线"。
    yAxis: {
      type: "value", minInterval: 1,
      max: (v) => Math.max(1, Math.ceil((v.max || 0) * 1.25)),
      ...axisStyle(t),
    },
    series: series.map((s) => ({
      // ★ 不用 smooth：0/1 这种两三个点的计数，平滑插值会拱出"浪"，
      //   看起来像数据在剧烈波动，其实只是 0 和 1 两个值。
      name: s.label, type: "line", smooth: false, symbolSize: 5,
      lineStyle: { width: 2 },
      areaStyle: s.key === "users" ? { opacity: 0.10 } : undefined,
      data: rows.map((r) => Number(r[s.key]) || 0),
    })),
  }, true);
}

/* ---- 4.2 反馈类型分布 ---- */
function drawFbKind(fb) {
  const t = tokens();
  const ch = chartOf("ch-fbkind");
  if (!ch) return;
  const kinds = (fb.byKind || []).filter((k) => Number(k.count) > 0);
  if (!kinds.length) {
    ch.setOption(EMPTY("还没有用户反馈"), true);
    return;
  }
  ch.setOption({
    color: [t.brand, t.warn, t.planner, t.faint],
    tooltip: {
      trigger: "item", backgroundColor: t.card, borderColor: t.line,
      textStyle: { color: t.ink, fontSize: 12 },
    },
    legend: {
      bottom: 0, icon: "circle", itemWidth: 8, itemHeight: 8,
      textStyle: { color: t.soft, fontSize: 11.5 },
    },
    series: [{
      type: "pie", radius: ["46%", "68%"], center: ["50%", "44%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: t.card, borderWidth: 2 },
      label: { color: t.soft, fontSize: 11.5, formatter: "{b} {c}" },
      labelLine: { length: 8, length2: 8, lineStyle: { color: t.line } },
      data: kinds.map((k) => ({ name: k.label, value: Number(k.count) })),
    }],
  }, true);
}

/* ---- 4.3 用户构成（有课表 / 没课表） ---- */
function drawUsers(u) {
  const t = tokens();
  const ch = chartOf("ch-users");
  if (!ch) return;
  const total = Number(u.total) || 0;
  const withC = Number(u.withCourses) || 0;
  if (!total) {
    ch.setOption(EMPTY("还没有注册用户"), true);
    return;
  }
  ch.setOption({
    color: [t.brand, t.faint],
    tooltip: {
      trigger: "item", backgroundColor: t.card, borderColor: t.line,
      textStyle: { color: t.ink, fontSize: 12 },
    },
    series: [{
      type: "pie", radius: ["52%", "72%"], center: ["50%", "48%"],
      itemStyle: { borderColor: t.card, borderWidth: 2 },
      label: {
        show: true, position: "center", formatter: `${total}\n注册用户`,
        color: t.ink, fontSize: 13, lineHeight: 18, fontWeight: 700,
      },
      emphasis: { label: { show: true } },
      data: [
        { name: "有课表", value: withC },
        { name: "还没课表", value: Math.max(0, total - withC) },
      ],
    }],
    legend: {
      bottom: 0, icon: "circle", itemWidth: 8, itemHeight: 8,
      textStyle: { color: t.soft, fontSize: 11.5 },
    },
  }, true);
}

/* ---- 4.4 反馈回复率 ----
 * 仪表盘比柱状更贴「一个比例」这件事：一眼看出还有多少没回。
 * ⚠️ 本树没有「待办」表，所以这里量的是**反馈**的回复率，
 *    不是开发树那张待办完成率 —— 看板上每个数都要能指到库里的出处。
 */
function drawFbReply(fb) {
  const t = tokens();
  const ch = chartOf("ch-fbreply");
  if (!ch) return;
  const total = Number(fb.total) || 0;
  const done = Number(fb.replied) || 0;
  if (!total) {
    ch.setOption(EMPTY("还没有用户反馈"), true);
    return;
  }
  const rate = Math.round((done / total) * 100);
  ch.setOption({
    series: [{
      type: "gauge", startAngle: 210, endAngle: -30, radius: "92%", center: ["50%", "58%"],
      min: 0, max: 100, splitNumber: 4,
      progress: { show: true, width: 12, itemStyle: { color: t.map } },
      axisLine: { lineStyle: { width: 12, color: [[1, t.line]] } },
      axisTick: { show: false },
      splitLine: { length: 8, lineStyle: { color: t.line } },
      axisLabel: { color: t.faint, fontSize: 10, distance: 12 },
      pointer: { width: 3, itemStyle: { color: t.soft } },
      detail: {
        valueAnimation: true, formatter: "{value}%", offsetCenter: [0, "-6%"],
        color: t.ink, fontSize: 22, fontWeight: 700,
      },
      title: { show: false },
      data: [{ value: rate }],
    }],
  }, true);
}

/* ---- 4.5 情绪两个维度（近 30 天均值） ---- */
function drawEmotion(e) {
  const t = tokens();
  const ch = chartOf("ch-emotion");
  if (!ch) return;
  if (!Number(e.assessed30d)) {
    ch.setOption(EMPTY("近 30 天没有测评"), true);
    return;
  }
  ch.setOption({
    grid: { left: 8, right: 34, top: 10, bottom: 6, containLabel: true },
    tooltip: {
      trigger: "item", backgroundColor: t.card, borderColor: t.line,
      textStyle: { color: t.ink, fontSize: 12 },
    },
    xAxis: { type: "value", max: 12, ...axisStyle(t) },
    yAxis: {
      type: "category", data: ["抑郁", "焦虑"],
      ...axisStyle(t), splitLine: { show: false },
    },
    series: [{
      type: "bar", barWidth: 18,
      itemStyle: { borderRadius: [0, 9, 9, 0] },
      label: { show: true, position: "right", color: t.soft, fontSize: 11.5 },
      data: [
        { value: Number(e.avgDepression30d) || 0, itemStyle: { color: t.brand } },
        { value: Number(e.avgAnxiety30d) || 0, itemStyle: { color: t.emotion } },
      ],
    }],
  }, true);
}

function drawAll(s) {
  drawDaily(s.daily);
  drawFbKind(s.feedback);
  drawUsers(s.users);
  drawFbReply(s.feedback);
  drawEmotion(s.emotion);
}

/* ============ 6. 数据加载 + 实时 ============ */
const REFRESH_MS = 10000;
let timer = null;

function setLive(state, text) {
  const box = el("adm-live");
  if (box) box.setAttribute("data-state", state);
  const t = el("adm-live-text");
  if (t) t.textContent = text;
}

async function loadStats() {
  try {
    const s = await api("/admin/stats");
    lastStats = s;
    renderKpis(s);
    drawAll(s);
    setLive("ok", `实时 · 最后一次 ${hms(new Date())}`);
  } catch (e) {
    setLive("err", "取数失败，一会儿自动重试");
    if (!lastStats) {
      el("ad-kpis").innerHTML = `<p class="muted" style="margin:0;">统计取不到：${
        escapeHtml(/** @type {Error} */ (e).message || "请稍后重试")}</p>`;
    }
  }
}

function startRealtime() {
  stopRealtime();
  timer = setInterval(() => {
    // 页面在后台时不刷 —— 省流量，也避免回来时一堆过期请求挤在一起
    if (document.visibilityState === "visible") loadStats();
  }, REFRESH_MS);
}
function stopRealtime() {
  if (timer) { clearInterval(timer); timer = null; }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  loadStats();                       // 切回来先补一次，别等下一个 10 秒
});

el("ad-refresh").addEventListener("click", async () => {
  toast("正在刷新…", "info");
  await loadStats();
});

/* 切主题要重画：Canvas 里的颜色是"快照"，不会自己跟着 CSS 变量变。
 *
 * 三条触发都接上，缺一条就会留下"页面变深色、图表还是浅色"的斑：
 *   ① `<html data-theme>` 变化 —— 手动切换走这条（applyTheme 的唯一出口）；
 *   ② 系统深浅色翻转 —— 「跟随系统」时由 common.js 重新 applyTheme，
 *      但如果它把属性**删掉**（切回浅色），属性没有"变化"事件，所以这里单独听；
 *   ③ storage —— 另一个标签页切了主题（同一份 localStorage）。
 */
const redrawCharts = () => { if (lastStats) drawAll(lastStats); };
new MutationObserver(redrawCharts)
  .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
try {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawCharts);
} catch { /* 老浏览器没有 addEventListener 版本的 matchMedia，忽略 */ }
window.addEventListener("storage", (e) => {
  if (!e.key || e.key === "theme") redrawCharts();
});

let rzTimer = null;
window.addEventListener("resize", () => {
  if (rzTimer) clearTimeout(rzTimer);
  rzTimer = setTimeout(() => {
    Object.values(charts).forEach((c) => /** @type {any} */ (c).resize());
  }, 160);
});

/* ============ 启动 ============ */
(async function start() {
  if (!(await gate())) return;
  await loadStats();
  startRealtime();
})();

