"""带 no-cache 的静态文件服务器（替代 `python -m http.server`）。

为什么不能直接用 `python -m http.server`
----------------------------------------
它会给每个响应加上 `Last-Modified`，并支持 `If-Modified-Since`。这本身没错，
但**它不会发 Cache-Control**，于是浏览器可以自行决定缓存多久。

真实事故：用户换了新压缩包、旧文件也删干净了、黑窗口也关了，**页脚却仍然
显示更早的构建号**（2026-09-26-c），后端的 /api/ping 也读不到 build 字段 ——
看起来就像「包没换成功」。实际上新代码在跑，只是浏览器把旧的 login.html
和 js 缓存住了，根本没有去服务器拿。
（端口 8000 那条路由走的是后端 FastAPI，后端已经加了 no-cache；
 但启动脚本默认打开的是 5500 端口的这个静态服务，它没有。）

这个项目里所有前端资源都带 `?v=` 版本号，本来可以破缓存；但 `?v=` 是写在
HTML 里的，**如果 HTML 本身被缓存了，新的版本号就永远读不到** —— 死循环。

所以这个服务器对 .html / .js / .css 一律发：
    Cache-Control: no-cache, no-store, must-revalidate
    Pragma: no-cache
    Expires: 0
「no-store」是刻意加的：对本项目来说，「每次拿到的一定是最新代码」远比
「省几 KB」重要，因为排障成本高得多。图片/字体这类不会变的大文件仍然走
普通缓存（省流量）。

用法：
    python serve_nocache.py 5500            # 监听 5500，服务当前目录
    python serve_nocache.py 5500 --dir .    # 显式指定目录
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
from pathlib import Path

# 这些后缀必须「每次都重新问服务器」，绝不直接用缓存
NO_STORE_SUFFIX = (".html", ".htm", ".js", ".mjs", ".css", ".json")

# 绝对不允许被下载的后缀。
# 为什么：这个服务器是**把整个 frontend 目录暴露出去**的。
# 而启动脚本是在 frontend 目录里跑这个脚本的，于是 serve_nocache.py 自己
# 也在目录里 —— 浏览器直接访问 /serve_nocache.py 就能把源码下载走。
# 现在里面没有密钥，但这个项目 .env / 配置类文件一旦将来挪进来就是事故，
# 所以从「能服务什么」入手，而不是「碰巧没放敏感文件」。
FORBIDDEN_SUFFIX = (".py", ".pyc", ".pyo", ".env", ".ini", ".cfg", ".log", ".db", ".sqlite")
FORBIDDEN_NAMES = {".env", ".env.local", "requirements.txt", "package.json"}


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """在标准静态服务之上：禁用代码资源缓存 + 拒绝下载源码/配置。"""

    def _blocked(self) -> bool:
        """是否属于「不允许被下载」的文件。"""
        raw = self.path.split("?", 1)[0]
        name = raw.rsplit("/", 1)[-1].lower()
        return name in FORBIDDEN_NAMES or raw.lower().endswith(FORBIDDEN_SUFFIX)

    def end_headers(self) -> None:
        # 注意：路径带查询串（?v=xxx / ?t=123），要按「后缀」判断而不是整串
        raw = self.path.split("?", 1)[0]
        if raw.lower().endswith(NO_STORE_SUFFIX):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):
        if self._blocked():
            self.send_error(404, "File not found")
            return None
        return super().send_head()

    # 不打印每个请求的日志到窗口会让人以为「卡住了」；保留但精简
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))


class ReuseServer(socketserver.ThreadingTCPServer):
    # 重开窗口后端口立刻可用，避免 TIME_WAIT 导致「端口被占用」
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("port", nargs="?", type=int, default=5500)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="监听地址，默认 0.0.0.0（手机也能访问）")
    ap.add_argument("--dir", default=".", help="要服务的目录，默认当前目录")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}")
        return 1

    handler = functools.partial(NoCacheHandler, directory=str(root))
    try:
        with ReuseServer((args.bind, args.port), handler) as httpd:
            print(f"  静态服务器已启动：http://127.0.0.1:{args.port}/")
            print(f"  服务目录：{root}")
            print("  （.html/.js/.css/.json 已禁用缓存，改完代码刷新即生效）")
            httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"[错误] 无法监听 {args.bind}:{args.port} —— {e}")
        print("       端口可能被占用，换一个端口试试。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
