// @ts-check
/* 账号设置页：改昵称 / 改密码 / 注销账号。 */

renderTopbar("account");
hydrateIcons();

/** 当前用户（从 /auth/me 拉最新的，避免 localStorage 里是旧昵称） */
let me = getUser();

function el(id) { return document.getElementById(id); }

/* 未登录直接踢回登录页 */
if (!getToken()) {
  location.href = "login.html";
}

/* ---------- 初始化：显示用户名与昵称 ---------- */
async function init() {
  if (me) fill(me);
  try {
    const fresh = await api("/auth/me");
    me = fresh;
    setUser(fresh);
    fill(fresh);
  } catch {
    /* 网络异常就用本地缓存，不打断页面 */
  }
}

function fill(u) {
  el("acc-username").textContent = u.username || "—";
  el("acc-nickname").value = u.nickname || u.username || "";

  // 管理员：个人页只留「账号相关」的东西。
  //   · 意见反馈 —— 他是**收**反馈的那一方，整张不显示；
  //   · 视角切换卡片 —— 跟着当前视角反过来写（见下面的 paintView）。
  // ⚠️ 这只是**显示开关**，权限在后端（改 localStorage 让按钮出现也没用，接口照样 403）。
  const isAdmin = !!(u && u.is_admin);
  const fbCard = el("card-feedback");
  if (fbCard) fbCard.hidden = isAdmin;
  const viewCard = el("card-view");
  if (viewCard) viewCard.hidden = !isAdmin;
  if (isAdmin) paintView();
}

/* ---------- 视角切换卡片 ----------
 * 「当前在哪个视角」由 common.js 在每页加载时写进 localStorage.adminView
 * （admin.html / admin-feedback.html 记 admin，其它页面记 user）。
 * 不这么记的话，个人页没法知道自己是从哪边点进来的 —— URL 上看不出来。
 */
function paintView() {
  const inAdmin = (localStorage.getItem("adminView") || "admin") === "admin";
  const title = el("card-view-title");
  const note = el("card-view-note");
  const link = /** @type {HTMLAnchorElement} */ (el("card-view-link"));
  const label = el("card-view-label");
  if (!title || !note || !link || !label) return;

  if (inAdmin) {
    title.textContent = "用户视图";
    note.textContent = "你现在在管理视角。想看看用户那边长什么样，从这里进去；回来随时再点一次。";
    link.href = "index.html?view=user";
    label.textContent = "进入用户视图";
  } else {
    title.textContent = "管理员视图";
    note.textContent = "你现在在用户视角。要回数据大屏和收到的反馈，从这里进去。";
    link.href = "admin.html";
    label.textContent = "进入管理员视图";
  }
}

/* ---------- 改昵称 ---------- */
el("btn-save-nick").onclick = async function () {
  const nick = el("acc-nickname").value.trim();
  if (nick.length < 2) { toast("昵称至少 2 个字符", "error"); return; }

  const btn = /** @type {HTMLButtonElement} */ (el("btn-save-nick"));
  btn.disabled = true;
  try {
    const r = await api("/auth/profile", { method: "PATCH", body: { nickname: nick } });
    // 同步本地用户信息 + 顶栏显示名，否则用户以为没生效
    me = Object.assign({}, me, { nickname: r.nickname });
    setUser(me);
    renderTopbar("account");
    hydrateIcons();
    toast(r.unchanged ? "昵称没有变化" : "昵称已更新", "ok");
  } catch (e) {
    toast(/** @type {Error} */ (e).message, "error");
  } finally {
    btn.disabled = false;
  }
};

/* ---------- 改密码 ---------- */
const hint = el("acc-pwd-hint");
function checkMatch() {
  const a = /** @type {HTMLInputElement} */ (el("acc-new-pwd")).value;
  const b = /** @type {HTMLInputElement} */ (el("acc-new-pwd2")).value;
  if (!b) { hint.textContent = ""; hint.className = "pw-hint"; return true; }
  const ok = a === b;
  hint.textContent = ok ? "两次输入一致" : "两次输入的密码不一致";
  hint.className = "pw-hint " + (ok ? "ok" : "bad");
  return ok;
}
el("acc-new-pwd").addEventListener("input", checkMatch);
el("acc-new-pwd2").addEventListener("input", checkMatch);

el("btn-save-pwd").onclick = async function () {
  const oldPwd = /** @type {HTMLInputElement} */ (el("acc-old-pwd")).value;
  const newPwd = /** @type {HTMLInputElement} */ (el("acc-new-pwd")).value;
  if (!oldPwd) { toast("请输入原密码", "error"); return; }
  if (newPwd.length < 6) { toast("新密码至少 6 位", "error"); return; }
  if (!checkMatch()) { toast("两次输入的新密码不一致", "error"); return; }

  const btn = /** @type {HTMLButtonElement} */ (el("btn-save-pwd"));
  btn.disabled = true;
  try {
    await api("/auth/password", {
      method: "POST",
      body: { old_password: oldPwd, new_password: newPwd },
    });
    el("acc-old-pwd").value = "";
    el("acc-new-pwd").value = "";
    el("acc-new-pwd2").value = "";
    hint.textContent = ""; hint.className = "pw-hint";
    toast("密码已修改，下次登录请用新密码", "ok");
  } catch (e) {
    toast(/** @type {Error} */ (e).message, "error");
  } finally {
    btn.disabled = false;
  }
};

/* ---------- 注销账号（弹窗输入密码确认） ---------- */
el("btn-delete").onclick = function () {
  const name = (me && (me.nickname || me.username)) || "当前账号";
  el("modal-title").textContent = "注销账号";
  el("modal-body").innerHTML = `
    <p class="muted" style="margin-top:0;">
      即将注销 <b>${escapeHtml(name)}</b>，账号下的课表、情绪记录、测评结果会一并删除，<b>无法恢复</b>。
    </p>
    <div class="field">
      <label>请输入密码确认</label>
      <input id="del-pwd" type="password" placeholder="输入当前密码" />
    </div>
    <div class="row" style="gap:10px;">
      <button class="btn ghost" style="flex:1" id="del-cancel" type="button">取消</button>
      <button class="btn danger" style="flex:1" id="del-ok" type="button">确认注销</button>
    </div>`;
  el("modal").classList.add("show");

  el("del-cancel").onclick = () => el("modal").classList.remove("show");
  el("del-ok").onclick = async function () {
    const pwd = /** @type {HTMLInputElement} */ (el("del-pwd")).value;
    if (!pwd) { toast("请输入密码", "error"); return; }
    const ok = /** @type {HTMLButtonElement} */ (el("del-ok"));
    ok.disabled = true;
    try {
      await api("/auth/account", /** @type {any} */ ({ method: "DELETE", body: { password: pwd } }));
      el("modal").classList.remove("show");
      clearToken();
      sessionStorage.setItem("login_msg", "账号已注销，感谢使用");
      location.href = "login.html";
    } catch (e) {
      toast(/** @type {Error} */ (e).message, "error");
      ok.disabled = false;
    }
  };
};

/* 点遮罩或 × 关闭弹窗 */
el("modal-close").onclick = () => el("modal").classList.remove("show");
el("modal").addEventListener("click", e => {
  if (/** @type {HTMLElement} */ (e.target).id === "modal") el("modal").classList.remove("show");
});

init();
