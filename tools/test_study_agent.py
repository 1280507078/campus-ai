#!/usr/bin/env python3
"""AI伴学 Agent（function calling）的契约与行为测试。

跑法：python tools/test_study_agent.py

## 为什么要单独测这一块

Agent 和普通接口不一样：它**会自己决定调哪个工具、传什么参数**。这意味着
"接口返回 200"根本不能说明它对 —— 模型可能拿错参数、拿错日期、甚至调用一个
不存在的工具，然后一本正经地输出一份看起来很正常、其实完全错的计划。
此处开发过程中就真的遇到了这个（见下面「锚点日期」那条），所以这里逐个钉死。

## 测试里的两个注意点，写的时候必须绕开

1. **必须用临时数据库。** 这个测试要 `init_db()` 才能读 `time_slots`，
   而默认路径是 `backend/campus_ai.db` —— 那个文件一旦出现在项目目录里，
   下次部署就会把它**原样推上线上覆盖真实数据**（这种情况今天已经出现过一次）。
   所以文件顶部先把 `DATABASE_URL` 指到 `%TEMP%` 下的临时文件。

2. **不许调真模型。** 这里全部是工具层的确定性断言：给定输入，输出必须可预期。
   要不要联网（天气）也做了跳过处理，CI / 断网机器上照样能跑完。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"

# ★ 先改数据库指向，再 import 任何 backend 模块 —— config 在导入时就会实例化 settings
_TMP_DB = Path(tempfile.gettempdir()) / "campus_ai_agent_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB.as_posix()

sys.path.insert(0, str(BACKEND))

from database import get_session_factory, init_db          # noqa: E402
from services import study_agent as A                       # noqa: E402

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
# 搭一个受控环境
# ---------------------------------------------------------------------------
init_db()
db = get_session_factory()()

D1, D2 = "2026-10-15", "2026-10-16"
COURSES = [
    # 两条**紧挨着**的课（09:45 结束 + 09:45 开始）—— 专门用来验证占用段会合并
    {"date": D1, "type": "course", "start": "08:10", "end": "09:45",
     "name": "数据结构", "location": "博文楼-301"},
    {"date": D1, "type": "course", "start": "09:45", "end": "11:00",
     "name": "高等数学", "location": "知行楼A203"},
    {"date": D2, "type": "activity", "start": "19:20", "end": "20:05",
     "name": "社团例会", "location": ""},
]
CTX = {"db": db, "user": None, "courses": COURSES, "window": (D1, D2)}
run = lambda name, args: asyncio.run(A._run_tool(name, args, CTX))   # noqa: E731


print("\n--- ① 工具定义（模型就是靠这些说明决定用不用的）---")
names = [t["function"]["name"] for t in A.TOOLS]
check("工具数量 = 6", len(A.TOOLS) == 6, str(names))
check("工具名唯一（重名会让派发错乱）", len(names) == len(set(names)))
check("每个工具都有非空 description", all((t["function"].get("description") or "").strip()
                                        for t in A.TOOLS))
check("每个工具都是 type=function 且带 parameters",
      all(t.get("type") == "function" and t["function"].get("parameters") for t in A.TOOLS))
check("★ TOOLS 里的名字全都能被派发（不会落到「未知工具」）",
      all(n in A._SYNC_TOOLS or n in A._ASYNC_TOOLS for n in names),
      str([n for n in names if n not in A._SYNC_TOOLS and n not in A._ASYNC_TOOLS]) or "全部可派发")
unknown_res, _ = run("no_such_tool", {})
check("★ 未知工具返回错误而不是抛异常（Agent 循环不能因为工具名错了就崩）",
      "error" in unknown_res, str(unknown_res))

# RAG 工具（查学生手册）的三条契约 —— 换句说，这是「不许编」在工具层的落点
_docs_tool = next((t["function"] for t in A.TOOLS
                   if t["function"]["name"] == "search_campus_docs"), None)
check("★ 有 search_campus_docs 工具（学校规定不许靠模型印象答）", _docs_tool is not None)
if _docs_tool:
    _desc = _docs_tool.get("description") or ""
    check("★ 描述里明确要求「查不到就说没写、不许编」",
          ("没写" in _desc or "查不到" in _desc) and "编" in _desc)
    check("描述里给了教务处的兜底联系方式", "0591-83969194" in _desc)
    check("★ 描述覆盖了语料的实际范围（休学/转专业/校历等）",
          all(k in _desc for k in ("休学", "转专业", "校历")))
    check("该工具走异步派发表（要读向量库，不能阻塞事件循环）",
          "search_campus_docs" in A._ASYNC_TOOLS)
    check("参数只有一个 query（检索词由模型按学生原话给）",
          list((_docs_tool.get("parameters") or {}).get("properties", {})) == ["query"])


print("\n--- ② ★ 锚点日期（本轮修掉的真实事故）---")
# 事故经过：不给出"今天是几号"时，模型把今天猜成 2023-04-15，
# 于是查了 2023 年的课表、拿到 0 门课，最后交出一份和真实课表毫无关系的计划，
# 而且从输出上完全看不出来错在哪。下面这几条把这个锚点钉死。
msg = A.build_user_message(dt.date(2026, 10, 14), 3, "期末冲 90 分", "我是一名大二学生。")
check("★ 提示词里必须写明「今天」的绝对日期", "2026-10-14" in msg, msg.replace("\n", " | ")[:90])
check("★ 并且写明计划的起止区间（含截止日）", "2026-10-16" in msg, "截止日 2026-10-16")
check("★ 明确禁止模型自己推测日期", "不要自己推测今天是几号" in msg)
check("★ 天数上限被夹到 30（传 999 也不能炸）",
      "这 30 天" in A.build_user_message(dt.date(2026, 10, 14), 999, "g"))
check("★ 天数下限被夹到 1（传 0 / 负数也不能出「这 0 天」）",
      "这 1 天" in A.build_user_message(dt.date(2026, 10, 14), 0, "g"))

# ★ 新增：**逐天把「日期（周X）」列出来，让模型照抄而不是自己算星期**。
#   起因：模型拿到「今天的日期是 2026-10-15（周四）」之后，
#   仍然把 10-15 写成「（周六）」，10-16 写成「（周日）」—— 整串星期跟着错。
#   计划里每天都顶着星期，错一天就是全篇错，而它自己完全意识不到。
#   给的是"抄"的清单之后就不再依赖它的日期算术。
check("★ 逐天给出「日期（周X）」清单（2026-10-14 是周三）",
      "2026-10-14（周三）" in msg, msg.replace("\n", " | ")[:160])
check("★ 清单里每一天都带星期（10-15 周四 / 10-16 周五）",
      "2026-10-15（周四）" in msg and "2026-10-16（周五）" in msg)
check("★ 并且明说「星期照抄、不要自己推算」", "不要自己推算" in msg)
check("★ 天数变了清单也跟着变（1 天的清单里不该出现第二天）",
      "2026-10-15（周四）" not in A.build_user_message(dt.date(2026, 10, 14), 1, "g"))

# ---- 多轮对话（2026-10-15：AI伴学页改成对话框之后才有的）----
ctx_msg = A.build_user_message(dt.date(2026, 10, 14), 3, "改成两周", "", 
                               "这是连续对话。用户还说过：「期末数据结构冲 90 分」。")
check("★ context 会被拼进用户消息（「改成两周」这种短追问要靠它才读得懂）",
      "连续对话" in ctx_msg and "期末数据结构" in ctx_msg)
check("★ context 为空时不会留下多余空行",
      "\n\n" not in A.build_user_message(dt.date(2026, 10, 14), 3, "g"))


print("\n--- ③ 工具：节次作息（读 time_slots 表）---")
slots, summary = run("get_class_schedule", {})
check("能读到 18 个节次（与 seed.py 的 DEFAULT_TIME_SLOTS 一致）",
      slots.get("count") == 18, summary)
cls = [s for s in slots["slots"] if s["period_type"] == "class"]
check("上课节次带南北区两套时间（计划里写时间点要靠它）",
      cls and all("start_north" in s and "start_south" in s for s in cls),
      str(cls[0]) if cls else "没有 class 节次")
hours = [s for s in slots["slots"] if s["period_type"] == "hour"]
check("整点节次不带分区时间（不制造「同一时间两个说法」）",
      hours and all("start_north" not in s for s in hours),
      str(hours[0]) if hours else "")


print("\n--- ③.5 用户点名的日期范围（「20-21号」必须真的排 20、21 号）---")
# 原句：「帮我安排20-21号这两天晚上18-22点的空闲时间学习高等数学」，
# 结果 Agent 排了 18、19 号 —— 因为 days 只有天数、起点永远写死成"今天"。
_date = dt.date
parse_date_range = A.parse_date_range
build_user_message = A.build_user_message
_T = _date(2026, 10, 18)          # 固定一个"今天"，否则月底跑测试会飘
_r = parse_date_range("帮我安排20-21号这两天晚上18-22点的空闲时间学习高等数学", _T)
check("★ 「20-21号」-> 起点 10-20、共 2 天", _r == (_date(2026, 10, 20), 2), str(_r))
check("「18-22点」这种**时间**区间不会被当成日期",
      parse_date_range("晚上18-22点学高数", _T) is None, str(parse_date_range("晚上18-22点学高数", _T)))
check("「10月20日-10月21日」-> 10-20 起 2 天",
      parse_date_range("10月20日-10月21日", _T) == (_date(2026, 10, 20), 2))
check("没点名日期时返回 None（继续按「今天起 N 天」）",
      parse_date_range("想补一下高数", _T) is None)
_msg = build_user_message(_T, 2, "排 20-21 号", start=_date(2026, 10, 20))
check("★ 点名日期时，用户消息里必须明说「不要从今天开始排」",
      "只排 2026-10-20 到 2026-10-21" in _msg and "不要从今天" in _msg, _msg[:120])
check("「今天几号」仍说实话（锚点不能被起点顶掉）",
      "今天的日期是 2026-10-18" in _msg, _msg[:80])

print("\n--- ③.6 中文相对日期（「下周」这类不带数字的也必须认）---")
# 原句：「帮我安排下周晚上20-22点的空闲时间学习高等数学和WPS技能考试训练」
# → 排出来是 10-18（今天，周日）和 10-19。原因：原来三条正则只认「数字+号/日」，
#   「下周」里一个数字都没有，整句话对它们完全不可见，起点又退回"今天"。
#   ★ 结论：用户用中文说日期时，那套等于没有。以下每条都是注意点。
_rel = {
    "帮我安排下周晚上20-22点的空闲时间学习高等数学和WPS技能考试训练":
        (_date(2026, 10, 19), 7),
    "下周": (_date(2026, 10, 19), 7),
    "下下周": (_date(2026, 10, 26), 7),
    "下周三": (_date(2026, 10, 21), 1),
    "这周三（已过）要顺延到下周同一星期几": (_date(2026, 10, 21), 1),
    "周三": (_date(2026, 10, 21), 1),
    "明天": (_date(2026, 10, 19), 1),
    "后天": (_date(2026, 10, 20), 1),
    "大后天": (_date(2026, 10, 21), 1),
    "周末": (_date(2026, 10, 24), 2),
    "下周末": (_date(2026, 10, 24), 2),
    "今晚8点学高数": (_date(2026, 10, 18), 1),
    "这周想补一下高数": (_date(2026, 10, 18), 1),   # 本周只剩今天（周日）
}
for _t, _exp in _rel.items():
    _got = parse_date_range(_t, _T)
    check("相对日期「%s」" % _t[:18], _got == _exp, "%s（期望 %s）" % (_got, _exp))
# ★ 反面：时间区间 / 纯目标，都不能被当成日期，否则「晚上18-22点」会排到下个月 18 号。
for _t in ("晚上18-22点学高数", "想复习高数", "帮我排个两周的计划"):
    check("不算日期：「%s」" % _t, parse_date_range(_t, _T) is None,
          str(parse_date_range(_t, _T)))
# ★ 绝对日期优先：两个都写了，以写死的那个日子为准。
check("★「下周三 10月23日」→ 以绝对日期 10-23 为准",
      parse_date_range("下周三 10月23日", _T) == (_date(2026, 10, 23), 1),
      str(parse_date_range("下周三 10月23日", _T)))
# 换个「今天」也要成立（周一）：下周 = 7 天后那个周一
check("今天若是周一，下周 = 7 天后",
      parse_date_range("下周", _date(2026, 10, 19)) == (_date(2026, 10, 26), 7),
      str(parse_date_range("下周", _date(2026, 10, 19))))
_msg2 = build_user_message(_T, 7, "下周晚上20-22点", start=_date(2026, 10, 19))
check("★ 相对日期也要落进用户消息（10-19 到 10-25、共 7 天）",
      "只排 2026-10-19 到 2026-10-25" in _msg2 and "不要从今天" in _msg2, _msg2[:120])

print("\n--- ③.7 说得很远的日期也必须落到**未来** ---")
# 问题记录：「点名日期久了就不行」的另一半：日期解析出来了、但停在过去，
# run_study_agent 会当成"没点名"，起点又退回今天 —— 症状和没解析一模一样。
check("「11月20日」-> 今年 11-20",
      parse_date_range("11月20日", _T) == (_date(2026, 11, 20), 1),
      str(parse_date_range("11月20日", _T)))
check("★「1月5日」-> 顺延到 2027-01-05（只加一个月会停在 2 月，仍在过去）",
      parse_date_range("1月5日", _T) == (_date(2027, 1, 5), 1),
      str(parse_date_range("1月5日", _T)))
check("「12月1日-12月3日」-> 12-01 起 3 天",
      parse_date_range("12月1日-12月3日", _T) == (_date(2026, 12, 1), 3),
      str(parse_date_range("12月1日-12月3日", _T)))

print("\n--- ③.8 时间配方（「先学英语60分钟，再学高数30分钟，休息10分钟」）---")
# 原句：「帮我安排下周晚上18-22点学习，每次学习完中间休息10分钟，
#   先学习大学英语60分钟，再学习高等数学30分钟，剩余时间练习WPS技能」
# 模型排出来 19:10-19:20 休息（19:00-19:10 凭空消失）、高数挪到 19:30、WPS 只到 21:00，
# 而且七天里写法各不相同。→ 时刻表改由后端算，模型只抄。
_recipe_parse = A.parse_time_recipe
_recipe_fmt = A.format_time_recipe
_GOAL = ("帮我安排下周晚上18-22点学习，每次学习完中间休息10分钟，"
         "先学习大学英语60分钟，再学习高等数学30分钟，剩余时间练习WPS技能")
_rc = _recipe_parse(_GOAL)
check("★ 认得出「时间配方」", bool(_rc), str(_rc)[:80])
if _rc:
    _seg = [(b["start"], b["end"], b["what"]) for b in _rc["blocks"]]
    check("★ 时刻表必须精确：18:00 英语60 → 休10 → 19:10 高数30 → 休10 → 19:50 WPS 到 22:00",
          _seg == [("18:00", "19:00", "学习大学英语"),
                   ("19:00", "19:10", "休息"),
                   ("19:10", "19:40", "学习高等数学"),
                   ("19:40", "19:50", "休息"),
                   ("19:50", "22:00", "练习WPS技能（剩余时间）")],
          str(_seg))
    # ★ 连续无缝：上一段 end 必须等于下一段 start，否则就是用户看到的"时间对不上"
    gaps = [(a[1], b[0]) for a, b in zip(_seg, _seg[1:]) if a[1] != b[0]]
    check("★ 段与段之间不能有缝（也不能重叠）", not gaps, str(gaps))
    check("★ 最后一项要排到窗口结束 22:00", _seg[-1][1] == "22:00", _seg[-1][1])
    _txt = _recipe_fmt(_rc, 7)
    check("★ 用户消息里要写明「7 天里每一天都按这张表」",
          "7 天里的**每一天**" in _txt, _txt[:100])
    check("★ 要明说不许写「自由学习」这类占位",
          "自由学习" in _txt and "占位" in _txt, "")
    check("★ 要明说「留半天机动」「拆成 1~2 小时」不适用",
          "不适用" in _txt, "")
# 反面：没给配方时不能误判，否则正常规划会被一张莫名其妙的表绑死
for _t in ("帮我排下周的学习计划", "下周晚上多学点英语", "这周想把高数补上"):
    check("不算配方：「%s」" % _t, _recipe_parse(_t) is None, str(_recipe_parse(_t)))
# 时间窗换了、任务换了，也要跟着变（不能只认死那一条原话）
_rc2 = _recipe_parse("下周 19:00-22:00，先做数学45分钟，休息10分钟，再背单词30分钟，剩下时间看专业课")
check("换一组配方也能算对",
      _rc2 and [(b["start"], b["end"]) for b in _rc2["blocks"]]
      == [("19:00", "19:45"), ("19:45", "19:55"), ("19:55", "20:25"),
          ("20:25", "20:35"), ("20:35", "22:00")],
      "「每次学完都休息」，所以背单词后面也要休 10 分钟（20:25-20:35）才进「剩余时间」；"
      "否则 WPS/专业课会提前 10 分钟起 —— 用户一眼就能看出和上面那条不是同一套算法。"
      + str(_rc2 and [(b["start"], b["end"]) for b in _rc2["blocks"]]))

print("\n--- ④ 工具：空闲时段（算出来的，不是模型猜的）---")
free, summary = run("find_free_slots", {"date": D1, "days": 2})
check("能算出两天", len(free.get("days", [])) == 2, summary)
day1 = free["days"][0]
# 08:10-09:45 与 09:45-11:00 必须**合并成一段** 08:10-11:00，
# 不能出现 "09:45-09:45" 这种零长度空档 —— 那说明合并判据写错了。
check("★ 紧挨着的两条课被合并成一段占用（不产生零长度空档）",
      len(day1["busy"]) == 1, str(day1["busy"]))
starts = [f["start"] for f in day1["free"]]
# ★ 起 policy 改了：小于 MIN_USABLE_MIN(25 分钟) 的空档**不再返回**。
#   原因：08:00-08:10 这种 10 分钟的缝根本不够学什么，但模型会把
#   「预习」「自由学习」往里面塞，计划看起来排满了、实际一条也执行不了。
#   所以这里只应剩 11:00-22:00 那一段。
check("★ 空档边界正确：10 分钟的小缝被丢掉，只剩 11:00-22:00",
      starts == ["11:00"], str(day1["free"]))
check("空档分钟数是真实差值（不是拍脑袋）",
      day1["free"][0]["minutes"] == 660, str([f["minutes"] for f in day1["free"]]))
check("过短的空档被跳过，且如实告诉调用方门槛是多少",
      free.get("min_slot_minutes") == 25, str(free.get("min_slot_minutes")))
# 第 2 天只有一条**活动**（19:20-20:05 = 45 分钟），没有课。
# 空档应当是整天 08:00-22:00（840 分钟）**减去**这 45 分钟 = 795。
# ⚠️ 注意「活动」也要算占用：社团例会同样是抽不出人的时间，
#    只算 type=course 会让计划把人排在开会的时候。
check("★ 活动也会占用时间（不只是 course）：840 - 45 = 795 分钟",
      free["days"][1]["free_total_minutes"] == 795 and len(free["days"][1]["busy"]) == 1,
      f"{free['days'][1]['free_total_minutes']} 分钟 / busy={free['days'][1]['busy']}")

print("\n--- ⑤ ★ 防幻觉兜底：越界日期必须被夹回计划窗口 ---")
# 模型自己编日期时（编过 2023 年），如果不夹回去，它会拿另一个年份的空档
# 去排"未来三天"的计划 —— 输出看着完全正常，错得悄无声息。
out_of_range, _ = run("find_free_slots", {"date": "2023-04-15", "days": 3})
check("★ 早于窗口的日期被夹回窗口起点，并在结果里说明",
      out_of_range["days"][0]["date"] == D1 and "note" in out_of_range,
      str(out_of_range["days"][0]["date"]) + " / " + str(out_of_range.get("note", ""))[:40])
late, _ = run("find_free_slots", {"date": "2099-01-01", "days": 1})
check("★ 晚于窗口的日期同样被夹回", late["days"][0]["date"] == D1, late["days"][0]["date"])

tt_all, _ = run("get_timetable", {})
check("课表工具默认返回全部（3 条）", tt_all["count"] == 3, str(tt_all["count"]))
tt_one, _ = run("get_timetable", {"date_from": D2, "date_to": D2})
check("按日期过滤生效（只剩第 2 天那条）", tt_one["count"] == 1, str(tt_one["count"]))
tt_bad, _ = run("get_timetable", {"date_from": "2023-04-15", "date_to": "2023-04-17"})
check("★ 与计划窗口毫无交集的区间会被改按窗口返回，并附说明",
      tt_bad["count"] > 0 and "note" in tt_bad,
      f"count={tt_bad['count']} note={str(tt_bad.get('note'))[:50]}")


print("\n--- ⑥ 工具：地点识别（注意 uncertain 不能被当成教学楼）---")
cases = [
    ("知行楼A203", "知行楼", "北区", False),
    ("博文楼-301", "博文楼", "南区", False),
    ("线上", None, None, True),
    ("田径场", None, None, True),
    ("东区宿舍", None, None, False),      # 非教学楼，但也**不是**虚拟地点
]
for text, want_b, want_r, want_v in cases:
    res, s = run("get_building_info", {"text": text})
    ok = (res.get("building") == want_b and res.get("virtual") is want_v
          and (res.get("region_label") == want_r if want_r else res.get("region") is None))
    check(f"「{text}」-> building={want_b} region={want_r} virtual={want_v}", ok, s)

check("★ 对不上任何楼的文本不会被当成教学楼（_norm_building 的兜底会原样返回 + uncertain）",
      run("get_building_info", {"text": "东区宿舍"})[0].get("building") is None)

db.close()

print("\n" + "=" * 60)
print(f"  结论: {_passed} 通过 / {_failed} 失败")
print("=" * 60 + "\n")
sys.exit(1 if _failed else 0)
