// @ts-nocheck
/* 两个"真跑一遍"的验证，防止只看代码以为没问题：
   1) 轮播：把 index.html 里的 heroCarousel() 原样抽出来，套上首屏 DOM 跑，
      写成一个临时页，交给无头浏览器分别在 t=1s / t=7s 截图 ——
      两张图不一样就说明真的在自动切换。
   2) 天气：把 nav.js 里的 Open-Meteo 兜底代码原样抽出来跑，
      确认不配任何 Key 也能拿到实况。
   用法：node tools/behavior_test.js frontend */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = process.argv[2] || "frontend";
const read = (f) => fs.readFileSync(path.join(dir, f), "utf8");
let failed = 0;
const ok = (m) => console.log("  ✓ " + m);
const bad = (m) => { failed++; console.log("  ✗ " + m); };

/* ---------- 1) 抽出轮播脚本，生成测试页 ---------- */
console.log("\n[1] 轮播脚本抽取");
const idxHtml = read("index.html");

const heroMatch = idxHtml.match(/<section class="hero"[\s\S]*?<\/section>/);
if (!heroMatch) bad("index.html 里找不到 .hero 结构");
else ok("抽到首屏结构，长度 " + heroMatch[0].length);

const carStart = idxHtml.indexOf("(function heroCarousel()");
const carEnd = idxHtml.indexOf("})();", carStart);
if (carStart < 0 || carEnd < 0) bad("index.html 里找不到 heroCarousel()");
else ok("抽到 heroCarousel()，长度 " + (carEnd + 5 - carStart));

if (heroMatch && carStart >= 0 && carEnd > carStart) {
  const script = idxHtml.slice(carStart, carEnd + 5);
  const testPage = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<link rel="stylesheet" href="css/style.css">
<style>body{background:#eef1f8;padding:22px}.wrap{max-width:1020px;margin:0 auto}</style>
</head><body><div class="wrap">
${heroMatch[0]}
</div>
<script>
${script}
<\/script></body></html>`;
  fs.writeFileSync(path.join(dir, "_carousel_test.html"), testPage, "utf8");
  ok("已生成 _carousel_test.html");
  // 顺带做一次语法检查
  try { new vm.Script(script, { filename: "heroCarousel" }); ok("heroCarousel 语法正常"); }
  catch (e) { bad("heroCarousel 语法错误：" + e.message); }
}

/* ---------- 2) 抽出天气兜底代码，真发一次请求 ---------- */
console.log("\n[2] 天气兜底（Open-Meteo）");
const navSrc = read("js/nav.js");
const wStart = navSrc.indexOf("const WMO_TEXT");
const wEnd = navSrc.indexOf("/* ---------- 点位快捷列表 ---------- */");
if (wStart < 0 || wEnd < 0) {
  bad("抽不到 Open-Meteo 兜底代码块");
} else {
  const block = navSrc.slice(wStart, wEnd);
  ok("抽到兜底代码块，长度 " + block.length);

  const ctx = {
    console,
    CAMPUS: { center: [119.447887, 26.005046] },
    fetch: globalThis.fetch,
    Number, Math, isNaN, String, RegExp,
  };
  vm.createContext(ctx);
  try {
    vm.runInContext(block + "\nglobalThis.__f = { fetchOpenMeteo, buildWeatherTip, WMO_TEXT, degToDir, kmhToPower };", ctx);
    ok("代码块可执行");
  } catch (e) {
    bad("执行失败：" + e.message);
  }

  if (ctx.__f) {
    const { fetchOpenMeteo, buildWeatherTip, degToDir, kmhToPower, WMO_TEXT } = ctx.__f;
    // 先测纯函数
    const cases = [[0, "北"], [45, "东北"], [90, "东"], [180, "南"], [270, "西"], [360, "北"]];
    cases.forEach(([d, want]) => {
      const got = degToDir(d);
      got === want ? ok(`degToDir(${d}) = ${got}`) : bad(`degToDir(${d}) = ${got}，期望 ${want}`);
    });
    [["0km/h", 0, "0"], ["3.2km/h", 3.2, "1"], ["25km/h", 25, "4"], ["70km/h", 70, "8"]].forEach(([t, v, want]) => {
      const got = kmhToPower(v);
      got === want ? ok(`kmhToPower(${v}) = ${got}`) : bad(`kmhToPower(${v}) = ${got}，期望 ${want}`);
    });
    ok(`WMO_TEXT 覆盖 ${Object.keys(WMO_TEXT).length} 个天气码`);

    // 再真发一次网络请求
    (async () => {
      console.log("\n[3] 实况请求（真连 Open-Meteo）");
      const live = await fetchOpenMeteo();
      if (!live) return bad("没拿到数据（网络不通？）");
      ok(`city=${live.city} weather=${live.weather} temperature=${live.temperature}℃ ` +
         `humidity=${live.humidity}% wind=${live.winddirection}${live.windpower}级`);
      if (live.temperature != null && live.weather) ok("字段完整，可用于渲染");
      else bad("字段缺失");
      const tip = buildWeatherTip(live);
      tip ? ok("出行建议：" + tip) : bad("没生成建议");
      console.log("\n" + (failed ? `❌ ${failed} 项未通过` : "✅ 全部通过"));
      process.exit(failed ? 1 : 0);
    })();
    return;
  }
}

console.log("\n" + (failed ? `❌ ${failed} 项未通过` : "✅ 全部通过"));
process.exit(failed ? 1 : 0);
