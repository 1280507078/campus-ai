// @ts-check
/* 学业规划 · 日程提醒子页
   ------------------------------------------------------------------
   这一页只做一件事：把「今天 / 明天」的安排列出来，并在临近上课时提醒。

   为什么从课表页拆出来：课表页的信息密度已经很高（表格 + 导入 + 核对），
   而「今天要上什么课」是**每天都会看**的高频需求，混在课表页里要滚动半天。
   拆成独立页之后，用户点一下就能看到，还能随手打开浏览器通知。

   依赖 planner-core.js（日期工具 / courses 状态 / loadTimetable / toast）。
   本文件必须在 core 之后引入。
   ================================================================== */

/**
 * 渲染「今天 / 明天」的课程清单。
 *
 * 注意这里**必须容忍 body 不存在**：本函数会被 core 的 refreshAllViews()
 * 按名字动态调用，如果哪天别的页面也碰巧定义了同名函数、或元素被改名，
 * 直接抛错会让整页停摆。所以先取元素、取不到就安静退出。
 */
function renderReminders() {
  const body = onEl("reminder-body");
  if (!body) return;

  const today = todayIso();
  const nowMin = nowMinutes();

  // 今天 / 明天各自的清单（按开始时间排序，没时间的排最后）
  //
  // ★ 2026-10-18 修：原来这里是 `c.date === iso`，把「库里存的那一条日期」
  //   当成了「这门课只在那天有」。而本项目的课是**每周重复**的（见 common.js
  //   的 courseOnDate）：一条 date=周二、weeks="2-3,6-8周" 的课，第 7 周的周二
  //   照样要上。用 date 相等判断的结果就是「课表页周二有课、这里却说今天没课」，
  //   反过来也会把别的周的记录抬到今天来 —— 用户看到的就是
  //   「今天 6 节、明天 0 节」这种和课表对不上的清单。
  const listOf = iso => coursesOnDate(courses, iso);

  const hasAny = listOf(today).length || listOf(isoOf(addDays(new Date(), 1))).length;

  if (!courses.length) {
    body.innerHTML = emptyHtml(
      "还没有时间表数据",
      "先去「时间表」页导入或手动添加课程，这里就会自动列出每天的上课安排。",
      "planner.html", "去添加时间表");
    return;
  }

  const tmr = addDays(new Date(), 1);
  body.innerHTML =
    dayBlockHtml(today, `今天（${DAYS[weekdayOf(today) - 1]} ${fmtMD(today)}）`, nowMin)
    + dayBlockHtml(isoOf(tmr), `明天（${DAYS[weekdayOf(isoOf(tmr)) - 1]} ${fmtMD(isoOf(tmr))}）`, nowMin)
    + (hasAny ? "" : `<p class="muted" style="margin-top:14px">
         你的时间表里还没有安排 —— 可能是课都在别的周次。去
         <a href="planner.html">时间表页</a> 看看是不是翻错周了。</p>`);
}

/** 一个「今天/明天」区块的 HTML。 */
function dayBlockHtml(iso, title, nowMin) {
  // ★ 口径与上面一致：走 common.js 的 coursesOnDate（星期 + 周次），
  //   不是 `c.date === iso`。别在这里另写一份过滤条件。
  const list = coursesOnDate(courses, iso);

  if (!list.length) {
    return `<div class="rem-day">
      <div class="rem-day-head"><span class="tag">${escapeHtml(title)}</span>
        <span class="muted">共 0 项</span></div>
      <p class="muted" style="margin:10px 0 0">没有安排，好好休息。</p></div>`;
  }

  const items = list.map(c => {
    const isAct = c.type === "activity";
    const start = toMin(c.start), end = toMin(c.end) || start;
    let badge = "";
    let cls = "";
    if (iso === todayIso()) {
      if (nowMin >= start && nowMin <= end) {
        cls = "now";
        badge = `<span class="rem-badge now">${isAct ? "进行中" : "正在上课"}</span>`;
      } else if (start - nowMin > 0 && start - nowMin <= 30) {
        cls = "soon";
        badge = `<span class="rem-badge soon">${isAct ? "即将开始" : `${start - nowMin} 分钟后上课`}</span>`;
      }
    }
    const where = [c.building, c.location].filter(Boolean).join(" ");
    // 有教学楼就给个「导航」入口，没楼名（线上课等）就不给，避免点了跳空
    const nav = c.building
      ? `<a class="rem-nav" href="nav.html?to=${encodeURIComponent(c.building)}"
            title="在地图上导航到${escapeHtml(c.building)}">导航</a>` : "";
    return `<div class="rem-item ${cls}">
      <div class="rem-time"><b>${escapeHtml(c.start || "—")}</b>
        <span>${escapeHtml(c.end || "")}</span></div>
      <div class="rem-main">
        <div class="rem-name">${escapeHtml(c.name || "（未命名）")}
          ${isAct ? '<span class="tag">活动</span>' : ""}${badge}</div>
        ${where ? `<div class="rem-where muted">${escapeHtml(where)}</div>` : ""}
      </div>
      ${nav}
    </div>`;
  }).join("");

  return `<div class="rem-day">
    <div class="rem-day-head"><span class="tag">${escapeHtml(title)}</span>
      <span class="muted">共 ${list.length} 项</span></div>
    <div class="rem-list">${items}</div></div>`;
}

/** 空态卡片（带一个去时间表页的按钮）。 */
function emptyHtml(title, desc, href, btn) {
  return `<div class="empty">
    <div class="e-ico" data-icon="inbox" data-icon-size="32"></div>
    <p><b>${escapeHtml(title)}</b></p>
    <p class="muted" style="margin:6px 0 14px">${escapeHtml(desc)}</p>
    ${href ? `<a class="btn" href="${href}">${escapeHtml(btn)}</a>` : ""}
  </div>`;
}

/** 当前时间的「分钟数」（0~1439）。 */
function nowMinutes() {
  const n = new Date();
  return n.getHours() * 60 + n.getMinutes();
}

/** 两个数字补零成 hh:mm。 */
function hhmmNow() {
  const n = new Date();
  return String(n.getHours()).padStart(2, "0") + ":" + String(n.getMinutes()).padStart(2, "0");
}

/**
 * 顶部「刷新」按钮。
 *
 * ★ 2026-10-18 修：这个按钮原来直接绑 `renderReminders()`，也就是
 *   拿内存里那份 courses **原样重画一遍** —— 画出来的 DOM 和刚才完全一样，
 *   用户点完页面没有任何变化，感受就是「按钮坏了 / 点了没反应」。
 *
 *   真正该刷的是**数据**（可能你在另一台设备上改了课表，或者首屏那次请求
 *   正好失败了）：重新去后端读一次时间表 → 渲染 → 给一句明确的反馈
 *   （带时分的「已刷新 15:01」，让用户看得见这次和上次不一样）。
 *
 *   刷新期间按钮 disabled + 文案变「刷新中…」，既是反馈也防止连点
 *   （连点会并发发好几个请求，回来顺序不定，反而把新数据盖成旧的）。
 */
async function refreshReminder() {
  const btn = onEl("btn-refresh-reminder");
  if (btn) { btn.disabled = true; btn.textContent = "刷新中…"; }
  try {
    // loadTimetable 内部已经 refreshAllViews()（会调 renderReminders），
    // 这里再显式画一次，是为了以后 core 改了刷新范围也不会漏掉本页。
    await loadTimetable(true);
    renderReminders();
    const t = hhmmNow();
    // 说真话：没连上后端时 loadTimetable 会退到本地缓存，别报「已同步」
    toast(lastLoadSource === "local"
      ? `已刷新（${t}）· 没连上服务器，显示的是本地缓存`
      : `已刷新（${t}）`, lastLoadSource === "local" ? "error" : "ok");
  } catch (e) {
    console.error("[reminder] 刷新失败：", e);
    toast("刷新失败，请检查网络后重试", "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "刷新"; }
  }
}

/** Date + n 天（返回新对象，不改原值）。 */
function addDays(d, n) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

/* ---------- 浏览器通知 ---------- */
/**
 * 每分钟检查一次「下一节课是不是 10 分钟内开始」，是就发一条系统通知。
 *
 * 注意：只有**用户手动点了按钮**才会启动这个轮询。
 * 不自动申请权限 —— 一进页面就弹权限框很容易被拒，拒了就再也开不了，
 * 不如让用户自己想开时再点。
 */
let notifyTimer = null;
let notifiedKeys = null;   // Set，记录已提醒过的「课程+时间」，避免重复推送

/**
 * 提醒开关当前是不是「开着」。
 *
 * ⚠️ 为什么需要这个状态，而不是只看 `Notification.permission`：
 *
 * 浏览器权限是**站点级**的，一旦用户点了「允许」，它就是永久的 "granted"。
 * 所以「权限=已允许」≠「我现在想收提醒」—— 用户完全可能允许了权限、
 * 但临时不想被打扰。原来只判断权限，导致：
 *   · 想关掉的时候，点按钮只会又走一遍 startNotifyLoop()，
 *     文案还一直写「通知已开启」，**根本没有关掉的路径**；
 *   · 按钮看着像个开关，实际是个只能开的单向按钮。
 *
 * 现在把「权限」和「开关意愿」拆成两件事：
 *   权限 granted  = 浏览器允许我们发通知（用户点按钮时才申请）
 *   notifyEnabled = 用户现在想不想要提醒（点按钮切换）
 */
let notifyEnabled = false;

/** 记住开关状态，刷新/换页回来还是上次的选择（按账号隔离）。 */
function notifyPrefKey() {
  const u = (typeof getUser === "function" && getUser()) || null;
  return "notify_on_" + ((u && u.id) || "guest");
}

function loadNotifyPref() {
  try { return localStorage.getItem(notifyPrefKey()) === "1"; } catch { return false; }
}

function saveNotifyPref(on) {
  try { localStorage.setItem(notifyPrefKey(), on ? "1" : "0"); } catch { /* 忽略 */ }
}

/** 把按钮文案/样式同步成当前开关状态。 */
function syncNotifyButton() {
  const btn = onEl("btn-notify");
  if (!btn) return;
  const on = notifyEnabled;
  btn.textContent = on ? "通知已开启（点击关闭）" : "开启通知";
  btn.classList.toggle("btn-on", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.title = on ? "点击关闭上课提醒" : "点击开启上课提醒";
}

function toggleNotify() {
  if (!("Notification" in window)) return toast("当前浏览器不支持通知功能", "error");

  // ---- 已经开着 -> 这一次点击是「关掉」 ----
  if (notifyEnabled) {
    stopNotifyLoop();
    notifyEnabled = false;
    saveNotifyPref(false);
    syncNotifyButton();
    return toast("已关闭上课提醒", "ok");
  }

  // ---- 当前是关的 -> 尝试开启 ----
  if (Notification.permission === "granted") {
    enableNotify();
    return;
  }
  if (Notification.permission === "denied") {
    // 被拒之后浏览器**不会再弹框**，只能让用户去设置里改，这里必须说清楚
    return toast("通知权限已被拒绝，请在浏览器地址栏左侧的站点设置里手动允许", "error");
  }
  Notification.requestPermission().then(p => {
    if (p === "granted") {
      enableNotify();
    } else {
      toast("你拒绝了通知权限，可在浏览器设置中开启", "error");
    }
  });
}

/** 统一「开启成功」后的收尾（起轮询 + 记状态 + 刷按钮）。 */
function enableNotify() {
  startNotifyLoop();
  notifyEnabled = true;
  saveNotifyPref(true);
  syncNotifyButton();
  toast("已开启上课提醒", "ok");
}

function startNotifyLoop() {
  if (notifyTimer) return;
  checkUpcoming();
  notifyTimer = setInterval(checkUpcoming, 60 * 1000); // 每分钟检查
}

/** 停掉轮询。⚠️ 必须 clearInterval 并把句柄置空，
 *  否则 notifyTimer 留着旧句柄，下次 startNotifyLoop 会因为
 *  `if (notifyTimer) return` 直接返回，**再也开不起来**。 */
function stopNotifyLoop() {
  if (notifyTimer) {
    clearInterval(notifyTimer);
    notifyTimer = null;
  }
}

function checkUpcoming() {
  // 开关关着就什么都不做（哪怕权限是 granted）
  if (!notifyEnabled) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!notifiedKeys) notifiedKeys = new Set();

  const nowMin = nowMinutes();
  const today = todayIso();
  courses.filter(c => c.date === today).forEach(c => {
    const diff = toMin(c.start) - nowMin;
    const key = (c._id || c.name) + "@" + c.date + "@" + c.start;
    if (diff > 0 && diff <= 10 && !notifiedKeys.has(key)) {
      notifiedKeys.add(key);
      const isAct = c.type === "activity";
      new Notification(`${isAct ? "即将开始" : "即将上课"}：${c.name}`, {
        body: `${c.start} 在 ${c.location || c.building || "教室"}`,
      });
    }
  });
}

/** 切回本页时立刻刷新一次（避免停留在页面上半天看到的还是旧数据）。 */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) callIfDefined("renderReminders");
});

/* ============================================================
   提醒页首屏骨架
   ------------------------------------------------------------
   和课表页同源的问题：`bootPlanner` 里 await loadTimetable() 之前，
   #reminder-body 是空的 —— 用户看到一张空白卡，不知道是在加载还是真没课。

   形状照真实 .rem-item 铺（左time块 + 两行文字），4 行足够：
   提醒清单本身不会很长，铺太多反而像"很多条待办"。
   数据一到 renderReminders() 会重建整块内容，占位自然消失。
   ============================================================ */
function remSkeleton() {
  const body = onEl("reminder-body");
  if (!body) return;
  const row = `
    <div class="rem-item" aria-hidden="true">
      <div class="rem-time">
        <span class="skel-line" style="width:34px;height:12px"></span>
        <span class="skel-line" style="width:26px;height:10px;margin-top:6px"></span>
      </div>
      <div style="flex:1 1 auto;min-width:0">
        <span class="skel-line w60" style="display:block;height:13px"></span>
        <span class="skel-line w40" style="display:block;height:11px"></span>
      </div>
    </div>`;
  body.innerHTML = row.repeat(4);
}

/* ---------- 启动 ---------- */
(async function initReminderPage() {
  remSkeleton();                       // 先占位，别让卡片空着
  await bootPlanner("reminder", ["renderReminders"]);

  // 恢复上次的开关选择。注意两个条件缺一不可：
  //   · 用户**之前开过**（localStorage 记着）
  //   · 浏览器权限**仍然允许**（用户可能事后在站点设置里收回了）
  // 只有都满足才自动进轮询，否则按钮老老实实显示「开启通知」。
  const wantOn = loadNotifyPref();
  if (wantOn && "Notification" in window && Notification.permission === "granted") {
    startNotifyLoop();
    notifyEnabled = true;
  } else {
    notifyEnabled = false;
  }
  syncNotifyButton();
})();
