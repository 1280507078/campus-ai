"""FastAPI 入口。

本项目设计成「换台电脑双击就能跑」，所以这里做了三件健壮性处理：

1. **同时托管前端静态文件**（挂载到 /app）。
   即使前端那个 http.server 没起来，直接访问后端端口也能用完整网站：
       http://127.0.0.1:8000/app/login.html
   前端仍可单独用 5500 端口跑（开发习惯），两条路互不影响。

2. **所有路径均基于文件自身位置推导**（见 config.BASE_DIR），
   所以从任何工作目录启动都能找到前端目录，不依赖 %CD%。

3. **启动端口可被环境变量覆盖**（CAMPUS_API_PORT），
   启动脚本检测到 8000 被占用时会换端口，并把实际端口注入前端。
"""
import asyncio
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from routers import auth, planner, emotion, map_weather, feedback, admin

# ---- 日志配置 ----
# 以前整个项目没有 basicConfig，导致各模块里已经写好的 logger.info / logger.warning
# **一条都打不出来**（默认 root logger 等级是 WARNING，且没配 handler）。
# 结果是「AI 识别失败」这类问题发生时后端一片安静，只能靠前端猜，非常难排查。
#
# 这里统一开 INFO。刻意**不加 force=True**：uvicorn 自己也会配 logging，
# force=True 会把它的 handler 一起冲掉，反而让访问日志消失。
# 只补一个 handler 给 root，uvicorn 的配置仍然生效。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# httpx 每次请求打一行 INFO，会把识别过程的关键日志淹掉，压到 WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)

# backend/ 的绝对路径；frontend/ 与它同级
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _api_port() -> int:
    """后端实际监听端口（可用环境变量覆盖）。

    优先级：CAMPUS_API_PORT → PORT → API_PORT → 8000。

    为什么顺序是这样，而不是把 PORT 放最前：

      · CAMPUS_API_PORT 是**本项目自己**的覆盖手段，由 start-windows.bat 在
        8000 被占用时写入。它排第一，本机「双击即用」的行为就一个字都不会变。

      · PORT 是**云平台 / 容器沙箱的通用约定**（新增，2026-10-14 build o）。
        这类环境里 CAMPUS_API_PORT 根本不存在，所以走不到第一条，正好落到
        这里。不认它的后果不是「端口差一点」而是**致命**：服务会听在一个
        平台没开放的口子上，外部一律连不上，整站打不开 —— 而本机怎么试都
        是好的（本机永远命中第一条），最难排查的那种。

      · API_PORT 是更早的历史名字，留着兼容。
    """
    for key in ("CAMPUS_API_PORT", "PORT", "API_PORT"):
        raw = os.environ.get(key, "").strip()
        if raw.isdigit():
            return int(raw)
    return 8000


async def _warm_docs() -> None:
    """启动自检：学校文档语料没进向量库就补进去。

    ★★★ 为什么必须有这一步（2026-10-21）：
      语料导入原本是个**手工**命令（`cd backend && python -m services.rag_docs`）。
      于是换一台机器、或者换一个 Qdrant 实例部署时，集合是空的 ——
      而系统**不会报任何错**：检索返回空 → 走「手册里没写」的拒答口径 →
      学生看到「手册里没写」，运维看到「一切正常」。
      这种错比直接崩掉难查一百倍（崩了至少知道要看日志），所以改成启动自检。

    ★ 为什么放后台任务、不用 await：
      `ingest()` 要调 embedding，29 篇 91 块要十几秒。启动时 await 它，
      健康检查（Dockerfile 里 30 秒一次）会连着超时，容器被判不健康。
      放后台先能服务，导完自然会好。

    ★ 判据是「集合为空 **或** 语料指纹变了」：
      只判空的话，语料新增了文档但集合非空时，新文档永远进不去库。
    """
    from services import qdrant as Q
    from services import rag_docs as R

    if not Q.is_configured():
        print("[文档问答] 未配置 QDRANT_URL / QDRANT_API_KEY —— "
              "学校规定类问题会统一回答「手册里没写」")
        return
    try:
        n = int((Q.docs_stats() or {}).get("points") or 0)
        fp = R.fingerprint()
        if n and fp and fp == R.read_fingerprint():
            print(f"[文档问答] 就绪 · 语料已入库 {n} 块")
            return
        why = "集合为空" if not n else "语料有变动"
        r = await R.ingest()
        if r.get("ok"):
            R.write_fingerprint(fp)
            print(f"[文档问答] {why}，已导入 {r.get('files')} 篇 / {r.get('points')} 块")
        else:
            print(f"[文档问答] 导入失败：{r.get('error')}（其他功能不受影响）")
    except Exception as e:                      # noqa: BLE001 —— 自检失败不能拖垮启动
        print(f"[文档问答] 预热失败，其他功能不受影响：{type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表并写入 6:00-23:00 时间槽，避免首次请求报 no such table。"""
    from config import settings
    from database import init_db

    try:
        init_db()
    except Exception as e:
        # 把满屏报错栈换成人能看懂的提示，否则初次运行很容易卡在这里
        print("\n" + "=" * 62, file=sys.stderr)
        print("  [启动失败] 数据库连接不上，后端无法启动。", file=sys.stderr)
        print(f"  当前 DATABASE_URL = {settings.resolved_database_url()}", file=sys.stderr)
        print(f"  原因：{type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        print("-" * 62, file=sys.stderr)
        if settings.resolved_database_url().startswith("mysql"):
            print("  你配置的是 MySQL，请检查：", file=sys.stderr)
            print("    1) MySQL 服务是否已启动", file=sys.stderr)
            print("    2) 是否已建库 campus_ai", file=sys.stderr)
            print("    3) 密码是否正确", file=sys.stderr)
            print("    4) 是否装了驱动：python -m pip install pymysql", file=sys.stderr)
            print("  想快速跑起来，删掉 backend/.env 改用默认 SQLite 即可。", file=sys.stderr)
        print("=" * 62 + "\n", file=sys.stderr)
        raise

    db_desc = settings.resolved_database_url().split("://")[0]
    port = _api_port()
    print(f"[启动成功] 后端已就绪 · 数据库: {db_desc}")
    print(f"           API 文档: http://127.0.0.1:{port}/docs")
    if (FRONTEND_DIR / "login.html").exists():
        print(f"           网页入口: http://127.0.0.1:{port}/app/login.html")

    # ★ 文档库自检：后台跑，不 await（理由见 _warm_docs 的注释）
    _docs_task = asyncio.create_task(_warm_docs())
    # 任务引用要留着，否则可能被 GC 回收；回调里把异常吃掉，避免
    # 「Task exception was never retrieved」刷满日志。
    _BG_TASKS.add(_docs_task)
    _docs_task.add_done_callback(_BG_TASKS.discard)
    yield


# 后台任务的强引用集合（否则 create_task 的返回值没人持有，可能被回收）
_BG_TASKS: set = set()


app = FastAPI(title="校园 AI 助手", version="0.1.0", lifespan=lifespan)

# 统一异常处理：把 Pydantic 的英文校验报错、未捕获异常都转成中文
from errors import register_exception_handlers  # noqa: E402

register_exception_handlers(app)

# 开发期放开跨域；部署时改为具体前端域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(planner.router, prefix="/api/planner", tags=["planner"])
app.include_router(emotion.router, prefix="/api/emotion", tags=["emotion"])
app.include_router(map_weather.router, prefix="/api/map", tags=["map"])
# 意见反馈：用户提一条、看自己提过的（含管理员回复）。
app.include_router(feedback.router, prefix="/api/feedback", tags=["feedback"])
# 管理后台：数据统计 + 反馈列表 + 回复。整个 router 挂 get_admin_user，
# 所以这里不需要再逐条加依赖 —— 少写一处就少一个漏权限的口子。
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


# ---- 构建标记：只从「一个地方」读，不再硬编码 ----
#
# 为什么要这么绕：以前构建号是**硬编码在这个文件里**的字符串，同时包里还有一个
# 构建标记文件，启动脚本也会把它印在横幅上。于是同一件事有两个来源，打包时改了
# 一个忘了另一个，就会出现「横幅说 h、接口说 g」—— 而构建号的全部意义就是
# **判断到底跑的哪份代码**，它自己都对不上就毫无意义了。
#
# ⚠️ 为什么不用 .package_marker：那个文件是**另一个用途**，由 database.py 在每次
#    启动时用「核心源码最新 mtime」**自动覆写**，用来检测「换了新包」并清库。
#    它必须保持机器生成，人工往里写版本号会在下次启动就被冲掉（这次就踩了：
#    手写的 "2026-09-26-h" 启动后被覆写成时间戳 "1790492747"）。
#    所以展示用的版本号另放一个文件：build.txt，只由打包脚本写。
#
# 读不到时给一个显眼的 UNKNOWN，而不是悄悄回落到旧号 —— 宁可显眼地报未知，
# 也不要给出一个「看起来正常但可能是错的」版本号。
def _build_tag() -> str:
    build = Path(__file__).resolve().parent / "build.txt"
    try:
        tag = build.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "UNKNOWN"
    return tag or "UNKNOWN"



@app.get("/", include_in_schema=False)
def root():
    """根路径：把人直接送到登录页，而不是甩一段 JSON。

    本机双端口开发时，返回 `{"msg": ..., "web": ...}` 那种 JSON 是够用的
    （本来就是给开发者看的）。但**公网部署之后，分享出去的链接就是域名根**，
    队友 / 评委点开看到一坨 JSON，第一反应是「这站点坏了」。

    所以：只要前端目录在，就 302 到 /app/login.html；前端不存在时
    （有人想只把后端当 API 跑）仍旧回落成 JSON，不给死链。
    """
    if (FRONTEND_DIR / "login.html").exists():
        return RedirectResponse("/app/login.html", status_code=302)
    return {
        "msg": "校园 AI 助手后端正在运行",
        "docs": "/docs",
        "web": f"http://127.0.0.1:{_api_port()}/app/login.html",
    }


@app.get("/api/ping")
def ping():
    """前端的「找后端」探针：无需登录、必定 200。

    以前前端用 /api/auth/me 探测，未登录时返回 401，浏览器控制台会留一条红色
    报错（虽然功能正常，但演示时容易被误认为出错）。改用这个公开端点后，
    探测既准确又干净。静态文件服务器不会有 /api/ping，仍能区分前后端。

    除了探测存活，这里还**自报功能版本**（features）。
    加这个是因为吃过亏：输入里说「表格还是不能识别」，我们查了半天，
    最后发现他跑的是旧代码 —— 而旧代码和新代码从外面**长得一模一样**，
    用户自己也无从判断。现在打开控制台请求一次 /api/ping，就能看到
    后端到底支不支持「宽容节次」「周次」这些特性，不用再靠猜。
    """
    return {
        "ok": True,
        "service": "campus-ai",
        "port": _api_port(),
        # 构建标记：与前端 login.html 页脚的「构建 20xx-xx-xx-x」对齐
        "build": _build_tag(),
        # 后端具备的关键能力，用来区分新旧版本（老版本没有这些键）
        "features": ["tolerant-period", "tolerant-weekday", "no-period-fallback", "weeks"],
    }


# 虚拟网卡（VMware / VirtualBox / WSL / Hyper-V）默认占用的网段。
# 这些地址只有本机能访问，手机连不上 —— 和 start-windows.bat 第 6 节同一套判据。
_VIRTUAL_PREFIXES = ("192.168.56.", "192.168.99.", "172.16.", "172.17.", "172.18.", "172.19.")


def _lan_ip() -> str:
    """本机局域网 IPv4（手机能连上的那个），拿不到返回空串。

    两步走：
      1. UDP「连」一个不可达地址（**不会真的发包**，只是让内核挑出默认出口网卡），
         这样拿到的是路由表里真正对外的网卡地址，天然避开虚拟网卡；
      2. 兜底用主机名解析。
    结果再过一遍虚拟网段 / 回环 / APIPA 过滤。
    """
    candidates: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            candidates.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        candidates.append(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass

    for ip in candidates:
        if not ip or ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if ip.startswith(_VIRTUAL_PREFIXES):
            continue
        return ip
    return ""


@app.get("/api/system/lan-ip")
def system_lan_ip():
    """本机局域网地址：给「电脑用 127.0.0.1 打开 + 手机扫码」这个组合用。

    ## 为什么需要这个接口

    电脑上的浏览器地址栏是 `http://127.0.0.1:端口`，但登录页的二维码**不能**
    就是这个地址 —— 手机扫到之后连的是**手机自己**，必然打不开（这是线上真实
    故障，见 tools/test_launcher_url.py 的说明）。所以电脑端在生成二维码时，
    需要知道本机的**局域网 IP** 来替换掉 127.0.0.1。

    前端在浏览器里没有别的途径知道这件事（WebRTC 那套既不可靠又要额外权限），
    而后端本来就跑在同一台机器上，直接问它最省事 —— 也就有了这个接口。
    拿不到局域网 IP 时返回空串，前端会退回用当前页面地址生成二维码，
    并在扫码面板里如实提示（而不是给一个扫了也没用的码）。
    """
    return {
        "ip": _lan_ip(),
        "port": _api_port(),
    }


# ---- 托管前端静态文件（放在所有 API 路由之后，避免抢占 /api 前缀）----
# 这样后端一个端口就能提供完整网站，是「双击即用」的重要兜底。
if FRONTEND_DIR.is_dir():
    # ⚠️ 给静态文件加 no-cache，而不是让它缓存。
    #
    # 为什么：改完前端（campus.js / planner.js / style.css…）后，浏览器常常
    # 还拿着旧的 js/css 跑，表现就是「代码明明改了、页面上却没变化」——
    # 排查半天最后发现只是缓存。这个项目前端资源都带 ?v= 版本号，本来可以
    # 靠版本号破缓存，但**用户手动打开、或直接用 file:// 打开时版本号可能没走对**，
    # 于是又回到缓存问题。
    #
    # 开发/演示场景下（本项目就是这个场景）「每次都拿最新的」远比
    # 「省那几 KB 流量」重要，所以统一 no-cache：
    # 浏览器仍会缓存，但每次都会带 If-None-Match 回来问一句，
    # 文件没变就 304（省流量），变了就立刻拿新的（不会跑旧代码）。
    @app.middleware("http")
    async def _no_cache_static(request, call_next):
        resp = await call_next(request)
        path = request.url.path
        # 只对静态资源动手，API 响应不管（API 本来就该由前端逻辑控制刷新）
        if path.startswith("/app/") or path.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # 直接 `python backend/main.py` 就能起服务（本机习惯，也是云平台的启动入口）。
    #
    # ★ 热重载只在**本机**开，被云平台用 PORT 拉起时关掉：
    #   · 线上没人改文件，watchfiles 白白常驻 + 扫目录，纯浪费；
    #   · 更重要的原因见下 —— 走这条路时端口由 _api_port() 从环境变量读，
    #     **不依赖 shell 展开 $PORT**，比 `uvicorn ... --port $PORT` 稳
    #     （沙箱若不经 shell 直接 exec，$PORT 会原样传进去变成非法端口）。
    #   · cwd 在哪都无所谓：BACKEND_DIR 由 __file__ 推导，sys.path[0] 就是 backend。
    uvicorn.run("main:app", host="0.0.0.0", port=_api_port(), reload=not os.environ.get("PORT"))
