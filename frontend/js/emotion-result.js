/* 测评结果页：从 localStorage 读取答卷结果并渲染详细报告。
   首次进入若无数据，展示空状态引导去做测评。 */
requireAuth();

const TONE_LABEL = { high: "偏高", mid: "中等", low: "较低" };
const TONE_COLOR = { high: "var(--danger)", mid: "var(--warn)", low: "var(--ok)" };

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, "0");
  return `测评时间：${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function render() {
  const r = loadQuizResult();
  if (!r) {
    document.getElementById("empty-card").hidden = false;
    document.getElementById("result-main").hidden = true;
    return;
  }

  document.getElementById("empty-card").hidden = true;
  document.getElementById("result-main").hidden = false;
  document.getElementById("result-time").textContent = fmtTime(r.at);

  const advice = quizAdvice(r);

  /* ---- 总体结论 ----
     ⚠️ 左边这块的 min-width 必须是 0（2026-10-18 修手机端被裁）：
        原来写死 `min-width:220px`，窄屏上它 + 右边分数块（约 90px）+ 间距
        一旦超过卡片内宽，整页就会被撑出横向滚动条 ——
        用户在手机上看到的是「标题左边被切、右边『偏高』标签跑到卡片外」，
        而且因为能横向滑，他会滑到中间，两边都不完整。
        `flex: 1 1 220px` 保留桌面端的起始宽度，但**允许收缩**。 */
  document.getElementById("result-hero").innerHTML = `
    <div class="spread result-hero-row" style="align-items:flex-start;">
      <div style="flex:1 1 220px; min-width:0;">
        <div class="rh-title">${escapeHtml(advice.headline)}</div>
        <p class="rh-body">${escapeHtml(advice.body)}</p>
      </div>
      <div class="rh-score">
        <div class="rh-num" style="color:${TONE_COLOR[advice.tone]}">${r.overallScore}</div>
        <div class="rh-max" style="color:${TONE_COLOR[advice.tone]}">/ ${r.max}</div>
        <div class="tag" style="margin-top:6px;">${escapeHtml(advice.tone === "high" ? "偏高" : advice.tone === "mid" ? "中等" : "较低")}</div>
      </div>
    </div>`;

  /* ---- 两个维度 ---- */
  document.getElementById("dim-dep").innerHTML = dimBlock(
    "抑郁维度", "PHQ-9 简版", "emotion", r.depression, r.depressionTone,
    ["情绪低落、兴趣减退、睡眠与精力相关的 4 道题"],
  );
  document.getElementById("dim-anx").innerHTML = dimBlock(
    "焦虑维度", "GAD-7 简版", "brain", r.anxiety, r.anxietyTone,
    ["紧张不安、难以控制的担忧相关的 4 道题"],
  );

  /* ---- 三件小事 ---- */
  const actions = quizActions(r);
  document.getElementById("result-actions").innerHTML = actions.map((a, i) => `
    <div class="action-row">
      <span class="action-no">${i + 1}</span>
      <span>${escapeHtml(a)}</span>
    </div>`).join("");

  /* ---- 专业性帮助提示 ---- */
  document.getElementById("result-help").innerHTML = quizNeedHelp(r)
    ? `<div class="alert error">
         <span class="h-ico" data-icon="alert" data-icon-size="18"></span>
         <div>
           <b>你不是一个人在面对这些。</b><br>
           建议联系学校心理健康中心进行专业评估，或拨打全国心理援助热线
           <b>400-161-9995</b>（24 小时）。如果需要紧急帮助，请直接联系辅导员或就近的医疗机构。
         </div>
       </div>`
    : `<div class="alert ok">
         <span class="h-ico" data-icon="check" data-icon-size="18"></span>
         <div>目前没有明显的预警信号。定期做一次自评，有助于及早发现情绪的变化。</div>
       </div>`;

  hydrateIcons();
}

/** 单个维度的卡片：分数 + 进度条 + 分级 */
function dimBlock(title, sub, iconName, score, tone) {
  const pct = Math.round((score / QUIZ_DIM_MAX) * 100);
  return `
    <div class="dim-head">
      <span class="h-ico" data-icon="${iconName}" data-icon-size="20"></span>
      <div>
        <div class="dim-title">${title}</div>
        <div class="muted" style="font-size:12.5px;">${sub}</div>
      </div>
      <span class="dim-score" style="color:${TONE_COLOR[tone]}">${score}<small>/${QUIZ_DIM_MAX}</small></span>
    </div>
    <div class="bar" style="margin-top:12px;">
      <i style="width:${pct}%; background:${TONE_COLOR[tone]}"></i>
    </div>
    <div class="spread" style="margin-top:8px;">
      <span class="muted">0 分</span>
      <span class="tag">${TONE_LABEL[tone]}</span>
      <span class="muted">${QUIZ_DIM_MAX} 分</span>
    </div>`;
}

/* ---------- 启动 ---------- */
renderTopbar("emotion");
render();
