// @ts-check
/* 内联 SVG 图标集：不依赖 emoji 字体，任何系统都能正确显示。 */

/**
 * 生成一个 SVG 图标。
 * @param {"planner"|"emotion"|"map"|"calendar"|"building"|"target"|"clock"|"brain"|"weather"|"pin"|"inbox"|"upload"|"wand"|"sun"|"cloudy"|"overcast"|"rain"|"storm"|"snow"|"fog"|"wind"|"moon"|"leaf"|"contrast"} name
 * @param {number} [size] 像素尺寸，默认 24
 * @returns {string} SVG 字符串
 */
function icon(name, size = 24) {
  const S = `width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"`;
  const PATHS = {
    // 学业规划：书本
    planner: `<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z"/>
              <path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H19v3H6.5A2.5 2.5 0 0 1 4 20.5z"/>
              <path d="M9 7.5h6M9 11h4"/>`,
    // 情绪陪伴：心形 + 微笑
    emotion: `<path d="M12 20.5S3.5 15 3.5 9.2A4.7 4.7 0 0 1 12 6.6a4.7 4.7 0 0 1 8.5 2.6c0 5.8-8.5 11.3-8.5 11.3z"/>`,
    // 地图导航：定位针 + 路径
    map: `<path d="M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20z"/>
          <path d="M9 4v13.5M15 6.5V20"/>`,
    calendar: `<rect x="3.5" y="5" width="17" height="15" rx="2.5"/>
               <path d="M3.5 10h17M8 3v4M16 3v4"/>
               <path d="M8 14h3v3H8z"/>`,
    building: `<path d="M5 21V6.5A1.5 1.5 0 0 1 6.5 5h7A1.5 1.5 0 0 1 15 6.5V21"/>
               <path d="M15 11h3.5A1.5 1.5 0 0 1 20 12.5V21M3 21h18"/>
               <path d="M8.5 9h3M8.5 13h3M8.5 17h3"/>`,
    target: `<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>`,
    clock: `<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>`,
    brain: `<path d="M12 5.5a3 3 0 0 0-5.7-1.2A3 3 0 0 0 4 8.4a3.2 3.2 0 0 0 .4 5.4A3 3 0 0 0 8 19.5a2.8 2.8 0 0 0 4-2.6z"/>
            <path d="M12 5.5a3 3 0 0 1 5.7-1.2A3 3 0 0 1 20 8.4a3.2 3.2 0 0 1-.4 5.4A3 3 0 0 1 16 19.5a2.8 2.8 0 0 1-4-2.6z"/>
            <path d="M12 5.5v11.4"/>`,
    weather: `<path d="M7 17.5a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17.3 9a3.8 3.8 0 0 1 .2 8.5z"/>
              <path d="M9.5 20.5l-.8 1.5M14 20.5l-.8 1.5M18.5 20.5l-.8 1.5"/>`,

    /* ---------- 按天气切换的一组图标 ----------
       原来标题旁边永远挂着一个「云+雨点」，晴天看着很别扭。
       现在按实况的 weather 文案选图标，晴/阴/雨/雪/雾各不相同。
       几何统一在 24×24 视窗里，互相切换时视觉重量接近。 */
    // 晴：太阳（光芒八向）
    sun: `<circle cx="12" cy="12" r="4.6"/>
          <path d="M12 2.6v2.2M12 19.2v2.2M4.4 12H2.2M21.8 12h-2.2
                   M6.3 6.3 4.8 4.8M19.2 19.2l-1.5-1.5M17.7 6.3l1.5-1.5M4.8 19.2l1.5-1.5"/>`,
    // 多云：太阳露在**左上**、云在右下压住 —— 比「云右上角挂个小圆」辨识度高得多
    cloudy: `<circle cx="8.4" cy="7.6" r="3.1"/>
             <path d="M8.4 2.4v1.7M3.2 7.6H1.5M4.6 3.8 3.4 2.6M12.2 3.8l1.2-1.2M13.8 11.6H15.5"/>
             <path d="M10.2 20.4a3.9 3.9 0 0 1-.3-7.8 5.3 5.3 0 0 1 10 1.3 3.7 3.7 0 0 1-.3 6.5z"/>`,
    // 阴：一朵云（无太阳）
    overcast: `<path d="M7.2 18.6a4.1 4.1 0 0 1-.4-8.2 5.6 5.6 0 0 1 10.6 1.4 3.9 3.9 0 0 1-.3 6.8z"/>
               <path d="M9.4 21.4h7.6"/>`,
    // 雨：云 + 下面三根雨点（斜着落）
    rain: `<path d="M7 16.6a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17.3 8.1a3.8 3.8 0 0 1 .2 8.5z"/>
           <path d="M9.4 19.4l-1 1.9M13.2 19.4l-1 1.9M17 19.4l-1 1.9"/>`,
    // 雷阵雨：云 + 闪电（闪电刻意画大、往下探出云外，一眼和普通阵雨区分开）
    storm: `<path d="M6.6 15.4a3.9 3.9 0 0 1-.4-7.8A5.4 5.4 0 0 1 16.6 7a3.7 3.7 0 0 1 .2 8.4z"/>
            <path d="M12.9 16.6h3.5l-2.6 5.4.5-3.6h-2.6z"/>`,
    // 雪：云 + 三片雪花（用小星号表示）
    snow: `<path d="M7 16.4a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17.3 7.9a3.8 3.8 0 0 1 .2 8.5z"/>
           <path d="M9.4 19.4v2.2M8.5 20.5h1.8M13.2 19.4v2.2M12.3 20.5h1.8M17 19.4v2.2M16.1 20.5h1.8"/>`,
    // 雾 / 霾：几条横线
    fog: `<path d="M6.2 13.4a3.4 3.4 0 0 1-.2-6.8 4.6 4.6 0 0 1 8.7 1.1 3.2 3.2 0 0 1 .2 5.7z"/>
          <path d="M4.6 16.6h13.2M7.4 19.6h11M3.6 20.4h5"/>`,
    // 大风：风的流线
    wind: `<path d="M3.4 8.6h9.8a2.4 2.4 0 1 0-2.4-2.4"/>
           <path d="M3.4 12.8h13a2.6 2.6 0 1 1-2.6 2.6"/>
           <path d="M3.4 16.6h6.4"/>`,
    pin: `<path d="M12 21.5s6.5-6 6.5-10.5a6.5 6.5 0 0 0-13 0C5.5 15.5 12 21.5 12 21.5z"/>
          <circle cx="12" cy="11" r="2.4"/>`,
    inbox: `<path d="M3.5 13.5h4l1.2 2.2h6.6l1.2-2.2h4"/>
            <path d="M6 4.5h12l2.5 9v6H3.5v-6z"/>`,
    // 学士帽
    cap: `<path d="M2.8 8.6 12 4.3l9.2 4.3L12 12.9z"/>
          <path d="M6.5 10.6v4.6c0 1.5 2.5 2.7 5.5 2.7s5.5-1.2 5.5-2.7v-4.6"/>
          <path d="M20.5 9.4v4.8"/>`,
    // 用户
    user: `<circle cx="12" cy="8.2" r="3.7"/>
           <path d="M4.8 20.2a7.2 7.2 0 0 1 14.4 0"/>`,
    // 提示条图标
    check: `<circle cx="12" cy="12" r="9"/><path d="m8.4 12.2 2.5 2.5 4.7-4.9"/>`,
    alert: `<path d="M12 3.6 2.6 20.2h18.8z"/><path d="M12 9.5v4.6M12 17.4h.01"/>`,
    // 情绪陪伴：气泡对话。整体上移 0.75，抵消尾巴下沉带来的视觉偏下
    chat: `<path d="M20.5 11.45c0 4-3.8 7.2-8.5 7.2a9.6 9.6 0 0 1-2.6-.35l-4.8 1.45 1.1-3.6a6.9 6.9 0 0 1-2.2-5.05c0-4 3.8-7.2 8.5-7.2s8.5 3.2 8.5 7.2z"/>`,
    // 加号（课表格子里的「＋」按钮）
    plus: `<path d="M12 5.2v13.6M5.2 12h13.6"/>`,
    // 罗盘（导航）
    compass: `<circle cx="12" cy="12" r="9"/>
              <path d="m15.6 8.4-1.9 5.3-5.3 1.9 1.9-5.3z"/>`,
    // 闪光（AI / 提示）
    sparkle: `<path d="M12 3.6 13.9 9l5.4 1.9-5.4 1.9L12 18.2 10.1 12.8 4.7 10.9 10.1 9z"/>
              <path d="M18.8 3.6v3.2M20.4 5.2h-3.2"/>`,
    // 箭头（"去做什么"）
    arrow: `<path d="M4.5 12h14.2M13.4 6.6 18.8 12l-5.4 5.4"/>`,
    // 复制（沿用上周）
    copy: `<rect x="8.6" y="3.6" width="11.8" height="11.8" rx="2.2"/>
           <path d="M15.4 20.4H5.8a2.2 2.2 0 0 1-2.2-2.2V8.6"/>`,
    // 退出登录
    logout: `<path d="M14.5 20.4H6.4a2.2 2.2 0 0 1-2.2-2.2V5.8a2.2 2.2 0 0 1 2.2-2.2h8.1"/>
             <path d="M17.2 8.4 20.8 12l-3.6 3.6M20.2 12H9.6"/>`,
    // 伞（天气）
    umbrella: `<path d="M12 3.2a8.8 8.8 0 0 1 8.8 8.8H3.2A8.8 8.8 0 0 1 12 3.2z"/>
               <path d="M12 12v6.4a2.2 2.2 0 0 0 4.4 0"/>`,
    // 导入（导入课表：截图 / 文件）
    // ⚠️ 箭头朝**下**（落进托盘），不是朝上。
    // 用户反馈「导入图标箭头反一下」—— 原来画的是朝上，读起来像"上传/导出"，
    // 而这里的动作是把课表**导进来**，箭头应该往下扎进托盘才符合直觉。
    upload: `<path d="M12 4.4v11.2"/><path d="m7.8 11.4 4.2 4.2 4.2-4.2"/>
             <path d="M4.4 15.2v3a2 2 0 0 0 2 2h11.2a2 2 0 0 0 2-2v-3"/>`,
    // 魔法棒（AI 识别）
    wand: `<path d="m3.6 20.4 9.8-9.8"/><path d="m14.8 9.2 3.4-3.4"/>
           <path d="M17.6 3.2v2.6M18.9 4.5h-2.6M6.4 3.6v2M7.4 4.6h-2M20.6 12.4v2M21.6 13.4h-2"/>`,

    /* ---------- 主题切换三件套 ----------
       白天复用上面的 sun（太阳），另加月亮与叶子。
       为什么护眼用叶子而不是眼睛：眼睛读起来像"查看/可见"，
       而这里表达的是"柔和不刺眼"，叶子的自然/温和语义更准。 */
    moon: `<path d="M20.6 14.9A8.8 8.8 0 0 1 9.1 3.4a8.8 8.8 0 1 0 11.5 11.5z"/>`,
    leaf: `<path d="M20.6 3.4C10.2 3.4 3.4 8.3 3.4 15.6c0 2.9 2.3 5.1 5.2 5.1 7.3 0 12-6.8 12-17.3z"/>
           <path d="M3.4 20.6c2.2-5.6 5.6-9.5 10.4-12.2"/>`,
    // 跟随系统：左半空心、右半实心的「对比」圆 —— 一眼看出是「跟系统走」
    contrast: `<circle cx="12" cy="12" r="8.8"/><path d="M12 4.35a7.65 7.65 0 0 1 0 15.3z" fill="currentColor"/>`,
  };
  return `<svg ${S} aria-hidden="true">${PATHS[name] || ""}</svg>`;
}

/**
 * 把页面上带 data-icon 的元素批量替换成 SVG。
 * 用法：<div data-icon="planner" data-icon-size="26"></div>
 * @returns {void}
 */
function hydrateIcons() {
  document.querySelectorAll("[data-icon]").forEach((el) => {
    const name = el.getAttribute("data-icon") || "";
    const size = Number(el.getAttribute("data-icon-size")) || 24;
    el.innerHTML = icon(/** @type {any} */ (name), size);
  });
}

/**
 * 按「天气文案」挑图标 + 配色。
 *
 * 高德实况返回的 `weather` 是一段中文文案，取值很杂：
 *     晴 / 多云 / 阴 / 小雨 / 中雨 / 大雨 / 暴雨 / 阵雨 / 雷阵雨 /
 *     小雪 / 雨夹雪 / 雾 / 霾 / 大风 / 浮尘 / 未知 …
 * 直接做「精确等于」判断必然会漏，所以按**关键字**归类（顺序有讲究，见下）。
 *
 * ⚠️ 判定顺序不能乱：先看「雷」再看「雨」，先看「雪」再看「雨」——
 *    因为「雷阵雨」同时含「雷」和「雨」、「雨夹雪」同时含「雨」和「雪」，
 *    把更具体的现象放前面才不会归错类。
 *
 * @param {string} weather 高德给的天气文案（如「多云」「雷阵雨」）
 * @returns {{name: string, color: string, label: string}}
 *          name  —— 图标名（给 icon() 用）
 *          color —— 图标主题色（晴天暖橙、雨天蓝、雪天青…）
 *          label —— 人话描述，给 title 提示用
 */
function weatherVisual(weather) {
  const w = String(weather || "").trim();

  // ① 雷（含「雷阵雨」「雷雨」）—— 必须在「雨」之前判
  if (w.includes("雷")) return { name: "storm", color: "#7c3aed", label: "雷阵雨" };
  // ② 雪（含「雨夹雪」）—— 必须在「雨」之前判
  if (w.includes("雪")) return { name: "snow", color: "#0ea5e9", label: "雪" };
  // ③ 雨
  if (w.includes("雨")) return { name: "rain", color: "#2563eb", label: "雨" };
  // ④ 雾 / 霾 / 沙尘 —— 能见度类
  if (/雾|霾|沙|尘/.test(w)) return { name: "fog", color: "#64748b", label: "雾霾" };
  // ⑤ 大风
  if (w.includes("风")) return { name: "wind", color: "#0891b2", label: "大风" };
  // ⑥ 晴（含「晴朗」「晴天」）
  if (w.includes("晴")) return { name: "sun", color: "#f59e0b", label: "晴" };
  // ⑦ 多云（云 + 露出的太阳）
  if (w.includes("多云")) return { name: "cloudy", color: "#f59e0b", label: "多云" };
  // ⑧ 阴天（纯云）
  if (w.includes("阴")) return { name: "overcast", color: "#475569", label: "阴" };

  // 认不出来：退回原来的「云+雨点」，保持中性灰
  return { name: "rain", color: "#64748b", label: w || "天气" };
}

/**
 * 品牌 Logo：紫色渐变方块 + 「Y」艺术字。
 *
 * 与 stroke 图标的写法不同 —— 这里是实心图形，靠 <linearGradient> 上色，
 * 所以单独一个函数，不走 icon() 的 stroke 通道。
 *
 * @param {number} [size] 方块边长（px），默认 28（顶栏）；登录页用 34。
 * @returns {string} 完整的 svg 字符串（自带圆角与渐变）
 */
function logoMark(size = 28) {
  // 每个实例用独立 gradient id，避免同页多个 logo 互相覆盖
  const gid = "ygrad-" + Math.random().toString(36).slice(2, 8);
  return `<svg width="${size}" height="${size}" viewBox="0 0 40 40" fill="none"
      aria-hidden="true" class="logo-svg">
    <defs>
      <linearGradient id="${gid}" x1="4" y1="2" x2="36" y2="38"
        gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#6366f1"/>
        <stop offset=".55" stop-color="#8b5cf6"/>
        <stop offset="1" stop-color="#ec4899"/>
      </linearGradient>
    </defs>
    <!-- 圆角方块底 -->
    <rect x="0" y="0" width="40" height="40" rx="11" fill="url(#${gid})"/>
    <!-- 顶部高光，让方块有立体感 -->
    <rect x="0" y="0" width="40" height="20" rx="11" fill="#fff" opacity=".13"/>
    <!-- 「Y」艺术字：两笔斜向汇合 + 一笔下伸，圆头收笔 -->
    <path d="M11.4 10.6 20 21.4l8.6-10.8" stroke="#fff" stroke-width="4.6"
      stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M20 21.4V30.2" stroke="#fff" stroke-width="4.6"
      stroke-linecap="round"/>
  </svg>`;
}
