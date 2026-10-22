"""打包前统一「构建号」：一处输入，全项目同步。

为什么要写这个脚本
------------------
这个项目里「版本/构建号」出现在**很多**地方：

    backend/build.txt            后端自报的构建号（/api/ping 直接读它）
    frontend/*.html              js/css 的 ?v= 查询参数（破浏览器缓存）
    start-windows.bat 横幅以     读的也是 backend/build.txt，不用改
    包内结构.txt                 说明区里写的「本次更新（构建 xxx）」

真实结论：这些地方以前**全靠手改**。改漏一处就会出现「横幅说 h、接口说 g、
前端资源还是旧 ?v=」，而构建号唯一的作用就是让人判断「我到底跑的是哪份代码」——
它自己都对不上，排查就会变成猜谜。用户为此多折腾了好几轮。

所以：构建号只在一个地方输入（本脚本的命令行参数），其余全部由脚本改写。

用法
----
    python tools/bump_build.py 2026-09-26-h      # 改成指定的构建号
    python tools/bump_build.py --check           # 只检查各处是否一致

构建号的写法约定：`YYYY-MM-DD-x`，x 是当天的小写字母序号（a/b/c...）。
前端 ?v= 用去掉连字符的形式（20260926h），因为历史上一直这么用。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD_FILE = ROOT / "backend" / "build.txt"

# 构建号形如 2026-09-26-h
BUILD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z]$")

# 前端 ?v= 的值：把 2026-09-26-h 压成 20260926h
# 前端页脚里那个静态的「构建 xxxx」参与同步。
# 它标识的是**前端这份代码**（后端版本由 /api/ping 自己在运行时上报），
# 两者必须分开显示，否则「前后端版本对不上」就看不出来了。
LOGIN_HTML = ROOT / "frontend" / "login.html"
FOOTER_TAG_RE = re.compile(r'(<span class="build-tag">构建 )[^<]*(</span>)')

HTML_FILES = sorted((ROOT / "frontend").glob("*.html"))
V_RE = re.compile(r"\?v=[0-9a-zA-Z]+")


def to_v(tag: str) -> str:
    """2026-09-26-h -> 20260926h（前端缓存参数的历史写法）"""
    return tag.replace("-", "")


def read_current() -> str:
    try:
        return BUILD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def check() -> int:
    tag = read_current()
    if not tag:
        print(f"[FAIL] 读不到 {BUILD_FILE.relative_to(ROOT)}")
        return 1
    if not BUILD_RE.match(tag):
        print(f"[FAIL] {BUILD_FILE.relative_to(ROOT)} 里的构建号格式不对：{tag!r}")
        print("       期望形如 2026-09-26-h")
        return 1

    want_v = to_v(tag)
    problems = []
    n_html = 0
    for html in HTML_FILES:
        text = html.read_text(encoding="utf-8", errors="replace")
        for m in V_RE.finditer(text):
            n_html += 1
            got = m.group(0)[len("?v="):]
            if got != want_v:
                problems.append(
                    f"{html.relative_to(ROOT)} 的资源版本是 ?v={got}，"
                    f"但 build.txt 是 {tag}（应为 ?v={want_v}）"
                )

    print(f"  build.txt       : {tag}  (前端应为 ?v={want_v})")
    print(f"  前端资源引用    : {n_html} 处")

    # 登录页页脚的静态「构建 …」也必须跟上，否则页脚会永远停在上一个号
    login = LOGIN_HTML.read_text(encoding="utf-8", errors="replace")
    fm = FOOTER_TAG_RE.search(login)
    if not fm:
        problems.append("frontend/login.html 里找不到页脚的「构建 …」标记")
    else:
        foot = fm.group(0)
        if tag not in foot:
            problems.append(
                f"frontend/login.html 页脚写的是 {foot!r}，"
                f"但 build.txt 是 {tag}"
            )
        else:
            print(f"  登录页页脚      : 构建 {tag}")

    if problems:
        print()
        for p in problems[:10]:
            print(f"  [!!] {p}")
        if len(problems) > 10:
            print(f"  ... 另有 {len(problems) - 10} 处")
        print()
        print("  修复： python tools/bump_build.py " + tag)
        return 1

    print("  [OK] 各处构建号一致。")
    return 0


def bump(tag: str) -> int:
    if not BUILD_RE.match(tag):
        print(f"[FAIL] 构建号格式不对：{tag!r}，期望形如 2026-09-26-h")
        return 1

    old = read_current()
    BUILD_FILE.write_text(tag + "\n", encoding="utf-8")
    print(f"[OK] {BUILD_FILE.relative_to(ROOT)}: {old or '(空)'} -> {tag}")

    want_v = to_v(tag)
    changed = 0
    for html in HTML_FILES:
        text = html.read_text(encoding="utf-8", errors="replace")
        new = V_RE.sub(f"?v={want_v}", text)
        if new != text:
            html.write_text(new, encoding="utf-8")
            changed += 1
    print(f"[OK] 前端 {changed} 个 html 的资源版本已改为 ?v={want_v}")

    # 登录页页脚的静态「构建 …」
    login = LOGIN_HTML.read_text(encoding="utf-8", errors="replace")
    new_login, n = FOOTER_TAG_RE.subn(rf"\g<1>{tag}\g<2>", login)
    if n == 0:
        print("[!!] frontend/login.html 里没找到页脚「构建 …」标记，未改动")
    else:
        LOGIN_HTML.write_text(new_login, encoding="utf-8")
        print(f"[OK] 登录页页脚「构建 …」已改为 {tag}（{n} 处）")

    # 提示人工仍需同步的地方（脚本不擅自改：这些是叙述性文本）
    print()
    print("    还需人工确认（脚本不自动改，避免改坏文案）：")
    print("      - README.md / 如何启动.md 里举例用到的构建号")
    print()
    print("    然后用 tools/gen_structure.py 重新生成包内结构.txt。")
    return 0


def main() -> int:
    print("=" * 62)
    print("  构建号一致性")
    print("=" * 62)
    if len(sys.argv) == 1 or "--check" in sys.argv:
        return check()
    return bump(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
