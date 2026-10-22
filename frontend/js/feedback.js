// @ts-check
/* 意见反馈页：提交一条 + 看自己提交过的（含管理员回复）。
 *
 * ## 为什么单独一页
 * 用户 2026-10-19 的要求：「在个人界面底部点按钮，跳到新页面填写」。
 * 「我的」页已经有四张卡，再嵌一个多行输入框会变成设置表单；
 * 反馈是要写字的事，单独一页能给足空间，也能把「我提交过的」放在下面。
 *
 * ## 三条口径
 *   1. **长度以后端为准**：前端 `maxlength` 只是提示，F12 能改掉；
 *      所以提交失败时**原样显示后端那句话**，不自己另编一套文案。
 *   2. 时间：后端给的是 UTC（带 Z），这里用 `new Date(iso)` 转本地再显示。
 *      直接截字符串会把「19:30 提交」显示成「11:30」。
 *   3. 回复是**单向**的（管理员回、用户看），所以没有输入框 ——
 *      要追问就再提一条。理由见后端 routers/feedback.py 顶部。
 */

renderTopbar("feedback");
hydrateIcons();

if (!getToken()) location.href = "login.html";

/* ★ 管理员不该在这里「提交反馈」——他看的是**收到的**反馈。
   这一页是给用户的：写一条 + 看自己提过的（含回复）。
   管理员从顶栏点「反馈」进的是 admin-feedback.html（收到的反馈，列表 + 回复）。
   万一他直接敲了这个地址，就送回去，别让他面对一个"给谁提意见"的表单。 */
(function adminBelongsElsewhere() {
  const u = getUser();
  if (u && u.is_admin) location.replace("admin-feedback.html");
})();

const FB_KINDS = {
  suggest: "功能建议",
  bug: "问题反馈",
  ux: "使用体验",
  other: "其他",
};
const FB_MAX = 1000;

/** ISO（UTC，带 Z）-> 本地「10 月 19 日 19:30」；解析不出来就原样返回 */
function fbTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 一条反馈的 HTML。正文与回复都要 escape —— 它们是用户/管理员自由输入的。 */
function fbItemHtml(f) {
  const kind = FB_KINDS[f.kind] || FB_KINDS.other;
  const replied = !!(f.reply && String(f.reply).trim());
  return `
    <article class="fb-item">
      <div class="fb-head">
        <span class="fb-kind">${escapeHtml(kind)}</span>
        <span class="fb-state ${replied ? "is-replied" : ""}">${replied ? "已回复" : "待回复"}</span>
        <span class="fb-time muted">${escapeHtml(fbTime(f.createdAt))}</span>
      </div>
      <p class="fb-content">${escapeHtml(f.content || "")}</p>
      ${replied
        ? `<div class="fb-reply">
             <div class="fb-reply-head"><span class="h-ico" data-icon="inbox" data-icon-size="15"></span> 管理员回复</div>
             <p>${escapeHtml(f.reply)}</p>
             <p class="muted fb-reply-time">${escapeHtml(fbTime(f.repliedAt))}</p>
           </div>`
        : `<p class="muted fb-wait">还没有回复。管理员看到后会回复在这里。</p>`}
    </article>`;
}

/** 拉取「我的反馈」并渲染 */
async function loadMine() {
  const box = document.getElementById("fb-list");
  if (!box) return;
  try {
    const rows = await api("/feedback/mine?limit=20");
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) {
      box.innerHTML = `
        <div class="empty">
          <div class="e-ico" data-icon="inbox" data-icon-size="32"></div>
          <p>还没有提交过反馈。</p>
          <p class="muted">上面写一条，提交后就会出现在这里；管理员回复也会显示在这里。</p>
        </div>`;
      hydrateIcons();
      return;
    }
    box.innerHTML = list.map(fbItemHtml).join("");
    hydrateIcons();
  } catch (e) {
    // 拉不到历史不该影响提交 —— 只在列表区说一句
    box.innerHTML = `<p class="muted" style="margin:0;">历史反馈暂时取不到：${
      escapeHtml(/** @type {Error} */ (e).message || "请稍后重试")}</p>`;
  }
}

/* ---------- 字数统计 ---------- */
const contentEl = /** @type {HTMLTextAreaElement} */ (document.getElementById("fb-content"));
const countEl = document.getElementById("fb-count");
if (contentEl && countEl) {
  const sync = () => { countEl.textContent = String(contentEl.value.length); };
  contentEl.addEventListener("input", sync);
  sync();
}

/* ---------- 提交 ---------- */
const form = /** @type {HTMLFormElement} */ (document.getElementById("fb-form"));
form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const kindEl = /** @type {HTMLSelectElement} */ (document.getElementById("fb-kind"));
  const contactEl = /** @type {HTMLInputElement} */ (document.getElementById("fb-contact"));
  const btn = /** @type {HTMLButtonElement} */ (document.getElementById("fb-submit"));

  const content = (contentEl.value || "").trim();
  // 前端先挡一道「空」，长度细节交给后端判（口径只留一处）
  if (!content) {
    toast("先写点内容再提交吧", "error");
    contentEl.focus();
    return;
  }
  if (content.length > FB_MAX) {
    toast(`最多 ${FB_MAX} 字，把最关键的一段留下就好`, "error");
    return;
  }

  btn.disabled = true;
  try {
    await api("/feedback", {
      method: "POST",
      body: {
        kind: kindEl.value,
        content,
        contact: (contactEl.value || "").trim(),
        page: "feedback",
      },
    });
    contentEl.value = "";
    contactEl.value = "";
    if (countEl) countEl.textContent = "0";
    toast("已提交，谢谢你的反馈", "ok");
    await loadMine();
    // 提交完把视线带到「我的反馈」，让用户确认真的进去了
    document.getElementById("fb-history")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    // 后端的原话（比如「再多写几个字吧」）比前端编一句更有用
    toast(/** @type {Error} */ (e).message || "提交失败，请稍后重试", "error");
  } finally {
    btn.disabled = false;
  }
});

loadMine();
