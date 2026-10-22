#!/usr/bin/env python3
"""编码守卫：防止「文件编码」引发的运行期崩溃。

背景：本项目在中文 Windows 上开发/演示，出现过两次同类事故：
  1. .bat 用 UTF-8 存 -> cmd.exe 按 GBK 读 -> 乱码、goto 失效、闪退
  2. requirements.txt 用 UTF-8 存 -> pip 按 GBK 读 -> UnicodeDecodeError

规则：
  A. 会被「非 Python 工具」读取的文件（bat/txt/sql/env/json...）
     必须能被 GBK 解码（中文 Windows 的默认代码页），最好是纯 ASCII。
  B. .py/.js/.html/.css 等由认识 UTF-8 的工具读取，保持 UTF-8 即可，
     但要确认没有「非 UTF-8」的文件混进来。

用法：  python3 tools/check_encoding.py      （在项目根目录执行）
退出码： 0 = 通过，1 = 发现问题
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 由「系统 ANSI 编码」读取的文件：必须 GBK 可读
ANSI_READ = (".bat", ".cmd", ".txt", ".sql", ".env", ".ini", ".cfg", ".csv")
# 由「认 UTF-8 的工具」读取的文件：必须 UTF-8 可读
UTF8_READ = (".py", ".js", ".json", ".html", ".css", ".md")

SKIP_DIRS = {".git", "__pycache__", "node_modules", "screenshots"}

errors = []
warnings = []


def walk():
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            yield os.path.join(root, f)



def looks_like_utf8_stored_as_gbk(data: bytes) -> bool:
    """判断 data 是否「其实是 UTF-8，却会被当 GBK 读」。

    思路：先按 UTF-8 试解。若成功且解得有意义的中文（CJK 字符占比高），
    说明文件本身是 UTF-8；再按 GBK 解同一段字节，会得到乱码。
    真正的 GBK 文件按 UTF-8 解会直接失败（或解出乱码），不会走到这里。
    """
    try:
        as_utf8 = data.decode("utf-8")
    except UnicodeDecodeError:
        return False  # 不是合法 UTF-8 -> 应该是真正的 GBK

    # 统计 CJK 与「典型 GBK 乱码字符」
    cjk = sum(1 for c in as_utf8 if "\u4e00" <= c <= "\u9fff")
    if cjk == 0:
        return False  # UTF-8 里没有中文，按 GBK 读也不会乱码

    # 用 GBK 解同样字节，看是否出现大量生僻/奇怪字符
    try:
        as_gbk = data.decode("gbk")
    except UnicodeDecodeError:
        return True

    weird = sum(
        1 for c in as_gbk
        if ("\u3000" <= c <= "\u303f")      # CJK 标点（乱码常见）
        or ("\u0400" <= c <= "\u04ff")      # 西里尔字母（「У԰」这类）
        or c in "鏍″洯锕絾鎬鑳鐨勬槸涓"
    )
    return weird >= max(2, cjk // 3)


def rel(p):
    return os.path.relpath(p, ROOT)


def main():
    for path in walk():
        name = os.path.basename(path)
        _, ext = os.path.splitext(name)
        ext = ext.lower()

        try:
            data = open(path, "rb").read()
        except OSError:
            continue

        if ext in ANSI_READ:
            # 1) 必须能被 GBK 解码，否则 cmd/pip 会直接抛 UnicodeDecodeError
            try:
                data.decode("gbk")
            except UnicodeDecodeError as e:
                errors.append(
                    f"{rel(path)}: 无法用 GBK 解码（{e}）\n"
                    f"    -> 会被 cmd/pip 等按 cp936 读取的工具读崩，"
                    f"请改存为 GBK 或纯 ASCII"
                )
                continue

            na = sum(1 for b in data if b > 127)
            if not na:
                continue  # 纯 ASCII，任何编码下都安全

            # 2) 能被 GBK 解码 ≠ 内容是好的。
            #    UTF-8 中文按 GBK 读通常也能解码，但会变成「鏍″洯」这类乱码。
            #    判断方法：若能作为合法 UTF-8 解码，且解出的是有意义的中文，
            #    则说明文件其实是 UTF-8 存的 —— 这才会让 pip/cmd 读出错。
            if looks_like_utf8_stored_as_gbk(data):
                errors.append(
                    f"{rel(path)}: 疑似 UTF-8 编码（含中文），"
                    f"但会被按 cp936/GBK 读取\n"
                    f"    -> 中文 Windows 上 cmd/pip 读到会报 "
                    f"UnicodeDecodeError 或乱码\n"
                    f"    -> 请转存为 GBK，或把内容改成纯 ASCII"
                )
            else:
                warnings.append(
                    f"{rel(path)}: 含 {na} 个非 ASCII 字节（GBK 可读，中文正常）"
                )

        elif ext in UTF8_READ:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as e:
                errors.append(
                    f"{rel(path)}: 不是合法 UTF-8（{e}）\n"
                    f"    -> Python/浏览器读取会报错"
                )

    print("=" * 60)
    print("  编码检查")
    print("=" * 60)

    if errors:
        print(f"\n[错误] {len(errors)} 项：\n")
        for e in errors:
            print("  " + e)
    else:
        print("\n[通过] 没有发现编码问题。")

    if warnings:
        print(f"\n[提示] {len(warnings)} 项（不阻塞，但建议处理）：\n")
        for w in warnings:
            print("  " + w)

    print()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
