#!/usr/bin/env python3
"""启动脚本「开浏览器用哪个地址」的回归测试。

## 这段逻辑前后踩过两次坑，所以必须钉死

**第一次（旧的坑）**：启动脚本用 `127.0.0.1` 开电脑浏览器。
登录页的二维码是从**当前页面地址**（`location.origin`）推导的，于是二维码里
就是 `127.0.0.1`；手机扫到后连的是**手机自己**，必然打不开。当时把「开浏览器
必须用局域网 IP」写成了铁律。

**第二次（上一个坑，现已随定位功能一起消失）**：曾经为了避免在局域网 IP 下
电脑端定位被浏览器拒绝，改成开 `127.0.0.1`。现在定位功能已整体删除，
谁也不再依赖切换地址，但**开 127.0.0.1 这个结论保留** ——
理由是：`127.0.0.1` 永远能解析、不受「选到虚拟网卡」影响；如果开的是
`http://局域网IP:端口` 而那个 IP 恰好是 VMware / VirtualBox / WSL 的虚拟网卡，
浏览器会停在一个永远打不开的页面上，启动第一屏就出错，很容易被误判成程序坏了。

**现在的契约**（两边同时成立，缺一不可）：
  1. 电脑浏览器开 **`http://127.0.0.1:端口`** —— 与虚拟网卡无关，必然能打开；
  2. **二维码不依赖「电脑用哪个地址打开」** —— 页面在回环地址下会向后端
     `/api/system/lan-ip` 要本机局域网 IP，把**局域网 IP** 写进二维码，
     手机扫码照样能打开（login.js 的 scanHostPrefix / scanUrl）；
     拿不到局域网 IP 时会在扫码面板里明确提示，而不是给一个扫不动的码；
  3. 横幅里给手机看的地址**必须继续打印局域网 IP**，那是用户在手机上敲的东西。

所以本测试同时检查 .bat（第 1、3 条）和 login.js（第 2 条）——
它们是一条链上的两个环，只改一头就会悄悄退回第一次的坑。

## 测什么

1. 主脚本里开浏览器那一行必须是 `http://127.0.0.1:!WEBPORT!`；
2. 开浏览器**不能**再用 `!LANIP!`（选到虚拟网卡时首屏就白页）；
3. 横幅必须保留手机用的局域网地址（`http://!LANIP!:!APIPORT!/app/login.html`）；
4. login.js 必须存在「局域网 IP 兜底」逻辑（`/system/lan-ip` + 回环判断）；
5. 两份启动脚本（根目录 + 启动工具/）在这段逻辑上必须一致；
6. 括号必须配对（早期同步漏掉半个 if 块，cmd 直接跑挂）。
"""
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
MAIN = BASE / "start-windows.bat"
ALT = BASE / "启动工具" / "start.bat"
LOGIN_JS = BASE / "frontend" / "js" / "login.js"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'[PASS]' if ok else '[FAIL]'} {name}{'  ' + detail if detail else ''}")


def read_ascii(p: pathlib.Path) -> str:
    return p.read_bytes().decode("ascii", errors="replace")


def read_text(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def browser_block(text: str) -> str:
    """抠出「开浏览器」那一整段（从注释标记到 goto :end）。"""
    m = re.search(
        r"(?s)rem ---- Open the browser on 127\.0\.0\.1, NOT the LAN address ----.*?(?=\ngoto :end)",
        text,
    )
    return m.group(0) if m else ""


print("=" * 62)
print("  启动脚本 · 开浏览器地址 回归测试")
print("=" * 62)

main_txt = read_ascii(MAIN)
alt_txt = read_ascii(ALT)

main_blk = browser_block(main_txt)
alt_blk = browser_block(alt_txt)

check("主脚本里找得到「开浏览器」区块", bool(main_blk), f"{len(main_blk)} 字符")
check("启动工具脚本里找得到同样的区块", bool(alt_blk), f"{len(alt_blk)} 字符")

if not main_blk or not alt_blk:
    print("\n  [!!] 区块缺失，后面的断言无法进行")
    sys.exit(1)

# ── 1. 必须用 127.0.0.1 开浏览器（它永远能解析、不受虚拟网卡影响）──
check(
    "★ 用 127.0.0.1 开浏览器（与虚拟网卡无关，必然能打开）",
    'start "" "http://127.0.0.1:!WEBPORT!/login.html"' in main_blk,
)

# ── 2. 开浏览器不许再用局域网 IP（选到虚拟网卡时首屏就白页）──
start_lines = [
    line.strip() for line in main_blk.splitlines()
    if line.strip().lower().startswith('start "" "http')
]
check(
    "★ 开浏览器不再使用局域网 IP（选到虚拟网卡会白页）",
    all("!LANIP!" not in s for s in start_lines),
    f"start 行数={len(start_lines)}",
)
check(
    "开浏览器只有一处 start（不留兜底分支绕回局域网 IP）",
    len(start_lines) == 1,
    f"非空 start 行数={len(start_lines)}",
)

# ── 3. 横幅必须继续给手机打印局域网地址 ──────────────────────────
# 电脑端开 localhost 之后，「手机该敲什么地址」就只剩横幅这一条出口了，
# 它一旦被删掉，用户就再也找不到手机入口。
check(
    "★ 横幅仍打印手机用的局域网地址（二维码之外的第二条出口）",
    "http://!LANIP!:!APIPORT!/app/login.html" in main_txt
    and "http://!LANIP!:!WEBPORT!/login.html" in main_txt,
)

# ── 4. 前端必须有「局域网 IP 兜底」逻辑（和上面 1/2 条是同一条链）────────
login_js = read_text(LOGIN_JS)
check(
    "★ login.js 会用后端 /system/lan-ip 补局域网 IP（二维码才能带上正确地址）",
    "/system/lan-ip" in login_js,
)
check(
    "login.js 能把「本机回环」与「局域网地址」分开判断",
    "isLoopbackHost" in login_js and "127.0.0.1" in login_js and "localhost" in login_js,
)

# ── 5. 两份脚本这段必须一致 ─────────────────────────────────────
check("两份启动脚本的该区块一致", main_blk.strip() == alt_blk.strip())

# ── 6. 括号配对（早期同步漏半块 -> cmd 跑挂）──────────────────────
def paren_balanced(text: str) -> tuple[bool, int]:
    """只看命令行的括号；rem 注释里的括号不算（注释里有 ^( 之类）。"""
    depth = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("rem"):
            continue
        # echo 行里的括号是输出文本，不参与语法
        if line.lower().startswith("echo"):
            continue
        # ^( ^) 是转义，语义上不是语法括号
        line = line.replace("^(", "").replace("^)", "")
        depth += line.count("(") - line.count(")")
    return depth == 0, depth


for label, txt in (("主脚本", main_txt), ("启动工具脚本", alt_txt)):
    ok, d = paren_balanced(txt)
    check(f"{label} 括号配对", ok, f"结束时深度={d}")

print()
print("=" * 62)
passed = sum(1 for _, ok, _ in results if ok)
print(f"  结论: {passed} 通过 / {len(results) - passed} 失败")
print("=" * 62)
sys.exit(0 if passed == len(results) else 1)
