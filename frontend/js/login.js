// @ts-check
/* 登录页：账号登录 / 注册 / 扫码登录（含二维码轮询）。
 *
 * 拆成独立 js 的原因：原来这些逻辑全写在 login.html 的 <script> 里，
 * 加上扫码之后行数翻倍，内联会很难维护（而且 HTML 里塞大段 JS 不利于缓存）。
 */

/* ============================================================
   一、进入登录页的第一件事
   ============================================================ */

/**
 * 二维码里带过来的票据（手机扫码打开本页时会有）。
 *
 * 语义：电脑上点了「扫码登录」出了一张票，手机用微信/QQ 扫到 → 打开本页
 * （login.html?t=xxx）。用户在本页注册或登录成功后，我们**顺手把票核销掉**，
 * 让电脑那端知道「人已经登进来了」。
 *
 * ⚠️ 票据**不参与登录本身**：登录还是靠用户输的账号密码。
 *    票据只用来让电脑端同步状态（见 consumeTicketAfterMobileLogin）。
 */
const QR_TICKET = (() => {
  try {
    return new URLSearchParams(location.search).get("t") || "";
  } catch {
    return "";
  }
})();

/**
 * 登录成功之后该进哪一页。
 *
 * ★ 管理员的首页是「数据 + 反馈」（admin.html），不是面向用户的欢迎页：
 *   主页、今日怎么过、三张功能卡那些「是给用户看的」，管理员登录后不该先落到那儿。
 *   普通用户仍然进 index.html。
 *
 * @param {any} u /auth/me 返回的用户对象
 * @returns {string}
 */
function homeTargetFor(u) {
  return u && u.is_admin ? "admin.html" : "index.html";
}

/**
 * 已经登录就直接进主页。
 *
 * ⚠️ 不能只看「有没有 token」：token 存在 ≠ token 还有效。
 * 换了新压缩包 / 新数据库之后，旧 token 指向的账号在库里已经不存在了，
 * 只看 token 会让用户卡在「进登录页就被弹回主页，可主页又用不了」的死循环。
 * 所以这里拿 token 去问一次 /auth/me，后端说「账号不存在」就清掉当未登录处理。
 */
(async function autoEnter() {
  if (!getToken()) return;
  try {
    const me = await api("/auth/me");
    setUser(me);
    // 是扫码进来的（URL 带票据）-> 顺手核销，让电脑端也亮起来
    await consumeTicketAfterMobileLogin();
    location.replace(homeTargetFor(me));
  } catch {
    clearToken();                       // 顺带清掉上一个账号残留的对话/课表
    toast("之前的登录已失效，请重新登录或注册", "info");
  }
})();

// 品牌 Logo（Y 艺术字）
document.getElementById("auth-logo").innerHTML = logoMark(34);

// 扫码头进来时，先给一句「已到达」的反馈，让用户明白自己没有白扫一下
(function showScanArrival() {
  if (!QR_TICKET) return;
  if (!isMobileDevice()) return;   // 电脑上不会有扫码进入这回事，不提示
  toast("已打开登录页，请登录或注册", "ok");
})();

// 被 api() 因登录过期踢回来时，展示一句中文原因
(function showKickedMsg() {
  const msg = sessionStorage.getItem("login_msg");
  if (msg) { sessionStorage.removeItem("login_msg"); setTimeout(() => toast(msg, "error"), 250); }
})();

/* ============================================================
   二、一级切换：账号登录 <-> 扫码登录
   ============================================================ */

/** 当前处于哪个一级模式 @type {"pwd"|"scan"} */
let MODE = "pwd";

/**
 * 切换一级模式。
 * @param {"pwd"|"scan"} mode
 */
function switchMode(mode) {
  MODE = mode;
  const isPwd = mode === "pwd";

  document.getElementById("tab-pwd").classList.toggle("active", isPwd);
  document.getElementById("tab-scan").classList.toggle("active", !isPwd);
  document.getElementById("tab-slider").classList.toggle("right", !isPwd);
  document.getElementById("pane-pwd").style.display = isPwd ? "block" : "none";
  document.getElementById("pane-scan").style.display = isPwd ? "none" : "block";
  // ★ 扫码页的副标题**不再按部署形态分叉**（2026-10-14 build r）：哪儿都是
  //   「用手机浏览器扫码」。原来本机形态这里写的是"用手机微信扫一扫"，与同一屏
  //   下面的提示语、图标必须三处口径一致，否则同一页面自己打架。
  document.getElementById("auth-sub").textContent = isPwd
    ? "登录后即可保存课表、情绪记录与常用路线"
    : "用手机浏览器扫码，在手机上打开登录页";

  if (isPwd) {
    // 离开扫码页 -> 停掉轮询，别让它在后台空跑
    stopPolling();
  } else {
    // 进入扫码页 -> 没有票就申请一张；有票（切走又切回来）就继续轮询
    setScanHint();
    if (!currentTicket) newTicket();
    else startPolling();
  }
}

/** 二级切换：登录 / 注册（只在账号模式下可见） @param {"login"|"register"} tab */
function switchTab(tab) {
  const isLogin = tab === "login";
  document.getElementById("subtab-login").classList.toggle("active", isLogin);
  document.getElementById("subtab-register").classList.toggle("active", !isLogin);
  document.getElementById("form-login").style.display = isLogin ? "block" : "none";
  document.getElementById("form-register").style.display = isLogin ? "none" : "block";
  if (isLogin) markMismatch(false);
}

/* ============================================================
   三、账号登录 / 注册
   ============================================================ */

/** @param {Event} e */
async function doLogin(e) {
  e.preventDefault();
  const username = /** @type {HTMLInputElement} */ (document.getElementById("login-username")).value.trim();
  const password = /** @type {HTMLInputElement} */ (document.getElementById("login-password")).value;
  if (!username || !password) { toast("请填写用户名和密码", "error"); return false; }
  try {
    const data = await api("/auth/login", { method: "POST", body: { username, password } });
    setToken(data.access_token);
    const me = await api("/auth/me");
    const switched = setUser(me);
    // 若是扫码进来的，顺手核销票据（让电脑那端也亮起来）；失败不影响本地已登录
    await consumeTicketAfterMobileLogin();
    toast(switched ? "已切换到新账号，正在进入…" : "登录成功，正在进入…", "ok");
    setTimeout(() => (location.href = homeTargetFor(me)), 600);
  } catch (err) {
    toast(/** @type {Error} */ (err).message || "登录失败，请稍后重试", "error");
  }
  return false;
}

/** @param {Event} e */
async function doRegister(e) {
  e.preventDefault();
  const username = /** @type {HTMLInputElement} */ (document.getElementById("reg-username")).value.trim();
  const pwd = /** @type {HTMLInputElement} */ (document.getElementById("reg-password")).value;
  const pwd2 = /** @type {HTMLInputElement} */ (document.getElementById("reg-password2")).value;

  if (username.length < 2) { toast("用户名至少 2 个字符", "error"); return false; }
  if (pwd.length < 6) { toast("密码至少 6 位", "error"); return false; }
  if (pwd !== pwd2) { toast("两次输入的密码不一致，请检查", "error"); markMismatch(true); return false; }

  try {
    const data = await api("/auth/register", { method: "POST", body: { username, password: pwd } });
    setToken(data.access_token);
    // 新账号必须是「干净的开始」：上一个账号留在浏览器里的对话/课表一律清掉
    clearUserScopedData();
    const me = await api("/auth/me");
    setUser(me);
    // 扫码进来的话，注册成功也算「人到位了」，顺手核销票据
    await consumeTicketAfterMobileLogin();
    toast("注册成功，正在进入…", "ok");
    setTimeout(() => (location.href = "index.html"), 600);
  } catch (err) {
    toast(/** @type {Error} */ (err).message || "注册失败，请稍后重试", "error");
  }
  return false;
}

/** 实时提示两次密码是否一致 @param {boolean} bad */
function markMismatch(bad) {
  const hint = document.getElementById("reg-match-hint");
  const p2 = document.getElementById("reg-password2");
  hint.textContent = bad ? "两次输入的密码不一致" : "";
  hint.classList.toggle("bad", bad);
  p2.classList.toggle("input-bad", bad);
}

document.getElementById("reg-password2").addEventListener("input", () => {
  const a = /** @type {HTMLInputElement} */ (document.getElementById("reg-password")).value;
  const b = /** @type {HTMLInputElement} */ (document.getElementById("reg-password2")).value;
  markMismatch(b.length > 0 && a !== b);
});

/* ============================================================
   四、扫码登录（把登录页「送到手机」）
   ============================================================ */

/** 当前票据；null 表示还没申请或已失效 */
let currentTicket = null;
/** 轮询定时器 */
let pollTimer = null;
/** 倒计时定时器 */
let tickTimer = null;
/** 剩余秒数 */
let secondsLeft = 0;
/** 是否正在领取 token（防止轮询与倒计时同时触发重复领取） */
let consuming = false;

/**
 * 当前设备是不是手机/平板。
 *
 * ⚠️ 为什么不能只看 UA：
 *   UA 可以随便伪造；而且**新款 iPad 的 UA 和 Mac 一模一样**（都是 "Macintosh"），
 *   只认 UA 会把它当成电脑。所以这里用「任一命中」的宽松策略：
 *     ① UA 里有明显的移动端关键字；或
 *     ② 有触摸屏 **且** 屏幕较窄（iPad 就靠这一条认出来）
 *   宁可把窄屏电脑误判成手机（后果只是文案变一句），
 *   也不要把手机漏判成电脑（后果是让用户对着手机扫自己屏幕上的码）。
 */
function isMobileDevice() {
  const ua = (typeof navigator !== "undefined" && navigator.userAgent) || "";
  const uaHit = /Android|iPhone|iPad|iPod|Windows Phone|Mobile|HarmonyOS/i.test(ua);
  const touch = (typeof navigator !== "undefined" && (navigator.maxTouchPoints || 0) > 0);
  const w = Math.min(
    (typeof window !== "undefined" && window.innerWidth) || 9999,
    (typeof window !== "undefined" && window.screen && window.screen.width) || 9999
  );
  return uaHit || (touch && w < 768);
}

// 供 login.html 的文案切换使用（本项目没有模块系统，只能挂到 window 上共享）
window.isMobileDevice = isMobileDevice;

/**
 * 二维码里放什么地址。
 *
 * 语义（**这是本项目扫码登录的核心，改之前务必读懂**）：
 *   二维码指向的是**登录页自己**（login.html?t=票据），不是哪个确认页。
 *   手机用微信/QQ「扫一扫」扫到这个码 → 手机上直接打开登录页 →
 *   用户在那里**注册或登录** → 手机自己拿到 token，进入移动版。
 *
 *   一句话：**扫码负责「把登录页送到手机上」，注册/登录还是走表单。**
 *
 * ⚠️ 为什么不能做到「扫完自动登录」：
 *   票据里不能携带密码（那等于把账号明文写进二维码）。纯网页没有原生的
 *   账号体系可供静默授权，所以「扫码即登录」在纯网页下做不到 ——
 *   这是形态的边界，不是实现偷懒。真实 App（微信等）能扫码即登录，
 *   是因为扫码方和被登录方都在**同一个 App 的账号体系**内。
 *
 * ⚠️ 路径前缀**不能写死 `/app/`**：
 *   页面既可能在后端托管下（http://IP:8000/app/login.html），
 *   也可能用 python -m http.server 单跑前端（http://IP:5500/login.html）。
 *   写死 /app/ 时后者会 404（二维码打开是错误页）。所以按**当前页面所在目录**推。
 *
 * ⚠️ 主机名**要看情况换**（见 scanHostPrefix）：电脑端请用 localhost 打开
 *   （理由见下），此时 `location.host` 是 127.0.0.1 —— 直接写进二维码，
 *   手机扫到连的是手机自己。所以要换成后端告诉我们的局域网 IP。
 */

/** 当前页面的主机是不是「本机回环」 */
function isLoopbackHost(h) {
  return !h || h === "localhost" || h === "127.0.0.1" || h === "::1" || h === "[::1]";
}

/**
 * 二维码该用的「协议 + 主机 + 端口」前缀；不需要替换时返回 ""。
 *
 * ## 为什么电脑端要用 127.0.0.1 打开
 *
 * 因为**它永远能解析** —— 不受「启动脚本挑到虚拟网卡」影响。若开的是
 * `http://局域网IP:端口` 而那个 IP 恰好是 VMware / VirtualBox / WSL 的虚拟网段，
 * 浏览器会停在永远打不开的页面上，启动第一屏就出错，很容易被当成程序坏了。
 *
 * （历史注：这个选择**曾经**是为了让电脑端能用浏览器定位 —— 浏览器只在
 * https / localhost 这类安全上下文里给坐标。定位功能已于 2026-10-13 整体删除，
 * 理由不再成立，但结论保留，换成上面那条「必然能打开」。）
 *
 * 代价是二维码不能再用当前页面地址（那会变成 127.0.0.1，手机打不开）。
 * 于是本函数在「当前是本机回环、但机器还有局域网 IP」时，向
 * 后端 `/system/lan-ip` 要一个局域网 IP 顶上去；拿不到就返回 ""（保持原样，
 * 页面另有提示文案告诉用户改用局域网地址打开）。
 */
async function scanHostPrefix() {
  if (!isLoopbackHost(location.hostname)) return "";   // 已经开局域网地址：手机能连，不用换
  if (_scanHostCache !== null) return _scanHostCache;
  try {
    const d = await api("/system/lan-ip");
    const ip = (d && d.ip) ? String(d.ip) : "";
    const port = location.port || (d && d.port ? String(d.port) : "");
    _scanHostCache = ip ? `http://${ip}${port ? ":" + port : ""}` : "";
  } catch (_) {
    _scanHostCache = "";   // 后端没起来/老版本没有这个接口：退回当前地址
  }
  return _scanHostCache;
}

/** 局域网地址查询结果缓存：null=还没查过，""=查过但没有 */
let _scanHostCache = null;

/**
 * 二维码里放什么地址（具体实现）。
 * @param {string} ticket 票据
 * @param {string} [hostPrefix] scanHostPrefix() 的结果，覆盖当前页面的主机
 */
function scanUrl(ticket, hostPrefix) {
  // file:// 打开时 origin 是 "null"，此时拼出来的地址没意义，交给页面上层提示
  const fallback = (location.origin && /^https?:/i.test(location.origin)) ? location.origin : "";
  const origin = hostPrefix || fallback;
  // 取当前页面所在目录：/app/login.html -> /app/ ； /login.html -> /
  const dir = location.pathname.replace(/[^/]*$/, "");
  return `${origin}${dir}login.html?t=${encodeURIComponent(ticket)}`;
}

/** 申请新票据并重新渲染二维码 */
async function newTicket() {
  stopPolling();
  const qrBox = document.getElementById("scan-qr");
  const mask = document.getElementById("scan-mask");
  mask.style.display = "none";
  // 刷新时把倒计时那行恢复显示（过期时被藏起来过）
  const timer = document.querySelector(".scan-timer");
  if (timer) timer.style.display = "";
  setScanHint();
  qrBox.innerHTML = '<div class="scan-qr-loading"><span class="spin"></span></div>';

  try {
    const data = await api("/auth/scan/create", { method: "POST" });
    currentTicket = data.ticket;
    secondsLeft = data.expires_in || 120;

    // 生成二维码（纯前端，离线也能用）
    try {
      // 电脑端用 127.0.0.1 打开时（推荐：这个地址永远能解析，不受虚拟网卡影响），
      // 二维码里的主机得换成局域网 IP，否则手机连的是它自己。
      const prefix = await scanHostPrefix();
      qrBox.innerHTML = qrSvg(scanUrl(currentTicket, prefix), { size: 196, margin: 2, ecc: "M" });
      // 换不到局域网 IP（没网卡 / 旧后端）：二维码是 127.0.0.1，手机扫了打不开，
      // 与其让用户对着一个「扫了没反应」的码发呆，不如直接把原因和办法说清楚。
      if (!prefix && isLoopbackHost(location.hostname) && !isMobileDevice()) {
        const hint = document.getElementById("scan-hint");
        if (hint) hint.textContent = "没有检测到局域网 IP，二维码只能在本机用；"
          + "手机登录请把地址栏里的 127.0.0.1 换成电脑的局域网 IP 后重新打开本页";
      }
    } catch (err) {
      qrBox.innerHTML = '<p class="scan-err">二维码生成失败，请刷新页面重试</p>';
      console.warn("二维码生成失败", err);
    }

    startCountdown();
    startPolling();
  } catch (err) {
    qrBox.innerHTML = '<p class="scan-err">二维码获取失败，请检查后端是否已启动</p>';
    currentTicket = null;
    console.warn("申请票据失败", err);
  }
}

/**
 * 按「当前是手机还是电脑」写扫码区的提示文案。
 *
 * ★ 本机 / 公网**不再分两套话术**（2026-10-14 build r，需求：「全站一致」）。
 *   本来这里按 isLocalOnlyOrigin() 分了两套：本机说「手机微信扫一扫」、公网说
 *   「手机浏览器扫码打开」。用户的意见是：**界面上只需要回答"用什么打开"这一个
 *   问题**，哪儿都答「用浏览器」最省事、也不会在不同页面看到互相打架的说法。
 *   于是提示语 / 副标题 / 图标三处全部合并成一套，见下面的 scanNoteHtml 与
 *   SCAN_APPS。
 */
function setScanHint() {
  const hint = document.getElementById("scan-hint");
  const note = document.getElementById("scan-note");
  const label = document.getElementById("scan-apps-label");
  if (!hint) return;
  if (isMobileDevice()) {
    // 已经在手机上了，再让用户"扫手机上这个码"很荒谬
    hint.textContent = "本页已在手机上，直接用下面的账号密码登录或注册即可";
    if (note) note.style.display = "none";
    if (label) label.textContent = "或在电脑上打开本页，用手机浏览器扫码";
  } else {
    hint.textContent = "请用手机浏览器扫码打开";
    if (note) { note.style.display = ""; note.innerHTML = scanNoteHtml(); }
    if (label) label.textContent = "用手机浏览器扫一扫";
  }
}

/**
 * 「手机怎么扫」那段说明。
 *
 * ★ 动作那句话**全站一套**：`请用手机上的浏览器扫上面这个码…`
 *
 *   这里原来按部署形态分了两套话术，本机推荐「微信「扫一扫」」、公网推荐浏览器，
 *   还搭了一句"网页本身无法调起手机摄像头，所以要借助微信…"来解释为什么。用户
 *   2026-10-14 明确否掉了这个方向：**界面上只需要回答"用什么打开"，不要解释
 *   谁不行、也不要解释为什么** —— 把平台限制/技术原因写进用户界面，既啰嗦，
 *   又容易被误读成"这应用有问题"。所以现在哪儿都只有「用浏览器打开」这一句。
 *
 * ⚠️ 唯一保留的差别是**「要不要连同一个网络」这个前提**，而且它不是话术差异、
 *   是**事实差异**：
 *     · 本机 / 局域网：二维码里放的是局域网 IP（见 scanUrl），手机不连同一个
 *       网络**就是打不开**。这属于"使用前提"，不写用户会以为应用坏了。
 *     · 公网部署：二维码里已经是公网域名，手机用 4G/5G 就能开，这句话直接删掉。
 *   写死任何一句都会在另一个形态下变成错话，所以只有这一句跟着 isLocalOnlyOrigin() 走。
 */
function scanNoteHtml() {
  const net = isLocalOnlyOrigin()
    ? "手机要和电脑连同一个网络（同一个 Wi-Fi）。"
    : "";
  return `ⓘ ${net}请用手机上的<b>浏览器</b>扫上面这个码，手机上会打开登录页，
    在那里<b>注册或登录</b>即可进入移动版。`;
}

/**
 * 当前页面地址是不是「手机不换网络就够不着」的那种（回环 / 局域网 / file://）。
 * 用来决定上面那段说明该说「要同一个 Wi-Fi」还是「不用」。
 *
 * ⚠️ 判据要和 scanUrl() 实际用的主机保持一致：那里是「非回环就用 location.origin」，
 *    所以只要能出现在 origin 里、又是外网够不着的地址，都算 local-only。
 *    172 段只认 16~31（172.16.0.0/12），172.32+ 是公网地址，别误伤。
 */
function isLocalOnlyOrigin() {
  if (!/^https?:$/i.test(location.protocol)) return true;   // file:// 双击打开的
  const h = location.hostname;
  if (isLoopbackHost(h)) return true;                       // 127.0.0.1 / localhost / ::1
  return /^10\./.test(h)
    || /^192\.168\./.test(h)
    || /^172\.(1[6-9]|2\d|3[01])\./.test(h)
    || /\.local$/i.test(h);
}

/** 倒计时：到点把二维码标成「已过期」 */
function startCountdown() {
  clearInterval(tickTimer);
  const el = document.getElementById("scan-countdown");
  el.textContent = String(secondsLeft);
  tickTimer = setInterval(() => {
    secondsLeft -= 1;
    el.textContent = String(Math.max(0, secondsLeft));
    if (secondsLeft <= 0) {
      clearInterval(tickTimer);
      showExpired();
    }
  }, 1000);
}

/** 轮询票据状态 */
function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (!currentTicket || consuming) return;
    try {
      const st = await api(`/auth/scan/status?ticket=${encodeURIComponent(currentTicket)}`);
      if (st.status === "expired") { showExpired(); return; }
      if (st.status === "scanned") {
        // 手机已经打开登录页了 —— 提示电脑这端「手机那边正在操作」，
        // 否则电脑前的人看着纹丝不动的二维码会以为卡死
        showScannedHint();
      }
      if (st.status === "confirmed") {
        // 手机那边登录成功并核销了票据 -> 电脑这端**也**顺势登录同一个账号。
        // 这是"两头都亮"的 bonus：手机登录是主线，电脑登录是顺带。
        await consumeAndEnter();
      }
    } catch {
      // 轮询失败（后端重启/短暂断网）不弹错，下一轮继续试即可
    }
  }, 1500);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

/** 手机已经打开登录页：给一句进度反馈（不遮二维码，方便继续扫）*/
function showScannedHint() {
  const hint = document.getElementById("scan-hint");
  if (hint && !isMobileDevice()) {
    hint.innerHTML = '<b class="scan-ok">手机已打开登录页</b>，请在那台手机上完成登录';
  }
}

/** 显示「已过期」遮罩 */
function showExpired() {
  stopPolling();
  clearInterval(tickTimer);
  const mask = document.getElementById("scan-mask");
  document.getElementById("scan-mask-ico").innerHTML = icon("clock", 34);
  document.getElementById("scan-mask-text").textContent = "二维码已过期";
  mask.style.display = "flex";
  setScanHint();
  document.getElementById("scan-hint").textContent = "二维码已失效，点「刷新二维码」重新获取";
  // 倒计时那行此时已无意义（会停在「0 秒后失效」），藏掉免得看着别扭
  const timer = document.querySelector(".scan-timer");
  if (timer) timer.style.display = "none";
  currentTicket = null;
}

/**
 * 【手机侧】登录成功之后，把二维码里的票据「消费」掉。
 *
 * 为什么要有这一步：
 *   票据是一次性的。手机扫码打开的是 login.html?t=xxx，
 *   用户在这个页面上完成登录后，如果不去处理票据，电脑那端会一直
 *   轮询到 120 秒超时，看起来像"扫了没反应"。
 *   这里主动 confirm 一下，让电脑端立刻收到 confirmed → 自动进主页，
 *   两头都亮，演示效果完整。
 *
 * ⚠️ 全程静默、失败不打扰：用户**已经登录成功了**，
 *    票据没核销只是"电脑那端没同步"，不影响他自己用。
 *    这里一旦弹错误提示，用户会以为「登录失败了」，反而更糟。
 */
async function consumeTicketAfterMobileLogin() {
  // 票据有两个来源，别漏了 URL 那个：
  //   · currentTicket  —— 本页自己开票时（电脑端点「扫码登录」）设的
  //   · QR_TICKET      —— 手机扫码打开本页时，票据在 URL 里（?t=xxx）
  // 手机上属于后者：这一页从来没调过 newTicket()，currentTicket 一直是 null。
  const ticket = currentTicket || QR_TICKET;
  if (!ticket) return;
  try {
    await api("/auth/scan/confirm", { method: "POST", body: { ticket } });
    currentTicket = null;
  } catch {
    // 忽略：票据过期/已被消费都无所谓，用户自己已经登录好了
  }
}

/**
 * 【电脑侧】轮询到票据已确认 → 领取 token 并进入主页。
 *
 * 与 consumeTicketAfterMobileLogin 的分工（别搞混）：
 *   · 手机侧 confirm（核销）→ 告诉后端"有人登录了"
 *   · 电脑侧 consume（领取）→ 把 token 取回来自己用
 * 一个票只能被领一次（consume_ticket 弹出即销毁），所以电脑端领到就是领到了，
 * 领不到（比如手机先领走了）就安静地把码刷新一下，不弹错误吓人。
 */
async function consumeAndEnter() {
  if (consuming) return;
  consuming = true;
  stopPolling();
  clearInterval(tickTimer);
  try {
    const data = await api(`/auth/scan/consume?ticket=${encodeURIComponent(currentTicket)}`,
                           { method: "POST" });
    setToken(data.access_token);
    const me = await api("/auth/me");
    const switched = setUser(me);
    toast(switched ? "已通过手机扫码登录，正在进入…" : "扫码登录成功，正在进入…", "ok");
    setTimeout(() => (location.href = homeTargetFor(me)), 500);
  } catch (err) {
    consuming = false;
    // 票据被手机端先领走 / 已过期，都属于正常竞争，不当成错误弹红字，
    // 直接换一张新码让用户可以再试。
    toast("这张二维码已失效，已为你刷新，请重新扫一次", "info");
    newTicket();
  }
}

/* ============================================================
   五、底部「支持扫码的 App」图标
   ============================================================ */

/**
 * 支持扫码的 App —— **只有一个：浏览器**。
 *
 * 演化过三轮，最后收敛到这里：
 *   ① 最初六个（校园 App / 微信 / 支付宝 / QQ / 企业微信 / 钉钉）—— 列一堆图标
 *      看着热闹，但"我该用哪个"的决策成本全甩给了用户。
 *   ② 收到两个（微信 + 百度）。
 *   ③ 现在只剩浏览器。用户 2026-10-14 的要求原话是「只要写请用浏览器打开就行，
 *      **下面小图标也只要浏览器的**」，并追加「不需要说微信打不开」「相机也不要说」。
 *      理由站得住：**界面上只需要回答"用什么打开"**，而"用浏览器打开"是唯一一个
 *      在本机、局域网、公网三种形态下都成立、且不用附带任何解释的答案。
 *      （托管平台对微信内置浏览器直接 403，把微信列上去等于劝人点一个打不开的入口。）
 *
 * ⚠️ 这里**用品牌配色 + 首字自绘，不是厂商的官方 logo 素材**。
 * 直接内嵌真 logo 有商标与版权风险（离线包里也不该塞别人的品牌资源），
 * 所以做成「品牌配色 + 文字标记」的形态：既传达了"这个能扫"，
 * 又不涉及侵权。
 */
const SCAN_APPS = [
  { name: "浏览器", color: "#2A6FF3", init: "浏" },
];

function renderScanApps() {
  const box = document.getElementById("scan-apps");
  if (!box) return;
  box.innerHTML = SCAN_APPS.map(a => `
    <div class="scan-app" title="${escapeHtml(a.name)}">
      <span class="scan-app-ico" style="background:${a.color}">${escapeHtml(a.init)}</span>
      <span class="scan-app-name">${escapeHtml(a.name)}</span>
    </div>`).join("");
}

renderScanApps();

/* ============================================================
   六、初始化：定好「进来时停在哪个视图」
   ============================================================ */

/**
 * ⚠️ 为什么必须有这个函数（之前缺了它，是个真实缺陷）：
 *   HTML 里账号模式的 class/display 是**写死在标签上的默认值**，页面一加载就是
 *   「账号登录 + 登录表单」。而 switchMode / switchTab / setScanHint 只在**用户点击**
 *   时才被调用 —— 从没有人在初始化时叫过它们。
 *   后果：手机扫码打开 login.html?t=xxx 时，setScanHint() 从来没执行，
 *   scan-hint 一直是给电脑看的「请用手机微信/QQ扫一扫」（手机上看到这句很荒谬）。
 *   所以这里补一次初始化，把界面收敛到与「谁、从哪来」相符的状态。
 */
function initAuthView() {
  // 默认视图：账号登录 + 登录子页。
  // 让 class/display 与 JS 状态 MODE 一致，避免"看起来在 A，实际状态是 B"。
  switchMode("pwd");
  switchTab("login");

  /* ★ 进页面就先按形态写一次扫码区文案。
   *   为什么不能只靠"用户点了扫码页签再写"：switchMode("pwd") 走的是账号分支，
   *   不会调 setScanHint()，于是扫码区的提示 / 图标**在首次点开页签之前一直是
   *   HTML 里那套静态默认值** —— 公网部署时那套默认值说的是"用手机微信扫一扫"，
   *   而微信在公网根本打不开（平台 403）。文案要么一开始就写对，要么就会在
   *   某个时刻给人看错话。放在这里，一次写清，后面点不点都对。 */
  setScanHint();

  // 欢迎语：扫码来的说清楚「你在手机上打开的是登录页」，
  // 其余情况维持原来的产品文案。
  const sub = document.getElementById("auth-sub");
  if (sub && QR_TICKET && isMobileDevice()) {
    sub.textContent = "已打开登录页，请登录或注册";
  }

  // 扫码到达的横幅：直接复用扫码头文案位，不额外占用垂直空间。
  if (QR_TICKET && isMobileDevice()) {
    const hint = document.getElementById("scan-hint");
    if (hint) hint.textContent = "已打开登录页，请在下方登录或注册";
  }
}

initAuthView();

/* 在页脚显示**后端自报**的构建标记。
 *
 * 为什么值得单独做这一块：
 *   这个项目交付方式是「发压缩包、用户解压覆盖」。改完 bug 告诉用户
 *   「重新解压再用」之后，用户其实**没有任何办法确认自己换成功了** ——
 *   新旧版本的界面长得一模一样。于是就出现了「我明明换过了、还是不行」
 *   这种来回拉扯（表格导入报错就来回折腾过三次）。
 *
 *   现在后端的 /api/ping 会自报 build 与 features，登录页把它显示出来：
 *   用户打开页面就能看到「后端构建 …」，一眼确认是不是新版。
 *   万一还是旧版，页面上会明确写「旧版」并提示要重启后端。
 *
 *   ⚠️ 为什么还要把「前端自己的构建号」一起显示出来（2026-09-26 补）：
 *   真实事故 —— 用户换包后、旧文件也删了、旧窗口也关了，页脚却仍显示
 *   更早的构建号（2026-09-26-c），看起来就像「包没换成功」。
 *   实际上新代码在跑，只是**浏览器把旧的 login.html 缓存住了**，
 *   根本没去服务器拿新页面。只显示后端版本时，这种情况完全看不出来
 *   （因为后端版本是实时请求来的，是对的；错的是这个页面本身）。
 *   把前端自己的号也摆出来，两边一比就能立刻区分「后端旧」还是「页面旧」。
 */
async function showBackendBuild() {
  const el = document.getElementById("backend-build");
  if (!el) return;

  // 前端自己的构建号：写死在这个页面里，页面被缓存 -> 这个号就是旧的。
  // 所以它其实是「这个页面有多旧」的证据。
  const feBuild = (() => {
    const t = document.querySelector(".build-tag");
    if (!t) return "";
    const m = /(\d{4}-\d{2}-\d{2}-[a-z])/.exec(t.textContent || "");
    return m ? m[1] : "";
  })();

  try {
    await resolveApiBase();          // 复用已有的探测，不额外发请求
    const info = (typeof getApiInfo === "function") ? getApiInfo() : null;
    const build = info && info.build;
    const feats = (info && info.features) || [];

    // 情况一：前后端构建号不一致 —— 最可能的原因就是「这个页面是缓存里的旧页面」。
    // 这条判定比后面所有情况都更该优先说，因为它是最容易被误判的一种。
    if (build && feBuild && build !== feBuild) {
      el.hidden = false;
      el.className = "backend-build is-old";
      el.textContent =
        `页面版本对不上：这个页面是 ${feBuild}，后端是 ${build}。` +
        `请按 Ctrl+F5（强刷）重新加载本页；若仍如此，说明浏览器缓存了旧页面。`;
      return;
    }

    if (!build) {
      // 老后端没有 build 字段 —— 说明跑的是旧代码，这本身就是最有用的信息
      el.hidden = false;
      el.className = "backend-build is-old";
      el.textContent = "后端是旧版，请重新解压最新的压缩包并重启后端";
      return;
    }
    el.hidden = false;
    el.className = "backend-build";
    // 显示关键特性，便于确认「周次」这类新功能确实已生效
    const tag = feats.includes("weeks") ? "含周次" : "";
    el.textContent = `后端构建 ${build}${tag ? "（" + tag + "）" : ""}`;
  } catch {
    // 探测失败不打扰用户：这只是个诊断信息，不该影响登录
    el.hidden = true;
  }
}

showBackendBuild();

/* 页面被切到后台时暂停轮询、切回来恢复 —— 省电，也避免电脑端
   在锁屏期间把票据轮询到过期还没提示。 */
document.addEventListener("visibilitychange", () => {
  if (MODE !== "scan") return;
  if (document.hidden) {
    stopPolling();
  } else if (currentTicket) {
    startPolling();
  }
});
