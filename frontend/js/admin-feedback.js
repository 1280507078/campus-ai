// @ts-check
/* 管理员·用户反馈页：收到的反馈列表 + 筛选 + 逐条回复。
 *
 * ## 为什么和数据分成两个页面
 *   原句：「把数据和反馈分成两个页面，不要在一个页面」。
 *   原来两块都塞在 admin.html 里（大屏在上、列表在下），点「反馈」只是滚下去 ——
 *   一页又高又杂，也不好单独把反馈这一页发给别人看。
 *   现在：admin.html = 数据；本页 = 反馈。顶栏两项各指向各的页面。
 *
 * ## 口径（和之前一致）
 *   1. **权限只在后端**：这里问一次 /auth/me 只是为了别把界面摆给普通用户，
 *      真正的门是 /api/admin/feedback 上的 get_admin_user。
 *   2. **回复是单向的**：管理员回一条，用户在「我的反馈」里看到。
 *      不做来回对话（理由见 backend/routers/feedback.py）。
 *   3. **统计数字不在这里算**：本页只关心列表。
 */

renderTopbar("feedback");
hydrateIcons();

if (!getToken()) location.href = "login.html";

const KIND_LABELS = { suggest: "功能建议", bug: "问题反馈", ux: "使用体验", other: "其他" };

/** 当前筛选：""=全部 / pending / replied */
let fbOnly = "";
/** 当前列表缓存 */
let fbRows = [];

const el = (id) => document.getElementById(id);

/** ISO（UTC）-> 本地「10 月 19 日 19:30」 */
function adTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ============ 1. 权限闸门 ============ */
async function gate() {
  try {
    const me = await api("/auth/me");
    setUser(me);                        // 顺便把 is_admin 写回本地，供「我的」页用
    if (!me || !me.is_admin) {
      el("ad-denied-msg").textContent =
        `当前登录的是「${(me && (me.nickname || me.username)) || "未知账号"}」，不是管理员账号。`;
      el("ad-denied").hidden = false;
      return false;
    }
    el("ad-fb").hidden = false;
    return true;
  } catch (e) {
    el("ad-denied-msg").textContent = "取不到登录状态：" +
      (/** @type {Error} */ (e).message || "请重新登录后再试");
    el("ad-denied").hidden = false;
    return false;
  }
}

/* ============ 2. 列表 + 回复 ============ */
function fbItemHtml(f) {
  const replied = !!(f.reply && String(f.reply).trim());
  const kind = KIND_LABELS[f.kind] || KIND_LABELS.other;
  return `
    <article class="fb-item" data-id="${f.id}">
      <div class="fb-head">
        <span class="fb-kind">${escapeHtml(kind)}</span>
        <span class="fb-state ${replied ? "is-replied" : ""}">${replied ? "已回复" : "待回复"}</span>
        <span class="fb-user">@${escapeHtml(f.username || "（已注销）")}</span>
        <span class="fb-time muted">${escapeHtml(adTime(f.createdAt))}</span>
      </div>
      <p class="fb-content">${escapeHtml(f.content || "")}</p>
      ${f.contact ? `<p class="fb-contact muted">联系方式：${escapeHtml(f.contact)}</p>` : ""}
      ${replied ? `<div class="fb-reply">
          <div class="fb-reply-head"><span class="h-ico" data-icon="inbox" data-icon-size="15"></span> 我的回复</div>
          <p>${escapeHtml(f.reply)}</p>
          <p class="muted fb-reply-time">${escapeHtml(adTime(f.repliedAt))}</p>
        </div>` : ""}
      <div class="ad-reply-box" hidden>
        <textarea rows="3" maxlength="500" placeholder="写一句回复，用户在「我的反馈」里能看到">${
          escapeHtml(f.reply || "")}</textarea>
        <div class="ad-reply-actions">
          <button class="btn sm" data-act="save" type="button">保存回复</button>
          <button class="btn ghost sm" data-act="cancel" type="button">取消</button>
          <span class="muted ad-reply-tip">${replied ? "清空后保存 = 撤回回复" : ""}</span>
        </div>
      </div>
      <button class="btn ghost sm ad-reply-open" data-act="open" type="button">
        <span class="h-ico" data-icon="chat" data-icon-size="15"></span> ${replied ? "修改回复" : "回复"}
      </button>
    </article>`;
}

function renderFeedback() {
  const box = el("ad-feedback");
  if (!fbRows.length) {
    box.innerHTML = `
      <div class="empty">
        <div class="e-ico" data-icon="inbox" data-icon-size="32"></div>
        <p>${fbOnly ? "这个筛选下没有反馈。" : "还没有用户提交反馈。"}</p>
        <p class="muted">用户可以在「我的 → 意见反馈」里提交，提交后会出现在这里。</p>
      </div>`;
    hydrateIcons();
    return;
  }
  box.innerHTML = fbRows.map(fbItemHtml).join("");
  hydrateIcons();
}

async function loadFeedback() {
  const q = "/admin/feedback?limit=100" + (fbOnly ? "&only=" + encodeURIComponent(fbOnly) : "");
  try {
    const rows = await api(q);
    fbRows = Array.isArray(rows) ? rows : [];
    renderFeedback();
  } catch (e) {
    el("ad-feedback").innerHTML = `<p class="muted" style="margin:0;">反馈列表取不到：${
      escapeHtml(/** @type {Error} */ (e).message || "请稍后重试")}</p>`;
  }
}

/* 事件代理：列表重渲染后不用重新绑事件 */
el("ad-feedback").addEventListener("click", async (ev) => {
  const btn = /** @type {HTMLElement} */ (ev.target).closest("[data-act]");
  if (!btn) return;
  const card = btn.closest(".fb-item");
  if (!card) return;
  const id = Number(card.getAttribute("data-id"));
  const box = card.querySelector(".ad-reply-box");
  const act = btn.getAttribute("data-act");

  if (act === "open" && box) {
    box.hidden = false;
    const ta = box.querySelector("textarea");
    if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    return;
  }
  if (act === "cancel" && box) {
    box.hidden = true;
    return;
  }
  if (act === "save" && box) {
    const ta = /** @type {HTMLTextAreaElement} */ (box.querySelector("textarea"));
    const reply = (ta.value || "").trim();
    const saveBtn = /** @type {HTMLButtonElement} */ (btn);
    saveBtn.disabled = true;
    try {
      await api("/admin/feedback/" + id, { method: "PATCH", body: { reply } });
      toast(reply ? "回复已保存，用户能看到" : "已撤回回复", "ok");
      await loadFeedback();
    } catch (e) {
      toast(/** @type {Error} */ (e).message || "保存失败，请稍后重试", "error");
    } finally {
      saveBtn.disabled = false;
    }
  }
});

/* 筛选 */
el("ad-filters").addEventListener("click", (ev) => {
  const btn = /** @type {HTMLElement} */ (ev.target).closest("[data-only]");
  if (!btn) return;
  fbOnly = btn.getAttribute("data-only") || "";
  el("ad-filters").querySelectorAll("[data-only]").forEach((b) => {
    b.classList.toggle("btn-on", b === btn);
  });
  loadFeedback();
});

el("fb-refresh").addEventListener("click", async () => {
  toast("正在刷新…", "info");
  await loadFeedback();
  setLive("ok");
});

/* ---------- 实时 ----------
 * ★ 需求：「反馈的刷新改的跟数据一样，要立即刷新」。
 *   所以这里跟 admin.js（数据页）用同一套：绿灯 + 最后刷新时间 + 每 10 秒自动拉，
 *   页面切到后台时暂停（省流量，回来也不会挤一堆过期请求）。
 */
const REFRESH_MS = 10000;
let timer = null;

function setLive(state) {
  const box = el("adm-live");
  if (box) box.setAttribute("data-state", state);
  const t = el("adm-live-text");
  if (!t) return;
  if (state !== "ok") {
    t.textContent = state === "err" ? "取数失败，一会儿自动重试" : "连接中…";
    return;
  }
  const d = new Date();
  const p2 = (n) => String(n).padStart(2, "0");
  t.textContent = `实时 · 最后一次 ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}

async function tick() {
  await loadFeedback();
  // loadFeedback 失败时会在列表里写一句"取不到"，灯就别装成功
  const bad = (el("ad-feedback").textContent || "").includes("取不到");
  setLive(bad ? "err" : "ok");
}

function startRealtime() {
  stopRealtime();
  timer = setInterval(() => {
    if (document.visibilityState === "visible") tick();
  }, REFRESH_MS);
}
function stopRealtime() {
  if (timer) { clearInterval(timer); timer = null; }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") tick();   // 切回来先补一次
});

/* ============ 启动 ============ */
(async function start() {
  if (!(await gate())) return;
  await tick();
  startRealtime();
})();
