// @ts-check
/* 公共工具：登录态、接口封装、Toast、HTML 转义、高德加载。 */

/** @returns {string|null} 本地保存的 JWT token */
function getToken() { return localStorage.getItem("token"); }

/** @param {string} t @returns {void} */
function setToken(t) { localStorage.setItem("token", t); }

/**
 * 清掉登录态。
 *
 * ⚠️ 这里必须把「跟随账号的本地数据」一并清掉，否则会串号：
 * 用户在同一个浏览器里注册了新账号（比如每次都注册 admin），
 * 但 localStorage 里的情绪对话 / 课表还是上一个账号留下的 ——
 * 表现就是输入里说的「我每次用新压缩包注册 admin 进来还是这个对话」。
 *
 * 后端数据是按 user_id 隔离的（新账号查到的是空库），但前端这三份数据
 * 存在浏览器里、原本用的是全局键名，跟账号没关系，所以必须显式清理。
 */
function clearToken() {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
  clearUserScopedData();
}

/**
 * 清除所有「跟随账号」的本地缓存。
 * 退出登录、以及检测到换账号时都会调用。
 */
function clearUserScopedData() {
  // 已知的、跟随账号的本地数据（键名与各页面保持一致）
  ["emotion_history",          // 情绪陪伴的对话记录
   "timetable_local",          // 课表的本地副本
   "emotion_assess_result",    // 情绪测评结果
  ].forEach(k => {
    try { localStorage.removeItem(k); } catch { /* 忽略 */ }
  });
  // 兜底：把同前缀的其它键也一并清掉，避免以后新增的键忘了加到这里
  try {
    Object.keys(localStorage)
      .filter(k => k.startsWith("emotion_") || k.startsWith("quiz_") ||
                   k.startsWith("timetable_") || k.startsWith("campus_u"))
      .forEach(k => localStorage.removeItem(k));
  } catch { /* 忽略 */ }
}

/** @returns {User|null} 当前登录用户 */
function getUser() {
  try { return JSON.parse(localStorage.getItem("user") || "null"); } catch { return null; }
}

/**
 * 记录当前账号，并在「切换了账号」时清掉上一个账号的本地数据。
 *
 * 为什么需要这一步：登录成功后后端换发新 token，但如果用户换了个账号
 * （退出再注册），localStorage 里的对话/课表还是旧的，页面上会看到
 * 上一个账号的内容。这里用 uid 做一次比对，发现换了人就把残留清空。
 * @param {User} u
 * @returns {boolean} 是否发生了账号切换
 */
function setUser(u) {
  let prevUid = null;
  try { prevUid = (JSON.parse(localStorage.getItem("user") || "null") || {}).id || null; } catch { /* 忽略 */ }
  const switched = prevUid !== null && u && u.id != null && String(prevUid) !== String(u.id);
  if (switched) clearUserScopedData();
  localStorage.setItem("user", JSON.stringify(u));
  return switched;
}

/**
 * 转义 HTML 特殊字符，防止 XSS。
 * @param {*} s
 * @returns {string}
 */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** @type {ReturnType<typeof setTimeout>|null} */
let toastTimer = null;

/**
 * 底部提示条。
 * @param {string} msg            提示文案
 * @param {"info"|"ok"|"error"} [kind] 类型，决定图标与配色
 * @returns {void}
 */
function toast(msg, kind = "info") {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  const ICON = { ok: "check", error: "alert", info: "chat" };
  el.className = "toast toast-" + kind;
  el.innerHTML =
    `<span class="toast-ico">${icon(/** @type {any} */ (ICON[kind] || ICON.info), 17)}</span>` +
    `<span>${escapeHtml(msg)}</span>`;
  // 触发重排以便重复调用时动画能重放
  void el.offsetWidth;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), kind === "error" ? 3200 : 2200);
}

/**
 * 探测可用的后端地址。
 *
 * 为什么要探测：本项目要求「换台电脑双击就能用」，但后端端口在冲突时
 * 会被启动脚本换掉（8000 -> 8001 ...）。与其让人手改配置，不如让前端
 * 自己试几个候选地址，第一个能用就用它。
 *
 * 候选顺序：
 *   1. 同源 /api —— 本项目默认就是后端托管前端（/app/xxx.html），同源必命中
 *   2. 本机常见端口 8000 / 8001 / 8002 ...（前后端分开跑的老方式）
 *   3. config.js 推导出来的地址（跟随当前 hostname，手机访问必需）
 *
 * 探测结果缓存在 _apiBaseResolved，只做一次。
 */
let _apiBaseResolved = null;
let _apiBaseProbing = null;
/** 探测到的后端自报信息（build / features），供页面显示「后端是不是新版」 */
let _apiInfo = null;

function getApiInfo() { return _apiInfo; }

async function _probeBase(candidate) {
  // 注意：不能用根路径「/」来判断，因为前端静态服务器对 / 也返回 200，
  // 会把前端自己误认成后端。必须打一个只有后端才有的接口。
  // 这里用 /api/ping：公开端点，必定 200，且静态服务器上没有这个路径。
  // （早期用 /api/auth/me，未登录返回 401，控制台会留下红色报错，演示时容易被误认为出错。）
  const url = candidate.replace(/\/+$/, "") + "/ping";
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 1200);
    // ⚠️ 必须带 cache: "no-store" 和时间戳破缓存。
    //
    // 为什么：这个请求的唯一职责就是**问后端「你是哪一版」**。
    // 如果它被浏览器 HTTP 缓存命中，拿到的就是**上次那个后端**的回答 ——
    // 于是「换了新包、页面却仍显示旧构建号」，而且不管重开多少次都一样，
    // 因为缓存没失效。真实事故：用户换了包，页脚一直显示更早的构建号，
    // 一度被误判成「没换包成功」，其实是这个探测请求被缓存了。
    // 判断版本的东西绝不能走缓存 —— 宁可不缓存，也不要一个可能是错的答案。
    const res = await fetch(url + "?t=" + Date.now(), {
      signal: ctl.signal,
      cache: "no-store",
    });
    clearTimeout(timer);
    if (!res.ok) return false;
    // 再校验一下响应体，避免别的服务恰好也返回 200
    const data = await res.json().catch(() => null);
    if (!(data && data.service === "campus-ai")) return false;
    // 记下后端自报的版本信息 —— 这是「旧版/新版」唯一可靠的判别依据。
    // 老后端没有这些键，_apiInfo 里就是 undefined。
    _apiInfo = { build: data.build || "", features: data.features || [] };
    return true;
  } catch {
    return false;
  }
}

async function resolveApiBase() {
  if (_apiBaseResolved) return _apiBaseResolved;
  if (_apiBaseProbing) return _apiBaseProbing;

  _apiBaseProbing = (async () => {
    const host = (typeof window !== "undefined" && window.location && window.location.hostname) || "127.0.0.1";

    // 候选顺序很关键：
    //   0) 【同源 /api】最优先 —— 这是本项目**默认部署方式**（前端由后端托管在 /app 下）。
    //      页面 http://IP:8000/app/nav.html 的后端就是 http://IP:8000/api，
    //      同源同端口，不该再去猜端口。放在最前面 = 一次命中、零探测延迟。
    //      注意点：原来没有这一项，上来就挨个试 8000~8005，每个最多等 1200ms；
    //      首页的天气是「页面加载即请求」，一旦探测排在后面就会迟迟拿不到数据，
    //      用户看到的是「暂时拿不到天气」——其实后端好端端地开着。
    //   1) 再试「跟当前页面同主机」的默认端口 —— 兼容前后端分开跑（前端 5500）的老方式，
    //      手机访问时会优先命中电脑的局域网地址（不能用 localhost，那是手机自己）。
    //   2) 最后试 config.js 推导出的地址（启动脚本可能已注入实际端口）。
    const candidates = [];

    // 0) 同源（仅 http/https 页面有 origin；file:// 打开时 origin 为 "null"，跳过）
    try {
      const origin = (typeof window !== "undefined" && window.location && window.location.origin) || "";
      if (origin && /^https?:/i.test(origin)) candidates.push(origin + "/api");
    } catch { /* 忽略 */ }

    // 端口候选用**稀疏采样**，不是从 8000 挨个数到 8020。
    //
    // 为什么：启动脚本在 8000 被占用时会一直 +1 试到 8020（见 start-windows.bat
    // 的 :portcheck）。所以真实端口完全可能落在 8006~8020 区间 —— 早期这里只列到
    // 8005，一旦真落到 8009，所有候选全部落空，页面就永远连不上后端。
    // 但也不能真的列 21 个：_probeBase 每个最多等 1200ms，
    // 21 个全失败最坏要 25 秒，用户会以为页面卡死了。
    // 折中：前 6 个密集（最常见的端口都在这里，通常第一个就命中），
    // 后面按 10,20 稀疏兜底，最坏 8 个 ≈ 10 秒，且同源那次命中时只花 0 秒。
    for (const p of [8000, 8001, 8002, 8003, 8004, 8005, 8010, 8020]) {
      candidates.push(`http://${host}:${p}/api`);
    }
    candidates.push(CONFIG.API_BASE);

    const seen = new Set();
    for (const c of candidates) {
      const norm = String(c).replace(/\/+$/, "");
      if (seen.has(norm)) continue;
      seen.add(norm);
      if (await _probeBase(norm)) {
        _apiBaseResolved = norm;
        CONFIG.API_BASE = norm;
        return norm;
      }
    }

    // 全都失败：保留原值，让后续请求报出明确错误
    _apiBaseResolved = CONFIG.API_BASE;
    return _apiBaseResolved;
  })();

  return _apiBaseProbing;
}

/**
 * 统一接口调用：自动带 token，返回 JSON；网络失败抛出便于页面兜底。
 * 后端返回的 detail 已在服务端转为中文，这里只做兜底保证不会漏出英文。
 * @param {string} path                相对 CONFIG.API_BASE 的路径
 * @param {{method?: string, headers?: Record<string,string>, body?: any}} [opts]
 * @returns {Promise<any>}
 */
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const token = getToken();
  if (token) {
    // ★ 两个头都发，别只发 Authorization（2026-10-14 build q 修的真 bug）。
    //
    // 公网部署后，托管平台的网关会**改写 / 注入 Authorization 头**：后端拿到的
    // 永远不是我们发的那个值，于是所有需要登录的接口一律 401，前端只能弹
    // 「登录状态已过期」并跳回登录页 —— 重新登录也还是一样，永远进不去。
    // 加一个网关不会碰的自定义头就绕开了（后端优先读它，见 routers/auth.py）。
    //
    // Authorization 仍然保留：本机 / 局域网直连后端时它一直是好用的，
    // 而且它是标准形态，别的客户端（curl / 小程序 / 第三方集成）也认。
    headers["X-Campus-Token"] = token;
    headers["Authorization"] = "Bearer " + token;
  }
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const base = await resolveApiBase();

  let res;
  try {
    res = await fetch(base + path, {
      method: opts.method || "GET",
      headers,
      body: opts.body ? (typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body)) : undefined,
    });
  } catch {
    // fetch 抛错 = 网络层就没通，几乎都是后端没启动
    throw new Error("无法连接服务器，请确认后端已启动（看后台那个黑色窗口）");
  }

  const data = await res.json().catch(() => ({}));

  // 只有「带着 token 却被拒绝」才是登录过期。
  // 登录接口本身也会返回 401（密码错误），那种情况必须把后端的中文原因透出来，
  // 否则用户会看到「登录已过期」这种莫名其妙的提示。
  if (res.status === 401 && token) {
    clearToken();
    sessionStorage.setItem("login_msg", "登录状态已过期，请重新登录");
    location.href = "login.html";
    throw new Error("登录状态已过期，请重新登录");
  }

  if (!res.ok) {
    throw new Error(toChineseError(data, res.status));
  }
  return data;
}

/**
 * 把任意错误响应整理成中文提示。
 * 覆盖三种情况：字符串 detail、FastAPI 校验错误的数组 detail、以及完全没 detail。
 * @param {any} data
 * @param {number} status
 * @returns {string}
 */
function toChineseError(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail.trim()) return detail;

  // FastAPI 默认的 422 是数组结构，兜底翻译一遍（正常情况下后端已转好）
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] || {};
    const loc = (first.loc || []).filter((p) => !["body", "query", "path"].includes(p));
    const field = loc.length ? loc[loc.length - 1] : "内容";
    const NAMES = { username: "用户名", password: "密码", nickname: "昵称", name: "课程名称" };
    return `${NAMES[field] || field}填写有误，请检查后重试`;
  }

  const BY_STATUS = {
    400: "请求有误，请检查填写的内容",
    401: "登录状态已失效，请重新登录",
    403: "没有权限执行该操作",
    404: "请求的资源不存在",
    422: "提交的内容不符合要求，请检查后重试",
    429: "操作过于频繁，请稍后再试",
    500: "服务器开小差了，请稍后重试",
    502: "服务暂时不可用，请稍后重试",
    // ★ 这两个以前落到「操作失败，请稍后重试」，用户完全看不出是**慢**而不是**坏**
    //   （2026-10-18：线上排 7 天计划超过网关时限，页面只说「生成失败」）。
    //   后端已经把单次规划压在 50 秒内（study_agent.BUDGET_S），这里补一句人话。
    503: "服务器正忙，等几秒再点一次",
    504: "这次算得太久，服务器先断开了 —— 再点一次通常会快很多",
  };
  return BY_STATUS[status] || "操作失败，请稍后重试";
}

/**
 * 需要登录的页面调用：未登录跳登录页。
 * @returns {boolean}
 */
function requireAuth() {
  if (!getToken()) { location.href = "login.html"; return false; }
  return true;
}

/**
 * 每个页面在加载最早期就调用：只要没登录，立刻跳登录页。
 * 为什么不用 requireAuth()：requireAuth 由各页面脚本自己调用，一旦某个页面
 * 忘了写就漏出去了（也挡不住用户直接在地址栏敲 URL）。这里由 common.js
 * 在脚本加载时就执行一次，属于「堵在门口」，任何页面都躲不过。
 * 登录页自己会调用 allowGuest() 放行，否则会无限跳转。
 */
(function guardGate() {
  const page = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  if (page === "login.html") return;                 // 登录页放行
  // 扫码确认页也放行：这一页**必须**能在未登录时打开 ——
  // 它自己会判断登录态并引导去登录。如果在这里就把它重定向到登录页，
  // 页面里那句「请先登录」的提示永远显示不出来，用户只会一脸茫然
  // 地被弹到登录页，也丢掉了 URL 里的票据参数（登录回来就接不上了）。
  if (page === "scan-confirm.html") return;
  if (window.__ALLOW_GUEST__) return;                // 显式放行的页面
  if (!localStorage.getItem("token")) {
    sessionStorage.setItem("login_msg", "请先登录后再使用");
    location.replace("login.html");
  }
})();

/**
 * 取昵称的首字符，用作窄屏顶栏上的圆形头像。
 *
 * 说明：≤361px 的窄机放不下第 6 个文字胶囊（实测差 6px），
 * 所以「我的」在那一档退化成 30px 的圆形首字母头像。没有昵称时退回用户名，
 * 都取不到就给个问号，保证圆里永远有东西、不会是个空圈。
 *
 * @param {{nickname?: string, username?: string}} user
 * @returns {string}
 */
function initialOf(user) {
  const raw = (user && (user.nickname || user.username)) || "";
  const ch = raw.trim().charAt(0);
  return ch ? ch.toUpperCase() : "?";
}

/* ============================================================
   主题：白天 / 护眼 / 黑夜
   ------------------------------------------------------------
   颜色本身全在 style.css 的令牌层里，这里只负责「选哪一套 + 记住它」。

   为什么放在 common.js：顶栏是 renderTopbar() 统一注入的，
   把开关做进顶栏，就不用每个页面各自接线。

   ⚠️ 持久化键 `theme` 是**浏览器级偏好**，不是账号数据 ——
      所以它**不在** clearUserScopedData() 的清理名单里：
      换账号 / 退出登录之后，用户选的还是自己那套主题。
   ============================================================ */

const THEME_KEY = "theme";

/** 四档主题设置。`auto` 是**默认**：系统深色的人第一次打开就是深色。 */
const THEMES = [
  { id: "auto",  label: "跟随系统", icon: "contrast" },
  { id: "light", label: "白天", icon: "sun" },
  { id: "eye",   label: "护眼", icon: "leaf" },
  { id: "dark",  label: "黑夜", icon: "moon" },
];

/** 系统是否偏好深色。matchMedia 在极老的浏览器上可能没有，兜底 false。 */
function systemPrefersDark() {
  try { return window.matchMedia("(prefers-color-scheme: dark)").matches; } catch { return false; }
}

/** 用户**存下来的设置**（可能是 auto）。非法值一律回落 auto。 @returns {string} */
function getThemeSetting() {
  let t = null;
  try { t = localStorage.getItem(THEME_KEY); } catch { /* 隐私模式下读写会抛 */ }
  return THEMES.some(x => x.id === t) ? String(t) : "auto";
}

/** 当前**实际生效**的主题 —— 把 auto 解析成 light / dark。
 * @param {string} [setting] 不传则读存储
 * @returns {string} light | eye | dark */
function resolveTheme(setting) {
  const s = setting || getThemeSetting();
  if (s !== "auto") return s;
  return systemPrefersDark() ? "dark" : "light";
}

/** 把主题写到 <html data-theme> 上 —— 这是「应用主题」的唯一入口。
 * 传 auto 也行（内部先解析）；白天不写属性，直接走 :root 那套默认值。
 * @param {string} [setting] @returns {void} */
function applyTheme(setting) {
  const t = resolveTheme(setting);
  const el = document.documentElement;
  if (t === "light") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", t);
}

/** 切换主题并记住。 @param {string} id auto | light | eye | dark @returns {void} */
function setTheme(id) {
  const safe = THEMES.some(x => x.id === id) ? id : "auto";
  try { localStorage.setItem(THEME_KEY, safe); } catch { /* 存不下不影响本次生效 */ }
  applyTheme(safe);
  syncThemeUI();
}

/** 系统深色偏好变化时**实时跟随** —— 只在 auto 状态下生效。
 * 已经显式选过主题的人，不该被系统设置改掉自己的选择。 @returns {void} */
let _themeWatcherBound = false;
function watchSystemTheme() {
  if (_themeWatcherBound) return;      // 顶栏会被重建，别重复绑
  _themeWatcherBound = true;
  let mq = null;
  try { mq = window.matchMedia("(prefers-color-scheme: dark)"); } catch { return; }
  const on = () => {
    if (getThemeSetting() !== "auto") return;
    applyTheme("auto");
    syncThemeUI();
  };
  if (mq.addEventListener) mq.addEventListener("change", on);
  else if (mq.addListener) mq.addListener(on);      // 老 Safari
}

/** 新建主题开关的 HTML（按钮 + 下拉菜单）。 @returns {string} */
function themeSwitchHtml() {
  const setting = getThemeSetting();
  const setT = THEMES.find(x => x.id === setting) || THEMES[0];
  // 按钮图标显示**实际生效**的主题：窄屏会把文字标签藏掉，图标是唯一线索；
  // 文字标签仍显示**存下来的设置**（「跟随系统」），两者各说一件事。
  const eff = THEMES.find(x => x.id === resolveTheme(setting)) || THEMES[1];
  const items = THEMES.map(x =>
    `<button type="button" role="option" data-theme-id="${x.id}"
             aria-selected="${x.id === setting}" class="${x.id === setting ? "active" : ""}">
       <span class="theme-ico">${icon(/** @type {any} */ (x.icon), 16)}</span>${x.label}
     </button>`).join("");
  return `<div class="theme-switch" id="theme-switch">
      <button type="button" class="theme-btn" aria-haspopup="listbox" aria-expanded="false"
              title="主题：${setT.label}（当前显示：${eff.label}）">
        <span class="theme-ico">${icon(/** @type {any} */ (eff.icon), 17)}</span>
        <span class="theme-label">${setT.label}</span>
      </button>
      <div class="theme-menu" role="listbox" aria-label="选择主题" hidden>${items}</div>
    </div>`;
}

/** 同步按钮上的图标/文字与菜单里的选中态。主题变化后调用。 @returns {void} */
function syncThemeUI() {
  const setting = getThemeSetting();
  const setT = THEMES.find(x => x.id === setting) || THEMES[0];
  const eff = THEMES.find(x => x.id === resolveTheme(setting)) || THEMES[1];
  const btn = document.querySelector(".theme-btn");
  if (btn) {
    btn.innerHTML = `<span class="theme-ico">${icon(/** @type {any} */ (eff.icon), 17)}</span>` +
                    `<span class="theme-label">${setT.label}</span>`;
    btn.setAttribute("aria-label", `主题：${setT.label}，当前显示 ${eff.label}`);
    btn.setAttribute("title", `主题：${setT.label}（当前显示：${eff.label}）`);
  }
  document.querySelectorAll(".theme-menu [data-theme-id]").forEach(el => {
    const on = el.getAttribute("data-theme-id") === setting;
    el.classList.toggle("active", on);
    el.setAttribute("aria-selected", on ? "true" : "false");
  });
}

/** 给已插入的主题开关接上交互：点开/收起、选主题、点外面或 Esc 收起。 @returns {void} */
function mountThemeSwitch() {
  const box = document.getElementById("theme-switch");
  if (!box) return;
  const btn = box.querySelector(".theme-btn");
  const menu = box.querySelector(".theme-menu");
  if (!btn || !menu) return;

  const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  const open = () => { menu.hidden = false; btn.setAttribute("aria-expanded", "true"); };

  btn.addEventListener("click", (e) => {
    e.stopPropagation();                       // 别冒泡到 document 上被"点外面"关掉
    if (menu.hidden) open(); else close();
  });
  menu.addEventListener("click", (e) => {
    const hit = /** @type {HTMLElement} */ (e.target).closest("[data-theme-id]");
    if (!hit) return;
    setTheme(hit.getAttribute("data-theme-id") || "light");
    close();
  });
  document.addEventListener("click", (e) => {
    if (!box.contains(/** @type {Node} */ (e.target))) close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  // 初始状态由每页 <head> 的内联脚本在首帧前写好（这里不再 apply，免得反而闪一下）；
  // 这里只补「系统偏好变化时实时跟随」的监听。
  watchSystemTheme();
  syncThemeUI();
}

/**
 * 注入顶部导航（带登录态）。
 *
 * ## 顶栏为什么只剩一个「学习规划」
 *
 * 历史演进（三版，每版都是因为上一版的实际体验不够好）：
 *   1. 「学业规划」做悬停下拉菜单（课表 / 日程提醒 / AI伴学）
 *      -> 多一次交互：鼠标要悬停、手机窄屏下菜单被平铺，点进去还得再挑。
 *   2. 改成三个一级平铺项（课表、日程提醒、AI伴学）
 *      -> 顶栏被占掉三格，把「情绪陪伴 / 地图导航」挤到边上；
 *         而这三个子页其实是**同一件事的三个视图**，平铺在顶栏会让人
 *         误以为它们是三个并列的功能模块。
 *   3. 现在：顶栏只留**一个**「学习规划」，直接指向课表页（planner.html）。
 *      课表是这三者里最高频的入口，所以点击就直达，不再多一层。
 *      另外两个子页（日程提醒 / AI伴学）的入口下移到**页面内的胶囊条**
 *      （见各页的 .subnav-row），去了课表页就能顺手切过去，功能一个没少。
 *
 * ⚠️ `active` 取值全部继续接受：timetable / reminder / plan / planner 都会
 *    点亮同一个「学习规划」项 —— 它们在用户眼里本就是同一块功能。
 *    `planner` 是历史别名，等价于 timetable。
 *
 * @param {"home"|"timetable"|"reminder"|"plan"|"planner"|"emotion"|"map"} active
 * @returns {void}
 */
function renderTopbar(active) {
  const user = getUser();
  // ★ 记下「当前在哪个视角」：管理页记 admin，其它页面记 user。
  //   个人页那张「切换视图」卡片靠它决定写「进入用户视图」还是「进入管理员视图」——
  //   URL 上看不出来人是从哪边点进来的（account.html 两边是同一个地址）。
  try {
    const path = location.pathname;
    if (/(?:^|\/)admin(?:-feedback)?\.html$/.test(path)) {
      localStorage.setItem("adminView", "admin");
    } else if (!/(?:^|\/)(?:account|login)\.html$/.test(path)) {
      // ⚠️ 个人页（account.html）**自己不写**这个标记：它两边共用同一个地址，
      //   写一次就会把「从管理视角点进来」这件事抹掉，卡片永远翻不回去（踩过）。
      localStorage.setItem("adminView", "user");
    }
  } catch { /* 隐私模式写不了 localStorage，卡片会退回默认（admin），不影响功能 */ }
  // 课表 / 日程提醒 / AI伴学 三个子页共用顶栏这一个入口
  const PLANNER_KEYS = ["timetable", "reminder", "plan", "planner"];
  const plannerOn = PLANNER_KEYS.includes(active);
  const isAdmin = !!(user && user.is_admin);

  /* ★★ 管理员看到的顶栏只有「数据」和「反馈」。
   *
   *   「主页 / 学习规划 / 情绪陪伴 / 地图导航」是**用户**的四个入口，
   *   管理员的入口换成他自己的两件事：看数据、看反馈。
   *
   *   ⚠️ 用户顶栏仍然是 4 项（红线：不加第 5 个）—— 这里只是按身份**二选一**，
   *      不是往用户那一排里塞东西。
   */
  const NAV_USER = `
      <a href="index.html" class="${active === 'home' ? 'active' : ''}">主页</a>
      <a href="planner.html" class="${plannerOn ? 'active' : ''}">学习规划</a>
      <a href="emotion-chat.html" class="${active === 'emotion' ? 'active' : ''}">情绪陪伴</a>
      <a href="nav.html" class="${active === 'map' ? 'active' : ''}">地图导航</a>`;
  // ★ 管理员的两件事是**两个页面**：
  //     数据 -> admin.html            （ECharts 大屏）
  //     反馈 -> admin-feedback.html   （收到的反馈 + 回复）
  //   ⚠️ 「反馈」不是 feedback.html —— 那是**用户**提交反馈的页子。
  const NAV_ADMIN = `
      <a href="admin.html" class="${active === 'admin' ? 'active' : ''}">数据</a>
      <a href="admin-feedback.html" class="${active === 'feedback' ? 'active' : ''}">反馈</a>`;

  const bar = document.createElement("div");
  bar.className = "topbar";
  bar.innerHTML = `
    <div class="logo"><span class="logo-mark">${logoMark(28)}</span> 校园 AI 助手</div>
    <nav>
      ${isAdmin ? NAV_ADMIN : NAV_USER}
      ${themeSwitchHtml()}
      ${user ? `<a class="user" href="account.html" title="账号设置"
                  aria-label="账号设置：我的 ${escapeHtml(user.nickname || user.username)}">
                  <span class="u-avatar" aria-hidden="true">${escapeHtml(initialOf(user))}</span>
                  <span class="u-short" aria-hidden="true">${icon("user", 14)} 我的</span>
                  <span class="u-name">${icon("user", 15)} ${escapeHtml(user.nickname || user.username)}</span>
                </a>
                <a href="#" id="logout">退出</a>`
              : `<a href="login.html">登录</a>`}
    </nav>`;

  // ⚠️⚠️ 必须幂等：先删掉**已存在的**顶栏再插入新的。
  //
  // 注意点（实测可见）：账号设置页改完昵称后，顶栏变成了**两条** ——
  // 上面一条是旧昵称、下面一条是新昵称，像页面被复制了一份。
  //
  // 原因：改昵称成功后要刷新顶栏上的显示名，account.js 会再调一次
  // `renderTopbar("account")`；而本函数原来是**无条件** document.body.prepend(bar)，
  // 于是新旧两条并存。刷新显示名是对的，错在「插入」没把「旧的」清掉。
  //
  // 所有现存的顶栏都带 .topbar 类，一并移除即可 ——
  // 用 querySelectorAll 而不是 querySelector，是为了把历史遗留的多余顶栏
  // （比如已经叠了两条的页面）也一次清干净，而不是每刷新一次只去掉一条。
  document.querySelectorAll("body > .topbar").forEach(el => el.remove());

  document.body.prepend(bar);
  const lo = /** @type {HTMLAnchorElement|null} */ (bar.querySelector("#logout"));
  if (lo) lo.onclick = (e) => { e.preventDefault(); clearToken(); location.href = "login.html"; };

  // 主题开关的交互必须在这里挂 —— 顶栏刚被插进 DOM，元素才存在
  mountThemeSwitch();
}

/**
 * 动态加载高德 JS API（需填入 CONFIG.AMAP_JS_KEY）。
 *
 * 加载的插件：
 *   · AMap.Walking      —— 步行路径规划（校外的起终点走它）
 *   · AMap.Geocoder     —— 逆地理编码
 *
 * @returns {Promise<any>} 解析为 window.AMap
 */
function loadAMap() {
  return new Promise((resolve, reject) => {
    if (window.AMap) return resolve(window.AMap);
    if (!CONFIG.AMAP_JS_KEY) {
      return reject(new Error("未配置高德 JS API Key（请在 js/config.js 填入）"));
    }
    window._AMapSecurityConfig = { securityJsCode: CONFIG.AMAP_SECURITY_CODE };
    const s = document.createElement("script");
    s.src = `https://webapi.amap.com/maps?v=2.0&key=${CONFIG.AMAP_JS_KEY}`
      + `&plugin=AMap.Walking,AMap.Geocoder`;
    s.onload = () => resolve(window.AMap);
    s.onerror = () => reject(new Error("高德地图加载失败"));
    document.head.appendChild(s);
  });
}


/* ============================================================
   学期日历 · 「某天到底该上哪些课」
   ------------------------------------------------------------
   为什么这段放在 common.js（而不是留在 planner-core.js）：

     课表页、日程提醒页、首页「今日课程」卡、以及后端 AI伴学算空档，
     **四处都要回答同一个问题**：今天要上什么课。
     以前它们各写各的：
       · 课表页 —— 按「星期 + 周次」判断（正确：一门课每周都要出现）
       · 提醒页 / 首页卡 —— 直接 `c.date === 今天`（错：只认库里那一条的日期）
     结果就是同一份课表，课表页显示周日没课、提醒页却说今天有 6 节，
     首页卡也跟着显示 6 节。用户看到的两个页面互相打脸。

     根子在数据模型：库里一条课存的是**某一周的某一天**（date="2026-10-18"），
     weeks 说明「这门课在哪几周上」。所以「今天上不上」= 星期相同 + 本周在 weeks 里，
     **不是** date 相等。date 只说明「这门课属于星期几」。

     所以把判断收成这里的 courseOnDate()，谁问「某天有什么课」都走它。
     ⚠️ 别再在页面里手写 `c.date === iso` —— 那是这个 bug 的源头。
   ============================================================ */

/** 开学第 1 周的周一（学校给的绝对概念，写死；不要用「本周」当原点，会漂） */
const SEMESTER_START = "2026-09-07";
const SEM_WEEKS_BEFORE = 0;            // 第 1 周就是最早的一周
const SEM_WEEKS_AFTER = 19;            // 第 1~20 周（一个标准学期）

/** 学期第 1 周的周一（00:00）。 */
function semesterStart() {
  const d = new Date(SEMESTER_START + "T00:00:00");
  d.setHours(0, 0, 0, 0);
  return d;
}

/** 把 Date 转成 "YYYY-MM-DD"（用本地时区，避免 toISOString 的 UTC 偏移） */
function isoOf(d) {
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 今天是哪天（"YYYY-MM-DD"） */
function todayIso() { return isoOf(new Date()); }

/** 由日期反推星期几（1=周一 ... 7=周日） */
function weekdayOf(iso) {
  const d = new Date(String(iso || "") + "T00:00:00");
  if (isNaN(d.getTime())) return 0;
  return ((d.getDay() + 6) % 7) + 1;
}

/**
 * 把周次串解析成周次数字集合。
 *
 * 教务导出的写法五花八门（实测）：
 *   "1周(全部)" / "2-3,6-8周(全部) 19周(全部)" / "1,3,5,7周(全部) 2,6,8周(全部)"
 *
 * ⚠️ 兜底原则：**宁可不隐藏，也不要误隐藏**。
 *    认不出任何数字时一律 all=true（当每周都上），绝不返回空集合把课藏起来。
 *
 * @param {*} raw 原始周次串（存的是原文，解析只在这里做）
 * @returns {{weeks: number[], all: boolean, parsed: boolean}}
 */
function parseWeeks(raw) {
  const s = String(raw == null ? "" : raw).trim();
  const TOTAL = SEM_WEEKS_BEFORE + SEM_WEEKS_AFTER + 1;   // 学期总周数（20）
  if (!s) return { weeks: [], all: true, parsed: false };

  // 「单周/双周」这类整学期周期性写法。
  // ⚠️ 必须带 `!/\d/` 守卫：串里已有明确数字说明是显式列周
  //    （如 "9,11周(全部)"），此时「周」字只是量词，不能被当成周期性写法。
  if (!/\d/.test(s)) {
    if (/每周|全周|所有周/.test(s)) return { weeks: [], all: true, parsed: false };
    const all = Array.from({ length: TOTAL }, (_, i) => i + 1);
    if (/单周/.test(s)) return { weeks: all.filter(w => w % 2 === 1), all: false, parsed: true };
    if (/双周/.test(s)) return { weeks: all.filter(w => w % 2 === 0), all: false, parsed: true };
  }

  const cleaned = s
    .replace(/[(（][^)）]*[)）]/g, " ")      // 去 "(全部)" 这类括号备注
    .replace(/[，、;；]/g, ",")             // 顿号/全角逗号统一
    .replace(/[－—–~～至]/g, "-");          // 各种连接号统一

  const set = new Set();
  const re = /(\d+)\s*(?:-\s*(\d+))?/g;
  let m;
  while ((m = re.exec(cleaned)) !== null) {
    const a = parseInt(m[1], 10);
    const b = m[2] ? parseInt(m[2], 10) : a;
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    const lo = Math.max(1, Math.min(a, b));
    const hi = Math.min(TOTAL, Math.max(a, b));
    for (let w = lo; w <= hi; w++) set.add(w);
  }

  if (!set.size) return { weeks: [], all: true, parsed: false };  // 兜底：当每周都上
  return { weeks: [...set].sort((x, y) => x - y), all: false, parsed: true };
}

/**
 * 某个日期落在学期第几周（1 起算）；算不出（学期外/日期非法）返回 null。
 * 原点与课表页的周次条完全一致 = SEMESTER_START（第 1 周 = 9/7 那周）。
 */
function weekNumberByDate(iso) {
  if (!iso) return null;
  const d = new Date(String(iso) + "T00:00:00");
  if (isNaN(d.getTime())) return null;
  const diffDays = Math.round((d - semesterStart()) / 86400000);
  const w = Math.floor(diffDays / 7) + 1;
  if (w < 1 || w > SEM_WEEKS_BEFORE + SEM_WEEKS_AFTER + 1) return null;
  return w;
}

/** 这条课在第 weekNo 周要不要显示（周次过滤的唯一判断点）。 */
function courseShownInWeek(c, weekNo) {
  if (weekNo == null) return true;          // 日期算不出周次：保守显示，别藏课
  const r = parseWeeks(c && c.weeks);
  if (r.all) return true;                   // 空 / 认不出 = 每周都上
  return r.weeks.includes(weekNo);
}

/**
 * 把周次数组压成给用户看的紧凑串。
 *   [1] -> "1周"；[14,15] -> "14-15周"；[2,3,6,7,8,19] -> "2-3,6-8,19周"
 */
function formatWeeksLabel(weeks) {
  if (!weeks || !weeks.length) return "";
  const sorted = [...weeks].sort((a, b) => a - b);
  const parts = [];
  let start = sorted[0], prev = sorted[0];
  // ⚠️ 循环到 `i <= sorted.length`（刻意越界一格）：越界时 sorted[i] 是 undefined，
  //    `undefined === prev + 1` 为 false，正好触发最后一次 flush。
  //    这是哨兵写法，别"顺手修正"成 i < length（那样最后一段会丢）。
  for (let i = 1; i <= sorted.length; i++) {
    const cur = sorted[i];
    if (cur === prev + 1) { prev = cur; continue; }
    parts.push(start === prev ? String(start) : `${start}-${prev}`);
    start = prev = cur;
  }
  return parts.join(",") + "周";
}

/**
 * 这条课在 iso 这一天要不要上。★ 全站唯一口径（课表页 / 提醒页 / 首页卡共用）。
 *
 * 判断两条，缺一不可：
 *   1. 星期相同 —— 一条课的 date 只说明「它属于星期几」（"2026-09-08" = 周二）
 *   2. 该日期所在周次在 weeks 里 —— 空串/认不出按「每周都上」
 *
 * ⚠️ 不要用 `c.date === iso`：那会让「每周重复」的课只在录它那一周出现一次，
 *    反过来也不会把别周的课挡掉 —— 两个方向都错。
 *
 * @param {any} c 课程记录
 * @param {string} iso "YYYY-MM-DD"
 * @returns {boolean}
 */
function courseOnDate(c, iso) {
  if (!c || !c.date || !iso) return false;
  if (weekdayOf(c.date) !== weekdayOf(iso)) return false;
  return courseShownInWeek(c, weekNumberByDate(iso));
}

/**
 * 把某一周并进「上课周次」串，返回新的周次串（写回数据库用）。
 *
 * 用途：同一时间点已经有一门课、但它只在别的周上，用户又要它在当前周出现时，
 * 不该新建一条（会撞 uq_user_date_slot），而是把当前周并进它的 weeks。
 *
 * @param {*} raw 原周次串（"1-5周" / "2-3,6-8周(全部)" / 空）
 * @param {number} wk 要并进去的周次（1 起算）
 * @returns {string} 新周次串；原串表示「每周都上」时原样返回
 */
function weeksWith(raw, wk) {
  const r = parseWeeks(raw);
  if (r.all) return String(raw == null ? "" : raw);   // 已经每周都上，不用改
  const set = new Set(r.weeks);
  set.add(Number(wk));
  return formatWeeksLabel([...set]);
}

/**
 * 某一天要上的课（按开始时间排序）。
 * @param {any[]} list 课程列表（整学期）
 * @param {string} iso "YYYY-MM-DD"
 * @returns {any[]}
 */
function coursesOnDate(list, iso) {
  return (list || [])
    .filter(c => courseOnDate(c, iso))
    .sort((a, b) => {
      const x = String(a.start || "99:99");
      const y = String(b.start || "99:99");
      return x < y ? -1 : x > y ? 1 : 0;
    });
}
