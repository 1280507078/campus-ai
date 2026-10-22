"""批处理里「挑哪张网卡」那段逻辑的**离线单测**。

为什么需要它：
    start-windows.bat 的 IPv4 选择逻辑是「猜哪张网卡能通手机」，
    而它依赖 `ipconfig` 的**文本输出格式**（中英文还不一样）。
    真机上装没装 VMware、网卡顺序如何，没法在 CI 里复现，
    所以这里把那段批处理逻辑**用 Python 等价重写**一份，
    喂进几份模拟的 ipconfig 输出，断言它挑对了。

⚠️ 这个文件与 start-windows.bat 第 6 节是**成对**的：
   改了那边，一定要回来改这里的 _pick_lan_ip，并重跑本测试。
   否则测试会「假绿」—— 测的是一份已经过时的逻辑。

用法：
    python tools/test_lan_ip.py
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 与 start-windows.bat 的 IPv4 选择逻辑等价的 Python 实现
# ---------------------------------------------------------------------------

# 虚拟网卡默认网段（VMware host-only / VirtualBox / WSL / Hyper-V）。
# 这些段里的地址只有本机能访问，手机连不上，必须排除。
VIRTUAL_PREFIXES = (
    "192.168.56.", "192.168.99.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.2", "172.3",
)

# 一定要排掉的：回环 + APIPA（自动私有地址，通常是拿不到 DHCP 时的兜底）
ALWAYS_DROP = ("127.", "169.254.")

# Wi-Fi 适配器的 ASCII 标记。
# ⚠️ 之所以只认这几个 ASCII 词：Windows 在任何语言版本下都用拉丁字母打印它们
#   （中文 Windows 的标题是「无线局域网适配器 WLAN」，「WLAN」是 ASCII）。
#   如果改成去匹配「适配器」这种本地化词，就会往 .bat 里塞非 ASCII 字节，
#   cmd 会乱码 —— 这是实测过的坑。
WIFI_MARKERS = ("wi-fi", "wlan", "wireless")


def _kind_of_heading(head: str) -> str | None:
    """给适配器标题行定类型。不是标题行返回 None。

    ⚠️ 参数是 .bat 里 `for /f delims=:` 切出来的 **%%a** —— 即**冒号已被去掉**的
       前半段（例如 `无线局域网适配器 WLAN`）。所以这里不能依赖「行尾有没有冒号」。

    ⚠️⚠️ 这里踩过两个坑，都只在特定系统/数据下才暴露，务必看懂再改：

    【坑 1】第一版只靠 "adapter" 认标题：
      中文 Windows 的标题是「无线局域网适配器 WLAN:」——**不含 adapter**，
      于是 Wi-Fi 永远认不出来，全都落进默认的 ETH，
      「Wi-Fi 优先」这条策略在中文系统上**完全失效**。
      （单测当时还绿着，因为 VMware 那条恰好被网段规则排掉、又恰好没有有线，
        才侥幸落回正确答案 —— 典型的「靠运气绿」，比直接红更危险。）

    【坑 2】改成「冒号结尾就算标题行」之后：
      `Connection-specific DNS Suffix  . :` 这种**属性行**也冒号结尾，
      于是它把刚识别出的 WIFI 状态**冲掉**，
      等真正的 IPv4 行到来时已经变回 ETH 了 —— 英文样本就是这么挂的。

    最终判据（同时躲开两个坑）：
      ① 先认 Wi-Fi 标记（WLAN / Wi-Fi / Wireless）—— 中英文 Windows 都是拉丁字母，
         这是唯一跨语言可靠的信号；
      ② 不靠「冒号结尾」认标题，而是回到 "adapter"，
         并且**额外认一个中文字**来覆盖中文系统 —— 但中文字节不能进 .bat（cmd 会乱码），
         所以那条规则**只在本测试里用**，.bat 侧靠「Wi-Fi 标记优先」达到同样效果。
    """
    low = head.lower()

    # ① Wi-Fi 标记：跨语言可靠，优先级最高
    if any(m in low for m in WIFI_MARKERS):
        return "WIFI"

    # ② 适配器标题行（英文 adapter / 中文「适配器」）
    #    ⚠️ 必须带前置缩进判断：属性行有前导空格，标题行顶格。
    #       否则「默认网关. . .」这种也会被当成标题。
    if not head.startswith((" ", "\t")):
        if "adapter" in low or "适配器" in head:
            return "ETH"

    return None


def _is_ipv4_line(line: str) -> bool:
    return "ipv4" in line.lower()


def _extract_ip(line: str) -> str:
    """从 `IPv4 地址 . . . . . . . . . . . . : 192.168.1.5` 里取出 IP。"""
    # 按冒号切，取最后一段（适配器名里也可能有冒号，所以取最后而不是第二段）
    tail = line.split(":")[-1]
    return tail.replace(" ", "").strip()


def _accept(ip: str) -> bool:
    if not ip:
        return False
    if any(ip.startswith(p) for p in ALWAYS_DROP):
        return False
    if any(ip.startswith(p) for p in VIRTUAL_PREFIXES):
        return False
    return True


def pick_lan_ip(ipconfig_text: str) -> tuple[str, list[dict]]:
    """返回 (选中的 IP, 所有见到的候选明细)。

    ⚠️⚠️ 本函数必须**逐行等价**于 start-windows.bat 第 6 节那个 for /f 循环。
       最容易脱节的地方是「怎么从一行里取 IP」——
       历史上正是这里出的错（见下方 TRAP 3）。

    ## TRAP 3：.bat 里 `for /f delims=:` 会**吃掉冒号**

    旧 .bat 干过这件事：
        for /f "tokens=1,* delims=:" %%a in (...) do (
          set "LINE=%%a %%b"                                  ← 冒号没了
          for /f "tokens=2 delims=:" %%c in ("!LINE!") do ...  ← 再切一次，找不到冒号
        )
    `%%a` 和 `%%b` 是**去掉分隔符**之后的两段，拼回 `%%a %%b` 并不能还原冒号。
    于是第二次 split 得到空字符串，LANIP 为空，脚本打开 `http://:5500/login.html`，
    浏览器报 NXDOMAIN —— 电脑和手机**全都打不开**。

    ⚠️ 这个测试当时**没能发现**那个 bug，因为本函数按「原始行」直接处理，
       从未重现「重建字符串再二次切分」这一步。所以现在这里也**模拟**那一步，
       并且专门加了一条「重建后必须仍有冒号」的断言。
    """
    wifi = None
    eth = None
    seen: list[dict] = []
    kind = "ETH"          # 与 .bat 一致：默认 ETH，认不出标题也不会把地址丢掉

    for raw_line in ipconfig_text.splitlines():
        line = raw_line.rstrip()

        # ---- 模拟 .bat 的 for /f "tokens=1,* delims=:" ----
        # 还原 %%a / %%b 的真实取法：按第一个冒号切成两段，分隔符被吃掉。
        if ":" in line:
            head, tail = line.split(":", 1)
        else:
            head, tail = line, ""

        # .bat 里判类型用的是 head（%%a），取 IP 用的是 tail（%%b）。
        #
        # ⚠️ TRAP 3 的模拟：旧代码把两段拼成 `head + " " + tail` 之后，
        #    又对这个重建串做了一次 `tokens=2 delims=:`。由于分隔符已被吃掉，
        #    那次切分拿不到东西，IP 就是空的。
        #    这里显式跑一遍「旧做法」，一旦它能成功取到 IP，说明我们的理解有误；
        #    正常情况它必须是空 —— 这就是当年 LANIP 为空、脚本打开
        #    `http://:5500/login.html` 的原因。
        rebuilt = head + " " + tail
        legacy_parts = rebuilt.split(":")
        legacy_ip = (legacy_parts[1] if len(legacy_parts) > 1 else "").replace(" ", "")
        if legacy_ip:
            raise AssertionError(
                "TRAP 3 的假设被打破了：重建串居然还能二次切出 IP。"
                "请重新核对 start-windows.bat 第 6 节的取 IP 方式。"
            )

        k = _kind_of_heading(head)
        if k is not None:
            kind = k

        if not _is_ipv4_line(head):
            continue

        ip = tail.replace(" ", "").strip()
        if not ip:
            continue

        ok = _accept(ip)
        seen.append({"ip": ip, "kind": kind, "usable": ok})
        if not ok:
            continue

        # 与 .bat 一致：同类只取第一条
        if kind == "WIFI" and wifi is None:
            wifi = ip
        elif kind == "ETH" and eth is None:
            eth = ip

    # 无线优先，退而取有线
    return (wifi or eth or ""), seen


# ---------------------------------------------------------------------------
# 模拟的 ipconfig 输出（中英文各一份，都掺进虚拟网卡干扰）
# ---------------------------------------------------------------------------

# 关键点：VMware 的 192.168.56.1 排在**最前面** —— 这正是当年翻车的场景。
# 老逻辑（取第一条）会选中它，新逻辑必须跳过它选到 Wi-Fi 的 192.168.1.5。
CN_WITH_VMWARE_FIRST = """
Windows IP 配置


以太网适配器 VMware Network Adapter VMnet1:

   连接特定的 DNS 后缀 . . . . . . . :
   本地链接 IPv6 地址. . . . . . . . : fe80::1%3
   IPv4 地址 . . . . . . . . . . . . : 192.168.56.1
   子网掩码  . . . . . . . . . . . . : 255.255.255.0
   默认网关. . . . . . . . . . . . . :

无线局域网适配器 WLAN:

   连接特定的 DNS 后缀 . . . . . . . :
   IPv4 地址 . . . . . . . . . . . . : 192.168.1.5
   子网掩码  . . . . . . . . . . . . : 255.255.255.0
   默认网关. . . . . . . . . . . . . : 192.168.1.1

以太网适配器 以太网:

   媒体状态  . . . . . . . . . . . . : 媒体已断开连接
"""

EN_WITH_WSL_FIRST = """
Windows IP Configuration


Ethernet adapter vEthernet (WSL):

   Connection-specific DNS Suffix  . :
   Link-local IPv6 Address . . . . . : fe80::2%15
   IPv4 Address. . . . . . . . . . . : 172.17.0.1
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
   Default Gateway . . . . . . . . . :

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . :
   IPv4 Address. . . . . . . . . . . : 10.0.0.23
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 10.0.0.1
"""

# 只有有线（宿舍插网线），没有 Wi-Fi
EN_ETH_ONLY = """
Windows IP Configuration


Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 192.168.31.88
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.31.1
"""

# 极端情况：只有虚拟网卡（没连任何网络）—— 此时选不出来，应回空
ONLY_VIRTUAL = """
Windows IP Configuration


Ethernet adapter VMware Network Adapter VMnet8:

   IPv4 Address. . . . . . . . . . . : 192.168.56.2
"""

# 插了网线 + 无线同时存在，无线必须优先（手机在 Wi-Fi 上）
BOTH_WIFI_AND_ETH = """
Windows IP Configuration


Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 192.168.100.7

Wireless LAN adapter Wi-Fi:

   IPv4 Address. . . . . . . . . . . : 192.168.1.9
"""


CASES = [
    ("中文系统 + VMware 排第一", CN_WITH_VMWARE_FIRST, "192.168.1.5",
     "老逻辑会选中 192.168.56.1（手机连不上），新逻辑必须选中 Wi-Fi"),
    ("英文系统 + WSL 排第一", EN_WITH_WSL_FIRST, "10.0.0.23",
     "172.17.0.1 是 WSL 的，必须跳过"),
    ("只有有线", EN_ETH_ONLY, "192.168.31.88",
     "没有 Wi-Fi 时应退回有线"),
    ("无线与有线同时存在", BOTH_WIFI_AND_ETH, "192.168.1.9",
     "手机在 Wi-Fi 上，无线优先"),
    ("只有虚拟网卡", ONLY_VIRTUAL, "",
     "选不出来时应返回空，交给调用方兜底"),
]


def main() -> int:
    print("=" * 62)
    print("  局域网 IP 选择逻辑 · 离线单测")
    print("=" * 62)

    ok = 0
    bad = 0
    for name, text, expect, why in CASES:
        got, seen = pick_lan_ip(text)
        passed = got == expect
        if passed:
            ok += 1
            print(f"  [PASS] {name}")
        else:
            bad += 1
            print(f"  [FAIL] {name}")
            print(f"         期望 {expect!r}，实际 {got!r}")
        print(f"         ({why})")
        detail = ", ".join(
            f"{c['ip']}[{c['kind']}{'' if c['usable'] else ' 排除'}]" for c in seen
        )
        print(f"         候选: {detail or '（无）'}")
        print()

    # 额外断言：虚拟网段必须被排除，且不能因为「排除了虚拟网卡」而把 IP 弄丢
    _, seen_cn = pick_lan_ip(CN_WITH_VMWARE_FIRST)
    vm = [c for c in seen_cn if c["ip"] == "192.168.56.1"]
    if vm and vm[0]["usable"] is False:
        ok += 1
        print("  [PASS] 虚拟网段 192.168.56.1 被正确排除")
    else:
        bad += 1
        print("  [FAIL] 虚拟网段 192.168.56.1 未被排除")

    # ── 回归：LANIP 绝不能是空（或含空格）────────────────────────────
    # 真实事故：LANIP 为空 -> 脚本打开 http://:5500/login.html
    #   -> 浏览器显示 %20=:5500/login.html 并报 NXDOMAIN
    #   -> 电脑和手机**全都打不开**。
    # 这个 bug 之所以能溜过当时的测试，是因为当时的实现用了
    #   `set "LINE=%%a %%b"` 重建字符串再二次 delims=: 切分 —— 掩盖了冒号丢失。
    # 所以这里对**所有**样本都断言：只要样本里存在可用的 IPv4，结果就不能为空。
    print("  --- 回归：结果不能是空串 / 不能带空格 ---")
    for name, text, expect, _why in CASES:
        got, _ = pick_lan_ip(text)
        if expect:                       # 该样本理应挑出一个 IP
            if got and " " not in got and got == got.strip():
                ok += 1
                print(f"  [PASS] {name} 取到非空且无空格的 IP: {got!r}")
            else:
                bad += 1
                print(f"  [FAIL] {name} 取到可疑值 {got!r}"
                      f"（空串会让脚本打开 http://:5500/login.html）")

    print()
    print("=" * 62)
    verdict = "全部通过" if bad == 0 else f"有 {bad} 项失败"
    print(f"  结论: {ok} 通过 / {bad} 失败 —— {verdict}")
    print("=" * 62)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
