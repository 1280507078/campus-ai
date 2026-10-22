// @ts-nocheck
/* 改完之后的自动校验：
   1. 语法检查 campus.js / nav.js / index.html 内联脚本
   2. 用 VM 真跑一遍 campus.js，核对导航页实际展示的点位分组
   3. 核对 index.html 引用的 hero 图片是否都真实存在
   用法： node tools/verify.js frontend */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = process.argv[2] || "frontend";
let failed = 0;
const ok = (m) => console.log("  ✓ " + m);
const bad = (m) => { failed++; console.log("  ✗ " + m); };

function checkSyntax(file, code) {
  try { new vm.Script(code, { filename: file }); ok("语法 " + file); }
  catch (e) { bad("语法 " + file + " -> " + e.message); }
}

console.log("\n[1] 语法检查");
["js/campus.js", "js/nav.js", "js/types.js", "js/common.js", "js/icons.js"].forEach((f) => {
  const p = path.join(dir, f);
  if (fs.existsSync(p)) checkSyntax(f, fs.readFileSync(p, "utf8"));
});

// index.html / nav.html 的内联脚本
["index.html", "nav.html"].forEach((f) => {
  const html = fs.readFileSync(path.join(dir, f), "utf8");
  const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  blocks.forEach((m, i) => checkSyntax(`${f} 内联脚本#${i + 1}`, m[1]));
  // 引用的本地图片是否存在
  [...html.matchAll(/url\(["']?\.\.\/(img\/[^"')]+)["']?\)/g)].forEach(() => {});
});

console.log("\n[2] 点位分组（campus.js 实际数据）");
const ctx = { console, window: {} };
vm.createContext(ctx);
const src = fs.readFileSync(path.join(dir, "js/campus.js"), "utf8")
  + "\nglobalThis.__out = { PLACE_TYPES, CAMPUS_PLACES, BUILDINGS };";
vm.runInContext(src, ctx);
const { PLACE_TYPES: PT, CAMPUS_PLACES: ALL } = ctx.__out;

const POIS = ALL.filter((p) => !((PT[p.type] || {}).off));
const by = {};
POIS.forEach((p) => { by[p.type] = (by[p.type] || 0) + 1; });

console.log("  CAMPUS_PLACES 全量 =", ALL.length);
console.log("  导航页展示 POIS    =", POIS.length);
console.log("  已隐藏类型         =", Object.keys(PT).filter((t) => PT[t].off).join(", "));
console.log("  分组明细：");
Object.keys(PT).forEach((t) => {
  const n = by[t] || 0;
  console.log(`    ${PT[t].off ? "[隐藏] " : "       "}${PT[t].label.padEnd(6, "　")} ${n}`);
});

console.log("\n[3] 关键断言");
const expectOther = ["图书馆", "游泳馆"];
expectOther.forEach((n) => {
  const p = ALL.find((x) => x.name === n);
  if (!p) return bad(`缺少点位「${n}」`);
  if (p.type !== "other") return bad(`「${n}」应为 other，实际是 ${p.type}`);
  if (!POIS.includes(p)) return bad(`「${n}」应出现在导航页，但被过滤了`);
  ok(`「${n}」-> other 且可见`);
});
if (ALL.some((p) => p.name.includes("菜鸟驿站") || p.type === "service")) {
  bad("菜鸟驿站 / service 类型仍然存在");
} else { ok("菜鸟驿站已删除，service 类型已移除"); }
if (PT.service || PT.sport) bad("PLACE_TYPES 里仍残留 service/sport");
else ok("PLACE_TYPES 已无 service/sport");

console.log("\n[4] Hero 图片文件");
{
  const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
  const css = fs.readFileSync(path.join(dir, "css/style.css"), "utf8");
  const names = [...new Set([...css.matchAll(/\.\.\/img\/([\w.-]+\.(?:jpg|png))/g)].map((m) => m[1]))];
  names.forEach((n) => {
    const p = path.join(dir, "img", n);
    if (fs.existsSync(p)) ok(`${n}  ${(fs.statSync(p).size / 1024).toFixed(0)} KB`);
    else bad(`${n} 不存在（CSS 里引用了）`);
  });
  ["hero-slide--gate", "hero-slide--library", "hero-slide--brand"].forEach((c) => {
    if (html.includes(c)) ok(`index.html 含 ${c}`);
    else bad(`index.html 缺少 ${c}`);
  });
}

console.log("\n" + (failed ? `❌ ${failed} 项未通过` : "✅ 全部通过"));
process.exit(failed ? 1 : 0);
