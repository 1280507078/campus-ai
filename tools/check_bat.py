"""批处理脚本静态检查：找出可能导致「双击闪退」的常见语法问题。

检查项：
  1. 非 ASCII 字节（会导致乱码/crash）
  2. 只有 LF（Windows cmd 需要 CRLF）
  3. goto 标签是否都有对应定义
  4. if/for 括号是否配对
  5. 是否以 @echo off 开头、以 pause 结尾（防闪退）
  6. 块内的 %VAR% 使用（可能被提前展开）
"""
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent

# ⚠️ 以前这里是一份**硬编码的文件清单**，结果新增 check-lan.bat 时
#    检查器根本没扫到它（跑完全绿，其实漏检），同时它也漏掉了 CRLF 错误。
#    「清单式」在任何自动化里都是隐患：忘记登记 = 静默跳过 = 假绿。
#    改成自动发现项目里所有 .bat，就不会再有「忘了加进来」这回事。
#
#    排除 .git 等目录，避免扫到版本库内部的东西。
_EXCLUDE_DIRS = {".git", ".codebuddy", "node_modules", "__pycache__", "screenshots"}


def discover_scripts() -> list[pathlib.Path]:
    """自动找出所有需要检查的批处理脚本（按路径排序，输出稳定）。"""
    found = []
    for p in BASE.rglob("*.bat"):
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        found.append(p)
    return sorted(found, key=lambda p: str(p.relative_to(BASE)))


def check(path: pathlib.Path) -> list[str]:
    issues = []
    raw = path.read_bytes()

    # 1. 非 ASCII
    non = sum(1 for b in raw if b > 127)
    if non:
        issues.append(f"含 {non} 个非 ASCII 字节（cmd 会乱码）")

    # 2. 换行
    crlf = raw.count(b"\r\n")
    lone_lf = raw.count(b"\n") - crlf
    if lone_lf:
        issues.append(f"含 {lone_lf} 个纯 LF 换行（需统一为 CRLF）")

    text = raw.decode("ascii", errors="replace")
    lines = text.splitlines()

    # 3. goto 标签配对
    labels = set()
    for ln in lines:
        m = re.match(r"\s*:([A-Za-z_][\w]*)", ln)
        if m:
            labels.add(m.group(1).lower())
    gotos = set()
    for ln in lines:
        for m in re.finditer(r"\bgoto\s+:?([A-Za-z_][\w]*)", ln, re.I):
            gotos.add(m.group(1).lower())
    missing = gotos - labels
    if missing:
        issues.append(f"goto 指向不存在的标签: {sorted(missing)}")
    unused = labels - gotos - {"end", "eof"}
    if unused:
        issues.append(f"定义了但未被 goto 的标签: {sorted(unused)}")

    # 4. 括号配对（忽略 rem 行和 echo 文本里的括号）
    depth = 0
    bad_line = None
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.lower().startswith("rem") or not s:
            continue
        # 去掉转义的括号 ^( ^)
        cleaned = re.sub(r"\^[()]", "", s)
        # 去掉 echo 的文本部分（粗略：echo 后的内容不算结构）
        depth += cleaned.count("(") - cleaned.count(")")
        if depth < 0:
            bad_line = (i, ln)
            break
    if bad_line:
        issues.append(f"第 {bad_line[0]} 行括号提前闭合: {bad_line[1].strip()[:50]}")
    elif depth != 0:
        issues.append(f"括号未配对，结束时深度={depth}")

    # 5. 首尾
    first = next((l for l in lines if l.strip()), "")
    if "@echo off" not in first.lower():
        issues.append(f"首行不是 @echo off（实际: {first[:40]}）")
    last_nonempty = [l for l in lines if l.strip()][-1]
    if "pause" not in last_nonempty.lower():
        issues.append(f"末行不是 pause（实际: {last_nonempty[:40]}）")

    # 6. 块内 %VAR%
    depth = 0
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.lower().startswith("rem"):
            continue
        in_block = depth > 0
        vars_ = re.findall(r"(?<!%)%([A-Za-z_][\w]*)%(?!%)", ln)
        if vars_ and in_block:
            issues.append(f"第 {i} 行在块内用 %VAR%（可能提前展开）: {vars_}")
        cleaned = re.sub(r"\^[()]", "", s)
        depth += cleaned.count("(") - cleaned.count(")")

    # 7. ★ 危险模式：把 for /f delims=: 的两段拼回去、再二次 delims=: 切分
    #
    # 为什么单独立一条：这个写法**语法完全合法**，静默产生空值，不报任何错。
    # 真实事故：脚本因此算出空的 LANIP，打开 http://:5500/login.html，
    # 浏览器显示 %20=:5500/login.html 并报 NXDOMAIN —— 电脑和手机全都打不开，
    # 看起来像网络故障，实际是字符串被吃掉了一个冒号。
    #
    # 原理：`for /f "tokens=1,* delims=:"` 的 %%a / %%b 是**去掉分隔符**之后的两段。
    # 拼成 "%%a %%b" 无法还原冒号，于是第二次 `tokens=2 delims=:` 什么都切不出来。
    # 正确做法：IP 就在 %%b 里，直接用。
    issues.extend(_check_rebuild_and_resplit(lines))

    # 8. ★ 危险模式：`--dir "%%~dp0"` —— 结尾反斜杠吃掉引号
    #
    # 为什么单独立一条：同样**语法合法、不报错**，只是运行时目录不存在。
    # 真实事故：前端窗口一开就打印
    #     [错误] 目录不存在：C:\Users\z1280\my-web\frontend"
    # 注意末尾那个孤零零的引号 —— 那就是线索。
    #
    # 原理：%%~dp0 展开后**自带结尾反斜杠**（...\frontend\）。
    # 于是 "--dir "%%~dp0"" 展开成  --dir "C:\...\frontend\"
    # cmd 把「反斜杠+引号」当成**转义的引号**，闭合引号被吞，
    # 参数里就真的混进去一个引号字符，Python 按字面路径去找，自然找不到。
    # 正确做法：根本不要传路径 —— 上一行已经有 cd /d "%%~dp0"，
    # 程序默认就在当前目录。
    issues.extend(_check_dir_with_dp0(lines))

    return issues


# 匹配 `--dir "%%~dp0"` / `--dir "%~dp0"` 这类「把自带反斜杠的路径塞进引号」的写法
#
# ⚠️ 正则要写成 `%{1,2}~dp0`：字面是「一到两个百分号，然后一个波浪号」。
#    第一版写成了 `%~{1,2}dp0`，那是「一个百分号 + 一到两个波浪号」，
#    永远匹配不上，检查形同虚设 —— 单测里重新引入 bug 却没报错才发现。
#    量词必须挂在**百分号**上。
_DIR_DP0 = re.compile(r'--dir\s+"%{1,2}~dp0\s*"', re.I)


def _check_dir_with_dp0(lines: list[str]) -> list[str]:
    """找出 `--dir "%%~dp0"` 这种会被结尾反斜杠坑掉的参数。"""
    out = []
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.lower().startswith("rem") or not s:
            continue
        if _DIR_DP0.search(s):
            out.append(
                f"第 {i} 行用了 `--dir \"%%~dp0\"` —— "
                f"%%~dp0 自带结尾反斜杠，会与引号拼成『反斜杠+引号』并被 cmd "
                f"当成转义引号，参数里会混进一个多余引号，运行时变成「目录不存在」。"
                f"请删掉 --dir 参数（上一行的 cd /d \"%%~dp0\" 已经切好目录了）"
            )
    return out


# 匹配 `set "VAR=%%x %%y"` 这种「把两段拼回去」的赋值
_SET_CONCAT = re.compile(
    r"""^\s*set\s+"?([A-Za-z_]\w*)=%{2}\w+\s+%{2}\w+"?\s*$""",
    re.I,
)


def _check_rebuild_and_resplit(lines: list[str]) -> list[str]:
    """找出「重建字符串 -> 再 delims=: 切分」这个静默失败模式。"""
    out = []
    concat_vars: dict[str, int] = {}

    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.lower().startswith("rem") or not s:
            continue
        m = _SET_CONCAT.match(s)
        if m:
            concat_vars[m.group(1).lower()] = i
            continue

        # 之后如果再对那个变量做 tokens=... delims=: 切分，就是踩坑了
        if "delims=:" not in s:
            continue
        for var, decl_line in concat_vars.items():
            if re.search(rf"!{re.escape(var)}!", s, re.I):
                out.append(
                    f"第 {i} 行对 「第 {decl_line} 行拼接出的 {var}」再做 delims=: 切分 —— "
                    f"分隔符已在拼接时丢失，这里必然取到空值。"
                    f"请直接用 %%b 取 IP，不要重建字符串"
                )
                break

    return out


def check_dependency_sync() -> list[str]:
    """检查「依赖探测清单」是否覆盖了 requirements.txt 里的必需包。

    ⚠️ 这个检查来自一次真实事故（用户报「xls 还是不能识别」）：

      requirements.txt 当初只列了 pandas 的**引擎**（xlrd / openpyxl），
      忘了 pandas 本身；同时 start-windows.bat 的探测语句只 import
      fastapi/uvicorn/sqlalchemy/jwt/pydantic_settings —— 于是缺 pandas
      的机器被判定为「依赖齐全」，**连 pip install 都不会跑**。
      用户重装多少次都没用，看到的还是「这个表格读不了」。

    两个清单各漏各的，还互相掩护。这个检查把它们钉在一起：
    凡 requirements.txt 里未注释的行，都必须出现在每个探测语句里。
    """
    issues: list[str] = []

    req = BASE / "backend" / "requirements.txt"
    if not req.exists():
        return ["找不到 backend/requirements.txt"]

    # 取 requirements 里真正生效的包名（去掉注释、空行、[extra]、版本约束）
    #
    # 「必需」与「可选」要分开：
    #   标记了 `# optional: <包名>` 的是**可选依赖** —— 代码里延迟导入，
    #   缺了只降级某个功能（如向量记忆）。这类包**故意不放进探测清单**，
    #   否则用户没装时会每次启动都被拉去 pip install，白等几分钟。
    #   只有「必需依赖」才要求出现在每个探测语句里。
    wanted, optional = set(), set()
    for raw in req.read_text(encoding="utf-8", errors="replace").splitlines():
        # 先看这行有没有 optional 标记（标记本身是注释，要在剥注释前抓）
        opt = re.search(r"#\s*optional:\s*([A-Za-z0-9_.\-]+)", raw)
        if opt:
            optional.add(opt.group(1).lower())
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!\[\s]", line, 1)[0].strip()
        if name:
            wanted.add(name.lower())

    wanted -= optional

    # uvicorn[standard] 和 python-multipart 的 import 名与包名不同，单独映射
    import_alias = {
        "uvicorn": "uvicorn",
        "python-multipart": "multipart",   # 实际很少直接 import，跳过亦可
        "pyjwt": "jwt",
        "pydantic-settings": "pydantic_settings",
    }

    # 找出所有探测语句
    probe_re = re.compile(r'import\s+([a-z_][\w\s,]*?)(?:"|\s*>|\s*;\s*print)')
    n_probes = 0
    for bat in BASE.rglob("*.bat"):
        if any(part in _EXCLUDE_DIRS for part in bat.parts):
            continue
        text = bat.read_text(encoding="utf-8", errors="replace")
        for m in probe_re.finditer(text):
            mods = {x.strip().lower() for x in m.group(1).split(",") if x.strip()}
            # 只关心那种一次 import 一串依赖的探测语句
            if len(mods) < 3:
                continue
            n_probes += 1
            rel = bat.relative_to(BASE)
            for pkg in sorted(wanted):
                if pkg in ("python-multipart",):
                    continue        # 没有稳定的顶层 import 名，不参与比对
                probe_name = import_alias.get(pkg, pkg.replace("-", "_"))
                # 探测语句里通常写的是 import 名（pandas / xlrd），也可能是包名
                if probe_name not in mods and pkg not in mods:
                    issues.append(
                        f"{rel} 的探测语句漏了 {pkg} —— "
                        f"缺这个包时脚本会误判「依赖齐全」而跳过安装"
                    )

    if n_probes == 0:
        issues.append("没有任何「多包探测」语句，启动脚本可能不会自动装依赖")
    return issues


def main() -> int:
    print("=" * 62)
    print("  批处理脚本静态检查")
    print("=" * 62)

    # 支持显式指定文件（方便单测某一个）；不给参数就自动发现全部 .bat
    if len(sys.argv) > 1:
        targets = [pathlib.Path(a) if pathlib.Path(a).is_absolute() else BASE / a
                   for a in sys.argv[1:]]
    else:
        targets = discover_scripts()

    if not targets:
        print("\n[!!] 没有找到任何 .bat 文件，这不对劲（是不是被误删了？）")
        return 1

    total = 0
    for p in targets:
        rel = str(p.relative_to(BASE)) if p.is_absolute() and p.is_relative_to(BASE) else str(p)
        if not p.exists():
            print(f"\n[缺失] {rel}")
            total += 1
            continue
        issues = check(p)
        if issues:
            print(f"\n[!!] {rel}")
            for it in issues:
                print(f"       - {it}")
            total += len(issues)
        else:
            print(f"[OK ] {rel}")

    # ---- 跨文件检查：依赖探测清单 vs requirements.txt ----
    print()
    print("[跨文件] 依赖探测清单与 requirements.txt 是否一致")
    dep_issues = check_dependency_sync()
    if dep_issues:
        for it in dep_issues:
            print(f"       - {it}")
        total += len(dep_issues)
    else:
        print("[OK ] 所有探测语句都覆盖了 requirements.txt 里的必需包")

    print()
    print("=" * 62)
    if total == 0:
        print("  结论: 全部通过，未发现会导致闪退的问题")
    else:
        print(f"  结论: 发现 {total} 个问题")
    print("=" * 62)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
