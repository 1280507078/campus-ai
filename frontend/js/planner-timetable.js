// @ts-check
/* 学业规划 · 课表子页（学业规划的主入口）
   ------------------------------------------------------------------
   本文件是原 planner.js 里「课表相关」部分的归位处：
     · 表格渲染（周次条 / 节次列 / 课程卡片 / 南北区作息高亮）
     · 课程与活动的增删改（详情弹窗 / 表单）

     · 课表截图导入（走 AI）+ JSON/CSV 导入（本地解析）+ 结果核对弹窗

   依赖 planner-core.js —— 必须先引 core，再引本文件。

   ⚠️ 所有「可能不在本页」的元素都走 onEl / onElBind。
   本页是唯一三个弹窗（#modal / #scan-mask / #review-mask）和课表 DOM 齐全的页面，
   但为了和另外两页保持同一种写法（也防止以后有人把函数挪走），
   统一用安全取元素的习惯，不写裸 getElementById。
   ================================================================== */

/* ---------- 课表渲染 ---------- */
function renderHead() {
  const dates = weekDates();
  const today = todayIso();
  onEl("tt-head").innerHTML = `<tr>
    <th>节次＼星期</th>
    ${dates.map((d, i) => {
      const isToday = d === today;
      return `<th class="${isToday ? "is-today" : ""}">${DAYS[i]}<br>
        <span class="th-date">${fmtMD(d)}${isToday ? " · 今天" : ""}</span></th>`;
    }).join("")}
  </tr>`;
}

function renderGrid() {
  renderHead();
  const dates = weekDates();
  const body = onEl("tt-body");
  body.innerHTML = "";

  // ---- 按周过滤 + 「每周重复」归位 ----
  //
  // ⚠️ 这里有个**必须理解**的数据模型冲突，不搞清会写出「课莫名消失」的 bug：
  //
  //   库里的课是按**具体日期**存的（`2026-09-08`），不是「周二」。
  //   而教务表格给的是「周二第1节 + 每周都上」——一门课要出现在**每一周**。
  //
  //   两者对不上：存成 9/8 的课，翻到第 2 周（9/15）时就匹配不到那个格子了，
  //   表现是「每周都上的课，翻到下一周就没了」。
  //
  //   解法（本节核心）：渲染当前周时，把课程按**星期**重新对号入座 ——
  //   只要这门课本周要上（courseShownInWeek），就把它放到本周对应的那一天。
  //   于是「每周都上」的课每周都出现，「只在第 2 周」的课只在第 2 周出现。
  //
  //   为什么不用「导入时按周展开成 N 条记录」：库里有唯一约束
  //   uq_user_date_slot(user_id,date,slot_order)，同一节次同一天只能一条，
  //   展开会大面积撞约束；而且展开后用户改一次课要改 N 条，改不干净。
  // ⚠️ 判「本周上不上」用的是**当前浏览的周次**，不是课程自己存的那个日期所属的周。
  //    用课程自己的日期会退化成「永远都上」（因为它无论如何都属于某一周），
  //    这正是本节要避免的。当前周次从 weekDates() 的第一天推。
  const curWeek = weekNumberByDate(dates[0]);
  const shown = courses.filter(c => courseShownInWeek(c, curWeek));

  // 本周每一列（周日..周六）对应的日期 -> 落在这一列的课
  const byDate = {};
  dates.forEach(d => (byDate[d] = []));
  shown.forEach(c => {
    // 把课挂到「本周同星期」的那一天上。
    // 用它在**本周对应的日期**，而不是它自己存的 date —— 这是「每周重复」的关键。
    const wd = weekdayOf(c.date);                     // 1..7
    const target = dates[wd - 1];
    if (!target) return;                              // 数据里星期非法，跳过
    // ⚠️ 用一份浅拷贝，只加一个渲染用的日期：
    //    绝不能直接改 c.date（那是要存库的原值，改了会把课真的挪到本周）。
    byDate[target].push(Object.assign({}, c, { _renderDate: target }));
  });

  // 课表里也要显示连堂拼接 —— 但**不能**把 _span_pos 写到 courses 上：
  // courses 是要存进库的（saveTimetable 会 serialize），多出这几个前端字段
  // 就会跟着存进后端，换个星期/刷新后对不上，数据越攒越脏。
  // 所以这里现算一份「课程 _id -> span 位置」的临时表，只用于本次渲染。
  // ⚠️ 传**本周实际要显示的**列表（带 _renderDate 的那份），
  //    否则「这周只上一节」的连堂课会留下孤零零的断口样式。
  const spanOf = computeSpanMap(Object.values(byDate).flat());

  slots.forEach(s => {
    // 该行是否含北区课程 —— 决定节次列显示哪套作息、以及是否高亮。
    // 必须用本周实际显示的列表算，否则「这周没课但别的周有北区课」也会高亮。
    const rowHasNorth = dates.some(d =>
      byDate[d].some(c => c.slot_order === s.order_no
        && c.type !== "activity" && regionOfBuilding(c.building) === "north"));

    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="slot-col">${renderSlotCell(s, rowHasNorth)}</td>`;
    dates.forEach(date => {
      const cellCourses = byDate[date].filter(c => c.slot_order === s.order_no);
      const td = document.createElement("td");
      // 一个时间点只放一条：已有内容时不再显示「＋」，避免同格堆叠。
      // 这周不上课 -> 这里自然为空 -> 显示「＋」，正是「没课就不显示」要的效果。
      td.innerHTML = `<div class="cell">
        ${cellCourses.map(c => renderChip(c, spanOf.get(c._id))).join("")}
        ${cellCourses.length ? "" : `<div class="add" title="添加课程 / 活动" onclick="openAdd(${s.order_no},'${date}')">＋</div>`}
      </div>`;
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

/**
 * 算一张「课程 _id -> {start,end,pos}」的连堂位置表（只用于渲染，不入库）。
 *
 * 判定口径与识别弹窗里的 markConsecutive 保持一致：同一天、课名相同、节次相邻。
 * 用户手动加的两门同名课如果恰好连排，也会被拼起来显示 —— 这是想要的效果，
 * 因为它们确实是连堂。
 *
 * @param {any[]} list
 * @returns {Map<string,{start:number,end:number,pos:string}>}
 */
function computeSpanMap(list) {
  const out = new Map();
  const norm = s => String(s || "").replace(/\s/g, "").trim();
  const byDate = {};
  (list || []).forEach(c => (byDate[c.date] = byDate[c.date] || []).push(c));

  Object.values(byDate).forEach(group => {
    group.sort((a, b) => (a.slot_order || 0) - (b.slot_order || 0));
    let run = [];
    const flush = () => {
      if (run.length > 1) {
        const s = run[0].slot_order, e = run[run.length - 1].slot_order;
        run.forEach((c, k) => out.set(c._id, {
          start: s, end: e,
          pos: k === 0 ? "first" : (k === run.length - 1 ? "last" : "mid"),
        }));
      }
      run = [];
    };
    group.forEach(c => {
      const prev = run[run.length - 1];
      const adjacent = prev && norm(prev.name) === norm(c.name) && norm(prev.name) !== ""
        && (c.slot_order || 0) - (prev.slot_order || 0) === 1
        && (c.type || "course") === (prev.type || "course") && c.type !== "activity";
      if (!adjacent) flush();
      run.push(c);
    });
    flush();
  });
  return out;
}

/**
 * 节次列：同时给出南区 / 北区作息，含北区课程的行高亮北区时间。
 * @param {TimeSlot} s
 * @param {boolean} rowHasNorth
 */
function renderSlotCell(s, rowHasNorth) {
  const south = slotTimeFor(s, "south");
  const north = slotTimeFor(s, "north");
  const same = south.start === north.start && south.end === north.end;
  if (same) {
    // 非节次/南北一致的整点行：时间单独一行，居中显示
    return `<b>${escapeHtml(s.label)}</b>
      <span class="slot-time-plain">${south.start}-${south.end}</span>`;
  }
  return `<b>${escapeHtml(s.label)}</b>
    <div class="slot-times">
      <span class="${rowHasNorth ? "muted" : "on"}">南 ${south.start}-${south.end}</span>
      <span class="${rowHasNorth ? "on" : "muted"}">北 ${north.start}-${north.end}</span>
    </div>`;
}

/**
 * 渲染一个课程 / 活动卡片。
 *
 * 连堂课（同一门课连排两节）在这里做**视觉合并**：
 * 第一节的卡片加 `span-first`，之后各节加 `span-cont`、最后一节加 `span-last` ——
 * 靠 CSS 把相邻两张之间的分割线抹掉、圆角拼平，看起来就是一张长卡，
 * 但数据仍是各自独立的两条，想单独改/删其中一节，点对应格子的卡片即可。
 *
 * 为什么不真合并成一条数据：两节各有各的起止时间（中间还夹着课间），
 * 合并后就分不开了，用户想调其中一节的时间都没处调。
 *
 * @param {any} c
 * @param {{start:number,end:number,pos:string}} [span] 由 computeSpanMap 现算的连堂位置
 */
function renderChip(c, span) {
  const isActivity = c.type === "activity";
  // 线上课：location 写的是「线上/慕课/网络工」这类虚拟地点，没有教学楼也没有坐标 ——
  // 卡片上挂一个「线上」小标，明确它不需要（也无法）导航。
  const isOnline = !isActivity && !String(c.building || "").trim()
    && isVirtualPlaceName(c.location);
  const place = isActivity ? (c.location || "") : (c.location || c.building || "");
  const bad = c._uncertain && c._uncertain.length;

  // 活动不参与连堂拼接（一场活动就是一个时间段）
  const pos = (!isActivity && span) ? span.pos : "solo";
  const spanCls = pos === "first" ? " span-first"
    : (pos === "last" ? " span-last" : (pos === "mid" ? " span-cont" : ""));
  const spanTip = (pos !== "solo" && span) ? `连堂：第 ${span.start}-${span.end} 节连排` : "";

  // 周次角标：把原始串（最长 60 字符）压成 "2-3,6-8,19周" 这种紧凑写法。
  // 空 weeks（手动加的课、图片识别的课）不显示角标 —— 那类就是「每周都上」，
  // 标一个「每周」反而啰嗦。
  const wkSet = parseWeeks(c.weeks);
  const wkLabel = wkSet.all ? "" : formatWeeksLabel(wkSet.weeks);
  const chipWeeks = wkLabel ? `<span class="chip-weeks">${escapeHtml(wkLabel)}</span>` : "";
  const weekTip = wkLabel ? `上课周次：第 ${wkSet.weeks.join("、")} 周` : "";

  const title = [
    bad ? "AI 识别存疑：" + c._uncertain.map(uncertainLabel).join("、") + "，点开补全" : "",
    spanTip,
    weekTip,
    isOnline ? "线上课，无需导航" : "",
  ].filter(Boolean).join("；");

  // 只有连堂的首节显示「连堂」小角标，后面的节靠 CSS 拼接，不再重复标
  const chipSpan = pos === "first" ? '<span class="chip-span">连堂</span>' : "";
  const chipOnline = isOnline ? '<span class="chip-online">线上</span>' : "";

  return `<div class="course-chip${isActivity ? " type-activity" : ""}${bad ? " need-check" : ""}${spanCls}"
    title="${escapeHtml(title)}"
    onclick="openDetail('${c._id}')">
    ${isActivity ? '<span class="chip-tag">活动</span>' : ""}${chipOnline}
    <b>${escapeHtml(c.name || "（未识别）")}</b>${chipWeeks}<br>${escapeHtml(place)}${chipSpan}</div>`;
}

/** 存疑字段名 -> 中文 */
function uncertainLabel(k) {
  return ({
    name: "课程名", building: "教学楼", location: "教室",
    slot_order: "节次", weekday: "星期", time: "时间",
    // 「日期」是「知道星期几、但没有参考周一所以算不出具体日期」，
    // 属于信息不全而非认错，单独给个说法，免得用户以为星期也错了
    date: "日期",
  })[k] || k;
}

/* ---------- 周切换（横向滚动周次条） ---------- */

/** 把周偏移翻译成「第几周」这个学期语义。
 *  现在 weekOffset 就是**相对第 1 周的偏移**（0 = 第 1 周），所以直接 +1 即可。 */
function weekNumberOf(offset) {
  return offset + 1;
}

/**
 * 渲染整条周次滚动条。
 *
 * 为什么用「滚动条 + 胶囊」而不是下拉框：
 *   1. 课表每周的长相都差不多，用户看的是「连续感」—— 左右滑一下就能对比
 *      「这周和下周的课差多少」，下拉框每次都要点开再点。
 *   2. 当前周在整学期里的位置一眼可见（胶囊条的滚动位置本身就是进度指示）。
 *   3. 触屏上横滑是肌肉记忆，比点两次下拉自然。
 */
function renderWeekPicker() {
  const box = onEl("week-scroll");
  if (!box) return;

  const today = todayIso();
  const curOff = currentWeekOffset();     // 本周在学期里是第几周（0 起算）
  const parts = [];
  for (let off = -SEM_WEEKS_BEFORE; off <= SEM_WEEKS_AFTER; off++) {
    const r = weekRange(off);
    const n = weekNumberOf(off);
    const isCur = off === weekOffset;          // 当前正在浏览
    // ⚠️ 「本周」= 今天真实落在的那一周，**不是 off === 0**。
    //    off=0 现在指「第 1 周」，学期过半时它早就不是本周了。
    const isThis = off === curOff;
    // 今天是这周里的某一天 -> 打个「今天在这周」的点
    const hasToday = today >= r.start && today <= r.end;

    const label = isThis
      ? `本周 · 第 ${n} 周 · ${fmtMD(r.start)} - ${fmtMD(r.end)}`
      : `第 ${n} 周 · ${fmtMD(r.start)} - ${fmtMD(r.end)}`;

    parts.push(
      `<button type="button" role="tab" aria-selected="${isCur}"
        class="week-chip${isCur ? " on" : ""}${isThis && !isCur ? " this-week" : ""}"
        data-off="${off}"
        onclick="shiftWeek(${off})"
        title="${isThis ? "本周（第 " + n + " 周）" : "第 " + n + " 周"}">${escapeHtml(label)}${
          hasToday && !isCur ? '<i class="week-dot" title="今天在这一周"></i>' : ""
        }</button>`
    );
  }
  box.innerHTML = parts.join("");
  // 把当前周滚到可视区中间（首次进入时也顺便「回到本周」的位置）
  scrollWeekIntoView();
}

/** 把当前选中的周胶囊滚到中间。用 scrollIntoView 的 inline:center 最省事。 */
function scrollWeekIntoView() {
  const el = document.querySelector("#week-scroll .week-chip.on");
  if (!el || !el.scrollIntoView) return;
  try {
    el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  } catch (_) {
    /* 老浏览器不支持 options 就退化成瞬移，不影响功能 */
    try { el.scrollIntoView(); } catch (__) { /* 忽略 */ }
  }
}

/** 切换周：只动 weekOffset 再整体重绘（渲染逻辑都从这里收口）。 */
function shiftWeek(offset) {
  // 夹在允许范围内，防止越界
  const min = -SEM_WEEKS_BEFORE, max = SEM_WEEKS_AFTER;
  weekOffset = Math.max(min, Math.min(max, offset));
  renderWeekPicker();
  refreshAllViews();
}

/* ---------- 弹窗：新增 / 编辑 ---------- */
function openAdd(orderNo, date) {
  const s = slotByOrder(orderNo) || {};
  onEl("modal-title").textContent =
    `添加 · ${DAYS[weekdayOf(date) - 1]} ${fmtMD(date)} ${s.label || ""}`;
  onEl("modal-body").innerHTML = entryForm({
    _id: "", date, type: "course", slot_order: orderNo,
    start: s.start || "", end: s.end || "",
    name: "", teacher: "", location: "", building: "",
    weeks: "",            // 手动加的课默认每周都上，想限周次自己在表单里填
  }, false);
  bindForm(() => saveFromForm());
  showModal();
}

function openDetail(id) {
  const c = courses.find(x => x._id === id);
  if (!c) return;
  const s = slotByOrder(c.slot_order) || {};
  const isActivity = c.type === "activity";
  const canNav = !isActivity && !!c.building;
  onEl("modal-title").textContent = isActivity ? "活动详情" : "课程详情";
  onEl("modal-body").innerHTML = `
    <div class="field"><label>${isActivity ? "活动名称" : "课程名称"}</label>
      <div class="plan-box">${escapeHtml(c.name)}</div></div>
    <div class="row" style="gap:14px;">
      <div class="field" style="flex:1"><label>日期 / 节次</label>
        <div class="plan-box">${DAYS[weekdayOf(c.date) - 1]} ${fmtMD(c.date)} · ${escapeHtml(s.label || "—")}</div></div>
      <div class="field" style="flex:1"><label>时间</label>
        <div class="plan-box">${c.start || "—"}${c.end ? " - " + c.end : ""}</div></div>
    </div>
    ${isActivity ? "" : `
      <div class="field"><label>教师</label><div class="plan-box">${escapeHtml(c.teacher || "—")}</div></div>
      <div class="row" style="gap:14px;">
        <div class="field" style="flex:1"><label>教学楼</label><div class="plan-box">${escapeHtml(c.building || "—")}</div></div>
        <div class="field" style="flex:1"><label>教室</label><div class="plan-box">${escapeHtml(c.location || "—")}</div></div>
      </div>
      ${(() => {
        // 只在真有周次时才显示这一行 —— 空 weeks 表示「每周都上」，
        // 硬写一个「每周」反而让人以为是没填。
        const r = parseWeeks(c.weeks);
        if (r.all) return "";
        return `<div class="field"><label>上课周次</label><div class="plan-box">第 ${r.weeks.join("、")} 周（${formatWeeksLabel(r.weeks)}）</div></div>`;
      })()}`}
    <div class="row" style="margin-top:8px; gap:10px;">
      ${isActivity ? "" : `<button class="btn" ${canNav ? "" : "disabled"} onclick="goNav('${escapeHtml(c.building)}')">
        导航去教学楼</button>`}
      <button class="btn ghost" onclick="openEdit('${c._id}')">编辑</button>
      <button class="btn ghost" style="color:var(--danger)" onclick="delCourse('${c._id}')">删除</button>
    </div>
    ${!isActivity && !canNav ? '<p class="muted">给课程填好教学楼后，就能一键导航过去。</p>' : ""}`;
  showModal();
}

function openEdit(id) {
  const c = courses.find(x => x._id === id);
  onEl("modal-title").textContent = c.type === "activity" ? "编辑活动" : "编辑课程";
  onEl("modal-body").innerHTML = entryForm(c, true);
  bindForm(() => saveFromForm());
}

/** 复用候选：已添加过的课程（按名称去重），用于「选一个已添加的课程」 */
function reusableCourses() {
  const seen = new Map();
  courses.filter(c => c.type !== "activity").forEach(c => {
    if (!seen.has(c.name)) seen.set(c.name, c);
  });
  return [...seen.values()];
}

function entryForm(c, isEdit) {
  const isActivity = c.type === "activity";
  // ⚠️ 这里只带「课程固有」的字段（教师 / 教学楼 / 教室），**不带时间** ——
  //    时间属于「节次 + 校区」，不是课程的属性，见 fillFromReuse 里的说明。
  const reuseOptions = reusableCourses().map(r =>
    `<option value="${escapeHtml(r.name)}"
      data-teacher="${escapeHtml(r.teacher || "")}"
      data-building="${escapeHtml(r.building || "")}"
      data-location="${escapeHtml(r.location || "")}">${escapeHtml(r.name)}</option>`).join("");

  return `
    <input type="hidden" id="f-id" value="${c._id || ""}" />
    <input type="hidden" id="f-date" value="${c.date}" />
    <input type="hidden" id="f-slot" value="${c.slot_order}" />

    <div class="field">
      <label>类型</label>
      <div class="type-switch" id="f-type">
        <button type="button" class="ts-btn ${isActivity ? "" : "on"}" data-type="course"
          onclick="pickType('course')">课程</button>
        <button type="button" class="ts-btn ${isActivity ? "on" : ""}" data-type="activity"
          onclick="pickType('activity')">活动</button>
      </div>
    </div>

    ${!isEdit && !isActivity && reuseOptions ? `
      <div class="field" id="f-reuse-wrap">
        <label>从已添加的课程中选择（可选）</label>
        <select id="f-reuse" onchange="fillFromReuse(this)">
          <option value="">— 手动填写 —</option>${reuseOptions}
        </select>
      </div>` : ""}

    <div class="field"><label id="f-name-label">${isActivity ? "活动名称" : "课程名称"} *</label>
      <input id="f-name" value="${escapeHtml(c.name)}" placeholder="${isActivity ? "如 社团招新" : "如 高等数学"}" /></div>

    <div class="row" style="gap:10px;">
      <div class="field" style="flex:1"><label>开始</label><input id="f-start" class="is-locked" value="${escapeHtml(c.start || "")}" readonly aria-readonly="true" /></div>
      <div class="field" style="flex:1"><label>结束</label><input id="f-end" class="is-locked" value="${escapeHtml(c.end || "")}" readonly aria-readonly="true" /></div>
    </div>
    <p class="muted" style="margin:-6px 0 16px;font-size:12px;">
      时间由你点的<b>节次</b>决定，不用手填；选了教学楼后会换成该楼所在校区的作息。
    </p>

    <div id="f-course-fields" ${isActivity ? "hidden" : ""}>
      <!-- 字段顺序（2026-10-18 用户指定）：教学楼 → 教室 → 教师 → 周次。
           教师原来排在最前面，比教学楼还靠上；而填的时候一定是先想「在哪上课」，
           最后才想到「谁上」。教师和教室都不是必填（只有课程名带 *），
           所以两个都标「（选填）」，免得用户以为不填就存不了。 -->
      <div class="field">
        <label>教学楼（下拉选择或直接输入）</label>
        <input id="f-building" list="building-list" value="${escapeHtml(c.building || "")}"
          placeholder="选一个，或输入楼名" oninput="onBuildingInput(this.value)" />
      </div>
      <div class="field"><label>教室（选填）</label><input id="f-location" value="${escapeHtml(c.location || "")}" placeholder="如 博文楼-301" /></div>
      <div class="field"><label>教师（选填）</label><input id="f-teacher" value="${escapeHtml(c.teacher || "")}" placeholder="如 邱烨" /></div>
      <div class="field">
        <label>上课周次（选填）</label>
        <input id="f-weeks" value="${escapeHtml(c.weeks || "")}"
          placeholder="留空 = 每周都上；如 2-3,6-8周" />
        <p class="muted" style="margin:4px 0 0;font-size:12px;">
          留空表示这学期的每周都上。只上某几周就写清楚，例如
          <code>1-8周</code>、<code>2-3,6-8周</code>、<code>1,3,5周</code>。
        </p>
      </div>
    </div>

    <p class="muted" id="f-activity-tip" ${isActivity ? "" : "hidden"}>
      活动只记名称和时间，不参与课程作息、也不提供教学楼导航。
    </p>

    <button class="btn block" type="button" id="f-save">${isEdit ? "保存修改" : "添加"}</button>`;
}

/** 类型切换：课程 <-> 活动 */
function pickType(type) {
  document.querySelectorAll("#f-type .ts-btn").forEach(b => {
    b.classList.toggle("on", b.dataset.type === type);
  });
  const isActivity = type === "activity";
  onEl("f-course-fields").hidden = isActivity;
  onEl("f-activity-tip").hidden = !isActivity;
  onEl("f-name-label").textContent = isActivity ? "活动名称 *" : "课程名称 *";
  const reuse = onEl("f-reuse-wrap");
  if (reuse) reuse.hidden = isActivity;
}

/** 选中「已添加的课程」后自动带出名称 / 教师 / 教学楼 / 教室 */
function fillFromReuse(sel) {
  const o = sel.options[sel.selectedIndex];
  if (!o || !o.value) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
  // ⚠️ 这里**故意不**带出开始 / 结束时间：时间是只读的、由「你点的节次 + 教学楼校区」
  //    唯一决定（见 onBuildingInput）。带出旧课程的时间会打破这条不变量 —— 被复用的
  //    课程往往在别的节次（比如上周的周三第 3 节），套到这一格就会出现「格子写第 1 节、
  //    时间却是 10:00」的矛盾。节次变了，时间本来就该跟着变。
  set("f-teacher", o.dataset.teacher);
  set("f-building", o.dataset.building);
  set("f-location", o.dataset.location);
  // 上面都是程序化赋值，**不会**触发 input 事件 —— 教学楼带出来了却没人通知时间框。
  // 手动补一次，让时间跟着这栋楼的校区走（不补的话时间会停在基准校区）。
  if (o.dataset.building) onBuildingInput(o.dataset.building);
  const nameEl = onEl("f-name");
  if (nameEl) nameEl.value = o.value;
}

/**
 * 教学楼输入时，把开始 / 结束时间换成该楼所在校区的作息。
 *
 * ⚠️ 这两个输入框现在是**只读**的（用户改不了），所以这里无条件覆盖就行。
 *    原来那句「只在用户没手动改过时间时才覆盖」的守卫不但没必要，本身还是个
 *    真 bug：先选 A 楼（时间变成 A 区作息）再改选 B 楼时，输入框里的值已经不等于
 *    基准节次时间了，守卫会判定为「用户改过」而拒绝更新 —— 时间就永远停在 A 区。
 *    只读之后不存在「用户改过」这种状态，一律按「当前节次 + 当前教学楼校区」
 *    重算，最省事也最不容易错。
 *
 * 教学楼认不出来（清空了、或不在点位表里）时 regionOfBuilding 返回 undefined，
 * slotTimeFor 会回落到基准（南区）时间 —— 顺手把「清空教学楼」也还原了。
 */
function onBuildingInput(name) {
  const s = slotByOrder(Number(onEl("f-slot").value));
  if (!s) return;
  const t = slotTimeFor(s, regionOfBuilding(name.trim()));
  onEl("f-start").value = t.start;
  onEl("f-end").value = t.end;
}

/** 给表单的「保存」按钮绑回调（元素缺失时静默跳过，不抛错）。 */
function bindForm(onSave) {
  const el = onEl("f-save");
  if (el) el.onclick = onSave;
}

function currentType() {
  const on = document.querySelector("#f-type .ts-btn.on");
  return on ? on.dataset.type : "course";
}

function saveFromForm() {
  const v = id => { const el = document.getElementById(id); return el ? el.value : ""; };
  const id = v("f-id");
  const type = currentType();
  const name = v("f-name").trim();
  if (!name) { toast(type === "activity" ? "请填写活动名称" : "请填写课程名称", "error"); return; }

  const building = type === "activity" ? "" : v("f-building").trim();
  // 教学楼在校区表内时自动带上经纬度，供地图导航使用
  const b = findBuilding(building);
  const obj = {
    _id: id || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
    date: v("f-date"),
    type,
    slot_order: Number(v("f-slot")) || 0,
    start: v("f-start").trim(),
    end: v("f-end").trim(),
    name,
    teacher: type === "activity" ? "" : v("f-teacher").trim(),
    location: v("f-location").trim(),
    building,
    // 上课周次（选填）。留空 = 每周都上，与「读不到就当每周都上」的兜底一致。
    // ⚠️ 这一行**必须有**：下面编辑分支是 `courses[i] = obj` 的整体替换，
    //    obj 里少了 weeks 就会把原来导入的周次**冲成空** ——
    //    用户表现为「编辑一下课程，它就变成每周都上了」，极难排查。
    weeks: type === "activity" ? "" : v("f-weeks").trim(),
    lng: b ? b.lng : null,
    lat: b ? b.lat : null,
  };
  if (id) {
    const i = courses.findIndex(x => x._id === id);
    if (i >= 0) courses[i] = obj; else courses.push(obj);
  } else {
    // 一个时间点只允许一条：同一天同节次已占用就拦下来。
    //
    // ★ 2026-10-18 修「添加的课会没显示」：
    //   原来只比 `date + slot_order`，不看周次。于是会出现最气人的一种情况 ——
    //   这个格子**已经有一门课**，但它 weeks 只写「1-5周」，当前看的是第 6 周，
    //   表格按周次把它藏了（看起来是空格、还带着「＋」），用户填完点添加却被告知
    //   「这个时间点已经有「高等数学」了」，而且**怎么找都找不到那门课**。
    //   （用户实测就是卡在这里：格子空着、加不进去、也看不见到底是谁占了。）
    //
    //   现在分两种情况：
    //     · 那条课本周看得到 -> 保持原提示（用户自己能看见，拦得合理）
    //     · 那条课本周看不到 -> 明说它在哪几周，并问一句「要不要让它本周也上」。
    //       同意就把当前周并进它的 weeks（等价于让它在本周显示），
    //       而不是新建一条 —— 后者会撞库的 uq_user_date_slot 唯一约束。
    const taken = courses.find(c => c.date === obj.date && c.slot_order === obj.slot_order);
    if (taken) {
      const curWeek = weekNumberByDate(obj.date);
      const shownHere = courseShownInWeek(taken, curWeek);
      if (shownHere) {
        toast(`这个时间点已经有「${taken.name}」了，一个时间点只能放一条`, "error");
      } else {
        const wk = parseWeeks(taken.weeks);
        const wkTxt = wk.all ? "" : formatWeeksLabel(wk.weeks);
        const ok = window.confirm(
          `这个时间点已经有「${taken.name}」，`
          + (wkTxt ? `但它只在第 ${wkTxt.replace("周", "")} 周上` : "但它不是每周都上")
          + `，本周（第 ${curWeek} 周）看不到。\n\n`
          + `点「确定」：让它本周（第 ${curWeek} 周）也上；\n`
          + `点「取消」：什么都不改。`
        );
        if (ok) {
          taken.weeks = weeksWith(taken.weeks, curWeek);
          closeModal();
          refreshAllViews();
          toast(`已让「${taken.name}」本周（第 ${curWeek} 周）也上，记得点「保存时间表」`, "ok");
          return;
        }
      }
      return;
    }
    courses.push(obj);
  }
  refreshAllViews();
  closeModal();
  toast(id ? "已保存修改，记得点「保存时间表」同步到账号" : `已添加${type === "activity" ? "活动" : "课程"}，记得点「保存时间表」`, "ok");
}

/**
 * 删除一节课 / 一个活动。
 *
 * ★ 2026-10-18 修：原来这里只改内存 + 弹一句「记得点保存时间表」，**不落库**。
 *   于是用户删完课去「日程提醒」页，看到那门课还好端端在那儿 ——
 *   因为提醒页是**重新从后端读**的，而删掉的那条从来没同步上去过。
 *   删除是明确的、后果最直接的破坏性操作（课表空了、提醒还在，
 *   人会照着提醒跑去教室），不该再等用户去点一次保存。
 *   所以这里删完立刻整份提交；没连上后端就明说「只在本机删了」，
 *   而不是假装成功。
 */
async function delCourse(id) {
  const gone = courses.find(x => x._id === id);
  courses = courses.filter(x => x._id !== id);
  refreshAllViews();
  closeModal();

  const ok = await saveTimetable({ quiet: true });
  const what = gone ? `「${gone.name || "这一项"}」` : "这一项";
  toast(ok
    ? `已删除${what}，日程提醒里也不会再出现`
    : `${what}已在本机删除，但没连上服务器 —— 联网后请再点一次「保存时间表」同步`,
    ok ? "ok" : "error");
}

/**
 * 跳转地图导航页，把该课程的教学楼设为终点。
 * @param {string} building 教学楼名
 * @returns {void}
 */
function goNav(building) {
  const b = findBuilding(building);
  const q = new URLSearchParams({
    to: building || "",
    lng: b ? String(b.lng) : "",
    lat: b ? String(b.lat) : "",
  });
  location.href = "nav.html?" + q.toString();
}

/* ---------- 沿用上周：已删除（2026-10-18） ----------
   课表按周次重复渲染，同一门课在 weeks 里的每一周都会自动出现，
   「把上周复制一份到本周」只会造出重复记录，用户要求去掉。 */

/* ---------- 导入课表（截图走 AI / 表格走后端解析 / JSON / CSV 走本地解析）----------

   三条路：
     · 图片 → 交给后端智谱视觉模型读图，产出结构化课表 → 走「核对弹窗」
     · 表格 → .xls/.xlsx 交给后端 pandas 解析（前端零依赖，装不了 SheetJS）
     · 文本 → 本地直接解析（不花 AI 额度，格式固定也不需要理解能力）

   图片这条路一定要让用户过一眼：模型识别课表不是 100% 准，
   所以识别存疑的格子会标黄 + 弹框列出，用户确认后才写进课表。
   表格这条路同理 —— 数据本身是准的，但「哪格是哪门课」依赖我的解析假设，
   所以也走同一套核对流程，让用户扫一眼再落库。                        */

function importTimetable() { onEl("file-import").click(); }

function handleImport(e) {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";                       // 允许连续选同一个文件
  if (!file) return;

  if (/^image\//.test(file.type) || /\.(png|jpe?g|webp|bmp|gif)$/i.test(file.name)) {
    scanTimetableImage(file);
    return;
  }
  // Excel 是二进制，走 readAsText 必然乱码，必须送后端解析。
  // ⚠️ 放在图片判断**后面**：有些系统给 .xls 的 file.type 是 image/... 之类
  //    的怪值（WPS 导出偶发），但扩展名是可靠的，这里以扩展名为准。
  if (/\.(xlsx?|xlsm)$/i.test(file.name)) {
    importSheetFile(file);
    return;
  }
  importTextFile(file);
}

/* ----- 表格（.xls/.xlsx）：交给后端 pandas 解析 ----- */

function importSheetFile(file) {
  if (file.size > 10 * 1024 * 1024) {
    toast("表格太大了（超过 10MB），时间表一般不会这么大，请确认选对文件了", "error");
    return;
  }
  startScanUI();
  const reader = new FileReader();
  reader.onload = async ev => {
    // readAsDataURL 出来是 "data:...;base64,XXXX"，后端会自动剥前缀
    const dataUrl = String(ev.target.result || "");
    try {
      const res = await api("/planner/import-sheet", {
        method: "POST",
        body: { data: dataUrl, name: file.name, monday: weekDates()[0] },
      });
      stopScanUI();

      const found = (res.courses || []).map(toLocalCourse);
      if (!found.length) {
        toast((res.warnings && res.warnings[0]) || "这个表格里没读到课程，确认一下选的是时间表文件吗", "error");
        return;
      }
      const extra = res.sheet ? [`读取的工作表：${res.sheet}`] : [];
      openReview(found, [...(res.warnings || []), ...extra], "");
    } catch (err) {
      stopScanUI();
      toast(String(err.message || "表格解析失败，请检查文件"), "error");
    }
  };
  reader.onerror = () => { stopScanUI(); toast("文件读取失败，请重新选一次", "error"); };
  reader.readAsDataURL(file);
}

/* ----- 图片：交给视觉模型 ----- */

/** 单张图片压到最长边 1600px / JPEG 0.82，避免 base64 过大被接口拒绝或拖慢 */
function compressImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      // MAX / 质量都往上提了一档（1600→2200、0.82→0.92）。
      // 原因：课表截图**格子密、字号小**，1600px + 0.82 压缩之后，
      // 「知行楼B302」这种小字在模型眼里糊成一团 —— 实测模型会因此
      // 编造课名（它不是读不到，是看不清又不想交白卷）。
      // 2200px 对 GLM-4V 是安全的（它本身会把长边缩到 1120 左右处理），
      // 而 0.92 的 JPEG 肉眼几乎无损，能保住笔画细节。
      // 代价是 base64 体积涨约 60%（~100KB → ~160KB），单次请求多几百毫秒，
      // 换来「识别得出来」，非常划算。
      const MAX = 2200;
      let { width: w, height: h } = img;
      const scale = Math.min(1, MAX / Math.max(w, h));
      w = Math.round(w * scale);
      h = Math.round(h * scale);
      const cv = document.createElement("canvas");
      cv.width = w; cv.height = h;
      const ctx = cv.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, w, h);            // 透明 PNG 转 JPEG 会变黑底，先铺白
      ctx.drawImage(img, 0, 0, w, h);
      resolve(cv.toDataURL("image/jpeg", 0.92));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("图片读取失败")); };
    img.src = url;
  });
}

/** 扫描进度条：真实进度拿不到，用一个「慢速逼近」的假进度条 + 文案轮播 */
let scanTimer = null;
function startScanUI() {
  const fill = document.querySelector("#scan-mask .scan-bar-fill");
  const step = onEl("scan-step");
  onEl("scan-mask").classList.add("show");

  let pct = 8;
  fill.classList.remove("indeterminate");
  fill.style.width = pct + "%";

  const STEPS = [
    [15, "正在上传图片…"],
    [32, "AI 正在读表头（星期 / 节次）…"],
    [52, "AI 正在逐格识别课程名…"],
    [70, "AI 正在辨认教学楼和教室…"],
    [85, "正在核对楼名是否在校园楼栋表里…"],
    [93, "快好了，正在整理结果…"],
  ];
  let i = 0;
  scanTimer = setInterval(() => {
    // 每 1.2 秒推进一档，封顶 93% —— 剩下的留给真实返回，避免「卡在 99%」
    if (i < STEPS.length) { pct = STEPS[i][0]; step.textContent = STEPS[i][1]; i++; }
    else if (pct < 93) { pct = Math.min(93, pct + 1); }
    fill.style.width = pct + "%";
  }, 1200);
}

function stopScanUI() {
  clearInterval(scanTimer);
  scanTimer = null;
  const fill = document.querySelector("#scan-mask .scan-bar-fill");
  fill.style.width = "100%";
  setTimeout(() => onEl("scan-mask").classList.remove("show"), 260);
}

/**
 * 把课表截图发给后端识别。
 * @param {File} file
 */
async function scanTimetableImage(file) {
  if (file.size > 12 * 1024 * 1024) {
    toast("图片太大了（超过 12MB），请先压缩或裁一下再传", "error");
    return;
  }
  startScanUI();
  let dataUrl;
  try {
    dataUrl = await compressImage(file);
  } catch (err) {
    stopScanUI();
    toast(String(err.message || "图片读取失败"), "error");
    return;
  }

  try {
    // 传当前周的周一，后端据此把「周三」换算成具体日期
    const monday = weekDates()[0];
    const res = await api("/planner/import-image", {
      method: "POST",
      body: { image: dataUrl, monday },
    });
    stopScanUI();

    const found = (res.courses || []).map(toLocalCourse);
    if (!found.length) {
      toast((res.warnings && res.warnings[0]) || "没能从这张图里读出课程，换张更清晰的截图试试", "error");
      return;
    }
    openReview(found, res.warnings || [], res.raw || "");
  } catch (err) {
    stopScanUI();
    toast("识别失败：" + (err.message || "请稍后重试"), "error");
  }
}

/**
 * 后端识别结果 -> 本地课程对象（补 _id、按节次对齐时间、算出是否存疑）
 * @param {any} r
 */
function toLocalCourse(r) {
  // ---- 节次兜底：先把 slot_order 收进一个**一定存在**的槽位 ----
  // 为什么必须收：识别结果里 slot_order 可能是 0 / 999 / 缺失（模型瞎写、
  // 或被截断救回来没填）。原样传下去会有两个后果：
  //   1. slotByOrder() 查不到 -> slot = {} -> 时间算出来是空字符串，
  //      课表按节次分格时这条课落在**不存在的行上**，整条消失；
  //   2. 核对表的节次下拉里没有这个值，select 会退回到第一项，
  //      用户看到的是「第一节」但数据里还是 999，改完保存就对不上了。
  // 所以这里一律夹到合法范围，并对越界值标黄提示用户核对。
  const rawSlot = Number(r.slot_order);
  const maxOrder = slots.length ? slots[slots.length - 1].order_no : 12;
  const minOrder = slots.length ? slots[0].order_no : 1;
  let slotOrder = Number.isFinite(rawSlot) && rawSlot ? rawSlot : 0;
  if (!slotOrder || slotOrder < minOrder || slotOrder > maxOrder) {
    // 越界 / 缺失：就近挂到第一节（或按开始时间找最近的槽）
    const near = r.start ? nearestSlot(r.start) : null;
    slotOrder = near || slots.find(s => s.period_type === "class")?.order_no || minOrder;
  }

  const slot = slotByOrder(slotOrder) || {};
  const building = r.building || "";
  const b = findBuilding(building);
  const uncertain = (r.uncertain || []).slice();

  // 节次是「原样透传」还是「被兜底改过」—— 改过就标黄让用户核对
  if (Number(rawSlot) !== slotOrder && !uncertain.includes("slot_order")) {
    uncertain.push("slot_order");
  }

  // ⚠️ 去掉后端可能带回来的 `date` 存疑标记。
  //
  // 后端在「没有参考周一」时算不出具体日期，会把它记进 uncertain。
  // 但**前端这里能自己算**：下面第 739 行就是用 weekday 去 weekDates() 里取对应那天。
  // 所以这个标记在前端毫无意义，留着只会让**每一格都变黄**，
  // 用户看到「全部都要核对」会以为识别得很差，其实星期和节次都是对的。
  // 真正需要用户补的是「星期认错了」，那由 `weekday` 这个键负责。
  const dateUncertain = uncertain.includes("date");
  const strictUncertain = uncertain.filter(k => k !== "date");

  // 后端没给时间就按节次套学校作息。
  // ⚠️ 线上课 / 未知楼（building 为空）一律按**南区**作息 ——
  //    南北区作息是「哪栋楼决定的」，没有楼就没有理由套北区时间；
  //    南区是基准作息（campus.js 里 start_south 就等于默认 start），
  //    取它最不容易出错，也和后端 _align_courses() 的兜底口径一致。
  const region = regionOfBuilding(building) || "south";
  const t = slot.order_no ? slotTimeFor(slot, region) : { start: "", end: "" };

  return {
    _id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()),
    date: r.date || weekDates()[Math.max(0, Math.min(6, (Number(r.weekday) || 1) - 1))],
    type: "course",
    slot_order: slotOrder,
    start: r.start || t.start || slot.start || "",
    end: r.end || t.end || slot.end || "",
    name: r.name || "",
    teacher: r.teacher || "",
    building,
    location: r.location || "",
    // 上课周次原始串（教务表格里那一列）。空 = 每周都上。
    // 从解析器 -> ImportSheetOut.ImportedCourse -> 这里，一路原样带过来；
    // 真正「按周显示」的判断在 planner-core.js 的 courseShownInWeek()。
    weeks: r.weeks || "",
    lng: b ? b.lng : null,
    lat: b ? b.lat : null,
    _uncertain: strictUncertain,    // 仅前端用，保存前会被剥掉
    // 识别度按「严格存疑」算；date 那种信息不全不算识别不准
    _probable: !strictUncertain.length,
    // 单独留个记号，核对弹窗可以温和地提一句（不标黄）
    _dateGuess: dateUncertain,
  };
}

/* ----- 文本：JSON / CSV 本地解析 ----- */

function importTextFile(file) {
  const dates = weekDates();
  const reader = new FileReader();
  reader.onload = ev => {
    const text = String(ev.target.result).trim();
    let rows = [];
    try {
      if (text.startsWith("[") || text.startsWith("{")) {
        const data = JSON.parse(text);
        rows = Array.isArray(data) ? data : (data.courses || []);
      } else {
        rows = text.split(/\r?\n/).filter(Boolean).slice(1).map(line => {
          const [day, start, end, name, teacher, location, building] = line.split(",").map(s => (s || "").trim());
          return { day: Number(day), start, end, name, teacher, location, building };
        });
      }
    } catch {
      toast("文件解析失败，请检查格式是否正确", "error");
      return;
    }
    const imported = rows.filter(r => r && r.name && (r.date || r.day)).map(r => {
      const building = r.building || "";
      const b = findBuilding(building);
      // 支持按 date 直接导入；也兼容旧的「星期几」写法，映射到当前浏览周的对应天
      const date = r.date ? String(r.date) : (dates[Math.max(0, Math.min(6, Number(r.day) - 1))] || dates[0]);
      return {
        _id: crypto.randomUUID ? crypto.randomUUID() : String(Math.random()),
        date,
        type: r.type === "activity" ? "activity" : "course",
        slot_order: r.slot_order != null ? Number(r.slot_order) : nearestSlot(r.start),
        start: r.start || "08:10",
        end: r.end || "08:55",
        name: String(r.name),
        teacher: r.teacher || "",
        location: r.location || "",
        building,
        lng: b ? b.lng : null,
        lat: b ? b.lat : null,
        // ⚠️ weeks 必须跟着走！
        //    这就是「白名单式拷贝」的经典坑：上面这份对象是**新造的**，
        //    少写一个字段就无声丢掉一个 —— JSON/CSV 里带了周次，
        //    导入后却全变成「每周都上」，而且**一点报错都没有**。
        //    （toLocalCourse 那条路早就带了 weeks，唯独这里漏了。）
        weeks: String(r.weeks || ""),
        _uncertain: [],
      };
    });
    if (!imported.length) { toast("没有解析到有效内容，请检查文件", "error"); return; }
    openReview(imported, [], "");
  };
  reader.readAsText(file, "utf-8");
}

/* ----- 核对弹窗 ----- */

/** 待确认的导入结果（openReview 填，confirmImport 消费） @type {any[]} */
let pendingImport = [];

/**
 * 打开核对弹窗：逐条列出识别结果，存疑字段标黄可改，确认后才并入课表。
 * @param {any[]} list
 * @param {string[]} warnings
 */
function openReview(list, warnings, raw) {
  pendingImport = list;
  markConsecutive(list);

  const bad = list.filter(c => c._uncertain && c._uncertain.length);
  // ⚠️ list 为空时不能直接除 —— 0/0 是 NaN，会渲染成「识别度：NaN%」。
  //    走到这里基本都是「识别出来的课全被过滤掉了」，给 0 更直观。
  const conf = list.length ? Math.round((list.length - bad.length) / list.length * 100) : 0;
  const spans = summarizeSpans(list);

  const title = onEl("review-title");
  title.textContent = bad.length
    ? `识别完成：${list.length} 条，其中 ${bad.length} 条需要你核对`
    : `识别完成：${list.length} 条，全部识别成功`;

  // 存疑字段统计，用于顶部说明
  const missCount = {};
  bad.forEach(c => (c._uncertain || []).forEach(k => { missCount[k] = (missCount[k] || 0) + 1; }));
  const missList = Object.entries(missCount)
    .map(([k, n]) => `${uncertainLabel(k)} ${n} 处`).join("、");

  onEl("review-body").innerHTML = `
    <div class="review-legend">
      <i>识别度：<b style="color:${conf >= 80 ? "var(--ok,#16a34a)" : "#b45309"}">${conf}%</b></i>
      <i><span class="badge-warn">黄色行</span> 需要你补全后再导入</i>
      ${spans.count ? `<i><span class="badge-span">连堂</span> ${escapeHtml(spans.text)}</i>` : ""}
      ${missList ? `<i>没认准的字段：${escapeHtml(missList)}</i>` : ""}
    </div>
    ${warnings.length ? `<div class="plan-box" style="background:#fffbeb;border-color:#fcd34d;margin-bottom:12px;">
      ${warnings.map(w => "· " + escapeHtml(w)).join("<br>")}</div>` : ""}
    <!-- 这层只负责「表格太高时内部滚动」。高度写在 CSS（.review-scroll）里，
         不在行内写 max-height —— 手机上要把它放开成 0，
         否则弹窗里还要再套一层滚不动的小窗口。 -->
    <div class="review-scroll">
      <table class="review-table">
        <!-- 用 colgroup 精确控宽，而不是给 <th> 写 width。
             原因：table-layout:fixed 下浏览器会**按比例重新分配**剩余的宽度，
             给课程名列「不写宽度」它就自动吃掉所有富余空间 ——
             结果课程名框宽到能跑马，星期/节次被压得只剩「第」一个字。
             写死每一列（含课程名），宽度才真的按设想的来。 -->
        <colgroup>
          <col style="width:34px" />   <!-- # -->
          <col style="width:92px" />   <!-- 星期：要放下「周一」+ 下拉箭头 -->
          <col style="width:104px" />  <!-- 节次：要放下「第一节」 -->
          <col />                      <!-- 课程名：吃掉剩余（但有合理上限） -->
          <col style="width:120px" />  <!-- 教学楼 -->
          <col style="width:96px" />   <!-- 教室 -->
          <!-- 教师排在教室后面（与「添加课程」表单的字段顺序一致：
               先想「在哪上课」，再想「谁上」）。列宽跟着字段走，
               所以下面周次变成 110px 的那一列。 -->
          <col style="width:88px" />   <!-- 教师 -->
          <col style="width:110px" />  <!-- 周次：要放下 "2-3,6-8周" -->
        </colgroup>
        <thead><tr>
          <th title="行号">#</th>
          <th title="上课的星期">星期</th>
          <th title="第几节课（一节两节的连堂会标出来）">节次</th>
          <th title="课名太长时，点一下单元格就能看全">课程名</th>
          <th title="从下拉里挑；没有的楼可手输，这类不参与导航">教学楼</th>
          <th title="教室号，如 301 / B203">教室</th>
          <th title="选填，空着也能导入">教师</th>
          <th title="教务给的周次，如 2-3,6-8周；留空 = 每周都上">周次</th>
        </tr></thead>
        <tbody>${list.map((c, i) => reviewRow(c, i)).join("")}</tbody>
      </table>
    </div>
    <p class="muted" style="margin:10px 0 0;font-size:12px;">
      课名/教师原本印在一串里（如 <code>数据库原理与应用-邱烨@知行楼B</code>）时，已自动拆成
      <b>课程名 · 教师 · 教学楼 · 教室</b> 四栏。
      <b>教师是选填</b>，空着也能导入；教学楼可从下拉里选，选不出来直接手输（这类楼不参与导航）。
      课名太长看不全，<b>点一下那一格</b>即可展开全文。
    </p>
    <!-- 底部操作条。布局全交给 CSS（.review-foot），不写在行内：
         手机上要把它从「一行左右分列」改成「上面统计、下面两个平分按钮」，
         行内样式优先级更高，会盖掉媒体查询，改了也看不出效果。 -->
    <div class="review-sticky-bar">
      <div class="review-foot">
        <span class="muted" id="review-count">共 ${list.length} 条${bad.length ? `，${bad.length} 条待补全` : ""}</span>
        <span class="review-foot-btns">
          <button class="btn ghost" onclick="closeReview()">取消</button>
          <button class="btn" id="btn-import-confirm" onclick="confirmImport()">
            确认导入这些课程
          </button>
        </span>
      </div>
    </div>`;

  onEl("review-mask").classList.add("show");
  bindReviewInputs();
  refreshImportButton();
}

/** 单行核对表格。
 *
 *  四栏字段各管各的，都是「后端拆好 + 用户可改」：
 *    name     课程名       必填（没它不给导入）
 *    building 教学楼       下拉选，选不出来可手输
 *    location 教室         手输（如 301 / B203）
 *    teacher  教师         选填，空着无所谓
 *
 *  学校课表格子常把这一串印在一起（`数据库原理与应用-邱烨@知行楼B`），
 *  后端 `_split_cell_text()` 已经按 `-` / `@` 拆成四栏，这里只负责让他改。
 */
function reviewRow(c, i) {
  const bad = c._uncertain && c._uncertain.length;
  const has = k => (c._uncertain || []).includes(k);
  const cls = v => (has(v) ? ' class="miss"' : "");

  // 课程名一律用 <textarea>（多行），而不是 <input>（单行）。
  // 原因：课名本身可能就有十几个字（「马克思主义基本原理概论」9 字、
  // 「大学英语(专升本)」也不短），加上中文比英文宽，单行输入框里必然被截断。
  // 换成 2 行高的 textarea，绝大多数课名一眼就能看全，不用点、不用悬停。
  // 行数按字数自适应：短课名 1 行，长的 2 行。
  const nameFull = String(c.name || "");
  const rows = nameFull.length > 10 ? 2 : 1;

  // 连堂课的角标：首节标「首」、末节标「尾」，中间的标「连」。
  // 一眼就能看出这几行是同一门课连排，不是识别重复了。
  const spanBadge = c._span_pos && c._span_pos !== "solo"
    ? `<span class="badge-span" title="同一门课连排（第${c._span_start}-${c._span_end}节）。导入后会合并成一张卡片显示。">`
      + `${c._span_pos === "first" ? "连堂·首" : c._span_pos === "last" ? "连堂·尾" : "连堂"}</span>`
    : "";

  // ⚠️ 节次下拉必须保证「当前值一定在选项里」。
  //    否则 <select> 会**静默**退回到第一项（显示「06:00」），
  //    而 c.slot_order 还是原值 —— 用户看着是第一节、存下去却是别的节次。
  //    正常路径上 toLocalCourse 已经把节次夹进合法范围了，这里是第二道保险。
  const hasSlot = slots.some(s => s.order_no === c.slot_order);
  const slotOpts = (hasSlot ? slots : [...slots])
    .map(s => `<option value="${s.order_no}" ${s.order_no === c.slot_order ? "selected" : ""}>${escapeHtml(s.label)}</option>`)
    .join("");

  // 节次格：线上课在节次旁边挂一个「南」小标，
  // 明说这一格用的是**南区作息**（没有教学楼就没有北区作息可言）。
  // 不标的话用户看到「线上课 + 08:00」会怀疑是不是套错了区 —— 猜不如写清楚。
  const isOnline = !String(c.building || "").trim()
    && isVirtualPlaceName(c.location);
  const regionTag = isOnline
    ? '<span class="badge-region" title="没有教学楼，按南区作息算时间">南</span>'
    : "";

  // 每个 <td> 都带 data-label：手机上没有表头（一行 8 列根本摆不下），
  // 改成「一条课一张卡」之后，靠 ::before{content:attr(data-label)} 当字段名。
  // 桌面端这些属性完全没用到，不影响表格。
  //
  // data-idx 是**从 0 开始**的数组下标（bindReviewInputs 用它取 pendingImport[idx]），
  // 绝不能改。卡片上要显示「第 N 条」是给人看的，从 1 开始，所以另给一个 data-no ——
  // 让 CSS 的 ::before 直接取这个 1 起的数字，省得在 ::before 里做不了加法。
  return `<tr class="${bad ? "row-bad" : ""}${c._span_pos && c._span_pos !== "solo" ? " row-span" : ""}" data-idx="${i}" data-no="${i + 1}">
    <td class="cell-idx">${i + 1}</td>
    <td data-label="星期"><select data-f="date"${has("weekday") ? ' class="miss"' : ""} onchange="onReviewDate(this)">
      ${DAYS.map((d, k) => {
        const iso = weekDates()[k];
        return `<option value="${iso}" ${iso === c.date ? "selected" : ""}>${d}</option>`;
      }).join("")}
    </select></td>
    <td class="cell-slot" data-label="节次">
      <select data-f="slot_order"${has("slot_order") ? ' class="miss"' : ""} onchange="onReviewSlot(this)">
        ${slotOpts}
      </select>
      ${regionTag}
    </td>
    <td class="cell-name" data-label="课程名">
      <textarea data-f="name"${cls("name")} rows="${rows}"
                placeholder="（务必填写）">${escapeHtml(nameFull)}</textarea>
      ${spanBadge}
    </td>
    <td data-label="教学楼"><input data-f="building" list="building-list"${cls("building")} autocomplete="off"
               value="${escapeHtml(c.building)}" placeholder="选楼名" title="从下拉里挑；没有的楼可手输，这类不参与导航" /></td>
    <td data-label="教室"><input data-f="location"${cls("location")} value="${escapeHtml(c.location)}" placeholder="如 301" /></td>
    <td data-label="教师"><input data-f="teacher" value="${escapeHtml(c.teacher)}" placeholder="选填" title="选填，不填也能导入" /></td>
    <td data-label="周次"><input data-f="weeks" value="${escapeHtml(c.weeks || "")}" placeholder="每周"
               title="${parseWeeks(c.weeks).parsed || !c.weeks
                 ? "教务给的周次，留空 = 每周都上。可写成 1-8周 / 2-3,6-8周"
                 : "没读懂这串周次，导入后会按「每周都上」处理，可以在这里改成 2-3,6-8周 这种写法"}" /></td>
  </tr>`;
}

/** 核对表里改了内容 -> 同步回 pendingImport（这样确认导入拿到的是用户改过的值） */
function bindReviewInputs() {
  document.querySelectorAll("#review-body .review-table tbody tr").forEach(tr => {
    const idx = Number(tr.dataset.idx);
    tr.querySelectorAll("[data-f]").forEach(el => {
      el.addEventListener("input", () => {
        const c = pendingImport[idx];
        if (!c) return;
        const f = el.dataset.f;
        c[f] = el.value;
        c._uncertain = (c._uncertain || []).filter(k => k !== f);
        el.classList.remove("miss");
        el.closest("tr").classList.toggle("row-bad", (c._uncertain || []).length > 0);
        // 教学楼是「边填边判断」：填的不是已知教学楼，标记出来但仍然收下
        if (f === "building") markBuildingCell(el, c);
        // 改了课名会改变「哪些行算连堂」，重算一遍标记。
        // 重算只更新 _span_* 数据，不重建整张表 —— 重建会把用户正在敲的
        // 输入框焦点弄丢（正打字呢光标突然没了，很恼人）。
        if (f === "name") { markConsecutive(pendingImport); paintSpanMarks(); }
        refreshImportButton();
      });
      // 选楼名（datalist 点选）不触发 input 的某些浏览器，补一个 change
      if (el.dataset.f === "building") {
        el.addEventListener("change", () => {
          const c = pendingImport[idx];
          if (!c) return;
          c.building = el.value;
          markBuildingCell(el, c);
        });
      }
    });

    // 初始渲染也要跑一遍「未知楼」判断 —— 识别结果里本来就可能有校内没有的楼
    // （附属学校、外校区、临时教室），只靠 input 事件的话打开弹窗时它们
    // 不会变色，用户会以为「已经识别对了」，导进去才发现没有坐标。
    const bEl = tr.querySelector('[data-f="building"]');
    if (bEl && pendingImport[idx]) markBuildingCell(bEl, pendingImport[idx]);

    // 课名格现在是 textarea（多行），高度自适应内容，不需要「点击才展开」了。
    // 但 <textarea> 有个隐形坑：它默认 `overflow: auto` + 固定行高，
    // 内容正好卡在两行边界时会露出一条滚动条。所以每次输入后把高度
    // 重算一遍（先归零再按 scrollHeight 撑开），永远刚好装下内容。
    const ta = tr.querySelector("td.cell-name textarea");
    if (ta) {
      const autosize = () => {
        ta.style.height = "auto";
        ta.style.height = Math.max(30, ta.scrollHeight) + "px";
      };
      autosize();
      ta.addEventListener("input", autosize);
      // 也要在初始化时跑一次：预填的长课名进来就可能是 2 行
      requestAnimationFrame(autosize);
    }
  });
}

/**
 * 教学楼这一格填了个「校内没有的楼」时，给个黄底提示。
 *
 * ⚠️ 刻意**不拦着**用户：真实校园总有导航表里没收录的教学楼
 *    （新楼、外校区、附属学校…），拦了他就没法录课表了。
 *    只是提醒一句「这栋楼不参与导航」，坐标留空，
 *    详情页/提醒里仍会如实写出楼名 —— 有名字总比什么都没有强。
 */
function markBuildingCell(el, c) {
  const v = String(el.value || "").trim();
  const known = !!v && !!findBuilding(v);
  el.classList.toggle("unknown", !!v && !known);
  el.title = !v ? "从下拉里挑；没有的楼可手输，这类不参与导航"
    : known ? `${v}：已收录，可用于导航`
      : `${v}：校内导航表里没有这栋楼，课程能存但不提供导航`;
  // 顺手把坐标同步到 pendingImport：已知楼才有坐标，未知楼清空
  const b = findBuilding(v);
  c.lng = b ? b.lng : null;
  c.lat = b ? b.lat : null;
}

/**
 * 重画连堂标记（行底色 + 课名格角标），**不重建整张表**。
 *
 * 为什么不用 `innerHTML = ...` 整表重绘：用户正在输入框里打字时重绘，
 * 焦点和光标位置都会丢，得重新点回来接着敲 —— 所以这里只动小标记。
 */
function paintSpanMarks() {
  document.querySelectorAll("#review-body .review-table tbody tr").forEach(tr => {
    const c = pendingImport[Number(tr.dataset.idx)];
    if (!c) return;
    const on = c._span_pos && c._span_pos !== "solo";
    tr.classList.toggle("row-span", !!on);

    const cell = tr.querySelector("td.cell-name");
    if (!cell) return;
    let badge = cell.querySelector(".badge-span");
    if (!on) { if (badge) badge.remove(); return; }
    const text = c._span_pos === "first" ? "连堂·首"
      : c._span_pos === "last" ? "连堂·尾" : "连堂";
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "badge-span";
      cell.appendChild(badge);
    }
    badge.textContent = text;
    badge.title = `同一门课连排（第${c._span_start}-${c._span_end}节）。导入后会合并成一张卡片显示。`;
  });
}

/** 改了星期 -> 同一天同节次可能撞车，顺手把节次时间重新套一遍 */
function onReviewDate(sel) {
  const idx = Number(sel.closest("tr").dataset.idx);
  pendingImport[idx].date = sel.value;
}

/** 改了节次 -> 时间跟着该节次走（用户没单独填时间的话） */
function onReviewSlot(sel) {
  const idx = Number(sel.closest("tr").dataset.idx);
  const c = pendingImport[idx];
  c.slot_order = Number(sel.value);
  const s = slotByOrder(c.slot_order);
  if (s) {
    // 线上课 / 未知楼一律按南区作息（见 toLocalCourse 的说明）
    const region = regionOfBuilding(c.building) || "south";
    const t = slotTimeFor(s, region);
    c.start = t.start || c.start;
    c.end = t.end || c.end;
    // 节次是用户自己选的，之前的「节次存疑」可以消掉了
    c._uncertain = (c._uncertain || []).filter(k => k !== "slot_order");
    sel.classList.remove("miss");
    sel.closest("tr").classList.toggle("row-bad", (c._uncertain || []).length > 0);
  }
  refreshImportButton();
}

/**
 * 标出「连续两节上同一门课」的行。
 *
 * 学校课表基本都是一门课连排两节（第一节+第二节、第三节+第四节…），
 * 截图里就是同一个课名在上下两格各印一遍。识别出来是两条独立记录 ——
 * 直接导进课表会在格子里上下堆两张一样的卡片，看着像识别重复了。
 *
 * 这里只做**标记 + 提示**，不做自动合并：
 *   · `_span_start` / `_span_end` 记录这段连续课的头尾，
 *     课表渲染时用 `_span_pos = first|mid|last|solo` 决定卡片怎么画（见 renderChip）
 *   · 弹窗里给这类行加一条浅蓝底 + 「连堂」角标，让用户知道不是重复
 *
 * 为什么不合并成一条：两节课真的是两个独立时间段（各有各的起止时间、
 * 中间还夹着课间休息），合并成一条就没法单独删掉其中一节了。
 *
 * 判定「同一门课」的口径放得比较宽 —— 只比课程名，不比教师/教室：
 * 同一门课连堂换教室的情况很少，但课名首尾空格、全半角差异很常见。
 */
function markConsecutive(list) {
  if (!Array.isArray(list) || !list.length) return;
  const norm = s => String(s || "").replace(/\s/g, "").trim();

  // 同一天内按节次升序扫一遍，把「课名相同且节次相邻」的串起来
  const byDate = {};
  list.forEach(c => (byDate[c.date] = byDate[c.date] || []).push(c));

  Object.values(byDate).forEach(group => {
    group.sort((a, b) => (a.slot_order || 0) - (b.slot_order || 0));
    let run = [];   // 当前这一段连续同课
    const flush = () => {
      if (run.length > 1) {
        run.forEach((c, k) => {
          c._span_start = run[0].slot_order;
          c._span_end = run[run.length - 1].slot_order;
          c._span_pos = k === 0 ? "first" : (k === run.length - 1 ? "last" : "mid");
        });
      } else if (run.length === 1) {
        run[0]._span_start = run[0]._span_end = run[0].slot_order;
        run[0]._span_pos = "solo";
      }
      run = [];
    };

    group.forEach(c => {
      const prev = run[run.length - 1];
      const adjacent = prev
        && norm(prev.name) === norm(c.name)
        && norm(prev.name) !== ""
        && (c.slot_order || 0) - (prev.slot_order || 0) === 1
        && (c.type || "course") === (prev.type || "course");
      if (!adjacent) { flush(); }
      run.push(c);
    });
    flush();
  });
}

/**
 * 统计有哪些「连堂课」，用于弹窗顶部一句话说明。
 * @returns {{count:number, text:string, saved:number}}
 */
function summarizeSpans(list) {
  const seen = new Set();
  let saved = 0;
  list.forEach(c => {
    if (c._span_pos === "first") {
      const key = `${c.date}#${c.name}#${c._span_start}`;
      if (!seen.has(key)) {
        seen.add(key);
        saved += (c._span_end - c._span_start);   // 合并显示省下来的格子数
      }
    }
  });
  const count = seen.size;
  return {
    count,
    saved,
    text: count ? `${count} 组连堂课（同一门课连着两节），已自动合并显示` : "",
  };
}

/** 还有缺课程名的行时，禁用「确认导入」并说明原因 */
function refreshImportButton() {
  const blank = pendingImport.filter(c => !String(c.name || "").trim()).length;
  const btn = onEl("btn-import-confirm");
  const cnt = onEl("review-count");
  if (!btn) return;
  btn.disabled = blank > 0;
  btn.textContent = blank > 0 ? `还有 ${blank} 条没填课程名` : "确认导入这些课程";
  if (cnt) {
    const bad = pendingImport.filter(c => (c._uncertain || []).length).length;
    cnt.textContent = `共 ${pendingImport.length} 条${bad ? `，${bad} 条待核对` : "，全部就绪"}`;
  }
}

function closeReview() {
  onEl("review-mask").classList.remove("show");
  pendingImport = [];
}

/**
 * 确认导入：把核对后的结果并进课表。
 *
 * 这里只改内存 + 重绘，**不自动保存**——沿用整个页面「改完自己点保存课表」的
 * 交互习惯，也避免识别错误直接落库。撞车（同一天同节次已有课）的自动跳过。
 */
/** 课名归一化：只为「这是不是同一门课」这个判断服务，不参与显示。
    教务导出的课名常带多余空格（「计算机网络 」/「计算机网络」），
    直接 === 比会把同一门课判成两门。 */
function normCourseName(s) {
  return String(s || "").replace(/[\s\u3000]+/g, "").trim();
}

function confirmImport() {
  const list = pendingImport.filter(c => String(c.name || "").trim());
  if (!list.length) { toast("没有可导入的课程", "error"); return; }

  // ★★★ 去重的 key 必须和「课表怎么摆格子」用同一套规则：星期 + 节次。
  //   （2026-10-18 修「重复导入会叠加，不是覆盖」）
  //   以前这里用 `${c.date}#${c.slot_order}` —— 具体日期。可渲染是按**星期**
  //   往格子里挂的（见 renderGrid：`weekdayOf(c.date)` 决定放哪一列），
  //   于是同一门课「这周导一次、下周再导一次」，date 变了、星期和节次没变，
  //   key 就不相等 → 被当成新课追加 → 两块叠在同一个格子里。
  //   用户看到的就是「重复导入课表，课程会叠加」。
  const cellKey = c => `${weekdayOf(c.date)}#${Number(c.slot_order)}`;
  const byCell = new Map();          // 星期#节次 -> 该格已有的课（可能多条：真冲突）
  courses.forEach(c => {
    if (!c.date || !c.slot_order) return;
    const k = cellKey(c);
    if (!byCell.has(k)) byCell.set(k, []);
    byCell.get(k).push(c);
  });

  let added = 0, updated = 0, dup = 0, bad = 0;
  const clashNames = [];            // 同格被挡下来的课名（用来区分「重复」和「真冲突」）
  const touched = new Set();        // 本轮已经覆盖过的记录（见下面 ① 的第二行）

  // 把多串周次并成一条：任一段是「每周」就整条每周（空串 = 每周）。
  const unionWeeks = arr => {
    if (arr.some(w => parseWeeks(w).all)) return "";
    const set = new Set();
    arr.forEach(w => parseWeeks(w).weeks.forEach(x => set.add(x)));
    return set.size ? formatWeeksLabel([...set]) : "";
  };

  list.forEach(c => {
    // ⚠️ 节次 / 日期缺失的**必须挡在这里并说出来**。
    //    以前是静默 push 进去：slot_order=undefined 的课在 renderGrid 里
    //    永远匹配不到任何一行，等于「导入成功但课表里找不到」——
    //    用户只能看到条数对不上，完全不知道哪条丢了。
    if (!c.date || !c.slot_order) { bad++; return; }

    const bucket = byCell.get(cellKey(c)) || [];

    // ① 同一个格子、同一个课名 = 「这条课又导了一遍」→ 原地覆盖，不再追加。
    //    这就是用户要的「覆盖」。_id 和 date 必须留着：
    //      · _id 换了前端会当成另一条（删旧增新，看起来还是重复）；
    //      · date 是后端唯一键 uq_user_date_slot 的一半，改它可能撞约束。
    const same = bucket.find(x => normCourseName(x.name) === normCourseName(c.name));
    if (same) {
      same.teacher = c.teacher || "";
      same.building = c.building || "";
      same.location = c.location || "";
      // ⚠️ 同一次导入里，同一格同一门课可能出现**两行周次不同**（单双周、分段上）。
      //    第一行直接覆盖周次（用户改过的周次要能盖掉旧的）；
      //    第二行起改成**并集**，否则后一行会把前一行刚写进去的周次冲掉 ——
      //    表现是「导进去只剩一半周次」，而且完全不报错。
      same.weeks = touched.has(same)
        ? unionWeeks([same.weeks, c.weeks])
        : (c.weeks || "");
      touched.add(same);
      same.start = c.start || same.start;
      same.end = c.end || same.end;
      same.type = c.type || same.type || "course";
      if (c.lng != null) same.lng = c.lng;
      if (c.lat != null) same.lat = c.lat;
      updated++;
      return;
    }

    // ② 格子被**别的**课占着 -> 仍按「放不下」处理。
    //    教务系统确实会在同一节次排两门课（理论 + 实验），课表一个格子放不下，
    //    叫它「重复」会让用户以为系统看错了，于是既不补也不查。
    if (bucket.length) { clashNames.push(c.name); dup++; return; }

    // ③ 空格子：照旧追加。
    // 只挑「该存的业务字段」白名单式拷贝，比黑名单（挑 _ 开头的删）稳：
    // 以后再加前端临时标记（_span_pos / _from_cell…）不会漏删存进库。
    const obj = {
      _id: c._id,
      date: c.date,
      type: c.type || "course",
      slot_order: c.slot_order,
      start: c.start,
      end: c.end,
      name: c.name,
      teacher: c.teacher || "",
      building: c.building || "",
      location: c.location || "",
      // ⚠️ weeks 必须在这份白名单里！
      //    这里只挑「该存的业务字段」，漏一个就静默丢掉一个 ——
      //    漏了 weeks 的话导入的课全变成「每周都上」，
      //    「按周显示」看起来像完全没生效，而且完全不报错。
      weeks: c.weeks || "",
      lng: c.lng != null ? c.lng : null,
      lat: c.lat != null ? c.lat : null,
    };
    courses.push(obj);
    if (!byCell.has(cellKey(obj))) byCell.set(cellKey(obj), []);
    byCell.get(cellKey(obj)).push(obj);
    // ★ 新推的这条也要登记进 touched：紧接着的下一行可能是「同格同课、另一段周次」
    //   （单双周/分段上），那一行走的是 ① 的覆盖分支，靠 touched 才会取并集。
    touched.add(obj);
    added++;
  });

  // ④ 顺手合并**历史遗留的完全重复**（同星期、同节次、同课名、同周次、同地点）。
  //    用户之前导过几遍留下的叠影，重导一次就能收干净，不用手动一条条删。
  //    周次不同的（同一格上半学期 / 下半学期两门课）**不算**重复，保留。
  let merged = 0;
  const seen = new Map();
  courses = courses.filter(c => {
    if (!c.date || !c.slot_order) return true;
    const k = [
      cellKey(c), normCourseName(c.name),
      String(c.weeks || "").replace(/[\s\u3000]+/g, ""),
      String(c.building || ""), String(c.location || ""),
    ].join("#");
    if (seen.has(k)) { merged++; return false; }
    seen.set(k, c);
    return true;
  });

  refreshAllViews();
  const hadUncertain = pendingImport.some(c => (c._uncertain || []).length);
  closeReview();

  // 结果分开报：新增 / 覆盖 / 清重 / 撞格 / 缺字段，别让它们互相背锅
  const parts = [];
  if (added) parts.push(`新增 ${added} 条`);
  if (updated) parts.push(`覆盖 ${updated} 条同名课`);
  if (merged) parts.push(`合并 ${merged} 条重复`);
  if (dup) {
    parts.push(clashNames.length
      ? `${clashNames.join("、")} 和已有课同格，没能导入`
      : `跳过 ${dup} 条`);
  }
  if (bad) parts.push(`${bad} 条缺星期/节次未导入`);

  if (!added && !updated && !merged) {
    if (bad) { toast(`有 ${bad} 条缺少星期或节次，没能导入 —— 补全后再试一次`, "error"); return; }
    if (dup) {
      toast(clashNames.length
        ? `这个时间点的格子放不下多门课，${clashNames.join("、")} 没能导入`
        : `这 ${dup} 个时间点时间表里已经有了，没有新增`, "info");
      return;
    }
    toast("没有可导入的课程", "error");
    return;
  }
  toast(`已导入：${parts.join("，")}。记得点「保存时间表」`,
        hadUncertain || bad ? "info" : "ok");
}


function nearestSlot(startStr) {
  if (!startStr) return slots[0] && slots[0].order_no;
  let best = slots[0], bestDiff = Infinity;
  slots.forEach(s => {
    const diff = Math.abs(toMin(s.start) - toMin(startStr));
    if (diff < bestDiff) { bestDiff = diff; best = s; }
  });
  return best.order_no;
}

/* ============================================================
   课表首屏骨架（占位）
   ------------------------------------------------------------
   为什么需要：打开课表页要等两个接口（时间槽 + 整学期课程），
   接口回来之前 tbody 是空的 —— 整张表只剩一行表头加一大片白。
   网慢的时候用户看到的是「课表空了」，而不是「正在加载」。

   形状照真实行铺：左边节次列 + 6 天，行高与空单元格同高（min-height 56px），
   数据一到 renderGrid() 会把 tbody 整个重建，占位自然消失 ——
   所以这里**不需要**任何「移除骨架」的清理代码，少一处可能漏掉的分支。

   ⚠️ 行数取 `slots.length`（实测 18 节次），不写死 8：
      bootPlanner 是「先 await loadSlots() → 再调 pageInit」，轮到本函数时
      时间槽已经回来了，能拿到真实行数。用真实行数，数据到达前后**表格高度
      一模一样**，不会出现「骨架 8 行、真表 18 行、页面猛地长高一截」。
      兜底 8：万一 slots 还没回来（接口失败），也照样有东西可看。

   ⚠️ 只在**登录后**才有意义：没登录时 loadTimetable 会走本地缓存，
      一样要等（读 localStorage 是同步的，但渲染在 await 之后），
      所以不区分登录态，一律先铺。
   ============================================================ */
function ttSkeleton() {
  const body = onEl("tt-body");
  if (!body) return;
  const DAYS = 6;
  const ROWS = (typeof slots !== "undefined" && slots && slots.length) ? slots.length : 8;
  const bar = '<span class="skeleton" style="display:block;height:44px;width:100%;border-radius:8px"></span>';
  body.innerHTML = Array.from({ length: ROWS }, () => `
    <tr>
      <td class="slot-col"><span class="skeleton" style="display:block;height:14px;width:28px;margin:0 auto"></span></td>
      ${Array.from({ length: DAYS }, () => `<td><div class="cell">${bar}</div></td>`).join("")}
    </tr>`).join("");
}

/* ============================================================
   课表页启动
   ============================================================ */

/** 课表页特有的初始化：把周次条定位到「本周在学期里的那一周」
 *  （core 的 bootPlanner 会调它）。
 *
 *  ⚠️ 这里**不能写 shiftWeek(0)**。weekOffset=0 现在是「第 1 周」，
 *     写死 0 会让每次打开都停在开学第一周 —— 学期过半后用户每次进来
 *     都得手动往右滑十几周，非常烦。
 *     正确行为是落在 currentWeekOffset()（本周真实对应的周次），
 *     开学前/学期结束后会被它夹到边界，不会越界。 */
globalThis.__plannerPageInit = function () {
  try { shiftWeek(currentWeekOffset()); } catch (e) { console.error("[planner] 周次条初始化失败：", e); }
  // 骨架必须在此刻铺 —— bootPlanner 在 pageInit 之后才 await loadTimetable，
  // 铺早了会被 renderHead 之外的重排挤掉，铺晚了就白屏完了才出现，没意义。
  ttSkeleton();
};

(async function initTimetablePage() {
  // extraViews 里的 renderGrid 是课表页独有的视图刷新
  await bootPlanner("timetable", ["renderGrid"]);
})();
