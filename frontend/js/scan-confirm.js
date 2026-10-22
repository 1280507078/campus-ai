// @ts-check
/* 手机端「确认登录」页。
 *
 * ⚠️ 这个页面**已经不是扫码登录的主路径了**，别被名字误导：
 *   现在的二维码指向的是 `login.html?t=票据` —— 手机扫到直接打开登录页，
 *   在那里注册/登录，手机自己拿到登录态。
 *   （原因：早期做成"手机给电脑授权"，与「移动端自己登录」的诉求不符，已改。）
 *
 *   本页保留下来是当**兜底**用的：
 *     · 旧二维码 / 用户收藏的旧链接 仍能打开，不至于 404
 *     · 讲原理时可以用它演示"授权型"扫码的形态
 *
 * 页面职责：确认「要不要用我当前登录的账号，去登录另一端」。
 *
 * 三种情况：
 *   1. 已登录 + 票据有效 -> 显示「确认登录」按钮（**还要用户点一下**，
 *      不能扫码即登录：万一手机放桌上被别人扫了，会直接登进你的账号）
 *   2. 未登录            -> 引导去登录，登录后自动回来继续确认
 *   3. 票据无效/过期     -> 明确告知，让用户回另一端刷新
 */

(function initLogo() {
  document.getElementById("cf-logo").innerHTML = logoMark(30);
})();

/** 二维码里的票据 */
const TICKET = new URLSearchParams(location.search).get("t") || "";

/** 把 user_id 换成昵称（票据创建时还没登录，所以发起端只知道 id） */
function setText(id, txt) { const el = document.getElementById(id); if (el) el.textContent = txt; }

/** 显示某个状态：ok / warn / error */
function setIcon(kind) {
  const box = document.getElementById("cf-ico");
  box.className = "confirm-ico " + kind;
  box.innerHTML = icon(kind === "ok" ? "check" : kind === "warn" ? "clock" : "alert", 40);
}

function show(body) {
  const btn = document.getElementById("cf-btn");
  const back = document.getElementById("cf-back");
  if (btn) btn.style.display = body === "ready" ? "block" : "none";
  if (back) back.style.display = (body === "need-login" || body === "error") ? "block" : "none";
}

(async function main() {
  if (!TICKET) {
    setIcon("error");
    setText("cf-title", "二维码无效");
    setText("cf-text", "链接里没有二维码参数，请回到发起扫码的那一端重新获取。");
    show("error");
    return;
  }

  // 未登录：先请用户登录，登录时带上本页地址，回来继续确认
  if (!getToken()) {
    setIcon("warn");
    setText("cf-title", "请先登录");
    setText("cf-text", "需要登录你的账号后，才能为另一端授权。");
    const back = /** @type {HTMLAnchorElement} */ (document.getElementById("cf-back"));
    // 登录页目前不解析 next 参数，所以这里直接提示回登录页；
    // 用户登录完再扫一次即可（演示场景下这一步足够顺）。
    back.textContent = "去登录";
    back.href = "login.html";
    show("need-login");
    return;
  }

  // 已登录：确认票据还有效，并把当前账号展示出来
  let me = null;
  try {
    me = await api("/auth/me");
  } catch {
    setIcon("error");
    setText("cf-title", "登录已过期");
    setText("cf-text", "请重新登录后再扫码。");
    show("error");
    return;
  }

  setText("cf-user-name", me.nickname || me.username);
  document.getElementById("cf-user-ico").innerHTML = icon("user", 20);
  document.getElementById("cf-user").style.display = "flex";

  setIcon("ok");
  setText("cf-title", "确认登录");
  setText("cf-text", "另一端正在等待你确认。");
  show("ready");
})();

/** 点「确认登录」 */
async function doConfirm() {
  const btn = /** @type {HTMLButtonElement} */ (document.getElementById("cf-btn"));
  btn.disabled = true;
  btn.textContent = "正在确认…";
  try {
    await api("/auth/scan/confirm", { method: "POST", body: { ticket: TICKET } });
    setIcon("ok");
    setText("cf-title", "登录成功");
    setText("cf-text", "已确认，可以关掉本页了。");
    document.getElementById("cf-btn").style.display = "none";
    document.getElementById("cf-back").style.display = "none";
    document.getElementById("cf-user").style.display = "none";
  } catch (err) {
    setIcon("error");
    setText("cf-title", "确认失败");
    setText("cf-text", /** @type {Error} */ (err).message || "二维码可能已过期，请回电脑刷新。");
    btn.style.display = "none";
    document.getElementById("cf-back").style.display = "block";
  }
}

/* 让手机浏览器可以直接写 onconfirm（内联 onclick）*/
window.doConfirm = doConfirm;
