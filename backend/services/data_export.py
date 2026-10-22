"""数据「输出」：在**删除之前**把库数据与向量库数据导出并打印出来。

## 用户的要求（2026-10-14 原话）

    「每次换包、用户注销账号都要把个人的或者全部的库里数据，向量库数据输出。」

这两条路径正好对应两个**范围**：

    · **换包**（检测到新的压缩包）-> 范围 = **全部**
    · **用户注销账号**           -> 范围 = **个人**（只那一个人）

## 为什么必须做

这个项目最疼的一次事故就是**静默丢数据** —— 换了一份包，库被重建、账号全没了，
而用户那边只表现为"登录不上"，查了半天才发现数据早就不在了。
（后来 `_reset_if_stale_package()` 改成默认只备份不删，就是为了这个。）

所以现在的原则是：**数据可以删，但不能悄悄删。**
动手之前先把「有什么、有多少」输出出来：

  1. 落一份 JSON 到 `backend/data_export/`（完整内容，可回溯）；
  2. 在服务端日志里打一份**人能读**的清单（数量 + 账号名，不用翻 JSON）。

## 两条读的人要能一眼看懂的信息

  · 换包那条会打印**涉及哪些账号** —— 因为"清掉 3 个账号"和"清掉谁"是两件事；
  · 注销那条会打印**这个人名下的全部东西**：课表 / 情绪对话 / 测评 / 向量条数。

## ⚠️ 三个必须遵守的约束

  1. **自己吞掉所有异常。** 这个模块是"记录"用的，绝不能因为导出失败就把
     注销 / 换包这些主流程带崩 —— 那等于用一个新故障换掉一个观测手段。
  2. **`data_export/` 必须排除在交付包之外。** 里面是真实用户数据，
     打进 zip 发出去就是隐私事故（两边排除规则：`gtest/pack_zip.py` +
     `tools/gen_structure.py`）。
  3. **不引入 async。** 换包那条路径是在 FastAPI 的 lifespan 里跑的（**已经在事件循环里**），
     那里用 `asyncio.run()` 会直接抛 "cannot be called from a running event loop"。
     所以本模块**保持同步**；需要联网的高成本项（按用户数向量条数）由调用方
     先 await 好再传进来。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 导出目录（backend/data_export/）。⚠️ 已在打包与清单两处排除，别往包里带。
EXPORT_DIR = Path(__file__).resolve().parent.parent / "data_export"

# 单次导出最多列几个账号名（免得库大了刷屏；完整名单在 JSON 里）
MAX_LISTED_USERS = 50
# 每个会话最多带几条消息进 JSON（情绪对话可能很长，避免导出文件失控）
MAX_MESSAGES_PER_SESSION = 60


def _iso_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def collect(user_id: int | None = None, username: str | None = None,
            vector_user_points: int | None = None) -> dict:
    """把「要删的东西」清点出来。

    Args:
        user_id: 传了就只清点这一个人（注销账号的场景）；不传 = 全量（换包的场景）。
        username: 仅用于日志可读性。
        vector_user_points: **按用户**的向量条数。由调用方先 await
            `qdrant.count_user_memory()` 得到再传进来 —— 本模块不开事件循环，
            原因见文件顶部第 3 条。全量场景不传（那时用 qdrant.stats()）。

    Returns:
        可直接 `json.dumps` 的字典。**任何异常都不会抛出去**，出错时返回带
        `"error"` 键的最小结构。
    """
    scope = "user" if user_id else "all"
    snap: dict = {
        "scope": scope,
        "user_id": int(user_id) if user_id else None,
        "username": username,
        "generated_at": _iso_now(),
        "sqlite": {},
        "vector": {},
    }

    try:
        from database import get_session_factory
        from models.orm import User, Course, ChatSession, Assessment, TimeSlot

        db = get_session_factory()()
        try:
            if user_id:
                uid = int(user_id)
                u = db.query(User).get(uid)
                snap["sqlite"] = {
                    "users": 1 if u else 0,
                    "courses": db.query(Course).filter(Course.user_id == uid).count(),
                    "chat_sessions": db.query(ChatSession).filter(ChatSession.user_id == uid).count(),
                    "assessments": db.query(Assessment).filter(Assessment.user_id == uid).count(),
                    "user": ({"id": u.id, "username": u.username, "nickname": u.nickname,
                              "created_at": str(u.created_at)} if u else None),
                    # 明细：这是"个人的库里数据"的主体，注销后就没了，所以留全
                    "courses_detail": [
                        {"date": c.date, "type": c.type, "start": c.start_time, "end": c.end_time,
                         "name": c.name, "teacher": c.teacher, "location": c.location,
                         "building": c.building, "weeks": c.weeks}
                        for c in db.query(Course).filter(Course.user_id == uid).all()
                    ],
                    "assessments_detail": [
                        {"created_at": str(a.created_at), "result": a.result}
                        for a in db.query(Assessment).filter(Assessment.user_id == uid).all()
                    ],
                    "chat_sessions_detail": [
                        {"created_at": str(s.created_at),
                         "message_count": len(s.messages or []),
                         "messages": (s.messages or [])[-MAX_MESSAGES_PER_SESSION:]}
                        for s in db.query(ChatSession).filter(ChatSession.user_id == uid).all()
                    ],
                }
            else:
                users = db.query(User).order_by(User.id).all()
                snap["sqlite"] = {
                    "users": len(users),
                    "courses": db.query(Course).count(),
                    "chat_sessions": db.query(ChatSession).count(),
                    "assessments": db.query(Assessment).count(),
                    "time_slots": db.query(TimeSlot).count(),
                    "usernames": [u.username for u in users[:MAX_LISTED_USERS]],
                    "usernames_truncated": max(0, len(users) - MAX_LISTED_USERS),
                }
        finally:
            db.close()
    except Exception as e:                       # noqa: BLE001
        logger.warning("清点 SQLite 数据失败：%s: %s", type(e).__name__, e)
        snap["sqlite"] = {"error": f"{type(e).__name__}: {e}"}

    try:
        from services import qdrant as memory

        v = memory.stats()
        if user_id:
            v["user_points"] = vector_user_points
        snap["vector"] = v
    except Exception as e:                       # noqa: BLE001
        logger.warning("清点向量库数据失败：%s: %s", type(e).__name__, e)
        snap["vector"] = {"error": f"{type(e).__name__}: {e}"}

    return snap


def emit(snapshot: dict, reason: str) -> dict:
    """把清点结果**输出**：写 JSON 文件 + 打一份人能读的日志。

    Args:
        snapshot: `collect()` 的返回值。
        reason: 人话原因，如「检测到新压缩包」「用户主动注销账号」。

    Returns:
        {"json": 文件路径或 None, "log": 日志文本}
    """
    scope = snapshot.get("scope")
    scope_cn = "全部" if scope == "all" else f"个人（{snapshot.get('username') or snapshot.get('user_id')}）"
    lines = [
        "",
        "=" * 62,
        f"[数据输出] 范围：{scope_cn}    原因：{reason}",
        f"           时间：{snapshot.get('generated_at')}",
    ]

    s = snapshot.get("sqlite") or {}
    lines.append("  ── SQLite（本地库）──")
    if "error" in s:
        lines.append(f"     ！清点失败：{s['error']}")
    else:
        pairs = [(k, v) for k, v in s.items() if isinstance(v, int)]
        lines.append("     " + "  ".join(f"{k}={v}" for k, v in pairs))
        if snapshot.get("user_id"):
            u = s.get("user") or {}
            if u:
                lines.append(f"     账号：{u.get('username')}（昵称 {u.get('nickname')}，建于 {u.get('created_at')}）")
            for key, label in (("courses_detail", "时间表"), ("assessments_detail", "测评"),
                               ("chat_sessions_detail", "情绪对话")):
                items = s.get(key) or []
                if label == "情绪对话":
                    lines.append(f"     {label}：{len(items)} 个会话，共 "
                                 f"{sum(i.get('message_count', 0) for i in items)} 条消息")
                elif items:
                    lines.append(f"     {label}：{len(items)} 条")
        else:
            names = s.get("usernames") or []
            if names:
                more = s.get("usernames_truncated") or 0
                lines.append(f"     涉及账号：{'、'.join(names)}" + (f" …另 {more} 个" if more else ""))

    v = snapshot.get("vector") or {}
    lines.append("  ── 向量库（Qdrant，情绪长期记忆）──")
    if "error" in v:
        lines.append(f"     ！清点失败：{v['error']}")
    else:
        lines.append(f"     已配置：{'是' if v.get('configured') else '否'}"
                     f"   可连通：{'是' if v.get('reachable') else '否'}"
                     f"   总向量数：{v.get('points', 0)}")
        if snapshot.get("user_id"):
            up = v.get("user_points")
            lines.append(f"     该用户向量数：{'未知（查询失败）' if up is None else up}")

    # 落盘。失败只降级成"日志里没有文件路径"，不影响主流程。
    path = None
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        slug = "user-%s" % (snapshot.get("username") or snapshot.get("user_id")) if snapshot.get("user_id") else "all"
        f = EXPORT_DIR / f"{_stamp()}-{slug}.json"
        f.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        path = str(f)
        lines.append(f"  ── 完整清单已落盘 ──")
        lines.append(f"     {path}")
        lines.append("     【注意】该目录不进交付包（里面是真实用户数据）。")
    except Exception as e:                       # noqa: BLE001
        logger.warning("写导出文件失败：%s: %s", type(e).__name__, e)
        lines.append("  ── 完整清单落盘失败（不影响主流程，上面的统计仍然有效）──")

    lines.append("=" * 62)
    text = "\n".join(lines)
    # 直接 print 而不是只走 logger：这是给**看启动窗口 / 沙箱日志**的人看的，
    # 而那些场景里 uvicorn 未必把 root logger 输出到同一个地方（见 main.py 顶部说明）。
    print(text, flush=True)
    logger.info("[数据输出] 范围=%s 原因=%s 清单=%s", scope, reason, path)
    return {"json": path, "log": text}


def audit_all(reason: str) -> dict:
    """全量输出（**换包**路径用）。"""
    return emit(collect(), reason)


def audit_user(snapshot_user_id: int, username: str | None = None,
               vector_user_points: int | None = None, reason: str = "用户主动注销账号") -> dict:
    """个人输出（**注销账号**路径用）。"""
    return emit(collect(user_id=snapshot_user_id, username=username,
                        vector_user_points=vector_user_points), reason)
