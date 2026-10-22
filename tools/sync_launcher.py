"""把 start-windows.bat 整份同步到 启动工具/start.bat。

============================================================================
 为什么会有两份 .bat
============================================================================

它们是**同一段逻辑的两份拷贝** —— 一份放在项目根目录，一份放在
`启动工具/` 子目录里。这样用户无论从哪一层进去，都能找到双击即用的入口。

两份拷贝就必然带来「改了 A 忘了改 B」的问题。以前靠自觉，结果就是分叉：
出问题时在 A 上调试、却在 B 上运行，极难查。

============================================================================
 为什么从「分区段同步」改成「整文件同步」
============================================================================

原来只同步第 0 / 4 / 6 / 7 节，其余部分默认「不重要、可以各自演化」。
这个假设是错的，而且错得很隐蔽：

  · `--reload` 只加在了主脚本上，备用脚本一直没有（没人发现）；
  · 第 1 节那段「依赖探测清单为什么要覆盖所有包」的注释，也只在主脚本里；
  · 2026-10-13 加 https 支持时，主脚本改了 5 处 —— 端口探测、证书生成、
    后端环境变量、https helper 生成、启动顺序 —— 只有 1 处落在同步范围内。
    而第 6 节（在同步范围内）被同步成了新版，会打印 `!HTTPSPORT!` 和
    `!CERT_OK!` 这两个**备用脚本里根本没定义**的变量 → 打印出空端口。
    「一半新一半旧」比「全旧」更难查，因为它看起来像是好的。

整文件同步把这个判断彻底删掉：两份文件要么逐字节一致，要么立刻报错。
备用脚本的项目根探测本来就写成「向上递归找项目根」，所以它放在哪个目录
都能跑，不需要为自己所在的位置保留任何差异 —— 整文件一致是可行的。

历史坑（保留，别重犯）：
  · 早期把边界放在 `echo Opening the browser...` 之前，导致一段 if/else 没被
    同步过去，备用脚本里剩下一个落单的 `if defined LANIP (`，括号不配对直接跑挂。
  · 边界里不能写 \\r\\n：用 read_text() 读时通用换行会把 \\r\\n 统一成 \\n，
    带 \\r 的常量永远匹配不上。统一用 \\n 书写，拼接后再整体转回 CRLF。
  · 尾部空白也必须一致，否则 test_launcher_url.py 会报出「差一个 \\r」这种
    极难定位的不一致。整文件同步后这些问题一并消失。

使用方法：
    python tools/sync_launcher.py          # 同步（以 start-windows.bat 为准）
    python tools/sync_launcher.py --check  # 只检查是否一致（不写文件）
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = ROOT / "start-windows.bat"
ALT = ROOT / "启动工具" / "start.bat"


def _crlf(raw: bytes) -> bytes:
    """统一成 CRLF。

    ⚠️ 必须先规范成 LF 再整体转回 CRLF：简单地把每个 \\n 换成 \\r\\n，
       会把已有的 \\r\\n 变成 \\r\\r\\n。cmd 对纯 LF 的 .bat 处理得很糟
       （可能不执行或行为诡异），而混合换行会被 tools/check_bat.py 判为
       「含 N 个纯 LF 换行」。
    """
    return raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def main() -> int:
    check_only = "--check" in sys.argv

    if not MAIN.exists():
        print(f"[FAIL] 找不到主脚本：{MAIN}")
        return 1
    if not ALT.exists():
        print(f"[FAIL] 找不到备用脚本：{ALT}")
        return 1

    main_raw = _crlf(MAIN.read_bytes())      # 顺手把主脚本也规范成 CRLF
    alt_raw = _crlf(ALT.read_bytes())

    if main_raw == alt_raw:
        # 主脚本万一之前是混合换行，上面已经修好了，只在真变了时才落盘
        if MAIN.read_bytes() != main_raw:
            MAIN.write_bytes(main_raw)
            print("[OK] 主脚本换行已规范为 CRLF。")
        print("[OK] 两份启动脚本完全一致。")
        return 0

    if check_only:
        print("[FAIL] 两份启动脚本**不一致**！")
        print(f"       以 {MAIN.name} 为准，请运行：python tools/sync_launcher.py")
        return 1

    MAIN.write_bytes(main_raw)
    ALT.write_bytes(main_raw)

    # 自检：写完后必须逐字节相等，否则立刻报错，而不是静默写坏
    if MAIN.read_bytes() != ALT.read_bytes():
        print("[FAIL] 同步后自检不通过：两份脚本仍不一致")
        return 1

    print(f"[OK] 已把 {MAIN.name} 整份同步到 {ALT.relative_to(ROOT)}（自检通过）")
    print("     注意：备用脚本从此不允许有「自己的差异」。")
    print("           若确实需要两份不同，请先改这里的同步策略，而不是手改文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
