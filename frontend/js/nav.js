/* 天气地图导航：高德 JS 地图、校园点位、起终点导航、天气提示。
   校园数据来自 campus.js（阳光学院福州校本部）。CAMPUS 在此引用。

   ─────────────────────────────────────────────────────────────
   地图点位：用 POIS（= CAMPUS_PLACES 去掉「关掉的类型」），不是 BUILDINGS
   ─────────────────────────────────────────────────────────────
   教学楼、宿舍、食堂、图书馆、游泳馆……**都能当导航目的地**
   （比如「导航到图书馆自习」）。所以这两个概念要分清：

     课表相关（存课、识别截图、判断上课地点）-> BUILDINGS，只有 10 栋楼
     地图展示 / 导航目的地                    -> POIS，见下方定义

   ⚠️ POIS 会按 campus.js 里 PLACE_TYPES 的 `off: true` 过滤掉整类，
      当前关掉的是「院系场馆 / 停车场 / 校区 / 附属学校」——
      筛选按钮、地图标点、右侧点位列表三处一起消失。

   标记按类型分色（见 campus.js 的 PLACE_TYPES），并可按类型筛选显示，
   否则几十个点同时铺满地图会糊成一片。

   ─────────────────────────────────────────────────────────────
   导航方案：**校内走本地路网，校外交回高德**（AMap.Walking）
   ─────────────────────────────────────────────────────────────
   两端都在校内时，用 campus-graph.js 的步道图自己算最短路 ——
   高德的步行路线会把校内的人带出校门去走外面的马路。
   沿线画蓝色实线，起终点到路网的「接入段」画灰色虚线（与高德画法一致）。
   只要有一端在校外、或坐标离校园路网太远（判定不可信），就交回高德规划。
   两条路都收敛在 walkRoute 一个函数里，替换寻路算法只动它即可。

   起点留空时用**校内默认出发点**（见 defaultOrigin）。
   ─────────────────────────────────────────────────────────────
   注意：本页**不提供浏览器定位** —— 该功能已于 2026-10-13 按需求整体删除
   （定位不准，得不偿失；原因见 README 的「为什么删掉了定位」一节）。
   起点只能由用户自己填，或留空走 defaultOrigin。所以这里既没有 geolocation
   调用，也没有 WGS-84 / GCJ-02 坐标转换：本页所有坐标都来自 campus.js 的
   校内点位（本身就是 GCJ-02，与高德底图一致）。 */

const CAMPUS_CENTER = CAMPUS.center;
/* 本页实际使用的点位 = CAMPUS_PLACES 里**没有关掉**的那些。
   关掉哪几类由 campus.js 的 PLACE_TYPES 上 `off: true` 决定（院系场馆 / 停车场 /
   校区 / 附属学校），改那一处即可，本文件不用动。
   过滤放在这里，是为了让下游（地图标点、筛选按钮、右侧点位列表、起终点解析、
   datalist 候选）全部**自动**跟着少掉这几类 —— 只在一处收敛，不会漏掉某一条路径。 */
const POIS = CAMPUS_PLACES.filter(p => !((PLACE_TYPES[p.type] || {}).off));

let map = null, walking = null, geocoder = null, routeDrawn = false;
let AMapLib = null;       // 全局保存 SDK 句柄，供各渲染函数使用
let lastFocusedField = "dest";   // 最近聚焦的输入框：点地图标记时填到它里面
let markersByType = {};   // { type: [AMap.Marker] } 便于按类型整批显示/隐藏
let visibleTypes = null;  // Set<type>：当前显示了哪些类型；null = 全显示
let markerPlace = new Map();  // AMap.Marker -> CampusPlace
let markerIndex = new Map();  // AMap.Marker -> 同类里的序号（决定标签挂上方还是下方，错开叠字）
let pinnedLabels = new Set(); // 被筛选逻辑「钉住」显示标签的标记（移开鼠标也不撤）
/* 当前因为「鼠标悬停」而临时挂上标签的非教学楼标记。
   和 pinnedLabels 是两回事，别混：
     pinnedLabels     —— 筛选逻辑主动保留的，不该被清理动作误撤
     hoverShownLabels —— 纯粹跟着鼠标走的临时标签，鼠标一离开就该消失
   用它才能做到「清理悬停残留时，不误伤常驻/筛选标签」。 */
let hoverShownLabels = new Set();
/* 为什么用外面的 Map 存点位，而不是 marker.setExtData()：
   高德的 setExtData(v) 是**整体覆盖**一个值，取的时候 getExtData() 不带键，
   想存多个字段就得自己拼对象，很容易取错（第一版就因此把标签渲染成了 undefined）。
   挂在外面的 Map 里更直白，标记和点位的对应关系一眼可见。 */


/* 底图风格跟着主题走：黑夜模式下高德默认的浅色瓦片会亮得刺眼。
   为什么放函数里而不是写死常量：主题可以在页面运行中被切掉，
   写死就只在首帧正确，之后切换就"卡"在旧底图上了。 */
function mapStyleForTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "amap://styles/dark" : "amap://styles/normal";
}

/* 主题切换后重设底图。
   ⚠️ 用 MutationObserver 盯 <html data-theme>，而不是在每个切换按钮里回调：
      applyTheme() 是主题的唯一入口，盯属性就能全覆盖 ——
      顶栏开关、系统深色偏好变化、别的页面回来的首帧，一个都不漏。 */
function watchMapTheme() {
  let cur = mapStyleForTheme();
  try {
    new MutationObserver(() => {
      const next = mapStyleForTheme();
      if (next === cur || !map) return;
      cur = next;
      try { map.setMapStyle(next); } catch (_) { /* 底图接口偶发失败不该影响地图可用 */ }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  } catch (_) { /* 老浏览器没有 MutationObserver，就当不跟随 */ }
}

/* ---------- 初始化地图 ---------- */
async function initMap() {
  handlePlannerTarget();   // 先填目的地，别等地图（地图 Key 没配也要能看到终点）
  fillBuildingOptions();   // 点位联想候选：不依赖地图，先填好
  try {
    const AMap = await loadAMap();
    AMapLib = AMap;
    // zoom 17 + 校园中心：默认就落在校园上。
    // 不给太大范围（原来用 16）是因为点位都在校园内，拉远了看不出楼与楼的关系。
    map = new AMap.Map("map", { zoom: 17, center: CAMPUS_CENTER, mapStyle: mapStyleForTheme() });
    watchMapTheme();
    // 步行路径规划插件：路线由它画在地图上（蓝色实线 + 转向箭头）
    walking = new AMap.Walking({ map, hideMarkers: false });
    geocoder = new AMap.Geocoder({ city: CAMPUS.city });
    maybeDrawGraphDebug();   // 路网校准图层（默认关闭，见函数注释）

    // 校园点位标记：按类型分色，可点击（点击后按「最近聚焦的输入框」填入名字）
    //
    // 标签策略（45 个点全带标签会糊成一片）：
    //   · 教学楼（10 个）—— **常驻**标签。用户最常去，光看圆点认不出哪栋是哪栋。
    //   · 其他类型     —— 平时只有圆点（颜色表类型），**鼠标移上去才显示名字**；
    //                     筛选后也一样（只保证圆点还在，标签仍然靠悬停）——
    //                     用户要的就是「地图上永远只有教学楼带字，别的别来挤」。
    markersByType = {};
    markerPlace = new Map();
    markerIndex = new Map();
    // 每个类型内部按纬度从北到南排一遍再建标记 ——
    // 这样同类里序号相邻的两个点，在地图上南北相邻，标签一上一下正好错开。
    const ordered = [...POIS].sort((a, b) => (b.lat - a.lat));
    const seqByType = {};
    ordered.forEach(p => {
      const conf = PLACE_TYPES[p.type] || { label: "其他", color: "#4a5568" };
      const isTeaching = p.type === "teaching";
      seqByType[p.type] = (seqByType[p.type] || 0) + 1;
      const idx = seqByType[p.type];
      const dir = idx % 2 === 0 ? "bottom" : "top";
      const marker = new AMap.Marker({
        position: [p.lng, p.lat], title: `${p.name}（${conf.label}）`, map,
        icon: makeDotIcon(conf.color, isTeaching ? 7 : 5.5),
        label: null,
        cursor: "pointer",
        zIndex: isTeaching ? 110 : 100,
      });
      // 标签方向记录在 index 里，applyLabel / hover 时用
      markerIndex.set(marker, idx);
      if (isTeaching) {
        // 教学楼：常驻标签（用同样的方向错开，南北相邻的楼不会叠字）。
        // 走自定义 HTML 而不是高德原生字符串标签，是为了和悬停标签字号一致。
        // ⚠️ 必须走 setHardenedLabel —— 它会顺手按死高德容器自带的纯蓝边框。
        setHardenedLabel(marker, teachingLabelHtml(p, dir), dir);
      } else {
        // 非教学楼：**只有鼠标悬停时才显示名字**，移开立刻收回。
        marker.on("mouseover", () => {
          if (!pinnedLabels.has(marker)) {
            setHardenedLabel(marker, placeLabelHtml(p, dir), dir);
            hoverShownLabels.add(marker);
          }
        });
        marker.on("mouseout", () => {
          if (!pinnedLabels.has(marker)) clearHardenedLabel(marker);
          hoverShownLabels.delete(marker);
        });
      }
      marker.on("click", () => pickPOI(p.name));
      markerPlace.set(marker, p);
      (markersByType[p.type] = markersByType[p.type] || []).push(marker);
    });
    // ★ 清理「悬停残留」标签 —— 保证「鼠标不在这点上，名字就不该在」。
    //
    // 为什么非要这一层（实测抓到的真 bug，不是防御性洁癖）：
    //   非教学楼的标签靠 marker 的 "mouseover" / "mouseout" 开关。
    //   但高德的 marker 事件**在边界情况下靠不住**：实测悬停一个点之后
    //   把鼠标移到地图空白处，mouseout **根本没触发**，那个标签就永久
    //   留在地图上了（复现出来的是「外语系」）。之后再拖地图、滚轮缩放、
    //   切换筛选，它都赖着不走 —— 滚两下地图，广告牌似的堆一片。
    //
    // 做法：不再指望 marker 的 mouseout，改为在**容器原生 DOM 事件**上兜底。
    //   浏览器给的原生事件是可靠的，高德那套事件系统是它自己的封装。
    //     mousemove  —— 指针在地图上移动时，检查它是否还在那个标记的 DOM 上，
    //                   不在就立刻收回。这是最直接、最可靠的一条。
    //     mouseleave —— 指针离开整个地图区域。
    //     movestart / zoomstart —— 拖动、缩放的瞬间（指针这时必然已经离开了原来的点）。
    //
    // ⚠️ 只清 hoverShownLabels（悬停临时标签），绝不碰教学楼常驻标签和筛选钉标签。
    const sweepHoverLabels = () => {
      hoverShownLabels.forEach(m => {
        if (!pinnedLabels.has(m)) clearHardenedLabel(m);
      });
      hoverShownLabels.clear();
    };

    const mapEl = document.getElementById("map");
    if (mapEl) {
      // 指针在地图上移动：只要它离开了当前显示标签的那个标记，就收回标签。
      // 用 DOM 的 elementFromPoint 判定「指针此刻在谁身上」，比监听 marker
      // 的 mouseout 稳得多 —— 后者会漏事件，前者是浏览器直接算出来的。
      mapEl.addEventListener("mousemove", (ev) => {
        if (hoverShownLabels.size === 0) return;
        const top = document.elementFromPoint(ev.clientX, ev.clientY);
        hoverShownLabels.forEach(m => {
          const dom = m.dom;
          const stillOnIt = dom && top && (top === dom || dom.contains(top) || top.contains(dom));
          if (!stillOnIt && !pinnedLabels.has(m)) {
            clearHardenedLabel(m);
            hoverShownLabels.delete(m);
          }
        });
      });
      mapEl.addEventListener("mouseleave", sweepHoverLabels);
    }
    map.on("movestart", sweepHoverLabels);
    map.on("zoomstart", sweepHoverLabels);
    renderTypeFilter();   // 类型筛选按钮（依赖 markersByType，所以放在建完标记之后）
    // 初始视野框住全部点位（校园其实挺大，南北区跨度约 1 公里）
    fitMarkers(Object.keys(markersByType));
    toast(`地图已加载，共 ${POIS.length} 个校园点位，点击标记可快速填入起终点`, "ok");
  } catch (e) {
    document.getElementById("map").innerHTML =
      `<div style="padding:30px;color:var(--ink-soft)">${e.message}<br>请在 <b>js/config.js</b> 填入高德 JS API Key 与安全密钥。</div>`;
  }
}

/**
 * 造一个圆形小点当标记图标（内联 SVG，省一次图片请求）。
 * 用 data: URI 而不是图片文件，是为了让颜色能跟类型走。
 *
 * ⚠️ 曾经这里的尺寸是自相矛盾的（也是「标签和字大小不匹配、看着有留白」的根因）：
 *       size = r * 2 + 6   —— 画布按「直径 2r + 6」算
 *       circle 的 r = r/2  —— 但圆只画了半径 r/2
 *    传 r=11 时画布 28px、圆半径却只有 5.5px，圆点只占画布的两成，
 *    剩下全是透明 —— 图标中心（AMap 按 size/2 对齐到坐标点）和视觉圆点
 *    差了半个画布，标签看着就像「飘在旁边、和点对不上」。
 *
 * 现在两边统一：圆半径就是 r，画布 = 直径 + 描边余量。
 *
 * @param {string} color  填充色，如 "#2b6cb0"
 * @param {number} r      圆点半径（像素），与画布严格自洽
 */
function makeDotIcon(color, r) {
  const stroke = 2;                 // 白色描边宽度（也是下面 size 的余量来源）
  const size = r * 2 + stroke + 2;  // 直径 + 描边 + 1px 抗锯齿余量
  const c = size / 2;
  // 半径要减去半个描边，否则描边会溢出画布被裁掉一半，圆看着比预期小
  const rr = r - stroke / 2;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">`
    + `<circle cx="${c}" cy="${c}" r="${rr}" fill="${color}" stroke="#fff" stroke-width="${stroke}"/></svg>`;
  return new AMapLib.Icon({
    size: new AMapLib.Size(size, size),
    image: "data:image/svg+xml;base64," + btoa(svg),
    imageSize: new AMapLib.Size(size, size),
    // anchor 显式钉在画布正中心 —— 不写的话高德按默认规则算，
    // 在不同 size 下偏移量不一致，圆点和坐标会错开。
    anchor: new AMapLib.Pixel(size / 2, size / 2),
  });
}

/**
 * 渲染「类型筛选」按钮行。
 * 45 个点全铺在地图上会糊，所以给一排按钮，点一下切换某类的显示。
 */
function renderTypeFilter() {
  const box = document.getElementById("type-filter");
  if (!box) return;

  // 按 PLACE_TYPES 的顺序生成，只列真的有数据的类型
  const items = Object.keys(PLACE_TYPES).filter(t => markersByType[t] && markersByType[t].length);
  const chips = items.map(t => {
    const conf = PLACE_TYPES[t];
    const n = markersByType[t].length;
    return `<button class="type-chip" data-type="${t}" onclick="toggleType('${t}')">`
      + `<span class="dot" style="background:${conf.color}"></span>`
      + `${conf.label}<b>${n}</b></button>`;
  }).join("");

  box.innerHTML = `<button class="type-chip all" data-type="__all__" onclick="toggleType('__all__')">`
    + `全部<b>${POIS.length}</b></button>` + chips;
  syncTypeChips();
}

/** 把按钮的选中态与 visibleTypes 同步（选中的高亮） */
function syncTypeChips() {
  document.querySelectorAll("#type-filter .type-chip").forEach(el => {
    const t = el.dataset.type;
    const on = t === "__all__"
      ? visibleTypes === null                 // 「全部」只在真的全选时点亮
      : (visibleTypes === null || visibleTypes.has(t));
    el.classList.toggle("on", !!on);
  });
}

/**
 * 点类型按钮：**独立切换**该类型的显示 / 隐藏。
 *
 * ==================================================================
 * 这里换过一次交互语义，原因值得记下来（实测问题：「无法取消全选模式」）。
 *
 * 旧逻辑是个「单选 / 全选」两态的开关：
 *     从全选点某类      -> 只看这一类
 *     在单选态再点这一类 -> **直接跳回全选**
 *     点「全部」        -> 回全选
 * 看着挺简洁，但实际用起来很别扭：用户想从「只看教学楼」变成
 * 「教学楼 + 食堂一起看」时，一点食堂 —— 因为此时 visibleTypes 已不是 null，
 * 走的是 add 分支没错；可一点回教学楼想「取消教学楼」，size===1 就**整个弹回全选 45 个**，
 * 用户要的「取消这一个」变成了「全部恢复」。所以用户会觉得
 * 「取消不了全选」—— 想微调，一按就全回来。
 *
 * 现在改成**标准多选**语义，每一个类型按钮都是独立的开 / 关：
 *     · 点某类：选中 -> 取消选中；没选中 -> 加入选中
 *     · 全部取消 = 回到全选（等价于点「全部」）—— 避免地图一片空白，
 *       这个兜底必须留着，否则用户能把自己点进「什么都看不见」的死角。
 *     · 点「全部」= 清空筛选，恢复全选。
 * 这样「取消某一个」就真的只取消那一个，不会再误伤其它选择。
 * ==================================================================
 * @param {string} type  点位类型，或 "__all__"
 */
function toggleType(type) {
  const all = Object.keys(markersByType).filter(t => markersByType[t].length);

  if (type === "__all__") {
    visibleTypes = null;                          // 「全部」：清空筛选
  } else if (visibleTypes === null) {
    // 当前是全选 -> 第一下点击理解为「只看这一类」（用户最常想要的）
    visibleTypes = new Set([type]);
  } else if (visibleTypes.has(type)) {
    visibleTypes.delete(type);                    // 取消这一类
    // 全取消光了就回到全选，别让地图空着（用户会以为程序坏了）
    if (!visibleTypes.size) visibleTypes = null;
  } else {
    visibleTypes.add(type);                       // 再加一类（多选）
  }

  all.forEach(t => {
    const show = visibleTypes === null || visibleTypes.has(t);
    markersByType[t].forEach(m => {
      if (show) m.show(); else m.hide();
      applyLabel(m, t, show);
    });
  });
  syncTypeChips();

  // 视野跟着筛选走：只看食堂时缩到食堂范围，否则它们可能都在屏幕外。
  // fitMarkers() 会算剩下这些标记的外接矩形并框住它。
  fitMarkers(all.filter(t => visibleTypes === null || visibleTypes.has(t)));
}

/**
 * 决定一个标记要不要显示名字标签。
 *
 * 规则（验收口径）：
 *   · **默认状态**：只有教学楼常驻显示标签，其它类型一律只有圆点，
 *     鼠标悬停时才把名字亮出来 —— 避免几十个标签糊成一片。
 *   · **筛选到只剩少数几类时**（需求：「如果单选的话这类的 POI 就开启显示」）：
 *     把当前选中类型的标签**全部亮出来**。理由很直接 ——
 *     屏幕上本来就被过滤掉一大半，点少了不挤，这时候直接看到名字比
 *     一个个去悬停方便得多。这也是用户点筛选的本来目的。
 *
 * 判据 `visibleTypes !== null`：它非空就代表用户主动筛过（不是全选状态）。
 * 为了不让「点两类 = 十几个标签」又糊掉，再加一道数量闸：
 * 选中的点总数不超过 LABEL_AUTO_MAX 才全亮，超过就维持悬停显示。
 *
 * @param {AMap.Marker} m
 * @param {string} type
 * @param {boolean} visible 该标记当前是否可见
 */
const LABEL_AUTO_MAX = 14;   // 筛选后自动亮标签的上限（超过就还是悬停显示，防糊）

function applyLabel(m, type, visible) {
  const p = markerPlace.get(m);

  // ⚠️ 看不见的标记，标签必须一起清掉。
  //    高德 marker.hide() 只隐藏图标本身，label 是另一个 DOM 元素，不受影响 ——
  //    第一版就是漏了这一步，导致「只看教学楼」时食堂的点没了、
  //    但食堂的名字还挂在地图上，看着像数据没过滤干净。
  if (!visible) {
    pinnedLabels.delete(m);
    clearHardenedLabel(m);
    return;
  }

  // 教学楼：常驻标签（恢复显示时要挂回来）
  if (type === "teaching") {
    const dir = labelDirOf(m);
    if (p) setHardenedLabel(m, teachingLabelHtml(p, dir), dir);
    else clearHardenedLabel(m);
    return;
  }

  // 非教学楼：只在「筛选态 + 数量不多」时把标签亮出来并钉住，
  // 其余情况一律收起（名字留给悬停）。
  if (visibleTypes !== null && visibleTypes.has(type) && labelAutoCount() <= LABEL_AUTO_MAX) {
    const dir = labelDirOf(m);
    if (p) {
      setHardenedLabel(m, placeLabelHtml(p, dir), dir);
      pinnedLabels.add(m);       // 钉住：移开鼠标也不撤
      hoverShownLabels.delete(m);
    } else {
      clearHardenedLabel(m);
    }
    return;
  }

  // 常规情况：**收起标签**，名字只在悬停时出现。
  pinnedLabels.delete(m);
  clearHardenedLabel(m);
}

/** 当前筛选状态下可见的点位总数（用来判断「点够不够少、要不要全亮标签」） */
function labelAutoCount() {
  if (visibleTypes === null) return POIS.length;
  let n = 0;
  Object.keys(markersByType).forEach(t => {
    if (visibleTypes.has(t)) n += markersByType[t].length;
  });
  return n;
}

/**
 * 决定标签挂在标记的上方还是下方。
 *
 * 高德的标签不会自动防碰撞，同一条街上挨着的两栋楼标签会直接叠成一块，
 * 只看得见上面那个。这里按「在同类里按纬度排序的序号」奇偶交替上下，
 * 让南北相邻的点错开 —— 属于力所能及的缓解，不是彻底解法。
 */
function labelDirOf(m) {
  const idx = markerIndex.get(m) || 0;
  return idx % 2 === 0 ? "bottom" : "top";
}

/** 生成点位标签的 HTML（彩色小方块 + 名字），非教学楼用。
 *
 *  @param {CampusPlace} p
 *  @param {"top"|"bottom"|"right"} [dir] 为了错开相邻标签的叠字，由调用方交替传 top/bottom */
function placeLabelHtml(p, dir) {
  const conf = PLACE_TYPES[p.type] || { color: "#4a5568" };
  const offset = dir === "bottom" ? "margin-top:10px" : "margin-bottom:2px";
  return `<span class="poi-label" style="--poi-color:${conf.color};${offset}">`
    + `${escapeHtml(p.name)}</span>`;
}

/** 教学楼标签的 HTML —— 同样走 .poi-label，保证两者字号/内边距完全一致。
 *  @param {CampusPlace} p
 *  @param {"top"|"bottom"} dir */
function teachingLabelHtml(p, dir) {
  return `<span class="poi-label teaching" style="--poi-color:#2b6cb0;`
    + `${dir === "bottom" ? "margin-top:10px" : "margin-bottom:2px"}">`
    + `${escapeHtml(p.name)}</span>`;
}

/* ==================================================================
   标签容器的样式（含高德自带的纯蓝边框）—— 一律交给 style.css 处理
   ------------------------------------------------------------------
   ## 曾经踩的坑：创四小镇外面那个蓝框
   高德注入的样式表里**写死**了：

       .amap-marker-label {
         position: absolute; z-index: 2;
         border: 1px solid rgb(0, 0, 255);      <- 纯蓝，就是它
         background-color: rgb(255, 255, 255);
         padding: 3px; font-size: 12px; line-height: 14px;
       }

   ## 曾经的做法（已废弃，留着是为了别再走一遍弯路）
   写了一个 hardenLabelBox()，想「拿到标签的 DOM，把样式行内按死」。
   思路本身没错（行内样式优先级最高），**但它拿不到 DOM**：
   `marker.getLabel()` 返回的是 {content, direction} 纯数据对象，
   没有 .dom / .domElement（实测两者都是 false）。
   于是那个函数每次都在 `if (!label || !label.style) return;` 直接返回，
   **一行都没执行过** —— 以为修好了，其实什么都没做。

   ## 现在的做法
   全部收敛到 style.css 的 `.amap-marker-label` 规则里。
   实测 computed style 确认生效（border 为 0、pointer-events 为 none）。
   好处：一处声明、随样式表一起加载，不存在「标签插入瞬间还没样式」的窗口。
   ================================================================== */

/** 给某个 marker 挂标签，并把容器样式按死。
 *
 *  @param {any} marker 高德 Marker
 *  @param {string} html 标签内容
 *  @param {"top"|"bottom"|"right"} dir 方向
 *
 * ⚠️ 关于「怎么拿到标签的 DOM」：
 *   `marker.getLabel()` 返回的是 **{content, direction} 这种纯数据对象**，
 *   **没有 .dom / .domElement**。所以想通过它去改标签容器的样式是行不通的
 *   （实测 dom 与 domElement 都是 false）。样式统一交给 style.css 的
 *   `.amap-marker-label` 规则处理，这里不再尝试逐元素改。 */
function setHardenedLabel(marker, html, dir) {
  marker.setLabel({ content: html, direction: dir });
}

/**
 * 撤掉标签（并把引用清干净）。
 *
 * ⚠️⚠️ 这里踩过一个**静默失效**的大坑，务必看清：
 *
 *   原本写的是 `marker.setLabel(null)` —— 这是高德文档里「移除标签」的写法，
 *   看起来天经地义。**但它在本项目用的这版高德里是空操作**：
 *   标签 DOM 一点没动，标签永远留在地图上。
 *
 *   实测证据（同一个 marker，数页面上 .amap-marker-label 的数量）：
 *       设置前 10 个 -> setLabel({...}) 后 11 个 -> setLabel(null) 后 **还是 11 个**
 *   而且这不是「只留下空壳」—— 文字、色块全都在，和没清一样。
 *
 *   后果：所有「悬停显示、移开就收」的功能**全是假的**。鼠标划过一个点，
 *   它的名字就永久钉在地图上了。滚一圈地图，几十个标签糊成一片。
 *   （用户看到的现象正是「悬停后就一直显示」，根因就是这一行。）
 *
 *   正确写法：把 content 设成空串。这会让高德真正把标签 DOM 撤掉：
 *       设置前 10 -> setLabel({content:''}) 后 **10**
 *   而不是移除 DOM 元素 —— 那样虽然看着干净了，marker 内部的 content
 *   状态还在，下次再挂标签会错乱。
 *
 * @param {any} marker 高德 Marker
 */
function clearHardenedLabel(marker) {
  marker.setLabel({ content: "" });
}

/**
 * 把地图视野框到指定类型的标记上。
 * 只有一个点时不 setFitView（那会让地图缩到最大层级），改为居中。
 * @param {string[]} types 要纳入视野的类型
 */
function fitMarkers(types) {
  if (!map) return;
  const pts = [];
  types.forEach(t => (markersByType[t] || []).forEach(m => pts.push(m.getPosition())));
  if (!pts.length) return;
  try {
    if (pts.length === 1) {
      map.setZoomAndCenter(18, pts[0]);
      return;
    }
    // 自己算外接矩形，比 setFitView(传 Marker 数组) 干净 ——
    // 后者需要在图上放临时 Marker，会留下多余节点。
    //
    // 两个细节：
    //   1. padding 用地图尺寸的比例（而不是固定 60px）：地图窄的时候固定值不够，
    //      点位会贴到边缘上，看着像被裁掉了。
    //   2. bounds 强制一个**最小跨度**。只有两三个点挨得极近时（比如全在
    //      东区餐厅那一小片），外接矩形会小到一个点那么大，地图会缩到最大层级，
    //      周围什么都没有，反而看不出这些点在学校哪个位置。
    const el = document.getElementById("map");
    const w = (el && el.clientWidth) || 600, h = (el && el.clientHeight) || 500;
    const pad = [Math.round(h * 0.10), Math.round(w * 0.10),
                 Math.round(h * 0.10), Math.round(w * 0.10)];
    const lngs = pts.map(p => p.lng), lats = pts.map(p => p.lat);
    const MIN_LNG = 0.0035, MIN_LAT = 0.0022;   // 约 350m × 240m，够看出校园格局
    let wLng = Math.min(...lngs), eLng = Math.max(...lngs);
    let sLat = Math.min(...lats), nLat = Math.max(...lats);
    const padLng = Math.max(0, MIN_LNG - (eLng - wLng)) / 2;
    const padLat = Math.max(0, MIN_LAT - (nLat - sLat)) / 2;
    wLng -= padLng; eLng += padLng; sLat -= padLat; nLat += padLat;

    map.setBounds(new AMapLib.Bounds([wLng, sLat], [eLng, nLat]), false, pad);
  } catch (_) { /* 视野调整失败不影响功能，忽略 */ }
}

/* 记录「用户最后在哪个框里打字」，点地图标记时就知道该填起点还是终点 */
function bindFieldFocus() {
  [["origin", "origin"], ["dest", "dest"]].forEach(([id, key]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("focus", () => { lastFocusedField = key; highlightField(key); });
  });
  highlightField(lastFocusedField);
}

/* 给当前会被填入的那个输入框加高亮，让用户一眼知道点地图会填到哪 */
function highlightField(key) {
  ["origin", "dest"].forEach(k => {
    const el = document.getElementById(k);
    if (el) el.classList.toggle("field-target", k === key);
  });
}

/** 点击地图标记 / 常用楼按钮时调用：把楼名填进当前聚焦的框 */
function pickPOI(name) {
  const el = document.getElementById(lastFocusedField);
  if (!el) return;
  el.value = name;
  const which = lastFocusedField === "origin" ? "起点" : "终点";
  toast(`已把「${name}」填入${which}`, "ok");
  // 填完自动跳到另一个框，方便连续操作；若另一个框已有值则不跳
  const other = lastFocusedField === "origin" ? "dest" : "origin";
  const otherEl = document.getElementById(other);
  if (otherEl && !otherEl.value.trim()) {
    lastFocusedField = other;
    highlightField(other);
  }
}

/* 来自 planner 的 ?to=<楼名> */
function handlePlannerTarget() {
  const q = new URLSearchParams(location.search);
  const to = q.get("to");

  // 只认楼名。以前还会用 ?lng=&lat= 回填一串「经度,纬度」，
  // 但经纬度输入已经去掉了，填进去反而会解析失败 —— 所以这里改成：
  // 有楼名就直接用；只给了坐标（没楼名）就反查是哪个楼，查不到就不填。
  if (to) {
    document.getElementById("dest").value = to;
    toast("已带入课程所在教学楼，点击「开始导航」", "ok");
    return;
  }

  const lng = Number(q.get("lng")), lat = Number(q.get("lat"));
  if (lng && lat) {
    const hit = POIS.find(p => Math.abs(p.lng - lng) < 1e-5 && Math.abs(p.lat - lat) < 1e-5);
    if (hit) {
      document.getElementById("dest").value = hit.name;
      toast("已带入课程所在教学楼，点击「开始导航」", "ok");
    }
  }
}

/**
 * 起点留空时用的**默认出发点**（校内）。
 *
 * ## 为什么需要它
 *
 * 起点留空意味着「我没指定从哪走」。旧版这时会去取当前位置，但那个功能已经
 * 整个删掉了（见文件头：定位不准，得不偿失）。如果什么都不做，用户点了
 * 「开始导航」会**什么也没发生**，还得自己再想一遍「那我该填什么」。
 * 校园就那么大、校内点位的坐标都是查证过的真实值，不如**直接给一个默认
 * 出发点把路线先算出来**，再把「如不合适请改起点」写在提示里 ——
 * 导航先能用，比让用户先读一段说明重要。
 *
 * 选图书馆是因为它在校园中部，去哪个楼的路线都不会离谱。
 */
function defaultOrigin() {
  for (const n of ["图书馆", "博文楼", "商德楼"]) {
    const p = POIS.find(x => x.name === n);
    if (p) return p;
  }
  return POIS[0];   // 兜底：至少有一个点位（POIS 为空时上层另有提示）
}

/* ---------- 起终点解析 ---------- */

/** 精确匹配楼名 */
function resolveByPOI(str) {
  return POIS.find(p => p.name === str.trim());
}

/**
 * 把用户输入的**点位名**解析成坐标。
 *
 * 点位不限于教学楼 —— 宿舍、食堂、图书馆、游泳馆都能作为目的地，
 * 全部来自 CAMPUS_PLACES（坐标是查证过的高德 POI 实际值）。
 *
 * 只接受点位名，「经度,纬度」这种输入方式已经去掉，原因有三：
 *   1. 用户手里根本没有楼栋的经纬度，这个入口实际没人会用；
 *   2. 手填坐标很容易写成「纬度,经度」（反了），算出来在几千公里外，
 *      而且报错信息很难懂，不如干脆不给这个入口；
 *   3. 点位名走 CAMPUS_PLACES 查表，坐标是真实值，不会错。
 *
 * 输入含模糊成分时（多打了空格、写「博文」而全名是「博文楼」）做一次宽松匹配，
 * 免得因为差一个字就失败。**尤其要容忍楼名后的分区/教室号**：
 * 「知行楼A」「知行楼B203」「知行楼(北)」都解析到「知行楼」同一个坐标 ——
 * 一栋楼只有一个点，A 座 B 座都在里面，没必要（也没法）分开导。
 *
 * @returns {Promise<[number, number]|null>}
 */
async function resolvePoint(str) {
  const s = (str || "").trim();
  if (!s) return null;

  // 1) 精确匹配
  const exact = resolveByPOI(s);
  if (exact) return [exact.lng, exact.lat];

  // 2) 去掉尾巴上的分区/编号，再精确比一次
  //    「知行楼A」「知行楼B203」「知行楼301」「知行楼（北）」-> 「知行楼」
  const cleanTail = t => t
    .replace(/[（(【\[].*?[)）】\]]/g, "")
    .replace(/[A-Za-z]\d*$/g, "")
    .replace(/[0-9]+$/g, "")
    .replace(/[-\s·]+$/g, "")
    .trim();
  const ct = cleanTail(s);
  if (ct && ct !== s && ct.length >= 2) {
    const hit = POIS.find(p => p.name === ct || cleanTail(p.name) === ct);
    if (hit) return [hit.lng, hit.lat];
  }

  // 3) 宽松匹配：去掉「楼/栋」再比，或名字互相包含
  const strip = t => t.replace(/[楼栋\s]/g, "");
  const loose = POIS.find(p => strip(p.name) === strip(s) && strip(s).length >= 2)
             || POIS.find(p => p.name.includes(s) && s.length >= 2)
             // ⚠️ 必须加上「输入里包含点位名」这条（前缀匹配），
             //    否则「知行楼A」这种带分区的写法一条都匹配不上。
             //    长的名字优先，避免「博文」抢在「博文楼」前面。
             || [...POIS].sort((a, b) => b.name.length - a.name.length)
                  .find(p => s.includes(p.name) && p.name.length >= 2)
             || POIS.find(p => strip(p.name).includes(strip(s)) && strip(s).length >= 2);
  if (loose) return [loose.lng, loose.lat];

  return null;
}

/** 填充点位联想候选（原生 datalist，输入即筛选）。
 *  候选来自 CAMPUS_PLACES 全量点位 —— 教学楼、宿舍、食堂、图书馆都能选。 */
function fillBuildingOptions() {
  const el = document.getElementById("building-options");
  if (!el) return;
  el.innerHTML = POIS.map(p =>
    `<option value="${escapeHtml(p.name)}"></option>`).join("");
}

/* ==================================================================
   导航：校内本地路网 + 校外交回高德
   ------------------------------------------------------------------
   ★ 以后要换寻路算法，改这里就行 ★

   walkRoute() 是唯一的路线出口：给起点、终点两个坐标，让它把路线画出来。
   校内 → planCampusRoute()（campus-graph.js 的步道图 + Dijkstra）；
   任一端在校园路网覆盖不到的地方 → 回落 AMap.Walking。
   换算法只需改 planCampusRoute 或 walkRoute 的函数体，
   startNav() / clearRoute() / 分步指引都不用动。

   参数约定：
     origin, dest   [lng, lat]
     labels         {originLabel, destLabel} 用于分步指引的标题
   ================================================================== */
function walkRoute(origin, dest, labels) {
  labels = labels || {};

  // ---- 校内优先：起终点都在校园范围内时，走自己的路网图 ----
  // 高德步行规划只认市政道路，校内小路它没有数据，会把人带出校门绕一大圈。
  // 校内路线用 CAMPUS_GRAPH（见 campus-graph.js）+ Dijkstra 自己算。
  let campus = null;
  try { campus = planCampusRoute(origin, dest); } catch (_) { /* 路网异常不挡高德兜底 */ }
  if (campus) {
    drawCampusRoute(campus, labels);
    return;
  }
  // ---- 校外/路网覆盖不到：回落高德（先清掉上一条校内自绘线）----
  clearCampusPolylines();
  walking.search(origin, dest, (status, result) => {
    if (status === "complete" && result && result.routes && result.routes.length) {
      const r = result.routes[0];
      routeDrawn = true;
      const meters = Math.round(Number(r.distance) || 0);
      const mins = Math.max(1, Math.round((Number(r.time) || 0) / 60));
      // 视野自动框住整条路线（Walking 插件已把线画在图上）
      try { map.setFitView(null, false, [70, 70, 70, 70]); } catch (_) { /* 忽略 */ }

      renderSteps(r, labels);
      toast(`步行约 ${meters} 米，约 ${mins} 分钟`, "ok");
      return;
    }

    // 失败：把高德返回的真实原因说清楚，而不是笼统一句「失败」
    const raw = String(result || "");
    if (raw.includes("INVALID_USER_SCODE")) {
      toast("路线服务被高德拒绝：JS Key 的「安全密钥」无效。"
        + "请到高德控制台该 Key 详情页生成安全密钥，填进 js/config.js 的 AMAP_SECURITY_CODE。", "error");
    } else if (raw.includes("INVALID_USER_KEY") || raw.includes("USERKEY_PLAT_NOMATCH")) {
      toast("高德 Key 无效或类型不对：请确认用的是「Web端(JS API)」类型的 Key。", "error");
    } else if (raw.includes("DAILY_QUERY_OVER_LIMIT")) {
      toast("今天的调用次数用完了，明天再试或换一个 Key。", "error");
    } else if (raw.includes("NO_ROUTE") || raw.includes("no result")) {
      toast("这两点之间没有可步行的道路。换个起终点试试。", "error");
    } else {
      toast(`路线规划失败（${raw || "原因未知"}）。换个起终点试试。`, "error");
    }
  });
}

/**
 * 把高德返回的分步指引渲染到右侧面板，形成「怎么走」的文字说明。
 * @param {any} route 高德 route 对象（含 steps）
 * @param {{originLabel?:string, destLabel?:string}} labels
 */
function renderSteps(route, labels) {
  const box = document.getElementById("route-steps");
  if (!box) return;
  const steps = (route && route.steps) || [];
  if (!steps.length) { box.innerHTML = ""; return; }

  const meters = Math.round(Number(route.distance) || 0);
  const mins = Math.max(1, Math.round((Number(route.time) || 0) / 60));
  const from = labels.originLabel || "起点";
  const to = labels.destLabel || "终点";

  const head = `<div class="route-head">`
    + `<b>${escapeHtml(from)}</b> → <b>${escapeHtml(to)}</b>`
    + `<span class="route-meta">全程 ${meters} 米 · 约 ${mins} 分钟</span></div>`;

  const rows = steps.map((s, i) => {
    const d = Math.round(Number(s.distance) || 0);
    const road = s.road ? ` <span class="route-road">${escapeHtml(s.road)}</span>` : "";
    return `<li><i>${i + 1}</i><span>${escapeHtml(s.instruction || "继续前行")}${road}`
      + `<em>${d} 米</em></span></li>`;
  }).join("");

  box.innerHTML = head + `<ol class="route-list">${rows}</ol>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- 导航入口 ---------- */
async function startNav() {
  if (!walking) {
    return toast("地图或路线服务未就绪。请刷新页面；若仍不行，"
      + "多半是高德 JS Key 的「安全密钥」没配好。", "error");
  }

  const originEl = document.getElementById("origin");
  const originStr = (originEl && originEl.value || "").trim();
  const destStr = document.getElementById("dest").value.trim();
  if (!destStr) return toast("请填写终点（教学楼、宿舍、食堂等校内点位都行）", "error");

  const dest = await resolvePoint(destStr);
  if (!dest) {
    return toast(`终点「${destStr}」不是校园里的点位。`
      + "请从右侧「校内点位」里点一个，或在输入框里从候选列表里挑。", "error");
  }

  let origin = null, originLabel = "";

  if (originStr) {
    origin = await resolvePoint(originStr);
    if (!origin) {
      return toast(`起点「${originStr}」识别不了。请从右侧「校内点位」里选一个，`
        + "或清空起点的输入框，就会用校内默认出发点（图书馆）。", "error");
    }
    originLabel = originStr;
  } else {
    // ★ 起点留空 = 用校内默认出发点 ★
    //
    // 为什么不用定位：该功能已整体删除（见文件头）。浏览器只在
    // https / localhost 下才给坐标，手机走 http 局域网必然拿不到；就算拿到，
    // 没有 GPS 时是网络推算的，偏几百米到几公里都正常 —— 拿它当起点会把
    // 路线带到校外绕一大圈。校内点位的坐标是查证过的真实值，比定位可靠得多。
    //
    // 兜底出发点**写回输入框**：否则起点框空着、指引里却写「从图书馆出发」，
    // 用户会以为导航算错了（或以为起点丢了），也没法一眼看出该在哪儿改。
    const fb = defaultOrigin();
    if (!fb) {
      return toast("起点为空，且校内点位表没加载出来。请从右侧「校内点位」里选一个再点。", "error");
    }
    origin = [fb.lng, fb.lat];
    originLabel = fb.name;
    if (originEl) originEl.value = fb.name;
    toast(`起点留空，已默认从「${fb.name}」出发；可在「起点」里改成任意校内地名。`, "info");
  }

  clearRoute();
  walkRoute(origin, dest, { originLabel, destLabel: destStr });
}

let campusPolylines = [];    // 校内自绘路线（实线主路 + 两端虚线接入段）

/** 清掉所有校内自绘线 */
function clearCampusPolylines() {
  campusPolylines.forEach(p => { try { map.remove(p); } catch (_) {} });
  campusPolylines = [];
}

/**
 * 把 planCampusRoute 的结果画到地图上并渲染文字指引。
 * 与高德 walking.clear() 对应，clearRoute 里也会清掉这条线。
 *
 * 画法跟高德一致：起终点到路网的「接入段」用灰色虚线，
 * 路网上的主路线用蓝色实线（带方向箭头）。
 */
function drawCampusRoute(route, labels) {
  if (!AMapLib || !map) return;
  routeDrawn = true;

  const pts = route.points;                 // [起点, 节点..., 节点, 终点]
  const solid = pts.length > 2 ? pts.slice(1, -1) : pts;
  const legIn  = pts.length > 2 ? [pts[0], pts[1]] : null;
  const legOut = pts.length > 2 ? [pts[pts.length - 2], pts[pts.length - 1]] : null;

  const dashedOpts = {
    strokeColor: "#8a97a8", strokeWeight: 5, strokeOpacity: 0.95,
    strokeStyle: "dashed", strokeDasharray: [8, 6], lineJoin: "round",
  };

  if (legIn)  campusPolylines.push(new AMapLib.Polyline({ path: legIn,  ...dashedOpts }));
  campusPolylines.push(new AMapLib.Polyline({
    path: solid,
    strokeColor: "#0a7cff", strokeWeight: 6, strokeOpacity: 0.9,
    lineJoin: "round", showDir: true,      // 箭头，跟高德路线一个观感
  }));
  if (legOut) campusPolylines.push(new AMapLib.Polyline({ path: legOut, ...dashedOpts }));
  map.add(campusPolylines);
  try { map.setFitView(campusPolylines, false, [70, 70, 70, 70]); } catch (_) { /* 忽略 */ }

  const mins = Math.max(1, Math.round(route.meters / 80));   // 校内步行 ~80m/min
  renderCampusSteps(route, labels);
  toast(`校内步行约 ${route.meters} 米，约 ${mins} 分钟（校内路线）`, "ok");
}

/**
 * 校内路线的文字指引：按「地标 -> 地标」分段列出，比高德的转向指令更贴合校园。
 * 复用高德路线那套样式类（route-head / route-list），视觉上保持一致。
 *
 * route.nodeNames 是**途中经过的地标**（由 campus-graph.js 按「路线离地标多近」
 * 算出来的）。起点/终点本身就是地标时，首尾两行会自动省略，避免出现
 * 「从 正德楼 到达 正德楼」这种废话。
 */
function renderCampusSteps(route, labels) {
  const box = document.getElementById("route-steps");
  if (!box) return;

  const from = labels.originLabel || "起点";
  const to = labels.destLabel || "终点";
  const mins = Math.max(1, Math.round(route.meters / 80));

  const head = `<div class="route-head">`
    + `<b>${escapeHtml(from)}</b> → <b>${escapeHtml(to)}</b>`
    + `<span class="route-meta">全程 ${route.meters} 米 · 约 ${mins} 分钟（校内路线）</span></div>`;

  const names = route.nodeNames || [];
  const legs = route.legs || [];
  const rows = [];
  let i = 1;

  if (!names.length) {
    // 全程没有经过任何地标（例如就走在同一条路上）：别硬编，如实说一句
    rows.push(`<li><i>${i++}</i><span>沿校内步道，从 ${escapeHtml(from)} 直达`
      + `<strong>${escapeHtml(to)}</strong><em>${route.meters} 米</em></span></li>`);
  } else {
    if (names[0] !== from) {
      rows.push(`<li><i>${i++}</i><span>从 ${escapeHtml(from)} 出发，前往`
        + `<strong>${escapeHtml(names[0])}</strong></span></li>`);
    }
    legs.forEach(l => {
      rows.push(`<li><i>${i++}</i><span>沿校内步道，经 ${escapeHtml(l.from)} 到`
        + `<strong>${escapeHtml(l.to)}</strong><em>${l.meters} 米</em></span></li>`);
    });
    const last = names[names.length - 1];
    if (last !== to) {
      rows.push(`<li><i>${i++}</i><span>从 ${escapeHtml(last)} 到达`
        + `<strong>${escapeHtml(to)}</strong></span></li>`);
    }
  }

  box.innerHTML = head + `<ol class="route-list">${rows.join("")}</ol>`
    + `<p style="margin:8px 0 0;font-size:12.5px;color:var(--ink-soft);">`
    + `已按校园内部步道规划，不会绕出校门；虚线是到楼门口的最后一段，具体走向看地图上的蓝线。</p>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/**
 * 路网调试：控制台执行 localStorage.setItem('campus_graph_debug','1')
 * 后刷新，把全部节点（绿点）和边（灰线）画出来，带名字的节点额外标名字
 * （现在只有地标最近的那个节点有名字，所以标签不会糊成一片）。
 */
function maybeDrawGraphDebug() {
  if (localStorage.getItem("campus_graph_debug") !== "1" || !AMapLib) return;
  const nodes = Object.entries(CAMPUS_GRAPH.nodes).map(([id, p]) =>
    new AMapLib.CircleMarker({
      center: [p.lng, p.lat], radius: 3,
      strokeColor: "#0a7", fillColor: "#0c8", fillOpacity: 1,
    }));
  const nodeLabels = Object.values(CAMPUS_GRAPH.nodes).filter(p => p.name).map(p =>
    new AMapLib.Marker({
      position: [p.lng, p.lat],
      content: `<span style="font-size:11px;color:#066;background:#fff8;padding:0 2px;">${p.name}</span>`,
    }));
  const edges = CAMPUS_GRAPH.edges.map(e =>
    new AMapLib.Polyline({
      path: edgePoints(e), strokeColor: "#999", strokeWeight: 2, strokeStyle: "dashed",
    }));
  map.add([...nodes, ...nodeLabels, ...edges]);
  console.info("[campus-graph] 调试图层已画。核对完执行 localStorage.removeItem('campus_graph_debug') 关闭。");
}

function clearRoute() {
  if (walking && routeDrawn) walking.clear();
  clearCampusPolylines();
  routeDrawn = false;
  const box = document.getElementById("route-steps");
  if (box) box.innerHTML = "";
}

/* ---------- 天气提示 ----------
 *
 * 取数分两级，**任何一级成功就能显示真实天气**：
 *
 *   ① 后端 `/map/weather`：后端配了高德 Web 服务 Key 时数据最全，
 *      还能让 GLM 顺手写一句口语化出行建议。
 *   ② 浏览器直连 Open-Meteo：免费、免 Key、支持跨域。后端拿不到实况时走它。
 *
 * ⚠️ 实测出现过：天气检测不到」，根因是 backend/.env 里的 AMAP_WEB_KEY
 *    默认是空的（config.py 里还是个 TODO），于是后端只能返回占位文案
 *    「暂时拿不到实时天气…」。加上第 ② 级兜底后，**不配任何 Key 也能出天气**。
 */
async function loadWeather() {
  const el = document.getElementById("weather-text");
  const box = document.getElementById("weather-tip");
  const icoBox = box ? box.querySelector(".h-ico") : null;

  let live = {};
  let tip = "";

  // ① 优先问后端（配了高德 Key 时最全，tip 还可能是 GLM 写的）
  try {
    const data = await api(`/map/weather?city_adcode=${CONFIG.CITY_ADCODE}`);
    live = data.raw || {};
    tip = data.tip || "";
  } catch {
    /* 后端没起 / 网络不通：不在这里报错，交给下面的兜底 */
  }

  // ② 后端拿不到实况 -> 浏览器直接查 Open-Meteo
  if (!live.temperature) {
    const om = await fetchOpenMeteo();
    if (om) {
      live = om;
      tip = buildWeatherTip(om);
    }
  }

  // 两级都失败才显示占位，页面不会空白
  if (!live.temperature) {
    el.textContent = tip || "暂时拿不到实时天气，出门前记得看一眼窗外，按需增减衣物。";
    resetWeatherVisual(box, icoBox);
    return;
  }

  const city = live.city || "";
  const weather = live.weather || "";
  const badge = `${city} ${weather} ${live.temperature}℃`.replace(/\s+/g, " ").trim();

  // 标题旁的图标 + 文字配色，都跟着实况天气换
  // （原来固定挂一个「云+雨点」+ 紫色徽标，晴天看着很违和）
  const v = weatherVisual(weather);
  if (icoBox) {
    icoBox.innerHTML = icon(v.name, 20);
    icoBox.style.color = v.color;
    icoBox.setAttribute("title", `当前天气：${v.label}`);
  }
  if (box) {
    box.classList.add("has-wx");
    box.style.setProperty("--wx", v.color);
  }

  el.innerHTML =
    `<span class="weather-live">${badge}</span>` +
    `<span class="weather-advice">${tip || ""}</span>`;
}

/** 拿不到天气时把图标/配色恢复中性，别留下上一次的晴雨颜色。 */
function resetWeatherVisual(box, icoBox) {
  if (icoBox) {
    icoBox.innerHTML = icon("weather", 20);
    icoBox.style.color = "";
    icoBox.removeAttribute("title");
  }
  if (box) {
    box.classList.remove("has-wx");
    box.style.removeProperty("--wx");
  }
}

/* ----- Open-Meteo 兜底（免费、免 Key）----- */

/** WMO 天气码 -> 中文文案。Open-Meteo 用的是这套码。 */
const WMO_TEXT = {
  0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
  45: "雾", 48: "雾凇",
  51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
  56: "冻雨", 57: "冻雨",
  61: "小雨", 63: "中雨", 65: "大雨",
  66: "冻雨", 67: "冻雨",
  71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
  80: "阵雨", 81: "阵雨", 82: "强阵雨",
  85: "阵雪", 86: "阵雪",
  95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
};

/** 风向度数 -> 中文 8 方位。 */
function degToDir(deg) {
  if (deg == null || isNaN(deg)) return "";
  const dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"];
  const i = Math.round((((deg % 360) + 360) % 360) / 45) % 8;
  return dirs[i];
}

/** 风速 km/h -> 蒲福风级，跟高德 `windpower` 的口径对齐。 */
function kmhToPower(kmh) {
  if (kmh == null || isNaN(kmh)) return "";
  const edges = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118];
  let level = 0;
  for (const e of edges) { if (kmh >= e) level++; else break; }
  return String(level);
}

/**
 * 浏览器直连 Open-Meteo 取实况 + 3 天预报。
 * 校园坐标是固定的，所以不需要定位权限，精度也够用。
 * 返回的字段名刻意对齐高德口径（weather / temperature / humidity…），
 * 这样上面的渲染逻辑和后端那条路径完全共用一套，不用写两遍。
 */
async function fetchOpenMeteo() {
  const c = (typeof CAMPUS !== "undefined" && CAMPUS.center) || [119.447887, 26.005046];
  const url = "https://api.open-meteo.com/v1/forecast"
    + `?latitude=${c[1]}&longitude=${c[0]}`
    + "&current=temperature_2m,relative_humidity_2m,weather_code,"
    + "wind_speed_10m,wind_direction_10m"
    + "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    + "&timezone=Asia%2FShanghai&forecast_days=3";
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    const j = await res.json();
    const cur = j.current || {};
    if (cur.temperature_2m == null) return null;
    const daily = j.daily || {};
    const codes = daily.weather_code || [];
    return {
      city: "福州",
      weather: WMO_TEXT[cur.weather_code] || "多云",
      temperature: Math.round(cur.temperature_2m),
      humidity: cur.relative_humidity_2m == null ? "" : String(Math.round(cur.relative_humidity_2m)),
      winddirection: degToDir(cur.wind_direction_10m),
      windpower: kmhToPower(cur.wind_speed_10m),
      forecast: (daily.time || []).map((d, i) => ({
        date: d,
        dayweather: WMO_TEXT[codes[i]] || "",
        nightweather: WMO_TEXT[codes[i]] || "",
        daytemp: Math.round((daily.temperature_2m_max || [])[i]),
        nighttemp: Math.round((daily.temperature_2m_min || [])[i]),
      })),
      source: "open-meteo",
    };
  } catch {
    return null;
  }
}

/** 前端规则版出行建议（与后端 `_rule_based_tip` 同口径，只是简化了）。 */
function buildWeatherTip(live) {
  const weather = live.weather || "";
  const t = Number(live.temperature);
  if (/雨|雪|雷|冰雹|冻/.test(weather)) return "有雨雪，出门记得带伞，注意路面湿滑。";
  if (!isNaN(t) && t >= 32) return "天气偏热，注意防晒补水。";
  if (!isNaN(t) && t <= 5) return "气温很低，注意保暖，多穿一件。";
  return "气温适中，出门可酌情带把伞以防万一。";
}

/* ---------- 点位快捷列表 ---------- */
/**
 * 渲染「常用点位」快捷按钮，**按类型分组**显示。
 * 45 个点平铺成一长条没法看，所以按类型分块，每块前面加个小标题。
 */
function renderPOIs() {
  const box = document.getElementById("poi-list");
  if (!box) return;

  // 按 PLACE_TYPES 的顺序分组，只输出有数据的类型
  const html = Object.keys(PLACE_TYPES).map(type => {
    const conf = PLACE_TYPES[type];
    const items = POIS.filter(p => p.type === type);
    if (!items.length) return "";
    const chips = items.map(p =>
      `<span class="poi" style="border-left:3px solid ${conf.color}" `
      + `role="button" tabindex="0" onclick="pickPOI('${p.name}')">${p.name}</span>`).join("");
    return `<div class="poi-group">`
      + `<div class="poi-group-head"><span class="dot" style="background:${conf.color}"></span>`
      + `${conf.label}<b>${items.length}</b></div>`
      + `<div class="poi-chips">${chips}</div></div>`;
  }).join("");

  box.innerHTML = html;
}
/** 兼容旧调用（常用楼按钮以前叫 usePOI） */
function usePOI(name) { pickPOI(name); }

/* ---------- 启动 ---------- */
renderTopbar("map");

// 把 data-icon 占位换成内联 SVG（不依赖 emoji 字体）
hydrateIcons();
renderPOIs();
bindFieldFocus();
loadWeather();
initMap();
