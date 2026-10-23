// @ts-check
/* 前端全局配置：API 地址与高德 Key。
 *
 * API_BASE 采用「自动推导」，这是为了让本项目在**任何电脑、任何目录、
 * 任何端口**下都能开箱即用，不需要任何人手动改这一行：
 *
 *   跟随当前网页的 hostname + 8000 端口推导：
 *     - PC 上用 http://127.0.0.1:5500 打开 -> 后端 http://127.0.0.1:8000
 *     - 手机用 http://192.168.x.x:5500 打开 -> 后端 http://192.168.x.x:8000
 *   这样手机访问时不会再去连 localhost（那是手机自己，必然失败）。
 *   若页面是以 file:// 直接双击打开的（没有 hostname），兜底 localhost:8000。
 *
 *   另有 window.__API_PORT__ 作为预留钩子（当前无人注入，详见 resolveApiBase）。
 */

/**
 * @typedef {Object} AppConfig
 * @property {string} API_BASE            后端 FastAPI 地址（自动推导）
 * @property {string} AMAP_JS_KEY         高德 JS API Key（前端地图）
 * @property {string} AMAP_SECURITY_CODE  高德 JS API 安全密钥
 * @property {string} CITY_ADCODE         学校所在城市 adcode（福州；高德天气用）
 */

/** 推导后端地址：跟随当前访问的主机名，端口默认 8000。 */
function resolveApiBase() {
  // 0) 【同源】当前页面所在的源就是后端时，直接用它 —— 连协议和端口一起跟。
  //
  //    为什么必须排在最前面：本函数原来一律拼
  //    `http://${host}:8000`，在**本机**没问题，但**公网部署后是 https**，
  //    拼出来的 https 页面去连 http 接口属于「混合内容」，会被浏览器**直接
  //    拦掉**，而且拦得很安静 —— 表现为整站连不上后端、控制台一条红字，
  //    最坏的情况是探测正好超时（后端冷启动），于是连兜底也落到这个 http
  //    地址上，页面就彻底白屏。
  //    同源地址天然带当前协议，http 页面得 http、https 页面得 https，
  //    不会踩混合内容。
  //
  //    两种「同源即后端」的形态：
  //      ① 页面在 /app/ 下 —— 这是本项目**默认部署形态**（后端托管前端）；
  //      ② 页面是 https —— 公网部署后前端必然由后端托管（本项目没有
  //         独立的 https 前端服务器），所以 https 一定是同源。
  //    刻意**不**把「任意 http 页面」都算作同源：前端单独跑在 5500 的开发
  //    习惯必须保留（见下面第 1 步），那时同源是静态服务器、不是后端。
  if (typeof window !== "undefined" && window.location) {
    const loc = window.location;
    const origin = loc.origin || "";
    const isHttpLike = /^https?:$/i.test(loc.protocol);
    const sameOriginIsBackend = isHttpLike && (/^\/app\//.test(loc.pathname) || loc.protocol === "https:");
    if (sameOriginIsBackend) {
      return origin.replace(/\/+$/, "") + "/api";
    }
  }

  // 1) 跟当前网页的 hostname 走 —— 手机 / 前后端分开跑的主路径。
  //    PC 上用 http://127.0.0.1:5500 打开 -> 后端 http://127.0.0.1:8000
  //    手机用 http://192.168.1.5:5500 打开 -> 后端 http://192.168.1.5:8000
  //    ⚠️ 这一步是手机能用的关键：绝不能写死 localhost/127.0.0.1，
  //       否则手机会去连「手机自己」，必然失败。
  if (typeof window !== "undefined" && window.location) {
    const host = window.location.hostname;
    if (host) {
      // __API_PORT__ 是一个**预留钩子**：目前全项目没有任何地方注入它
      // （已全仓库确认）。保留是因为将来若要改成「启动脚本生成一个 _env.js
      // 注入真实端口」就会用到，那时前端能零探测直接命中。
      // ⚠️ 之前这里的注释写着「启动脚本会注入实际端口」，那是**不成立的** ——
      //    注释描述了一个并不存在的机制，害得后来的人（包括我）以为注入已经接好、
      //    跑去别处找 bug。注释必须如实反映现状，所以这里写明「无人注入」。
      // @ts-ignore
      const port = (window.__API_PORT__ || 8000);
      return `http://${host}:${port}/api`;
    }
  }

  // 2) file:// 直接双击打开时没有 hostname，只能兜底到本机。
  //    这条路径 PC 上可用；手机上不存在（手机不会用 file:// 打开）。
  return "http://localhost:8000/api";
}

/** @type {AppConfig} */
const CONFIG = {
  API_BASE: resolveApiBase(),

  // ---- 高德地图（前端 JS API）----
  // 申请：https://console.amap.com/dev/key/app → 新建 Key → 选「Web端(JS API)」
  //
  // ⚠️ 这两个值**不是一回事**，别填成同一个：
  //
  //   AMAP_JS_KEY         JS API Key，形如 32 位十六进制。用于加载地图 SDK、
  //                       渲染地图、标记点位、展示天气。**不填只是地图空白**。
  //
  //   AMAP_SECURITY_CODE  安全密钥（jscode），在**同一个 Key 的详情页**里生成，
  //                       是一串和 Key 完全不同的随机值（形如
  //                       "a1b2c3d4e5f6...（50 位左右）"）。
  //
  //   为什么必须分开：高德的「路径规划 / 地理编码」接口做了签名校验，
  //   浏览器端由 JS SDK 自动用安全密钥算签名。**地图渲染不需要它，
  //   但点「开始导航」一定会需要** —— 否则高德会返回：
  //       INVALID_USER_SCODE（无效的用户签名）
  //   表现就是「地图能显示、点位能标，但一算路线就失败」。
  //   本项目已按这个错误码给出明确的中文提示（见 js/nav.js 的 startNav）。
  //
  //   取法：控制台 → 应用管理 → 我的应用 → 该 Key 右侧「设置」→ 安全密钥 → 生成。
  //
  // ⚠️ 建议在控制台给这个 Key 配置「域名白名单」，防止被他人盗用额度。
  AMAP_JS_KEY: "5c9db014509e532d761847c6d072660d",
  AMAP_SECURITY_CODE: "44022b965685790b39c4e1c277c475ac",   // 高德控制台生成的安全密钥（路径规划签名用）

  CITY_ADCODE: "350100",                     // 学校所在城市 adcode（福州；高德天气用）
};
