#!/usr/bin/env python3
"""「扫码登录能用」+「起点兜底不卡死」这条链的静态守卫测试。

## 为什么要单独立一个测试

（一）扫码登录这条链是**四个文件一起**成立的，任何一个被改回旧写法都会悄悄退化：

    start-windows.bat / 启动工具/start.bat   电脑浏览器开 127.0.0.1
            ↓
    backend/main.py 的 /api/system/lan-ip    告诉前端本机局域网 IP
            ↓
    frontend/js/login.js 的 scanHostPrefix   把局域网 IP 写进二维码（手机才扫得通）
            ↓
    frontend/js/nav.js                       起点留空时用校内默认出发点，不报错卡死

.bat 那一环由 tools/test_launcher_url.py 覆盖（只读文本，Linux/CI 上也能跑）。
本文件覆盖**其余三环**，全部是纯文本断言，无需浏览器、无需联网。

（二）★ 负向守卫：这一版把「我的位置」整个删掉了。
    踩过的坑就在这里 —— 删功能比加功能容易留残渣：删了面板忘了删样式、删了调用
    忘了删函数、删了 JS 忘了删 HTML 里的按钮，最后页面看着正常、代码里躺着一堆
    死代码。所以下面**逐个标识符断言「必须不存在」**，把这次删除钉住，
    下次谁想加回来都会先看到这组失败。

用法：
    python tools/test_scan_login_contract.py
"""
from __future__ import annotations

import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
MAIN_PY = BASE / "backend" / "main.py"
LOGIN_JS = BASE / "frontend" / "js" / "login.js"
NAV_JS = BASE / "frontend" / "js" / "nav.js"
NAV_HTML = BASE / "frontend" / "nav.html"
GRAPH_JS = BASE / "frontend" / "js" / "campus-graph.js"
COMMON_JS = BASE / "frontend" / "js" / "common.js"
STYLE_CSS = BASE / "frontend" / "css" / "style.css"
START_BAT = BASE / "start-windows.bat"
ALT_BAT = BASE / "启动工具" / "start.bat"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'[PASS]' if ok else '[FAIL]'} {name}{'  ' + detail if detail else ''}")


def read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


print("=" * 62)
print("  扫码登录 + 起点兜底 契约 · 静态守卫测试")
print("=" * 62)

main_py = read(MAIN_PY)
login_js = read(LOGIN_JS)
nav_js = read(NAV_JS)
nav_html = read(NAV_HTML)

# ── 后端：必须提供局域网 IP 接口，且要排除虚拟网卡 ────────────────
print("\n-- 后端 /api/system/lan-ip --")
check("接口已注册", '"/api/system/lan-ip"' in main_py)
check("返回 ip 与 port 两个字段", '"ip": _lan_ip()' in main_py and '"port": _api_port()' in main_py)
check(
    "排除虚拟网卡 / 回环 / APIPA（否则会把手机连不上的地址给前端）",
    all(s in main_py for s in ("192.168.56.", "127.", "169.254.")),
)
check("接口不需要登录（登录页还没登录就要用它）", "Depends" not in main_py.split('"/api/system/lan-ip"')[1][:260])

# ── login.js：二维码要能换成局域网 IP ────────────────────────────
print("\n-- login.js 的二维码兜底 --")
check("会向后端要局域网 IP", "/system/lan-ip" in login_js)
check("区分回环与本机地址", "isLoopbackHost" in login_js)
check(
    "回环时才替换（已在局域网地址上打开就不要画蛇添足）",
    "if (!isLoopbackHost(location.hostname)) return" in login_js,
)
check("端口跟随当前页面（前端单独跑 5500 时也要对）", "location.port ||" in login_js)
check("拿不到局域网 IP 时有提示，不显示一个扫不动的码", "没有检测到局域网 IP" in login_js)
check(
    "scanUrl 仍按当前页面目录拼（不能写死 /app/）",
    "location.pathname.replace" in login_js,
)

# ── nav.js：起点留空不报错卡死 ───────────────────────────────────
print("\n-- nav.js 的起点兜底 --")
check("默认出发点函数在", "function defaultOrigin()" in nav_js)
check(
    "★ 起点留空走默认出发点（不再报错卡死、也不再偷偷定位）",
    "const fb = defaultOrigin();" in nav_js,
)
check(
    "★ 兜底出发点会写回输入框（否则起点框空着、指引却写从别处出发）",
    "if (originEl) originEl.value = fb.name;" in nav_js,
)
check(
    "起点识别失败时的提示指路「清空起点」",
    "或清空起点的输入框，就会用校内默认出发点" in nav_js,
)

# ── ★ 负向守卫：「我的位置」必须彻底消失 ─────────────────────────
# 这一版按需求把定位整个删了（定位不准，得不偿失）。下面逐个断言「不存在」。
print("\n-- ★「我的位置」已删除（负向守卫）--")
LOCATE_MARKERS = [
    # JS 层
    "GeoLocator", "navigator.geolocation", "getCurrentPosition", "watchPosition",
    "myLocation", "meCircle", "meMarker", "lastLocateResult", "GeoError",
    "getMyLocation", "getTrustedLocation", "locateMe", "locateAndNav",
    "dropMeMarker", "renderGeoQuality", "isSecureContextForGeo", "GEO_TRUST_RADIUS",
    "distanceToNetwork", "locateByIP", "wgs84ToGcj02",
    # 界面层
    "geo-quality", "geo-note", "我的位置", "用我的位置出发",
    # 后端 / 启动脚本
    "https_port", "_https_port", "CAMPUS_HTTPS_PORT", "HTTPSPORT", "make_cert",
    # 高德插件
    "AMap.Geolocation",
]
for f, text in (("nav.js", nav_js), ("nav.html", nav_html), ("common.js", read(COMMON_JS)),
                ("campus-graph.js", read(GRAPH_JS)), ("style.css", read(STYLE_CSS)),
                ("main.py", main_py), ("start-windows.bat", read(START_BAT))):
    hits = [m for m in LOCATE_MARKERS if m in text]
    check(f"{f} 里没有定位残留", not hits, "、".join(hits))

check("frontend/js/locate.js 已删除", not (BASE / "frontend" / "js" / "locate.js").exists())
check("tools/make_cert.py 已删除", not (BASE / "tools" / "make_cert.py").exists())
check("tools/test_locate.mjs 已删除", not (BASE / "tools" / "test_locate.mjs").exists())
check(
    "高德只加载 Walking + Geocoder 两个插件",
    "&plugin=AMap.Walking,AMap.Geocoder`" in read(COMMON_JS),
)
check(
    "nav.html 不再引入 locate.js",
    "locate.js" not in nav_html,
)
check(
    "两份启动脚本逐字节一致（含这次删掉的 https 分支）",
    read(START_BAT) == read(ALT_BAT),
)

print()
print("=" * 62)
passed = sum(1 for _, ok, _ in results if ok)
print(f"  结论: {passed} 通过 / {len(results) - passed} 失败")
print("=" * 62)
sys.exit(0 if passed == len(results) else 1)
