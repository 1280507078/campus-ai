"""数据库引擎与会话。开发期可用 Navicat Premium 连接 DATABASE_URL 管理表结构。

默认使用 SQLite（零配置，开箱即跑）。要用 MySQL 只需在 .env 里设置：
    DATABASE_URL=mysql+pymysql://root:密码@127.0.0.1:3306/campus_ai?charset=utf8mb4

引擎采用「懒加载」：create_engine 不在 import 时执行，否则配置错误会在
模块导入阶段就抛异常，导致启动期的友好提示没机会打印出来。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import BASE_DIR, settings

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_engine():
    """首次调用时创建引擎（懒加载，便于统一捕获配置错误）。"""
    global _engine
    if _engine is None:
        url = settings.resolved_database_url()   # 相对路径 -> 基于 backend 目录的绝对路径
        # SQLite 需要 check_same_thread=False，否则多线程下会报线程限制
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """首次运行建表并写入 6:00-23:00 空白时间槽（开发期用；生产建议用 Alembic 迁移）。"""
    import models.orm  # noqa: F401 确保模型被注册
    _reset_if_stale_package()  # 换了一份新的压缩包 -> 丢掉上一份留下的老库
    was_empty = _is_fresh_sqlite()   # 建表前判断：是不是一个全新的空库
    _ensure_columns()          # 老库补新列（SQLite 没有自动迁移）
    Base.metadata.create_all(bind=get_engine())
    from seed import seed_time_slots, migrate_course_dates, polish_slot_labels, seed_admin
    db = get_session_factory()()
    try:
        seed_time_slots(db)
        polish_slot_labels(db)     # 老库节次名去掉「第」字
        migrate_course_dates(db)   # 旧 day 数据补 date
        # 管理员账号（用户名/密码见 config.ADMIN_USERNAME / ADMIN_PASSWORD）。
        # ★ 放在最后：它依赖 users 表已建好，且不该影响任何参考数据的写入。
        # ★ 它是**幂等**的：每次启动都跑，已存在就什么都不做（不重置密码）。
        seed_admin(db)
    finally:
        db.close()
    _write_package_marker()

    if was_empty:
        print("\n" + "=" * 58)
        print("[提示] 检测到数据库是新建的空库，当前没有任何账号。")
        print("       如果你之前注册过账号却登录不上，多半是 campus_ai.db")
        print("       被删除或更换了位置 —— 这个文件就是账号数据的全部。")
        print("       重新注册一个账号即可继续使用。")
        print("=" * 58 + "\n")


# =====================================================================
# 换包检测（**不再自动删库**）
# ---------------------------------------------------------------------
# 历史：这段逻辑原来做的是「换了新包就把旧 SQLite 删掉重建」，理由是出现过
# 「解压了新包、注册了新账号，看到的却还是上一个账号的数据」。
#
# ★ build q：**改成默认不删**。因为「自动删用户数据」这件事本身太危险，
#   而且已经真出事了：
#     · 公网部署时，每次重新发布都会重置源码 mtime -> 判定成「换了包」-> 删库
#       -> 所有已注册账号被清空。用户浏览器里还留着旧 token，于是每个请求都 401、
#       被弹「登录状态已过期」并踢回登录页，看起来就是「一直登录不了」（真实案例）。
#     · 本机「把新包解压到旧文件夹」也一样会**静默丢掉全部课表和情绪记录**。
#
#   现在的行为：
#     · 默认什么都不删 —— 把旧库**另存一份备份**（campus_ai.db.bak-<旧标识>），
#       并明确告诉用户数据和备份在哪、想从零开始该怎么做；
#     · 只有显式设了 CAMPUS_RESET_DB=1 才真的删库重建（给确实要干净环境的人）。
#   一句话：数据只在用户亲手要求时才消失。
#
# 注意：只有在使用默认 SQLite 时才涉及这段；配了 MySQL 的属于自己管数据，一律不动。
# =====================================================================


def _env_truthy(name: str) -> bool:
    import os
    return (os.environ.get(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


# 包标识：用「核心源码最后修改时间」代表这一份包的版本，比手写版本号可靠。
def _package_id() -> str:
    """用核心源码的最新修改时间当作这一份包的标识。"""
    import os
    files = [
        BASE_DIR / "main.py",
        BASE_DIR / "models" / "schemas.py",
        BASE_DIR / "models" / "orm.py",
        BASE_DIR / "seed.py",
    ]
    newest = 0.0
    for f in files:
        try:
            newest = max(newest, os.path.getmtime(f))
        except OSError:
            pass
    return str(int(newest))


def _marker_path():
    return BASE_DIR / ".package_marker"


def _audit_before_reset(old: str, cur: str) -> None:
    """换包前的「数据输出」：把**全部**库数据 + 向量库数据打印出来并落一份 JSON。

    ⚠️ 这里**必须自己吞异常**：导出只是"观测手段"，绝不能因为导出失败
       就把换包流程带崩（那是用新故障换掉旧手段）。
    ⚠️ 用同步调用，**不要**在这里 `asyncio.run()` —— 本函数是在 FastAPI 的
       lifespan 里被调用的，那时事件循环已经在跑，会直接抛
       "cannot be called from a running event loop"。
    """
    try:
        from services import data_export
        data_export.audit_all(f"检测到新压缩包（包标识 {old} -> {cur}）")
    except Exception as e:                                   # noqa: BLE001
        print(f"[警告] 换包前的数据输出失败（不影响换包本身）：{type(e).__name__}: {e}",
              flush=True)


def _clear_vector_store() -> None:
    """清空向量库（Qdrant）的全部记忆。失败只警告，不阻断清库流程。"""
    try:
        from services import qdrant as memory
        result = memory.clear_all_memory()
        if result.get("skipped"):
            print("[提示] 未配置 Qdrant，向量库无需清理。", flush=True)
        elif result.get("ok"):
            print(f"[提示] 向量库已一并清空（原有向量 {result.get('before')} 条）。", flush=True)
        else:
            print(f"[警告] 清空向量库失败：{result.get('error')}；"
                  "数据库已清，向量可能残留，请手动处理。", flush=True)
    except Exception as e:                                   # noqa: BLE001
        print(f"[警告] 清空向量库时出错（库已清）：{type(e).__name__}: {e}", flush=True)


def _reset_if_stale_package():
    """换了新包时：默认**保留数据并另存备份**；只有 CAMPUS_RESET_DB=1 才真删库。"""
    if not settings.resolved_database_url().startswith("sqlite"):
        return                                    # MySQL 不动
    db_file = BASE_DIR / "campus_ai.db"
    if not db_file.exists():
        return                                    # 本来就没有，没必要处理

    marker = _marker_path()
    try:
        old = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    except OSError:
        old = ""
    cur = _package_id()

    # 没换包（old == cur），或老版本包没留标记、无从判断（not old）—— 都不动。
    if not old or old == cur:
        return

    # ★ 确实是换包了。**先把库里有什么输出出来**。
    #   范围 = 「全部」：换包这条路要么清库、要么整份换掉，
    #   而"删掉了什么、涉及哪些账号"必须留下痕迹 —— 这个项目吃过**静默丢数据**的亏
    #   （库被重建、账号全没，用户那边只表现为"登录不上"，查了半天才发现）。
    _audit_before_reset(old, cur)

    if _env_truthy("CAMPUS_RESET_DB"):
        try:
            db_file.unlink()
            print("\n" + "=" * 58)
            print("[提示] 你设了 CAMPUS_RESET_DB=1，已按你的要求清空本地数据库。", flush=True)
            print("       现在是一套全新环境，重新注册账号即可开始使用。", flush=True)
            print("       想保留数据就删掉这个环境变量再启动。", flush=True)
            # ★ 清库必须**连向量库一起清**。
            #   为什么不能只清 SQLite：users.id 没有 AUTOINCREMENT，清库后
            #   第一个注册的用户会拿到 id=1，而 id=1 的历史向量还躺在 Qdrant 里，
            #   新用户直接检索到**上一个人的隐私片段**（auth.py 注销账号时
            #   反复强调的同一个理由，只是"清库"这条路更容易被忘掉）。
            _clear_vector_store()
            print("=" * 58 + "\n")
        except OSError as e:
            print(f"[警告] 想清空旧数据库但失败了：{e}。"
                  f"请手动删除 {db_file} 后重启。", flush=True)
        return

    import shutil
    backup = BASE_DIR / f"campus_ai.db.bak-{old}"
    saved = False
    try:
        if not backup.exists():
            shutil.copy2(db_file, backup)
        saved = True
    except OSError as e:
        print(f"[警告] 想给旧数据库留备份但没成功：{e}（数据本身没有被改动）。", flush=True)

    print("\n" + "=" * 58)
    print("[提示] 检测到程序包已更新（换了一份新包）。", flush=True)
    print("       为**不丢数据**，现有账号 / 课表 / 情绪记录继续沿用，没有清空。", flush=True)
    if saved:
        print(f"       旧库另存了一份备份：{backup.name}", flush=True)
        print("       （确认新环境没问题后，这个备份可以手动删除。）", flush=True)
    print("       想从零开始：删掉 backend/campus_ai.db 再启动，", flush=True)
    print("       或者设环境变量 CAMPUS_RESET_DB=1 启动一次。", flush=True)
    print("=" * 58 + "\n")


def _write_package_marker():
    try:
        _marker_path().write_text(_package_id(), encoding="utf-8")
    except OSError:
        pass


def _is_fresh_sqlite() -> bool:
    """SQLite 且文件不存在（或没有任何表）时返回 True —— 说明这是全新空库。"""
    if not settings.resolved_database_url().startswith("sqlite"):
        return False
    from sqlalchemy import inspect
    try:
        return not inspect(get_engine()).get_table_names()
    except Exception:
        return False


def _ensure_columns():
    """给已存在的旧库补上后加的列（SQLite 用 ALTER TABLE ADD COLUMN 即可）。

    只处理「加了新字段」的增量场景；如果列已经存在会抛错，忽略即可。
    老库的 courses 表若已存在，create_all 不会改结构，所以这里必须显式补列。
    """
    engine = get_engine()
    # 表名 -> [(列名, 类型定义)]
    wanted = {
        "courses": [("date", "VARCHAR(10)"), ("type", "VARCHAR(8) DEFAULT 'course'"),
                    # 上课周次原始串，空 = 每周都上（老数据天然是空，不用回填）
                    ("weeks", "VARCHAR(128) DEFAULT ''")],
        "time_slots": [("start_south", "VARCHAR(8) DEFAULT ''"),
                       ("end_south", "VARCHAR(8) DEFAULT ''"),
                       ("start_north", "VARCHAR(8) DEFAULT ''"),
                       ("end_north", "VARCHAR(8) DEFAULT ''")],
        # 管理员标记：老库没有这一列，补上后已有行默认 0（普通用户）。
        # 管理员本身由 seed_admin 在启动时创建/升级，不靠这个默认值。
        "users": [("is_admin", "BOOLEAN DEFAULT 0")],
    }
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if table not in existing_tables:
                continue                      # 新库交给 create_all 建全字段
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
