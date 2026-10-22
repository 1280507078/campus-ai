# -*- coding: utf-8 -*-
"""把主题令牌 + 黑夜/护眼主题块注入竞赛树 style.css，并把它写死的浅色换成令牌。

原则（照抄开发树纪律）：
  · 表面色（底、描边、槽、淡色状态面板）→ 令牌
  · 彩底上的文字 `color:#fff`、`mask-image` 的 `#000`、英雄卡上的白点 → **一律不动**
  · 功能色（--brand/--emotion/--map/--warn/--danger/--ok）不随主题变
"""
import io, re, sys

P = "frontend/css/style.css"
src = io.open(P, encoding="utf-8").read()
lines = src.split("\n")

# (行号, 必须存在的旧串, 新串)  行号 1-based
SUBS = [
    (56,  "--ink-faint: #8b94a8;", "--ink-faint: #646e85;          /* 无障碍：原 #8b94a8 在白底上仅 3.04:1，低于 WCAG AA 正文 4.5:1；这个值最差 4.52:1 */"),
    (224, "background: #fff; color: var(--brand);", "background: var(--card); color: var(--brand);"),
    (271, "background: #fbfcfe; color: var(--ink);", "background: var(--card-2); color: var(--ink);"),
    (276, "background: #fff; box-shadow: var(--ring);", "background: var(--card); box-shadow: var(--ring);"),
    (278, "background: #fff7f7;", "background: var(--danger-bg-2);"),
    (306, "background: #fffafa;", "background: var(--danger-bg-2);"),
    (495, "linear-gradient(135deg, #dbeafe, #eff6ff)", "linear-gradient(135deg, var(--tint-planner), var(--tint-planner-2))"),
    (500, "linear-gradient(135deg, #fce7f3, #fdf2f8)", "linear-gradient(135deg, var(--tint-emotion), var(--tint-emotion-2))"),
    (638, "background: #eef1f8; border: 1px solid var(--line);", "background: var(--card-3); border: 1px solid var(--line);"),
    (698, "background: #fff;", "background: var(--card);"),
    (954, "background: #f1f5f9; color: #475569;", "background: var(--chip-neutral-bg); color: var(--chip-neutral-ink);"),
    (1144, "background: #fff;", "background: var(--card);"),
    (1185, "background: #f0f2fa;", "background: var(--card-3);"),
    (1229, "background: #fff; color: var(--brand);", "background: var(--card); color: var(--brand);"),
    (1245, "background: #fff; border-radius: var(--r-lg);", "background: var(--card); border-radius: var(--r-lg);"),
    (1268, "background: #fbfcfe;", "background: var(--card-2);"),
    (1285, "background: #fff;", "background: var(--card);"),
    (1321, "border: 1px solid #dcd8ff;", "border: 1px solid var(--brand-line);"),
    (1333, "background: #fff; border: 1px solid #dcd8ff;", "background: var(--card); border: 1px solid var(--brand-line);"),
    (1398, "background: #fbfcfe;", "background: var(--card-2);"),
    (1401, "border-color: #d8ddf0; background: #fff;", "border-color: var(--line); background: var(--card);"),
    (1403, "linear-gradient(135deg, #fff7ed, #fffbeb)", "linear-gradient(135deg, var(--warn-bg-3), var(--warn-bg))"),
    (1419, "background: #eef1f8;", "background: var(--card-3);"),
    (1490, "background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534;",
           "background: var(--ok-bg); border: 1px solid var(--ok-line); color: var(--ok-ink);"),
    (1520, "background: #fff; border: 1px solid var(--line);", "background: var(--card); border: 1px solid var(--line);"),
    (1697, "background: #fff; border: 1px solid var(--line); color: var(--ink-soft);",
           "background: var(--card); border: 1px solid var(--line); color: var(--ink-soft);"),
    (1754, "linear-gradient(90deg, #eef1f8 25%, #f6f8fd 37%, #eef1f8 63%)",
           "linear-gradient(90deg, var(--skel-1) 25%, var(--skel-2) 37%, var(--skel-1) 63%)"),
    (1770, "background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c;",
           "background: var(--danger-bg); border: 1px solid var(--danger-line); color: var(--danger-ink);"),
    (1771, "background: #fffbeb; border: 1px solid #fde68a; color: #b45309;",
           "background: var(--warn-bg); border: 1px solid var(--warn-line); color: var(--warn-ink);"),
    (1772, "background: #eff6ff; border: 1px solid #bfdbfe; color: #1d4ed8;",
           "background: var(--info-bg-2); border: 1px solid var(--info-line-2); color: var(--info-ink-2);"),
    (1773, "background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d;",
           "background: var(--ok-bg); border: 1px solid var(--ok-line); color: var(--ok-chip-ink);"),
    (1948, "color-mix(in srgb, var(--brand) 4%, #fff)", "color-mix(in srgb, var(--brand) 4%, var(--card))"),
    (2026, "color-mix(in srgb, var(--brand) 12%, #fff)", "color-mix(in srgb, var(--brand) 12%, var(--card))"),
    (2050, "linear-gradient(135deg, #fffbeb, #fef3c7) !important", "linear-gradient(135deg, var(--warn-bg), var(--warn-chip-bg)) !important"),
    (2076, "background: #fffbeb;", "background: var(--warn-bg);"),
    (2080, "background: #fff;", "background: var(--card);"),
    (2089, "background: #fffdf5;", "background: var(--warn-bg-2);"),
    (2093, "background: #fffbeb;", "background: var(--warn-bg);"),
    (2101, "background: #fff;", "background: var(--card);"),
    (2112, "background: #fffdf5;", "background: var(--warn-bg-2);"),
    (2132, "background: #fef3c7; color: #92400e;", "background: var(--warn-chip-bg); color: var(--warn-chip-ink);"),
    (2138, "background: #fffbeb;", "background: var(--warn-bg);"),
    (2152, "background: #fff;", "background: var(--card);"),
    (2213, "background: #fff;", "background: var(--card);"),
    (2224, "background: #fffbeb;", "background: var(--warn-bg);"),
    (2278, "background: #f1f5f9;", "background: var(--chip-neutral-bg);"),
    (2305, "background: #f8fafc;", "background: var(--card-2);"),
    (2307, "background: #fef2f2;", "background: var(--danger-bg);"),
    (2309, "background: #fffbeb;", "background: var(--warn-bg);"),
    (2327, "background: #fef3c7; color: #b45309;", "background: var(--warn-chip-bg); color: var(--warn-ink);"),
]

bad = []
for ln, old, new in SUBS:
    i = ln - 1
    if old not in lines[i]:
        bad.append((ln, old, lines[i]))
        continue
    lines[i] = lines[i].replace(old, new, 1)
if bad:
    print("!! 目标行与预期不符，已中止：")
    for b in bad:
        print("   行", b[0], "期望含", repr(b[1]), "实际", repr(b[2]))
    sys.exit(1)

TOKENS = """
  /* ---------- 主题令牌（浅色=白天为基准；护眼 / 黑夜在下方覆盖）----------
     为什么要有这一层：这些颜色原来**写死在各个选择器里**（`background:#fff` 这类），
     于是想做「白天 / 护眼 / 黑夜」就得改上百处、还极容易漏。现在反过来 ——
     选择器只引用令牌，主题只改令牌，加一套主题就是加一个覆盖块。

     ⚠️ 命名纪律：`--on-brand` 是「彩色底上的文字」，任何主题都是白（品牌色底不变深）。
        而 `--card` / `--bg` 这类是**表面**，会随主题翻转。别混用。 */

  /* 表面层级：页面底 → 主表面 → 内嵌浅面 → 槽位底 */
  --card-2: #fbfcfe;              /* 代码块、行 hover、次级面板 */
  --card-3: #f0f2fa;              /* 节次列、周次条这类"槽" */
  --glass: rgba(255, 255, 255, .78);         /* 顶栏毛玻璃 */
  --glass-strong: rgba(255, 255, 255, .93);  /* 弹层 / 下拉毛玻璃 */
  --glass-line: rgba(255, 255, 255, .9);     /* 毛玻璃面板的描边 */
  --topbar-line: rgba(231, 234, 243, .9);    /* 顶栏下沿那条 1px */
  --scrim: rgba(20, 24, 40, .45);            /* 弹窗遮罩 */
  --scroll: #cdd3e4;                          /* 滚动条滑块 */
  --scroll-hover: #b3bbd4;
  --skel-1: #eef1f8;  --skel-2: #f6f8fd;      /* 骨架屏微光 */

  /* 彩色底上的文字/图标：任何主题都不翻转 */
  --on-brand: #fff;

  /* 品牌淡色 */
  --brand-soft-2: #f5f3ff;
  --brand-soft-3: #e5e9ff;
  --brand-soft-4: #f0e9ff;
  --brand-line: #dcd8ff;
  --brand-line-2: #c3c7ff;
  --brand-line-3: #cfd3ff;
  --brand-chip-bg: #e0e7ff;
  --brand-chip-ink: #4338ca;

  /* 状态淡色面板：bg（底）/ line（描边）/ ink（文字）三件套成组换，别只换底 */
  --ok-bg: #f0fdf4;    --ok-line: #bbf7d0;     --ok-ink: #166534;
  --ok-bg-2: #ecfdf5;  --ok-line-2: #a7f3d0;   --ok-ink-2: #047857;
  --ok-soft: #d1fae5;  --ok-line-3: #6ee7b7;
  --ok-chip-bg: #dcfce7; --ok-chip-ink: #15803d;

  --warn-bg: #fffbeb;  --warn-line: #fde68a;   --warn-ink: #b45309;
  --warn-bg-2: #fffdf5; --warn-bg-3: #fff7ed;  --warn-line-2: #fed7aa;
  --warn-ink-2: #d97706;
  --warn-chip-bg: #fef3c7; --warn-chip-ink: #92400e;

  --danger-bg: #fef2f2; --danger-line: #fecaca; --danger-ink: #b91c1c;
  --danger-bg-2: #fff7f7;
  --danger-chip-bg: #fee2e2;

  --info-bg: #ecfeff;   --info-line: #a5f3fc;   --info-ink: #0e7490;
  --info-bg-2: #eff6ff; --info-line-2: #bfdbfe; --info-ink-2: #1d4ed8;
  --info-bg-3: #f0fdfa; --info-bg-4: #e0f2fe;   --info-chip-bg: #cffafe;

  /* 中性 chip */
  --chip-neutral-bg: #f1f5f9; --chip-neutral-ink: #475569;

  /* 首页功能卡的图标底（浅色渐变） */
  --tint-planner: #dbeafe;   --tint-planner-2: #eff6ff;
  --tint-emotion: #fce7f3;   --tint-emotion-2: #fdf2f8;
"""

THEMES = '''
/* ============================================================
   主题：白天 / 护眼 / 黑夜
   ------------------------------------------------------------
   切换方式：<html data-theme="eye|dark">（不写 = 白天，走上面的 :root）。
   属性由每页 <head> 里的一小段内联脚本在**首次绘制之前**写上，
   所以刷新不会闪一下白。

   ⚠️ 两套主题**只覆盖令牌**，不覆盖选择器 —— 这是刻意的。
      如果哪天发现某个元素在黑夜下还是浅色，正确修法是
      「把它写死的颜色换成令牌」，而不是「在主题块里再补一条选择器」，
      否则主题块会越滚越大、最后没人敢动。
   ============================================================ */

/* ---------- 护眼：暖调、低蓝光、降低亮度对比，久看不累 ---------- */
html[data-theme="eye"] {
  --bg: #e7ecdf;
  --card: #f5f8ef;
  --card-2: #eef2e7;
  --card-3: #e4e9db;
  --ink: #2c3327;
  --ink-soft: #5a6450;
  --ink-faint: #656c5a;          /* 无障碍：原 #88917b 在米绿底上 3.06:1 -> 现最差 4.54:1 */
  --line: #d5ddc7;

  --glass: rgba(245, 248, 239, .80);
  --glass-strong: rgba(245, 248, 239, .94);
  --glass-line: rgba(255, 255, 255, .85);
  --topbar-line: rgba(213, 221, 199, .9);
  --scrim: rgba(30, 38, 26, .42);
  --scroll: #c2cbb2;  --scroll-hover: #aab595;
  --skel-1: #e7ecdf;  --skel-2: #f0f4ea;

  /* 品牌淡色整体往暖里收一点，免得蓝紫调在米绿纸上发"跳" */
  --brand-soft: #e8eaf1;   --brand-soft-2: #eeeaf1;
  --brand-soft-3: #e3e6ef; --brand-soft-4: #ece7f0;
  --brand-line: #d3d6e4;   --brand-line-2: #bcbfd4;  --brand-line-3: #c8ccdd;
  --brand-chip-bg: #dde1f2; --brand-chip-ink: #3f4694;

  /* 状态淡色也要跟着暖化：只换底色不换这三个，浅绿/浅蓝的提示条
     会像贴错在米绿纸上 —— 三件套必须成组换。 */
  --ok-bg: #e4eeda;    --ok-line: #c6dcb4;    --ok-ink: #35683f;
  --ok-bg-2: #e8f0e0;  --ok-line-2: #cfe0bf;  --ok-ink-2: #2f6b3c;
  --ok-soft: #dce9cd;  --ok-line-3: #b9d5a4;
  --ok-chip-bg: #d8e8cb; --ok-chip-ink: #2f6b3c;

  --warn-bg: #f4ecdb;  --warn-line: #e2d0a9;  --warn-ink: #8a5a12;
  --warn-bg-2: #f6f0e2; --warn-bg-3: #f4ecdb; --warn-line-2: #dfc79a;
  --warn-ink-2: #7d5210;
  --warn-chip-bg: #ecdfbe; --warn-chip-ink: #7d5210;

  --danger-bg: #f2e5e1; --danger-line: #ddc2bb; --danger-ink: #9a3b33;
  --danger-bg-2: #f4e9e6;
  --danger-chip-bg: #e8d3ce;

  --info-bg: #e1ebea;   --info-line: #bfd6d4;   --info-ink: #166a6f;
  --info-bg-2: #e3eaf0; --info-line-2: #c4d6e0; --info-ink-2: #1c5a7a;
  --info-bg-3: #e2ecea; --info-bg-4: #e0ebec;   --info-chip-bg: #cfe2e0;

  --chip-neutral-bg: #dfe4d5; --chip-neutral-ink: #55604b;

  /* 功能卡的图标底也往暖里收 */
  --tint-planner: #dce6ee;  --tint-planner-2: #e8eff4;
  --tint-emotion: #f0dfe6;  --tint-emotion-2: #f5e8ee;

  --shadow-sm: 0 2px 8px rgba(44, 51, 39, .06);
  --shadow: 0 10px 30px rgba(44, 51, 39, .09);
  --shadow-lg: 0 24px 60px rgba(44, 51, 39, .16);
  --ring: 0 0 0 4px rgba(99, 102, 241, .15);
}

/* ---------- 黑夜：深墨蓝底 + 提亮文字，对比度按 WCAG 正文 >= 4.5:1 取 ---------- */
html[data-theme="dark"] {
  --bg: #10141f;
  --card: #1a2032;
  --card-2: #161b29;
  --card-3: #141826;
  --ink: #e8ebf5;
  --ink-soft: #a7b0c8;
  --ink-faint: #7d87a2;          /* 无障碍：原 #7b85a0 在卡片底上 4.40:1 -> 现最差 4.52:1 */
  --line: #2a3145;

  --glass: rgba(22, 27, 41, .78);
  --glass-strong: rgba(22, 27, 41, .95);
  --glass-line: rgba(255, 255, 255, .08);
  --topbar-line: rgba(255, 255, 255, .07);
  --scrim: rgba(0, 0, 0, .62);
  --scroll: #39415a;  --scroll-hover: #4a5470;
  --skel-1: #1a2032;  --skel-2: #232a3f;

  --brand-soft: #232a4d;   --brand-soft-2: #2a2550;
  --brand-soft-3: #2d2f5c; --brand-soft-4: #33295c;
  --brand-line: #3a3f6b;   --brand-line-2: #4a4f80;  --brand-line-3: #3d4372;
  --brand-chip-bg: #2c3260; --brand-chip-ink: #b9c0ff;

  --ok-bg: #12261c;    --ok-line: #1f4a33;    --ok-ink: #7ee2a8;
  --ok-bg-2: #10281f;  --ok-line-2: #1c4430;  --ok-ink-2: #6fd39b;
  --ok-soft: #163425;  --ok-line-3: #2a5c40;
  --ok-chip-bg: #1a3a28; --ok-chip-ink: #86e0ae;

  --warn-bg: #2a2113;  --warn-line: #4d3a17;  --warn-ink: #f5c86b;
  --warn-bg-2: #2b2214; --warn-bg-3: #2a2113; --warn-line-2: #5a4320;
  --warn-ink-2: #e8b05a;
  --warn-chip-bg: #332612; --warn-chip-ink: #f0c274;

  --danger-bg: #2c1618; --danger-line: #4d2529; --danger-ink: #f79b9b;
  --danger-bg-2: #2a1517;
  --danger-chip-bg: #38201f;

  --info-bg: #0f2729;   --info-line: #1d4a4f;   --info-ink: #6fd8e6;
  --info-bg-2: #131f36; --info-line-2: #274070; --info-ink-2: #8fb4ff;
  --info-bg-3: #0e2827; --info-bg-4: #10242f;   --info-chip-bg: #123036;

  --chip-neutral-bg: #232a3f; --chip-neutral-ink: #a7b0c8;

  --tint-planner: #1c2a4a;  --tint-planner-2: #1a2440;
  --tint-emotion: #3a1f31;  --tint-emotion-2: #331b2b;

  --shadow-sm: 0 2px 8px rgba(0, 0, 0, .35);
  --shadow: 0 10px 30px rgba(0, 0, 0, .45);
  --shadow-lg: 0 24px 60px rgba(0, 0, 0, .55);
  --shadow-brand: 0 10px 26px rgba(99, 102, 241, .38);
  --ring: 0 0 0 4px rgba(129, 140, 248, .28);
}

/* 让表单控件（日期选择器、下拉、滚动条）跟随主题，
   否则黑夜模式下点开 <input type="date"> 会弹出一个刺眼的白面板 */
html { color-scheme: light; }
html[data-theme="dark"] { color-scheme: dark; }
'''

# 1) 令牌块插到 :root 的收尾（--ease 那行之后）
idx_ease = next(i for i, l in enumerate(lines) if l.strip().startswith("--ease:"))
lines.insert(idx_ease + 1, TOKENS.rstrip("\n"))
# 2) 主题块插到 :root 结束的 `}` 之后
idx_close = next(i for i in range(idx_ease, len(lines)) if lines[i].rstrip() == "}")
lines.insert(idx_close + 1, THEMES.rstrip("\n"))

# 3) 骨架屏占位条：放在 .skeleton 的 @keyframes 之后
idx_shimmer = next(i for i, l in enumerate(lines) if l.startswith("@keyframes shimmer"))
SKEL = '''
/* 骨架屏用的占位条（比整块 .skeleton 更贴卡片形状） */
.skel-line {
  height: 12px; border-radius: 6px;
  background: linear-gradient(90deg, var(--skel-1), var(--skel-2), var(--skel-1));
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}
.skel-line.w60 { width: 60%; }
.skel-line.w40 { width: 40%; margin-top: 10px; }
/* 列表骨架是**横排**的：图标位是方块、右侧那条不需要「上间距」 */
.skel-ico {
  width: 36px; height: 36px; flex: none; border-radius: var(--r-md);
  background: linear-gradient(90deg, var(--skel-1), var(--skel-2), var(--skel-1));
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}
'''
# 插到 shimmer 的结束大括号之后
j = idx_shimmer
while lines[j].rstrip() != "}":
    j += 1
lines.insert(j + 1, SKEL.rstrip("\n"))

out = "\n".join(lines)
io.open(P, "w", encoding="utf-8", newline="\n").write(out)
print("OK 已写回", P)
print("   行数", len(src.split(chr(10))), "->", len(out.split(chr(10))))
print("   替换", len(SUBS), "处；data-theme 出现", out.count("data-theme"), "次")
