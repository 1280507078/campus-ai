#!/usr/bin/env python3
"""数据「输出」的契约测试（换包 / 注销两条路径都必须先把数据输出出来）。

跑法：python tools/test_data_export.py

## 为什么值得单独测


向量库数据输出」。这条要求有两个**很容易被悄悄破坏**的点，都靠测试钉住：

  1. **顺序**：输出必须在**删除之前**。改代码时顺手挪一下位置（比如把导出放到
     `db.delete(user)` 后面）逻辑上照样跑、日志照样打，但打出来的是**空数据** ——
     而且没人会发现。所以这里直接对源码断言调用顺序。
  2. **两处排除规则一致**：导出的 JSON 里是**真实用户数据**（账号、课表、
     情绪对话原文），一旦进了交付包就是隐私事故。而排除规则在
     `gtest/pack_zip.py` 与 `tools/gen_structure.py` **两个文件**里各写了一份，
     这个项目的历史上已经因为"两处不同步"出现过一次，所以这里两边都断言。

⚠️ 测试全程用**临时数据库 + 临时导出目录**，不碰 `backend/campus_ai.db`，
   也不往 `backend/data_export/` 里丢垃圾。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# ★ 先改数据库指向再 import backend 模块（config 在导入时就实例化 settings）
_TMP_DB = Path(tempfile.gettempdir()) / "campus_ai_export_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB.as_posix()
sys.path.insert(0, str(BACKEND))

from database import get_session_factory, init_db          # noqa: E402
from models.orm import Assessment, ChatSession, Course, User  # noqa: E402
from services import data_export as DX                      # noqa: E402

_passed = 0
_failed = 0


def check(title: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {title}" + (f"  {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  [FAIL] {title}  {detail}")


# ---------------------------------------------------------------------------
# 造一点数据
# ---------------------------------------------------------------------------
init_db()
db = get_session_factory()()
u1 = User(username="alice", hashed_password="x", nickname="小爱")
u2 = User(username="bob", hashed_password="x", nickname="小博")
db.add_all([u1, u2])
db.commit()
db.refresh(u1)
db.refresh(u2)
db.add_all([
    Course(user_id=u1.id, date="2026-10-15", type="course", slot_order=3,
           start_time="08:10", end_time="08:55", name="数据结构", location="博文楼-301"),
    Course(user_id=u1.id, date="2026-10-16", type="course", slot_order=5,
           start_time="10:05", end_time="10:50", name="高等数学"),
    ChatSession(user_id=u1.id, messages=[{"role": "user", "content": "今天好累"},
                                         {"role": "assistant", "content": "抱抱你"}]),
    Assessment(user_id=u1.id, result={"mood": "疲惫", "score": 42}),
])
db.commit()
# ⚠️ 先把要用的 id 取出来再关 session：关了之后再读 ORM 属性会触发懒加载刷新，
#    直接抛 DetachedInstanceError（这个测试第一版就是这么挂的）。
ALICE_ID, BOB_ID = u1.id, u2.id
db.close()

# 导出目录指到临时位置，别往项目里写
_TMP_OUT = Path(tempfile.gettempdir()) / "campus_ai_export_test_out"
for f in _TMP_OUT.glob("*.json"):
    f.unlink()
DX.EXPORT_DIR = _TMP_OUT

print("\n--- ① 全量输出（换包路径）---")
snap_all = DX.collect()
check("scope = all", snap_all.get("scope") == "all", str(snap_all.get("scope")))
sq = snap_all.get("sqlite") or {}
# ★ 库里除了本测试插的 alice / bob，还有启动时 seed 的**默认管理员 admin**
#   （2026-10-21 起 admin 是默认入库的系统账号）。导出是「如实列出库里有什么」，
#   所以 user 数不能写死 2 —— 拿实际账号数比，同时仍然要求 alice / bob 都在。
_expected_users = len(sq.get("usernames") or [])
check("数对了：courses=2 chat_sessions=1 assessments=1（users 按实际账号数）",
      sq.get("users") == _expected_users and sq.get("courses") == 2
      and sq.get("chat_sessions") == 1 and sq.get("assessments") == 1, str(sq))
check("★ 列出了涉及哪些账号（「清了 2 个账号」和「清了谁」是两件事）",
      {"alice", "bob"} <= set(sq.get("usernames") or []), str(sq.get("usernames")))
check("带上了向量库信息（有没有配、通不通、多少条）",
      "configured" in (snap_all.get("vector") or {}), str(snap_all.get("vector"))[:120])

res = DX.emit(snap_all, "测试：全量")
check("★ 落盘了一份 JSON", bool(res.get("json")) and Path(res["json"]).exists(), str(res.get("json")))
check("日志文本里包含账号名与数量",
      "alice" in res["log"] and f"users={_expected_users}" in res["log"])
saved = json.loads(Path(res["json"]).read_text(encoding="utf-8"))
check("JSON 内容可被解析且结构与内存一致",
      saved["sqlite"]["users"] == _expected_users and saved["scope"] == "all")

print("\n--- ② 个人输出（注销路径）---")
snap_u = DX.collect(user_id=ALICE_ID, username="alice", vector_user_points=7)
sq2 = snap_u.get("sqlite") or {}
check("scope = user 且只清点这一个人",
      snap_u.get("scope") == "user" and sq2.get("users") == 1, str(snap_u.get("scope")))
check("只统计该用户名下：courses=2 chat_sessions=1 assessments=1",
      sq2.get("courses") == 2 and sq2.get("chat_sessions") == 1
      and sq2.get("assessments") == 1, str({k: sq2.get(k) for k in ("courses", "chat_sessions", "assessments")}))
check("★ 带出了**明细**（注销后这些就没了，只有这里留得下）",
      len(sq2.get("courses_detail") or []) == 2
      and (sq2.get("courses_detail") or [{}])[0].get("name") == "数据结构")
check("★ 情绪对话的**原文**也在（不只是条数）",
      "今天好累" in json.dumps(sq2.get("chat_sessions_detail") or [], ensure_ascii=False))
check("★ 该用户向量条数被记下（注销后无法再查）",
      (snap_u.get("vector") or {}).get("user_points") == 7,
      str((snap_u.get("vector") or {}).get("user_points")))
res_u = DX.audit_user(ALICE_ID, username="alice", vector_user_points=7)
check("个人输出也落了盘，且文件名带账号名",
      bool(res_u.get("json")) and "alice" in Path(res_u["json"]).name, str(res_u.get("json")))

print("\n--- ③ 容错：导出失败绝不能带崩主流程 ---")
_bak = DX.EXPORT_DIR
# ★ 跨平台修正：原来写的是 Path("Z:/definitely-not-a-real-drive/out")。
#   Windows 上「Z: 盘不存在」确实写不进去，但 **Linux 上「Z:」只是一个普通目录名**
#   —— os.makedirs 会欢快地在仓库根下建出 `Z:/definitely-not-a-real-drive/out/`
#   并把 JSON 真写进去，于是「写盘失败」这条断言在 CI 上必挂（第一次跑 GitHub
#   Actions 就出错在这：18 通过 / 2 失败，两条都是这一处）。
#   改成「拿一个普通文件当目录用」：两个平台都会抛 NotADirectoryError / OSError，
#   而且不会在任何地方留下垃圾目录。
_not_a_dir = Path(tempfile.mkdtemp()) / "regular-file.txt"
_not_a_dir.write_text("x", encoding="utf-8")
DX.EXPORT_DIR = _not_a_dir / "out"      # 往文件底下建目录 → 必定失败
try:
    res_bad = DX.emit(snap_all, "测试：写盘失败")
    check("★ 写盘失败时仍返回结果、不抛异常",
          isinstance(res_bad, dict) and res_bad.get("json") is None)
    check("日志里明确说明落盘失败（不装作成功）", "落盘失败" in res_bad["log"])
except Exception as e:
    check("★ 写盘失败时仍返回结果、不抛异常", False, f"抛了 {type(e).__name__}: {e}")
DX.EXPORT_DIR = _bak

print("\n--- ④ 静态守卫：顺序与排除规则 ---")
auth_src = (BACKEND / "routers" / "auth.py").read_text(encoding="utf-8")
i_audit = auth_src.find("data_export.audit_user")
# ⚠️ 用 rfind 而不是 find：**文档字符串里也提到了 `db.delete(user)`**
#    （"取 user.id 必须在 db.delete(user) 之前"），用 find 会匹配到那句注释，
#    导致这条守卫恒失败 —— 断言写错方向比不写更糟。
i_delete = auth_src.rfind("db.delete(user)")
check("★ auth.py 里「输出」必须出现在 db.delete(user) **之前**",
      0 <= i_audit < i_delete, f"audit@{i_audit} delete@{i_delete}")
check("auth.py 注销时取了该用户的向量条数（count_user_memory）",
      "count_user_memory" in auth_src)

db_src = (BACKEND / "database.py").read_text(encoding="utf-8")
check("★ database.py 换包时会先输出数据（_audit_before_reset）",
      "_audit_before_reset(" in db_src and "audit_all(" in db_src)
check("★ 清库分支里连向量库一起清（_clear_vector_store）",
      "_clear_vector_store()" in db_src and "clear_all_memory" in db_src)
i_audit2 = db_src.find("_audit_before_reset(old, cur)")
i_unlink = db_src.rfind("db_file.unlink()")   # 同上：顶部注释里也提过它
check("★ 换包的「输出」在 unlink() **之前**（否则输出的是已经没了的库）",
      0 <= i_audit2 < i_unlink, f"audit@{i_audit2} unlink@{i_unlink}")

pack_src = Path("C:/Users/z1280/WorkBuddy/2026-10-12-11-02-53/gtest/pack_zip.py")
if pack_src.exists():
    check("★ pack_zip.py 排除了 data_export（里面是真实用户数据）",
          '"data_export"' in pack_src.read_text(encoding="utf-8"))
    check("★ gen_structure.py 也排除了 data_export（两处必须一致）",
          '"data_export"' in (ROOT / "tools" / "gen_structure.py").read_text(encoding="utf-8"))
else:
    print("  [SKIP] 没找到 gtest/pack_zip.py，跳过打包排除规则断言")

print("\n" + "=" * 60)
print(f"  结论: {_passed} 通过 / {_failed} 失败")
print("=" * 60 + "\n")
sys.exit(1 if _failed else 0)
