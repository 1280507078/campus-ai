"""学业规划 **AI Agent**（function calling / 工具调用）。

## 这个模块解决什么问题

原来的「AI伴学」是**一次性提问**：把课表和目标拼成一段提示词丢给大模型，
拿到一段文字就结束。它有两个绕不开的毛病：

  1. **模型看不到它需要的事实。** 「你周三下午几点没课？」「这周还有几天？」
     这类问题要靠提示词里塞的那点上下文硬猜，课表稍微复杂一点就开始编。
  2. **算不准。** 「未来 7 天」到底是哪 7 天、第几周、每天空档多长，
     让语言模型自己对日期做算术，出错是常态。

现在的做法是**给它工具，让它自己去查**（这就是 Agent 与「一次问答」的区别）：

    模型先想「我需要什么」→ 回一个 `tool_calls` 请求 → 我们真的去数据库/接口取
    → 把结果喂回去 → 模型再决定是继续查还是给答案。

于是「7 天的空档」是**算出来的**（`find_free_slots` 走的是和课表页同一套节次数据），
不是模型拍脑袋写出来的。

## 工具都是真的，不是摆设

五个工具全部接在项目已有的真实数据/服务上，没有一个是为了"看起来像 Agent"凑数的：

  | 工具                 | 数据来源                                   |
  |----------------------|--------------------------------------------|
  | `get_timetable`      | 用户课表（与课表页所见即所得的那一份）      |
  | `get_class_schedule` | `time_slots` 表（阳光学院真实节次 + 南北区作息）|
  | `find_free_slots`    | 上面两者相减，算出某天的空闲时段            |
  | `get_building_info`  | `routers/planner.py` 的楼名表（判断南区/北区）|
  | `get_weather`        | 高德天气接口（`services/amap.py`）          |

## 为什么循环必须有上限

模型有可能会**反复请求同一个工具**（尤其是工具返回空结果时）。没有上限就是
"跑到天荒地老 + 烧额度"。所以 `MAX_ROUNDS` 之后强制用 `tool_choice="none"`
要一次不带工具的最终答复 —— 宁可给一份基于已有信息的计划，也不能无限转下去。

## 降级

未配置 GLM Key、网络不通、模型返回异常 —— 一律返回 **("", [])**，
由调用方（`routers/planner.py`）落到本地规则计划。**页面不能因为 AI 挂了就白屏。**
"""
import json
import logging
import re
import time
from datetime import date as _date, datetime, timedelta

from services.glm import glm_chat, glm_chat_tools

logger = logging.getLogger(__name__)

# 校本部所在城市 adcode（福州）—— 与 routers/map_weather.py 的默认值保持一致
CITY_ADCODE = "350100"

# 工具调用最多几轮（含最后一次强制作答前）
MAX_ROUNDS = 4
# 单轮工具调用的读取超时：工具轮不需要长文输出，给 60 秒足够
TOOL_TIMEOUT = 60.0
# 最终作答的读取超时（与 glm_chat 的默认口径一致，长计划要等得起）
FINAL_TIMEOUT = 180.0

# ★★★ 整轮 Agent 的**总时间预算**（秒）。
#
# 为什么必须有这个数：线上的页面走的是平台网关，**请求超过约 60 秒会被网关
# 直接掐断，返回 504**（实测：本机一条「排 7 天」的请求要 63 秒，而线上同一个
# 请求直接 504 Gateway Time-out，前端只能显示「操作失败」）。
# 也就是说：只要一次规划跑过 60 秒，用户拿到的就不是计划，而是一个错误 ——
# 而且**完全看不出是"太慢"**，只看到"生成失败"。
#
# 所以这里主动封顶：预算之内尽量用工具把事实查准，一旦超时就**立刻降级**成
# 本地规则计划（`routers/planner.py::_local_plan`，毫秒级、不联网），
# 宁可给出朴素但可用的计划，也不让用户等到网关把他掐断。
# 留 20 秒余量给网关回包、写库和慢网络。
BUDGET_S = 50.0

# 一个空档至少多长才值得排进计划（分钟）。
# 课间只有 5 分钟，把它当"空闲"返回，模型就会往缝里塞「预习」「自由学习」。
MIN_USABLE_MIN = 25


# ===========================================================================
#  工具定义（OpenAI 兼容的 function calling schema）
# ===========================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_timetable",
            "description": (
                "查询这名学生的时间表（含课程与活动）。"
                "返回每条安排的日期、起止时间、课程名、教师、地点。"
                "时间表为空时会返回空列表 —— 这是正常情况（还没导入时间表），"
                "不要因为空列表就拒绝生成计划。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string",
                                  "description": "起始日期 YYYY-MM-DD（含）。不传表示不限。"},
                    "date_to": {"type": "string",
                                "description": "结束日期 YYYY-MM-DD（含）。不传表示不限。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_class_schedule",
            "description": (
                "查询学校的节次作息表（第几节几点到几点），含南北区差异。"
                "当你要把计划写成「第一节/第三节」这种表述，或者需要把节次换算成"
                "具体时间点时，先调这个，不要凭印象写时间。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_free_slots",
            "description": (
                "算出某一天（或多天）的空闲时段：把当天的时间表占用从 08:00-22:00 里扣掉，"
                "返回可以自由安排的连续时间段及各自时长（分钟）。"
                "排学习计划前**务必**先调这个，这样「几点到几点学什么」才是真的可行，"
                "而不是排在上课时间里。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string",
                             "description": "要查的日期 YYYY-MM-DD。不传表示从今天开始。"},
                    "days": {"type": "integer",
                             "description": "要连续查几天，默认 1。最多 30。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_building_info",
            "description": (
                "把时间表里的地点文字规范化成正式教学楼名，并告诉你是**南区还是北区**"
                "（两区作息相差最多 10 分钟）。"
                "例：输入「知行楼A203」返回「知行楼 / 北区」；"
                "输入「线上」返回「非实体地点，不能导航」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "时间表里的地点原文，如「博文楼-301」「知行楼B栋」。"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询学校所在地（福州马尾）的当前天气：天气现象、气温、风向风力、湿度。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_campus_docs",
            "description": (
                "检索**学校公开文档**（教务处办事指南 13 项 + 2026-2027 学年校历）。"
                "凡是问到「学校怎么规定 / 要走什么流程 / 去哪办 / 要带什么材料 / "
                "学期什么时候开始、什么时候放假、第几周是几号」，都**必须先查这里**，"
                "不要凭印象回答——你对这所学校的具体规定一无所知。\n"
                "覆盖范围：休学、复学、退学、转专业、辅修、重修、缓考、查卷、"
                "学分认定与置换、毕业与学位证明、出国在读证明、多媒体教室借用、"
                "新教务系统功能，以及校历（学期起止、注册与上课日、节假日、20 周周次对照）。\n"
                "**查不到就如实说「学校手册里没写」并建议问教务处（0591-83969194），"
                "绝对不要编流程、编日期、编材料清单。**"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "检索词，用学生原话即可，如「休学需要什么材料」「什么时候放寒假」。"},
                },
                "required": ["query"],
            },
        },
    },
]


# ===========================================================================
#  工具实现
# ===========================================================================

def _minutes(hhmm: str) -> int:
    """ "08:10" -> 490；解析不了返回 -1。"""
    try:
        h, m = str(hhmm).strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def _today() -> str:
    return _date.today().isoformat()


def _tool_get_timetable(ctx: dict, args: dict) -> tuple[dict, str]:
    """查课表。

    ⚠️ 数据源是 `ctx["courses"]` —— 也就是**这个请求实际生效的那份课表**
    （前端传上来的优先于数据库，见 routers/planner.py 的说明）。
    为什么不直接查库：用户刚导入、还没点保存时库里是空的，
    直接查库会让 Agent 回答「你还没有任何课程」，而页面上明明写着课表 ——
    这个问题在改造前就踩过一次，别再踩。
    """
    rows = list(ctx.get("courses") or [])
    win_from, win_to = ctx.get("window") or ("", "")
    d_from = (args.get("date_from") or "").strip()
    d_to = (args.get("date_to") or "").strip()
    note = ""

    # ★ 防幻觉兜底：模型有可能自己编一个和计划区间完全不搭的日期（实测编过 2023 年）。
    #   如果它给的区间和本次计划窗口**毫无交集**，就不要拿它去过滤 —— 那只会返回
    #   0 条课程，然后模型会一本正经地说"你这几天没有课"。改成退回计划窗口，并如实说明。
    if (d_from or d_to) and win_from and win_to:
        lo, hi = d_from or "0000-00-00", d_to or "9999-99-99"
        if hi < win_from or lo > win_to:
            note = (f"（你给的日期区间 {d_from or '不限'}~{d_to or '不限'} 与本次计划区间 "
                    f"{win_from}~{win_to} 没有交集，已改按计划区间返回）")
            d_from, d_to = win_from, win_to

    # ★ 日期过滤也要按「这门课在这段时间里会不会上」判断，不能比 date 字符串。
    #   一条 date=9/8 的周二课，查 10/19~10/25 时 date 比不出交集，
    #   以前会返回 0 条 → 模型据此说「你这周没课」。
    #   现在给每条课补一个 dates_in_window（窗口内实际要上的日期），既过滤也告诉模型哪天要上。
    if d_from or d_to:
        try:
            lo = datetime.fromisoformat(d_from or "2000-01-01").date()
            hi = datetime.fromisoformat(d_to or "2099-12-31").date()
        except ValueError:
            lo = hi = None
        if lo and hi:
            hit = []
            for c in rows:
                ds = _course_dates_in(c, lo, hi)
                if ds:
                    c = dict(c)
                    c["dates_in_window"] = ds
                    hit.append(c)
            rows = hit
    rows.sort(key=lambda c: (c.get("date") or "", c.get("start") or ""))

    out = {"count": len(rows), "courses": rows}
    if note:
        out["note"] = note
    return out, f"读到 {len(rows)} 条课程/活动"


def _tool_get_class_schedule(ctx: dict, args: dict) -> tuple[dict, str]:
    """查节次作息表（time_slots）。"""
    from models.orm import TimeSlot

    db = ctx["db"]
    rows = db.query(TimeSlot).order_by(TimeSlot.order_no).all()
    out = []
    for s in rows:
        base_start = s.start_time.strftime("%H:%M") if s.start_time else ""
        base_end = s.end_time.strftime("%H:%M") if s.end_time else ""
        item = {
            "order_no": s.order_no,
            "label": s.label,
            "period_type": s.period_type,
            "start": base_start,
            "end": base_end,
        }
        # 只有「南北区作息不同」的节次才带分区时间，避免模型以为所有节次都分两套
        if (s.region or "") == "split" or s.start_north or s.end_north:
            item["start_south"] = s.start_south or base_start
            item["end_south"] = s.end_south or base_end
            item["start_north"] = s.start_north or base_start
            item["end_north"] = s.end_north or base_end
        out.append(item)
    return {"count": len(out), "slots": out}, f"读到 {len(out)} 个节次"


# ===========================================================================
#  「某天到底上不上这门课」—— 与前端 common.js 的 courseOnDate 同一口径
# ===========================================================================
#  ⚠️ 这是本项目最容易出错的一处，务必先看懂数据模型：
#
#    库里一条课存的是**某一周的某一天**（date="2026-09-08"），
#    而 weeks（"2-3,6-8周(全部)"）说明「这门课在哪几周上」。
#    所以「9 月 8 日那条课」在第 7 周的周二**照样要上** —— 它是「周二第 1 节」，
#    不是「只有 9 月 8 日这一节」。
#
#    以前这里（以及前端提醒页、首页今日卡）都写 `c["date"] == 某天`，后果：
#      · 排计划时认为「除录课那一周外全天空闲」→ 把上课时段当自习时段，
#        实测输出过「14:50-15:35 预习计算机组成原理」（那正是计组的上课时段）
#      · 提醒页 / 首页卡把别周的记录抬到今天 → 「今天 6 节、明天 0 节」
#
#    判断条件两条：**星期相同** + **该日期所在周次在 weeks 里**。
#    前端同一份逻辑见 frontend/js/common.js 的 courseOnDate()。
#    两边**必须同时改**，否则又会出现「课表页」和「AI 排的计划」互相打架。
SEMESTER_START = _date(2026, 9, 7)      # 开学第 1 周的周一（与前端 SEMESTER_START 一致）
SEM_TOTAL_WEEKS = 20                    # 第 1~20 周


def _parse_weeks(raw: str):
    """周次原始串 -> 周次集合；返回 None 表示「每周都上」（空串 / 认不出）。

    ⚠️ 兜底原则与前端一致：**宁可不隐藏，也不要误隐藏**。
       认不出任何数字时返回 None（当每周都上），绝不返回空集合把课藏起来。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    cleaned = re.sub(r"[（(][^)）]*[)）]", " ", s)
    cleaned = re.sub(r"[，、;；]", ",", cleaned)
    cleaned = re.sub(r"[－—–~～至]", "-", cleaned)
    weeks: set[int] = set()
    for a, b in re.findall(r"(\d+)\s*(?:-\s*(\d+))?", cleaned):
        lo = int(a)
        hi = int(b) if b else lo
        if lo > hi:
            lo, hi = hi, lo
        for w in range(max(1, lo), min(SEM_TOTAL_WEEKS, hi) + 1):
            weeks.add(w)
    return weeks or None


def _week_number(d: _date):
    """某天是学期第几周（1 起算）；学期外返回 None。"""
    w = (d - SEMESTER_START).days // 7 + 1
    return w if 1 <= w <= SEM_TOTAL_WEEKS else None


def _course_on_date(c: dict, d: _date) -> bool:
    """这条课在 d 这一天要不要上（星期相同 + 该周次在 weeks 里）。"""
    raw = str(c.get("date") or "")[:10]
    try:
        anchor = _date.fromisoformat(raw)
    except ValueError:
        return False
    if anchor.weekday() != d.weekday():      # 0=周一：星期必须一致
        return False
    wk = _week_number(d)
    if wk is None:
        return True                          # 学期外算不出周次：保守当成要上
    ws = _parse_weeks(c.get("weeks"))
    return ws is None or wk in ws


def _course_dates_in(c: dict, lo: _date, hi: _date) -> list[str]:
    """这条课在 [lo, hi] 里实际要上的日期（最多扫 31 天，防误传大区间）。"""
    out, d = [], lo
    while d <= hi and len(out) < 31:
        if _course_on_date(c, d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _tool_find_free_slots(ctx: dict, args: dict) -> tuple[dict, str]:
    """算空闲时段：08:00-22:00 减去当天的课表占用。"""
    days = int(args.get("days") or 1)
    days = max(1, min(30, days))
    start_day = (args.get("date") or "").strip()
    try:
        base = datetime.fromisoformat(start_day).date() if start_day else _date.today()
    except Exception:
        base = _date.today()

    # ★ 防幻觉兜底：把起始日**夹回**本次计划的窗口。
    #   模型自己编日期时（实测编过 2023-04-15），若不夹，它算出的空档属于另一个年份，
    #   而它拿这份空档去排"未来三天"的计划，输出看起来完全正常、错得悄无声息。
    win_from, win_to = ctx.get("window") or ("", "")
    clamp_note = ""
    if win_from and win_to:
        if base.isoformat() < win_from:
            clamp_note = f"（你请求的 {base.isoformat()} 早于本次计划起点，已按 {win_from} 起算）"
            base = datetime.fromisoformat(win_from).date()
        elif base.isoformat() > win_to:
            clamp_note = f"（你请求的 {base.isoformat()} 晚于本次计划终点，已按 {win_from} 起算）"
            base = datetime.fromisoformat(win_from).date()

    courses = ctx.get("courses") or []
    day_start, day_end = 8 * 60, 22 * 60        # 08:00 - 22:00

    result = []
    for i in range(days):
        d = (base + timedelta(days=i)).isoformat()
        day = base + timedelta(days=i)
        busy = []
        for c in courses:
            # ★ 用「星期 + 周次」判断，不是 `c["date"] == d`。
            #   一条 date=周二、weeks="2-3,6-8周" 的课，这几周的**每个周二**都占时间；
            #   按 date 相等判断会认为「只有录课那一周有课，之后全天空闲」。
            if not _course_on_date(c, day):
                continue
            s, e = _minutes(c.get("start")), _minutes(c.get("end"))
            if s < 0 or e <= s:
                continue
            busy.append((s, e, c.get("name") or "", c.get("location") or ""))
        busy.sort()

        # 合并重叠/相接的占用段（同一天两条课紧挨着是常态，不该被当成两个空档）
        merged: list[list] = []
        for s, e, name, loc in busy:
            if merged and s <= merged[-1][1]:
                if e > merged[-1][1]:
                    merged[-1][1] = e
                    merged[-1][2].append(name or loc)
            else:
                merged.append([s, e, [name or loc]])

        free, cursor = [], day_start
        for s, e, names in merged:
            if s > cursor:
                free.append({"start": f"{cursor // 60:02d}:{cursor % 60:02d}",
                             "end": f"{s // 60:02d}:{s % 60:02d}", "minutes": s - cursor})
            cursor = max(cursor, e)
        if cursor < day_end:
            free.append({"start": f"{cursor // 60:02d}:{cursor % 60:02d}",
                         "end": f"{day_end // 60:02d}:{day_end % 60:02d}",
                         "minutes": day_end - cursor})

        # ★ 太短的缝隙不往上报（2026-10-18）。
        #   课间只有 5 分钟，把它们当"空档"返回，模型就会认真地在里面塞东西 ——
        #   实测输出过「08:55-09:00 预习计算机网络」「14:45-14:50 自由学习时间」，
        #   连排的课就被切成一段段垃圾。小于 MIN_USABLE_MIN 的缝隙一律丢掉：
        #   它排不了任何事，报给模型只会被滥用。
        free = [f for f in free if f["minutes"] >= MIN_USABLE_MIN]

        result.append({
            "date": d,
            "busy": [{"start": f"{s // 60:02d}:{s % 60:02d}", "end": f"{e // 60:02d}:{e % 60:02d}",
                      "what": "、".join(n for n in names if n)} for s, e, names in merged],
            "free": free,
            "free_total_minutes": sum(f["minutes"] for f in free),
        })

    total_free = sum(r["free_total_minutes"] for r in result)
    out = {"days": result, "min_slot_minutes": MIN_USABLE_MIN}
    if clamp_note:
        out["note"] = clamp_note
    return out, f"算出 {days} 天共 {total_hours(total_free)} 小时空档"


def total_hours(minutes: int) -> str:
    """分钟 -> "12.5" 这种小时字符串（只用于给用户看的摘要）。"""
    return f"{minutes / 60:.1f}"


def _tool_get_building_info(ctx: dict, args: dict) -> tuple[dict, str]:
    """地点文字 -> 正式楼名 + 所属校区。

    ⚠️ 这里**延迟导入** `routers.planner`：planner 反过来要 import 本模块的
    `run_study_agent`，模块级导入会成环。函数内导入时两个模块都已加载完，没问题。
    楼名表就一份，别再抄第二份 —— 抄了就会出现"课表页认得出、Agent 认不出"的分裂。
    """
    from routers import planner as planner_mod

    text = str(args.get("text") or "").strip()
    if not text:
        return {"error": "text 不能为空"}, "地点为空"

    # ⚠️ `_is_virtual_place` 拦的不只是「线上课」，还有**校园场馆**
    #    （田径场/操场/体育馆/游泳馆…，见 planner.py 里它内部的 VENUE 元组）。
    #    所以这里的措辞要同时覆盖两类，不能只说"线上地点" —— 那是假信息，
    #    而 Agent 会拿这句话去推理。这里**不去抄那份 VENUE 清单**（抄了就会不同步），
    #    只如实说明"不是教学楼，且有两类可能"。
    if planner_mod._is_virtual_place(text):
        return ({"input": text, "building": None, "region": None, "virtual": True,
                 "is_teaching_building": False,
                 "note": ("不是教学楼。可能是线上/虚拟教室（如「线上」「腾讯会议」），"
                          "也可能是田径场、体育馆这类校园场馆 —— 场馆在校园地图上"
                          "有独立点位，但不属于教学楼，也不按南北区算作息")},
                f"「{text}」不是教学楼（线上课或场馆）")

    # ⚠️ `_norm_building(raw, location)` 是**两个参数**、返回三个值
    #    `(building, location, uncertain)` —— 它本来用于「楼名 + 教室号」两列输入。
    #    这里只有一个字符串，所以第二参传空串，把整串都当候选去匹配。
    #    （第一次写成了单参数调用，直接 TypeError，被 `_run_tool` 兜住变成了
    #      "工具执行失败" —— 结论：跨模块调私有函数前先看签名。）
    b, _loc, uncertain = planner_mod._norm_building(text, "")
    # ⚠️ **`uncertain` 一定要看**：`_norm_building` 的最后一档兜底是
    #    「对不上任何楼 -> 把原文原样返回，同时 uncertain=True」（那是给前端标黄用的）。
    #    只看 b 非空就当成教学楼的话，「东区宿舍」会被报成"教学楼=东区宿舍"，
    #    再被下面按 NORTH_BUILDINGS 判成"南区" —— 一条彻头彻尾的假信息。
    if not b or uncertain:
        return ({"input": text, "building": None, "region": None, "virtual": False,
                 "note": "不在校园教学楼表里（可能是宿舍/食堂这类生活设施，或学校新楼）"},
                f"「{text}」不是已收录的教学楼")

    region = "north" if b in planner_mod.NORTH_BUILDINGS else "south"
    return ({"input": text, "building": b, "region": region,
             "region_label": "北区" if region == "north" else "南区",
             "virtual": False},
            f"「{text}」= {b}（{'北区' if region == 'north' else '南区'}）")


async def _tool_get_weather(ctx: dict, args: dict) -> tuple[dict, str]:
    """查天气（高德）。失败不抛异常，返回 error 让模型自己决定要不要提天气。"""
    from services.amap import amap_weather, is_configured as amap_ok

    if not amap_ok():
        return ({"error": "未配置高德 Key，天气不可用"}, "天气不可用")
    try:
        data = await amap_weather(CITY_ADCODE)
    except Exception as e:                      # noqa: BLE001 —— 工具不该把异常抛给模型
        logger.warning("Agent 查天气失败：%s: %s", type(e).__name__, e)
        return ({"error": "天气接口暂时不可用"}, "天气查询失败")
    if not data:
        return ({"error": "天气接口没有返回数据"}, "天气查询失败")

    # ⚠️ `amap_weather()` 返回的**就是实况字典本身**（不是 {"live": {...}} 那种嵌套），
    #    只是额外多挂了一个 "forecast" 键；而且实况取不到时它直接返回 {}。
    #    按 `data["live"]` 取会拿到空 dict、摘要变成"未知 ℃"（第一版就是这么错的）。
    live = data
    brief = f"{live.get('weather', '未知')} {live.get('temperature', '')}℃".strip()
    return ({
        "city": live.get("city"),
        "weather": live.get("weather"),
        "temperature": live.get("temperature"),
        "winddirection": live.get("winddirection"),
        "windpower": live.get("windpower"),
        "humidity": live.get("humidity"),
        "reporttime": live.get("reporttime"),
        "forecast": live.get("forecast") or [],
    }, f"天气：{brief}")


async def _tool_search_campus_docs(ctx: dict, args: dict) -> tuple[dict, str]:
    """查学校公开文档（教务处办事指南 + 校历）。

    这一条是「不许编」的落地实现：
      · 查到 → 原样返回带 `source` 的片段，模型照着说，答案能标出处；
      · 查不到 → 返回 `count: 0` 并附带一条 note，明确要求模型承认没写，
        而不是「检索为空就自由发挥」。RAG 最典型的出错方式就是后者。

    与 `get_weather` 一样，任何异常都收敛成 `{"error": ...}`，
    工具不该把异常抛给模型（模型看到 traceback 只会更胡说）。
    """
    from services import qdrant as qd

    query = (args.get("query") or args.get("q") or "").strip()
    if not query:
        return ({"error": "缺少 query 参数"}, "未提供检索词")

    stats = qd.docs_stats()
    if not stats.get("configured"):
        return ({"error": "未配置 QDRANT_URL / QDRANT_API_KEY，校园文档库不可用"},
                "文档库未配置")
    if not stats.get("points"):
        return ({"error": "校园文档库还没有导入语料（见 services/rag_docs.py）",
                 "count": 0, "hits": []},
                "文档库为空")

    try:
        hits = await qd.search_docs(query, top_k=4)
    except Exception as e:                      # noqa: BLE001
        logger.warning("检索校园文档失败：%s: %s", type(e).__name__, e)
        return ({"error": "文档检索暂时不可用"}, "文档检索失败")

    if not hits:
        # ★ 这段话是给模型看的「行为约束」，不是给用户看的文案。
        #   少写这一句，模型会自己补一段听起来很合理的流程 —— 实测过。
        return ({
            "count": 0,
            "hits": [],
            "note": ("学校文档里没有检索到相关内容。回答时必须明确告诉学生"
                     "「学校手册里没有写这一条」，并建议咨询教务处（0591-83969194）或辅导员；"
                     "绝对不要凭常识编造流程、材料或日期。"),
        }, "文档里没有相关内容")

    out = []
    for h in hits:
        out.append({
            "text": h["text"][:600],
            "source": h["source"],
            "section": h["section"],
            "score": round(h["score"], 3),
        })
    sources = "、".join(sorted({h["source"] for h in out}))
    return ({"count": len(out), "hits": out, "sources": sources},
            f"命中 {len(out)} 段（{sources}）")


# 工具名 -> 实现。分同步/异步两本账，主循环里按签名 await。
_SYNC_TOOLS = {
    "get_timetable": _tool_get_timetable,
    "get_class_schedule": _tool_get_class_schedule,
    "find_free_slots": _tool_find_free_slots,
    "get_building_info": _tool_get_building_info,
}
_ASYNC_TOOLS = {
    "get_weather": _tool_get_weather,
    "search_campus_docs": _tool_search_campus_docs,
}


async def _run_tool(name: str, args: dict, ctx: dict) -> tuple[dict, str]:
    """执行一个工具，返回 (结果字典, 给人看的摘要)。未知工具返回错误结果，不抛异常。"""
    try:
        if name in _SYNC_TOOLS:
            return _SYNC_TOOLS[name](ctx, args)
        if name in _ASYNC_TOOLS:
            return await _ASYNC_TOOLS[name](ctx, args)
        return {"error": f"没有名为 {name} 的工具"}, f"未知工具 {name}"
    except Exception as e:                      # noqa: BLE001
        logger.warning("工具 %s 执行失败：%s: %s", name, type(e).__name__, e)
        return {"error": f"工具执行失败：{type(e).__name__}"}, f"{name} 执行失败"


# ===========================================================================
#  Agent 主循环
# ===========================================================================

COACH_PROMPT = (
    "你是「校园 AI 伴学」里的**学习教练**。学生这句话是在问方法、问原理、或者卡住了，"
    "**不是**让你排时间表。\n\n"
    "回答按这个结构，总长 350 字以内：\n"
    "① 一句话点出他真正卡在哪（不要复述他的话）。\n"
    "② 给 2–3 条具体做法，每条都要能落地：做什么、做多久、做到什么算完成。\n"
    "③ 给一个**具体例子**，就用他问的科目/知识点，给一步能照着做的。\n"
    "④ 最后一句：今天 10 分钟内能动手的第一步。\n\n"
    "硬规则：\n"
    "- **不要输出时间表**，不要写「第 1 天」「08:00-10:00」这种格式 —— 他要排表会说「帮我排」。\n"
    "- 知识点拿不准就直说「这块我不确定，建议查教材第 X 章 / 问老师」，**不要编**。\n"
    "- 不许说没信息量的空话：多练习 / 制定计划 / 保持专注 / 劳逸结合。\n"
    "- 不用客服腔（同学你好 / 希望对你有所帮助）。\n"
    "- 用中文，手机上一屏能读完。\n"
)


async def ask_coach(goal: str, who_line: str = "", context: str = "") -> str:
    """走「学习教练」人格回答**方法与讲解**类问题。

    ★★★ 2026-10-20：为什么要单独一条路 —— 原来所有问题都进规划 Agent，
      问句：「高数怎么复习」收到一张时间表。模型不笨，是系统提示词只教了它
      「先查工具、再排分天计划」这一件事。这里给它另一套身份：
      不调工具、不排表、只讲方法 + 一个例子 + 今天的第一步。
    """
    head = (who_line or "").strip()
    ctx = (context or "").strip()
    user = "\n".join(x for x in (head, ctx, goal) if x)
    msgs = [{"role": "system", "content": COACH_PROMPT},
            {"role": "user", "content": user}]
    try:
        return (await glm_chat(msgs, temperature=0.6, max_tokens=900)).strip()
    except Exception as e:                      # noqa: BLE001
        logger.warning("学习教练调用失败：%s: %s", type(e).__name__, e)
        return ""


_COACH_TIPS = (
    (r"单词|背词|英语词|四级词|六级词",
     "背单词卡住，多半是只背不认：合上书就想不起意思，说明记的是字形不是用法。\n"
     "· 三遍法：第一遍看词 + 例句，第二遍遮住中文回想，第三遍默写词性；每题不超过 8 秒，想不起来直接看答案。\n"
     "· 例子：abandon v. 放弃 —— 例句 He abandoned the plan. 遮住中文问自己：词性是什么、什么意思。\n"
     "今天第一步：挑 10 个你最讨厌的词，按上面做一遍，8 分钟。"),
    (r"数学|高数|微积分|线代|线性代数|概率|公式|定理|洛必达",
     "数学学不进去，通常是看懂了但没自己推过：课本的推导你只是读了一遍。\n"
     "· 合书重推：看完一个定理，合上书拿白纸把推导写一遍，卡住的地方就是真没懂的点。\n"
     "· 例子：洛必达法则 —— 合上书问自己「它凭什么成立（柯西中值定理）」「为什么要求 0/0 型」，答不上就回去看那一节。\n"
     "今天第一步：只挑一个定理，合书重推一次，10 分钟。"),
    (r"记不住|背不下来|背了就忘|忘得快",
     "记不住不是脑子问题，是复习时机问题：你在快忘光之前没复习。\n"
     "· 间隔重复：学完当天回顾 1 次、第 2 天 1 次、第 4 天 1 次、第 7 天 1 次，每次 5 分钟。\n"
     "· 例子：今天学的公式，今晚睡前默写一遍，明晚再默一遍，比连学 3 小时管用。\n"
     "今天第一步：把今天学的内容写 3 个关键词进备忘录，睡前回想。"),
    (r"拖延|不想学|静不下|专注|效率|学一会|手机",
     "拖延通常不是懒，是任务太模糊：复习高数没有起点，大脑就绕开它。\n"
     "· 把任务切到能立刻开始的粒度：不是复习高数，而是做第 3 章课后题 5 道。\n"
     "· 例子：坐下先说「我只做 10 分钟」，手机放另一个房间 —— 门槛低到不费劲，往往就做下去了。\n"
     "今天第一步：现在写下你要做的那个任务，缩到 10 分钟内能完成的版本。"),
)


def local_coach(goal: str) -> str:
    """AI 不可用时的兜底讲解：按话题给三条可执行建议（不排表）。"""
    g = goal or ""
    for pat, txt in _COACH_TIPS:
        if re.search(pat, g):
            return txt
    return ("想把这门课学明白，先别急着加时间，先换方法：\n"
            "· 合书复述：学完一段合上书，用自己的话讲一遍，讲不顺的地方就是没懂的点。\n"
            "· 做题优先：每学一个知识点立刻做 2 道题，比反复看笔记有效。\n"
            "· 例子：把今天课上最重要的一条结论，用一句话说清它解决什么问题。\n"
            "今天第一步：挑今天课上一个点，合书讲给自己听 3 分钟。")


# ★★★ 2026-10-20：求方法 / 求讲解的判据（本树没有 intent_of，单独给一份）
_EXPLAIN_PAT = re.compile(
    r"怎么(?:学|复习|背|记|理解|读|练|提高|提升|才能|入门)|"
    r"如何(?:学|复习|背|记|理解|提高|提升|入门)|"
    r"记不住|背不下来|背了就忘|学不进去|看不进去|看不懂|听不懂|"
    # ★ 2026-10-21：评测暴露 ——「总是拖延，静不下心」这类**状态诊断**原来不认，
    #   整句落到规划 Agent，回了一张作息表。输入里说的明明是"我卡住了"。
    r"拖延|静不下|静不心|集中不了|专注不下来|坐不住|提不起|不想学|学不动|效率低|"
    # ★ 2026-10-22 扩口径：「学不下去」「三分钟热度」「坚持不下来」是最常见的
    #   三种说法，原来一个不认，全落 plan 去排计划 —— 他要的是方法，不是作息表。
    r"学不下去|学不进去|坚持不下来|坚持不住|三分钟热度|没动力|学废了|摆烂|"
    # ★ 2026-10-22：「上课总是走神」「复习效率特别低」原来都不认（效率低中间夹了「特别」）。
    r"走神|分心|注意力不集中|效率.{0,4}低|"
    r"起不来|睡过头|赖床|作息乱|熬夜|失眠|"
    # ★ 2026-10-21：留出集 H18「这门课老师讲得太快了怎么办」判不出来 ——
    #   这是**课堂跟不上的抱怨**，该讲方法（怎么补、怎么预习），不该排计划。
    r"跟不上|讲得太快|太快了|节奏太快|讲不清楚|讲不明白|听不进去|"
    r"不会做|不会写|不会算|不会解|做不对|老是错|总是错|我不会|不会啊|不会呀|"
    r"讲讲|讲一下|讲解|教我|解释一下|说一下原理|"
    r"什么是|是什么意思|什么原理|有什么区别|为什么会|为什么我|"
    r"有什么(?:方法|技巧|诀窍|思路)|窍门|学习方法|复习方法")
# 只挑"动作"：要排 / 要写 / 要列。不含 复习、备考、提高 这些话题词。
_PLAN_ACTION_VERBS = ("排", "安排", "计划", "规划", "制定", "方案", "日程", "时间表",
                      "列个", "写一份", "拆一下", "拆成", "拆分", "理一下", "梳理")


def _is_explain(s: str) -> bool:
    """这句话是在求方法 / 求讲解 / 求诊断，而不是要一份时间表。"""
    s = s or ""
    if not _EXPLAIN_PAT.search(s):
        return False
    if any(h in s for h in _PLAN_ACTION_VERBS):
        return False
    return True


# ===========================================================================
#  第三条路：学校文档问答（RAG）
# ===========================================================================
# ★★★ 2026-10-21：为什么需要单独一条路 ——
#   本树原先只有两条：`_is_explain` → 学习教练，其余全归规划 Agent。
#   于是「休学需要准备什么材料」被当成排计划，端到端实测返回的是
#   「08:00-10:00 学习专业课内容」这种时间表 —— 答得一本正经，但完全答非所问。
#   规划 Agent 的 system 提示词整篇都在教它「怎么排表」，它当然只会排表；
#   指望它在排表人格里主动去查手册，是把两件事混在一句提示词里，做不到的。

# 判据分三层，比原来那条"一个大正则"更好维护，也更好解释：
#   ① `_DOCS_STRONG`   —— 出现就一定是文档问题（这些词在排计划里不会出现）
#   ② `_DOCS_QUESTION` —— 疑问形态（怎么办 / 几点 / 多久 / 在哪 …）
#   ③ `_DOCS_OBJECT`   —— 学校事务对象（校园卡 / 食堂 / 医保 …）
#   ②+③ 同时成立才算，避免「怎么提高效率」这种被拉进文档问答。

_DOCS_STRONG = re.compile(
    # 学籍异动与考务
    r"休学|复学|退学|转专业|辅修|重修|缓考|查卷|免听|旁听|"
    r"学分(?:认定|置换|互认|转换)|奖励成绩|"
    # 证明与证书
    r"学位证|毕业证|证明书|在读证明|成绩单|均分证明|"
    # 教务系统与教室
    r"教室借用|多媒体教室|一站式服务平台|教务系统|学籍异动|微专业|"
    # 时间安排（校历）
    r"校历|寒假|暑假|"
    r"(?:第一|第二|第三|1|2|3|这|本|下)学期|"
    r"中秋|国庆|元旦|清明|端午|劳动节|春节|元宵|"
    r"第\s*\d+\s*周(?:是|在|从|几|哪)"
)

_DOCS_QUESTION = re.compile(
    r"怎么办|怎么弄|怎么申请|怎么办理|怎么补|怎么开|怎么查|怎么报销|怎么"  # 「怎么」垫底，靠对象兜住
    r"|几点|多久|多长时间|在哪|去哪里|去哪个|要带|需要什么|要什么|"
    r"|什么时候|几号|哪天"
)

_DOCS_OBJECT = re.compile(
    r"休学|复学|退学|转专业|辅修|重修|缓考|查卷|免听|旁听|学分|学位|毕业|证明|"
    r"成绩|教室|教务|学籍|校园卡|学生证|图书|食堂|宿舍|医保|报销|报修|"
    r"奖学金|助学|贷款|交流|留学|选课|退课|实习|毕业设计|档案|户口|"
    # ★ 2026-10-22 收窄：`上课` / `考试` 太泛 —— 「下周一开始上课吗」是看课表，
    #   「下周要考试了」是陈述，两句都被拖进文档路，去查手册。
    #   改成「考试安排 / 考试时间 / 补考 / 缓考」这类**事务性**说法，
    #   并补上学生最常问的「四六级 / 报名」。
    r"开学|放假|注册|报到|考试安排|考试时间|补考|缓考|考务|四六级|报名|"
    # ★ 2026-10-21 扩语料时暴露：语料里已经有《校园网准入系统密码找回方法》，
    #   但「校园网密码忘了怎么办」判不成文档题，落到 plan 去排了一份学习计划。
    #   补的是**对象词**，不是问法 —— 问法那层已经够了。
    r"校园网|上网认证|网络密码|一卡通|完美校园|开水房|专座|自习室|研修间"
)


# 状态诊断（走神 / 拖延 / 效率低…）：这类是"我卡住了"，归 explain，不进文档路
_STATE_HINT = re.compile(
    r"走神|分心|静不下|拖延|效率.{0,4}低|记不住|背不下来|学不进去|学不下去|"
    r"坚持不下|坐不住|提不起|没动力|注意力")

# 明确的"帮我排一份计划"：即使句子里出现「考试」「放假」这类对象词，也该进 plan
_PLAN_HINT = re.compile(
    r"(?:帮我|给我|替我|帮忙).{0,6}(?:排|安排|规划|制定|列|做).{0,4}(?:计划|安排|日程|方案|复习)"
    r"|(?:排|安排|规划|制定).{0,4}(?:一周|本周|这周|下周|明天|今天|复习)")


def _is_docs_question(s: str) -> bool:
    """这句话是在问**学校的规定 / 流程 / 时间安排**吗？

    ★ 2026-10-21 重写理由（评测 R 组）：原来只有一个大正则，结果是
      「图书馆几点开门」「宿舍报修怎么弄」这类**该答"手册里没写"**的问题
      全部落到规划 Agent，回了一张作息表 —— 问的是图书馆几点开门，
      收到「08:00-10:00 学习专业课内容」，比说"不知道"更糟。
      改成「强特征 OR（疑问形态 + 学校事务对象）」后，这类问题能进文档路，
      由 `ask_docs` 明确告诉他手册里没写、该问谁。
    """
    s = s or ""
    # ★ 2026-10-22：两句实测被误抢的，先排掉再判：
    #   「上课总是走神怎么办」—— 有「怎么办」+「上课」，被判成问流程；
    #     可他要的是"怎么才能不走神"，那是 explain。
    #   「下周要考试了，帮我排个计划」—— 有「考试」+「什么时候」形态吗？没有，
    #     但「考试」这个对象词把它拖进来了；而「帮我排个计划」明明是 plan。
    if _STATE_HINT.search(s):
        return False
    if _PLAN_HINT.search(s):
        return False
    if _DOCS_STRONG.search(s):
        return True
    return bool(_DOCS_QUESTION.search(s) and _DOCS_OBJECT.search(s))


# ---------------------------------------------------------------------------
# 单工具直答（第六类路）—— 「今天天气怎么样」「博文楼在哪」「XX 是谁教」
# ---------------------------------------------------------------------------
# ★★★ 2026-10-21 加（在线评测逼出来的，两条都是一眼可见的错）：
#   · X03「今天天气怎么样」→ 实际调了 get_weather + get_timetable + find_free_slots；
#   · X04「博文楼在哪」    → 一个正确工具都没调，调的是 get_timetable + find_free_slots，
#                            最后交出一份作息计划。
#   根因和「你好」那次完全一样：这两句连五条路都不匹配，只能落 `plan`，
#   而 `plan` 的入口就是「先把课表和空档查一遍」。于是问路也被当成要排计划。
#
#   为什么不再试提示词：上一轮「寒暄别调工具」加进 system prompt 后实测零效果
#   （多余调用率 100% → 100%）。模型对"我在干什么"的理解来自整段提示词的角色设定，
#   末尾一条禁令压不过它。**在代码层判出来、只跑一个工具**，才是确定性的修法。

_WEATHER_Q = re.compile(
    # ★ 2026-10-22 扩口径：学生问天气很少说「天气怎么样」，多是「多少度」「要带伞吗」
    #   「雾霾大不大」「明天降温吗」。这些原来都落 plan，去排了一张学习计划。
    r"天气|下雨|下雪|气温|温度|几度|多少度|冷不冷|热不热|带伞|拿伞|伞|"
    r"风力|大风|台风|雾|霾|紫外线|降温|升温|闷热|潮湿|穿什么衣服"
)
# 「在哪 / 怎么走」——问的是**空间位置**，答案在地图，不在课表
#
# ★★★ 「在哪」后面跟动作，问的就**不是位置**（2026-10-21 离线评测逼出来的）：
#   「校历在哪看」「成绩单在哪打印」——学生问的是"去哪个系统/找哪个部门"，
#   那是文档题。第一版 `_WHERE_Q` 只写了 `在哪`，这两句全被当成问路，
#   于是「校历在哪看」会去查楼名表，然后答「校园教学楼表里没有『校历』」。
#   所以后面必须挂一个动作负向断言：跟在「看/打印/办/领…」后面的「在哪」不算位置。
#   ⚠️ `在哪(?!儿)` 里那个负向是为了让「在哪儿」不被「在哪」抢先匹配掉
#      （交替分支先匹配先赢，否则「在哪儿看」会走到「在哪」这一支再回溯成功）。
_WHERE_Q = re.compile(
    r"(?:在哪儿|在哪(?!儿))"
    r"(?!看|打印|办|领|拿|查|下载|找|开|交|盖|申请|预约|报|签|考|学|上|借|还|要|弄)"
    r"|怎么走|怎么去|怎么到|从.{1,10}到.{1,10}怎么|哪个楼|哪栋|几号楼|哪个校区"
)
# 但同样是「在哪」，问的若是**规定/时间/手续**，那就是文档题，不是导航题：
#   「图书馆几点开门」「报到在哪办」→ 手册里查，别答成地图
_WHERE_NOT = re.compile(r"几点|开放时间|什么时候|预约|申请|手续|材料|规定|流程|办理|要带")

# 「某门课的老师 / 教室」——课表里查得到，查课表就够，不该顺带排计划
_COURSE_DETAIL_Q = re.compile(
    r"哪位老师|哪个老师|谁教|老师是谁|任课|授课老师|在哪上|哪间教室|哪个教室|第几节上|"
    # ★ 2026-10-22 扩口径：「计组几点上」「高数什么时候上」既不命中「几点上课」
    #   （那句问的是今天有没有课），也不命中上面任何一个，于是落 plan 排了计划。
    r"几点的?上|几点钟?上|什么时候上|周几上|星期几上|第几周上"
)

# 「学校有几个楼」这类**校园事实**：既不排计划，也不需要查课表。
# ★ 它原来落 plan，实测 33 秒调了 get_timetable + find_free_slots 两个工具，
#   就为了回答「有几个楼」—— 楼名表是常量，翻一下就有。
_CAMPUS_COUNT_Q = re.compile(
    r"(?:几个|多少)(?:栋|座|个)?(?:教学)?楼|(?:几|多少)(?:栋|座)楼|学校有多大|校园有多大"
)


# 办事动作词：问句：「怎么补 / 怎么报修」，答案里就必须真出现这个动作。
_DOC_ACTION = re.compile(
    r"补办|补卡|挂失|换卡|报修|维修|充值|退款|退费|续借|预约|申请|办理|打印|"
    r"领取|开具|开证明|报销|转专业|休学|复学|退学|缓考|重修|查卷|辅修|借用|"
    r"找回|重置|认证|绑定|激活|缴纳|缴费|"
    # ★ 学生口语常说「丢了」，不说「挂失/补办」。
    #   第一版这里只有「补办|补卡」，于是「校园卡丢了怎么补」抽不出任何动作词，
    #   缺失检查形同虚设，模型照样编了一套补办流程。
    #   ⚠️ 不要加「坏了」：「宿舍坏了怎么报修」的正确答案就是报修指南，
    #      加了会让它误判成"片段没写"，把本来答得对的问题答成不知道。
    r"丢了|丢失|遗失"
)


def _missing_actions(query: str, hits: list) -> list:
    """问句里的动作，哪些在检索片段里**一个字都没提到**。

    ★★★ 为什么要单独算这个（2026-10-21，扩语料时踩的）：
      语料从 14 篇扩到 29 篇后，问「校园卡丢了怎么补」，检索命中了
      《校园一卡通线上充值指南》—— 两篇都有「校园卡」三个字，余弦相似度够高。
      于是模型照着这份**充值**指南编了一套**补办**流程：
      「去校园卡服务中心、带学生证、交补办费」，还标了个看起来可信的出处。
      三句话里没有一句是真的。

      光靠提示词「片段里没有的不要写」拦不住：片段里确实有「校园卡」，
      模型判断不了「有校园卡 ≠ 有补办」。所以把「哪个动作词没出现」
      **算出来直接摆给它看** —— 这是确定性的，不依赖模型自觉。

    Returns:
        缺失的动作词列表（空列表 = 问句里的动作在片段里都有对应说法）
    """
    q = set(_DOC_ACTION.findall(query or ""))
    text = "".join(str(h.get("text") or "") for h in hits)
    return sorted(w for w in q if w not in text)


def quick_kind(s: str) -> str | None:
    """这句话能**只查一个工具**就答完吗？能就返回该走哪一类。

    Returns:
        `"weather"` / `"place"` / `"course"` / `"campus"` / `None`

    ★ 判据放在 `services` 里而不是 `routers` 里，是因为**要用工具的语义**：
      「place」对应 `get_building_info`、「weather」对应 `get_weather`、
      「course」对应 `get_timetable` —— 三者的映射关系写在路由器里会分裂成两份。
      `"campus"` 是唯一一个**连工具都不用调**的（楼名表是常量）。
    """
    s = s or ""
    # 天气优先：命中了「天气」就不该再按别的类判（「下雨天要不要带伞去图书馆」）
    if _WEATHER_Q.search(s):
        return "weather"
    # 位置题：先排除「借问路之名问规定」的说法
    if _WHERE_Q.search(s) and not _WHERE_NOT.search(s):
        return "place"
    # 「几个楼」放在 place 之后：带「在哪」的仍按位置处理
    if _CAMPUS_COUNT_Q.search(s):
        return "campus"
    # 课程细节题：问的是具体某门课的人/地/时
    if _COURSE_DETAIL_Q.search(s):
        return "course"
    return None


DOCS_PROMPT = (
    "你是学校教务处的**办事向导**。学生问的是学校的规定、办事流程或学期时间安排。\n"
    "下面会给你检索到的《学校文档片段》。**只能依据片段回答**，片段以外的一律不写。\n\n"
    "回答要求：\n"
    "① 第一句直接给结论（能不能办 / 在哪办 / 是哪天），不要「同学你好」这种开场。\n"
    "② 需要多步的，用 1./2./3. 写清：走哪个系统或去哪个办公室、找谁、带什么、"
    "有没有前置条件（比如「以报告形式附家长意见」）。\n"
    "③ 最后**单独一行**写出处：`来源：《文档名》`，多篇用「、」并列。\n"
    "④ 片段里没有的细节（办理时限、具体表格名、电话）**一律不写**，"
    "不要用常识补全 —— 补出来的东西学生照着做会白跑一趟。\n"
    "⑤ 如果片段明显答不上这个问题，就直说「学校手册里没有写这一条」，"
    "并建议联系教务处（0591-83969194）或辅导员。\n"
    "⑥ 用中文，300 字以内，不要客服腔。"
)


async def ask_docs(goal: str) -> str:
    """查学校文档并作答。**查不到就不调模型**，直接给固定口径。

    ★ 这一点是故意的：检索为空时让模型自由发挥，它一定会编出一套流程，
      而且编得像模像样 —— RAG 最典型的出错方式。既然手上没有依据，
      索性连模型都不调，从机制上保证「不知道就说不知道」。
      顺带还省一次 token。
    """
    from services import qdrant as qd

    question = (goal or "").strip()
    if not question:
        return ""

    try:
        hits = await qd.search_docs(question, top_k=4)
    except Exception as e:                      # noqa: BLE001
        logger.warning("文档检索失败：%s: %s", type(e).__name__, e)
        hits = []

    if not hits:
        return ("学校公开的手册里没有写这一条，我不替你编流程。\n"
                "建议直接问教务处（0591-83969194）或辅导员，他们能给准确答复。")

    snippets = []
    for i, h in enumerate(hits, 1):
        snippets.append(f"[片段{i}]《{h['source']}》·{h['section']}\n{h['text']}")
    user = ("学生的问题：" + question + "\n\n"
            "检索到的学校文档片段：\n\n" + "\n\n".join(snippets))

    # ★ 问句里的动作在片段里压根没有 —— 这时最危险：模型会拿同主题的另一件事顶替。
    _miss = _missing_actions(question, hits)
    if _miss:
        user += ("\n\n⚠️ **检索结果里没有用户问的这个操作**："
                 "「" + "」「".join(_miss) + "」在所有片段里一次都没出现。\n"
                 "这说明检索到的是**同主题的另一件事**（比如用户问「补办」，"
                 "片段讲的是「充值」）。这时**不要**把别的流程套过来 —— "
                 "直接说「学校手册里没有写这条流程」，再说该去问谁。\n"
                 "⚠️ 措辞要求：说「这条流程没写」，**不要**把上面那个词原样引用成"
                 "「没有写\"X\"这一条」—— 那读起来像机器人在对词典。"
                 "编一套流程比说不知道糟糕得多。\n"
                 # ★ 2026-10-22 补：线上实测「校园卡丢了怎么补」虽然答对了「手册里没写」，
                 #   但末尾仍标了《一卡通线上充值指南》当出处，还自己造了「校园卡服务中心」
                 #   这个窗口。前者等于把无关文档当依据，后者是凭常识补出来的地点 ——
                 #   学生真照着跑一趟会白跑。所以这条路上**既不许标出处、也不许编窗口名**。
                 "⚠️ 这一条**不要写「来源：」那一行**：检索到的片段没有一篇讲到用户问的"
                 "这件事，标上出处等于把无关文档当成依据。\n"
                 "⚠️ 也**不要自己提一个办事窗口的名字**（例如「校园卡服务中心」"
                 "「一卡通服务点」这类片段里没有的说法）—— 只建议去问教务处或辅导员。")

    try:
        answer = (await glm_chat(
            [{"role": "system", "content": DOCS_PROMPT},
             {"role": "user", "content": user}],
            temperature=0.2, max_tokens=700,
        )).strip()
        if answer:
            return answer
    except Exception as e:                      # noqa: BLE001
        logger.warning("文档问答调用模型失败，降级为原文摘录：%s: %s", type(e).__name__, e)

    # 模型不可用：直接把最相关的一段原文摆出来，**不加工**，也不假装是答案
    top = hits[0]
    return (f"（AI 暂时不可用，先给你最相关的一段原文）\n\n{top['text']}\n\n"
            f"来源：《{top['source']}》")


def quick_answer(kind: str, res: dict, goal: str = "") -> str:
    """把「只查了一个工具」的结果说成人话。**不调模型**。

    ★★★ 为什么不调模型（2026-10-21）：
      · 天气和楼名都是**确定数据**，模板化 0 延迟、0 token，问句：「天气怎么样」
        不用等 20 秒；
      · 更重要是**不许它编**：模型在「博文楼到底在南区还是北区」上有幻觉空间
        （「博文」很容易被联想成文科楼），而 `_norm_building` 是查楼名表的，
        它说南区就是南区。这类事实题没有任何理由经过一轮语言模型。
      · 查不到时同样诚实说明，不给「大概在附近」这种猜测 —— 猜错的导航
        比说"不知道"更害人。

    Args:
        kind: `quick_kind()` 的返回值。
        res:  工具返回的结构化结果。
        goal: 原句（课程题要从里面取课名）。
    """
    if kind == "weather":
        if res.get("error"):
            return f"天气查不到（{res['error']}）。学校所在地是福州马尾，可以自己看一眼天气 App。"
        parts = [f"福州马尾 现在 {res.get('weather') or '未知天气'} "
                 f"{res.get('temperature') or '—'}℃"]
        wind = "".join(str(res.get(k) or "") for k in ("winddirection", "windpower"))
        if wind.strip():
            parts.append(f"{wind.strip()}风")
        if res.get("humidity"):
            parts.append(f"湿度 {res['humidity']}%")
        line = "，".join(parts) + "。"
        if res.get("reporttime"):
            line += f"\n（数据时间 {res['reporttime']}）"
        return line

    if kind == "place":
        if res.get("error"):
            return "没听清是哪个地点，可以说得再具体一点（比如「博文楼」「知行楼B栋」）。"
        if res.get("virtual"):
            return (f"「{res.get('input')}」不是教学楼，可能是线上课（腾讯会议这类），"
                    f"也可能是田径场、体育馆这样的校园场馆 —— 场馆在校内地图上有独立点位，"
                    f"但不按南北区算作息。")
        if not res.get("building"):
            return (f"校园教学楼表里没有「{res.get('input')}」。"
                    f"它可能是宿舍、食堂这类生活设施，或者是学校新盖的楼。"
                    f"你可以在「校园地图」页直接搜名字，那里收的点位比教学楼表全。")
        return (f"{res['building']} 在 **{res.get('region_label')}**。\n"
                f"南区和北区的作息最多差 10 分钟，赶课的话别按另一区的点走。\n"
                f"要走路路线就打开「校园地图」，输入起点终点，会给校内步行道（不是机动车道）。")

    if kind == "course":
        courses = res.get("courses") or []
        if not courses:
            return ("你的时间表里还没有课程。先到「学习规划」页导入课表，"
                    "之后问「某门课几点上、在哪上、谁教」我就能直接答。")
        # ★ 用句子里出现过的课名去匹配，而不是拿整句去比 —— 中文没有空格，
        #   整句永远匹配不上课程名（「计算机组成原理是哪位老师」≠「计算机组成原理」）。
        hit = None
        best = 0
        for c in courses:
            name = str(c.get("name") or "")
            if not name:
                continue
            # 从课名里滑窗取子串，落在输入原句里且最长的那次就是它
            for n in range(min(len(name), 10), 1, -1):
                for i in range(0, len(name) - n + 1):
                    sub = name[i:i + n]
                    if sub in (goal or "") and n > best:
                        best, hit = n, c
                if hit and best >= n:
                    break
        if not hit:
            return (f"你这学期的课表里没有「{goal.strip('？?。 ')}」这门课。\n"
                    f"可能是还没导入时间表，也可能是课程名跟我这边对不上 —— "
                    f"到「学习规划」页看一眼课表就知道。")
        bits = [f"{hit.get('name')}"]
        if hit.get("weekday") is not None and hit.get("start_time"):
            wd = "一二三四五六日"[int(hit["weekday"]) % 7]
            bits.append(f"周{wd} {hit.get('start_time')}-{hit.get('end_time')}")
        if hit.get("location"):
            bits.append(str(hit["location"]))
        if hit.get("teacher"):
            bits.append(f"任课教师 {hit['teacher']}")
        elif hit.get("name"):
            bits.append("课表里没登记任课教师")
        return "：".join([bits[0], "，".join(bits[1:])]) + "。" if len(bits) > 1 else \
               f"{bits[0]}：课表里只登记了课程名，时间和地点都空着。"

    if kind == "campus":
        # 楼名表就一份，别再抄 —— 抄了就会出现「课表页认得出、这里数不对」。
        # 延迟导入的原因和 `_tool_get_building_info` 一样：planner 反向 import 本模块。
        from routers import planner as planner_mod
        known = sorted(planner_mod.KNOWN_BUILDINGS)
        north = [b for b in known if b in planner_mod.NORTH_BUILDINGS]
        south = [b for b in known if b not in planner_mod.NORTH_BUILDINGS]
        return (f"校园里的**教学楼**一共 {len(known)} 栋：\n"
                f"· 北区 {len(north)} 栋：{'、'.join(north)}\n"
                f"· 南区 {len(south)} 栋：{'、'.join(south)}\n"
                f"（宿舍、食堂、体育馆这些生活场馆不算教学楼，"
                f"在「校园地图」页能看到全部点位。）")

    return ""


SYSTEM_PROMPT = (
    "你是「校园 AI 伴学」的规划 Agent，服务对象是**在校大学生**。"
    "你的任务是基于这名学生的**真实时间表**，生成可执行、分天落地的学习计划。\n"
    "\n"
    "工作方式（很重要）：\n"
    "① 你有一组工具可以调用。**先查再答**，不要凭印象编造学生的时间表、时间、天气。"
    "排计划前至少调用 find_free_slots 拿到真实空档；需要写节次/时间点时先调 "
    "get_class_schedule；拿不准上课地点在南区还是北区就调 get_building_info。\n"
    "② 工具返回的是**事实**，以它为准。如果时间表为空，那是正常情况（还没导入），"
    "按「一名没有排课的大学生的一天」给通用节奏，**不要因为空时间表就拒绝生成**。\n"
    "③ 计划要贴合大学生真实节奏：有早八、有下午课、有晚自习式高效自学段，"
    "但不要照搬中学作息（不要写「早读」「课间操」）。\n"
    "④ 时段必须具体到「几点到几点做什么」，不要写「多看书」这种空话。\n"
    "   工具给的空档可能长达四五小时，**务必拆成 1~2 小时的具体任务**"
    "（写明学哪一章、做哪类题），不要原样写成一整块「自由学习时间」——"
    "那等于没排。每个时段后面可以带一句完成标准（如「做完 20 道选择题」）。\n"
    "⑤ 照顾多元目标：专业课、四六级、竞赛/项目、社团、实习、考研，按用户目标分配权重。\n"
    "⑥ 每周留出至少半天机动时间和一晚休息，不要把时间排满。\n"
    "⑦ 最终输出用中文、分天列出、条理清晰，可以直接照着执行；"
    "**不要在最终答案里复述工具返回的原始 JSON**。\n"
    "\n"
    "⑧ **日期一律以用户消息里给出的「今天」和日期区间为准，绝对不要自己推测今天是几号。**"
    "（这条是用真金白银换来的：不给出锚点日期时，模型会把「今天」猜成完全不相干的日期 ——"
    "实测它猜成 2023-04-15，于是查了 2023 年的时间表、拿到 0 门课，"
    "最后排出一份和真实时间表毫无关系的计划，而且从输出上完全看不出来错在哪。）\n"
    "⑧b **星期一概照抄用户消息里那条「要覆盖的日期」**，一个字都不要自己推算。"
    "（同一类错误的小号版本：锚点给了「2026-10-15（周四）」，模型仍把 10-15 写成「周六」，"
    "后面整串星期跟着错 —— 计划每天都顶着星期，错一天就是全篇错。）\n"
    "⑨ 页面上这是**连续对话**：用户消息里若出现「在此之前用户还说过…」，那是**上文**，"
    "用来读懂「改成两周」「再细一点」这类简短追问；但**以【我的目标是】那一句为准**，"
    "每次都要输出一份**完整**的计划，不要只说「同上」或只写改动部分。\n"
    "\n"
    "⑩ **只允许把任务排在 find_free_slots 返回的 free 段里**（2026-10-18 加，实测得出）：\n"
    "   · free 段之外的任何时间都是**有课的**，绝不能排学习任务 ——"
    "更不要用「预习这门课」去占它自己的上课时段（实测排过「14:50-15:35 预习计算机组成原理」，"
    "而 14:50-15:35 正是计算机组成原理的上课时间，用户一眼就看出这份计划是假的）。\n"
    "   · busy 里列出的时段一律视为不可用，连「课上顺便复习」这种写法也不要有。\n"
    "   · 空档比 min_slot_minutes（默认 25 分钟）还短的，**直接跳过不排**。"
    "课间 5 分钟写不进任何有意义的任务，硬塞只会让整份计划失去可信度。\n"
    "   · 一天里所有 free 段加起来都排不下一个完整任务时，就写「这天课多，不安排额外任务」，"
    "**不要编**。诚实比看起来很努力更重要。\n"
    "\n"
    "⑪ **用户给了「时间配方」时，一律照抄，不许自己再分配**（2026-10-18 加，实测得出）：\n"
    "   如果用户消息里出现「★ 用户给了**明确的时间配方**」那张算好的时刻表，"
    "那就是用户要的**逐日时刻**，你的任务只剩「按天重复 + 写上具体内容」：\n"
    "   · **每一天**都按那张表排，七天就是七段，一天都不许漏 ——"
    "更不许拿「自由学习」「机动」「自行安排」占位（实测排过「18:00-22:00 自由学习」，"
    "用户要的是具体安排，这等于没排）；\n"
    "   · **表里的时间一个字都不要改**，不要合并、不要平移、不要重新算；"
    "最后一项标了「（剩余时间）」的，就是要一直排到窗口结束；\n"
    "   · 此时 ④ 的「拆成 1~2 小时」和 ⑥ 的「留半天机动、留一晚休息」**都不适用** ——"
    "用户点名就要这么排，是用户在定节奏，不是你在给建议；\n"
    "   · 只有工具明确说那天该时段有课（busy 里能查到）时，才写「此时段有课，跳过」，"
    "并把「跳过」写清楚是哪一天，**不要把它挪到别的时间**。\n"
    "   （为什么写成硬规则：让他自己算 60+10+30+10+… 这串加法，"
    "实测同一条配方他在七天里能算出**三种不同的时刻**，还有一天直接没排。）\n"
    "\n"
    "⑫ **学校规定类问题先查 search_campus_docs，不许凭印象回答**（2026-10-21 加）：\n"
    "   · 休学 / 复学 / 退学 / 转专业 / 辅修 / 重修 / 缓考 / 查卷 / 学分认定与置换 /"
    "毕业与学位证明 / 出国在读证明 / 教室借用 / 多学期什么时候开学放假、第几周是几号 ——"
    "这些都算「学校手册里的事」。**你对这所学校的具体规定一无所知**，必须先查。\n"
    "   · 回答要基于检索片段，并在**最后单独一行**用「来源：《文档名》」标出处；"
    "多篇就并列写。这是让人相信答案的唯一凭据。\n"
    "   · 检索返回 count=0 时，直接说「学校手册里没有写这一条」，并建议联系教务处"
    "（0591-83969194）或辅导员。**绝对不要编流程、编材料、编日期** ——"
    "语料里根本没有「校园卡补办」，编出来的流程一眼就能被拆穿。\n"
    "   · 不要把片段整段贴出来，用你自己的话讲清「走哪个系统、几步、找谁」，"
    "步骤多于两步就用 1./2./3. 列出来。"
)


# 中文里的「周几」→ Python weekday（周一=0 … 周日=6）。1-7 也认，周7 就是周日。
_WEEKDAY_IDX = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}


def _parse_relative_dates(s: str, today: _date):
    """认「下周 / 下周三 / 这周六 / 周末 / 明天」这类**中文相对日期**。

    ★★ 2026-10-18 实测（截图）：「帮我安排**下周**晚上 20-22 点的空闲时间…」
       Agent 排出来的是 **10-18（今天，周日）和 10-19**。
       原因：`parse_date_range` 原来的三条正则只认「数字 + 号/日」，
       「下周晚上 20-22 点」里唯一的数字是**时间**（而且刻意不当日期），
       于是整句话对它们**完全不可见**，起点又退回"今天"。
       结论：用户用**中文**说日期（一个数字都不带）时，原来那套等于没有。

    Returns:
        (start_date, days) 或 None。
    """
    if not s:
        return None
    wk = today.weekday()                        # 周一=0 … 周日=6
    next_mon = today + timedelta(days=7 - wk)   # 下周一

    # ① 周末 = 周六 + 周日两天（要先于「周X」判断，否则「周末」会被当成别的）
    m = re.search(r"(下下|下|这|本)?\s*个?\s*周\s*末", s)
    if m:
        pre = m.group(1) or ""
        if pre == "下下":
            sat = next_mon + timedelta(days=12)
        elif pre == "下":
            sat = next_mon + timedelta(days=5)
        else:                                    # 这/本/没说 → 最近的周末（含今天）
            sat = today + timedelta(days=(5 - wk) % 7)
        return sat, 2

    # ★ 2026-10-20 补：「明后天」是**两天**（明天 + 后天），
    #   原来只认到里面的「后天」，于是问「明后天有没有课」只答了后天一天。
    #   必须写在下面的单天表前面 —— 表里第一个命中的「后天」会把整句吃掉。
    m = re.search(r"明\s*后\s*[两二]?\s*[天日]", s)
    if m:
        return today + timedelta(days=1), 2

    # ② 单天：大后天要写在后天前面，否则被「后天」先吃掉
    # ★ 2026-10-20 补：后晚 / 昨日 / 前天 这些口语，原来一个都不认 →
    #   整句当成"没点名日期"→ 按今天答，问昨天答今天。
    for kw, off in (("大后晚", 3), ("大后天", 3),
                    ("后晚", 2), ("后天", 2), ("后日", 2),
                    ("明晚", 1), ("明早", 1), ("明天", 1), ("明日", 1), ("明儿", 1),
                    ("今儿", 0), ("今晚", 0), ("今早", 0), ("今天", 0), ("今日", 0),
                    ("昨晚", -1), ("昨儿", -1), ("昨天", -1), ("昨日", -1),
                    ("前晚", -2), ("前天", -2), ("前日", -2)):
        if kw in s:
            return today + timedelta(days=off), 1

    # ★ 2026-10-20 补：「周六日」= 周六 + 周日两天。口语这么写很常见，
    #   少了第二个「周」字就只算出周六一天，「周六周日」那句反而认得。
    m = re.search(r"(下下|下|这|本)?\s*个?\s*(?:周|星期|礼拜)\s*六\s*[、,，和跟与]?\s*[日天]", s)
    if m:
        pre = m.group(1) or ""
        if pre == "下下":
            return next_mon + timedelta(days=12), 2
        if pre == "下":
            return next_mon + timedelta(days=5), 2
        if pre in ("这", "本"):
            d = today - timedelta(days=wk) + timedelta(days=5)
            if d < today:
                d = next_mon + timedelta(days=5)
            return d, 2
        return today + timedelta(days=(5 - wk) % 7), 2

    def _day_of(wd: int, pre: str = "") -> _date:
        """「周X」落到具体哪天。pre 是前面的「下下 / 下 / 这 / 本」。"""
        if pre == "下下":
            return next_mon + timedelta(days=7 + wd)
        if pre == "下":
            return next_mon + timedelta(days=wd)
        if pre in ("这", "本"):
            d = today - timedelta(days=wk) + timedelta(days=wd)   # 本周那一天
            if d < today:            # 已经过去了 → 顺延到下周同一个星期几
                d = next_mon + timedelta(days=wd)
            return d
        # 光说「周三」→ 最近的那个周三（今天就是周三则算今天）
        return today + timedelta(days=(wd - wk) % 7)

    # ③a 星期区间：周三到周五 / 下周三至周五。
    #     ★ 必须写在单天前面，否则「周三到周五」会被当成"周三"一天。
    m = re.search(r"(?<![一两几])(下下|下|这|本)?\s*个?\s*(?:周|星期|礼拜)\s*([一二三四五六日天1-7])"
                  r"\s*[到至\-~—–]\s*(?:周|星期|礼拜)?\s*([一二三四五六日天1-7])", s)
    if m:
        pre = m.group(1) or ""
        a, b = _WEEKDAY_IDX[m.group(2)], _WEEKDAY_IDX[m.group(3)]
        if b >= a:
            return _day_of(a, pre), b - a + 1

    # ③b 连着说两天：周三周四 / 周三和周五。
    #     ★ 原来只认第一天，于是「周三周四晚上都没课」只答了周三。
    m = (re.search(r"(?<![一两几])(下下|下|这|本)?\s*个?\s*(?:周|星期|礼拜)\s*([一二三四五六日天1-7])"
                   r"\s*(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", s)
         or re.search(r"(?<![一两几])(下下|下|这|本)?\s*个?\s*(?:周|星期|礼拜)\s*([一二三四五六日天1-7])"
                      r"\s*[和跟与、,，]\s*(?:周|星期|礼拜)?\s*([一二三四五六日天1-7])", s))
    if m:
        pre = m.group(1) or ""
        a, b = _WEEKDAY_IDX[m.group(2)], _WEEKDAY_IDX[m.group(3)]
        # 相邻（周三周四）当两天；不相邻（周三和周六）只取第一天，别给假区间
        return _day_of(a, pre), (2 if b == a + 1 else 1)

    # ③ 带星期几：下下周三 / 下周三 / 这周六 / 周三
    m = re.search(r"(?<![一两几])(下下|下|这|本)?\s*个?\s*(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", s)
    if m:
        return _day_of(_WEEKDAY_IDX[m.group(2)], m.group(1) or ""), 1

    # ★ 2026-10-20 补：这三个原来一律返回 None → 落到"没点名日期" → 按今天答，
    #   于是「工作日有课吗」被答成今天，「这两天有没有课」也只答今天一天。
    if re.search(r"工作日|上学日", s):
        # 今天还在工作日就今天起，否则下周一；一直到周五
        start = today if wk < 5 else next_mon
        return start, max(1, 5 - start.weekday())

    if re.search(r"这\s*[两二]\s*天|最近\s*[两二]\s*天", s):
        return today, 2
    if re.search(r"这\s*几\s*天|最近\s*几\s*天", s):
        return today, 3

    # ④ 整周：下下周 / 下周 / 这周（「这周」已经过掉的天不排）
    m = re.search(r"(?<![一两几])(下下|下|这|本)\s*个?\s*(?:周|星期|礼拜)", s)
    if m:
        pre = m.group(1)
        if pre == "下下":
            return next_mon + timedelta(days=7), 7
        if pre == "下":
            return next_mon, 7
        mon = max(today - timedelta(days=wk), today)
        return mon, max(1, 7 - mon.weekday())
    return None


def parse_date_range(goal: str, today: _date, allow_past: bool = False):
    """从用户这句话里认出他点名的日期范围。

    ★ 为什么必须在后端认（2026-10-18 实测）：
      他说「帮我安排 **20-21 号** 这两天晚上 18-22 点的空闲时间」，
      Agent 却排了 **10 月 18 日和 19 日**（就是从今天起算两天）。
      原因很简单：`days` 只有天数、没有起点，起点永远被写死成"今天"，
      用户在话里点的日期对后端**完全不可见** —— 模型只能看到一句
      「请为 2026-10-18 到 2026-10-19 这 2 天排计划」。
      于是它很听话地排了 18、19 号，看起来"没错"，但那两天根本不是用户要的。

    支持的写法（中文口语里最常见的几种）：
        绝对日期：20-21 号 / 20 到 21 日 / 10月20日-10月21日 / 20号 / 10月20号
        相对日期：明天 / 后天 / 大后天 / 下周 / 下周三 / 这周六 / 周末（见 _parse_relative_dates）
    刻意**不**匹配「18-22 点」这类**时间**区间（第二个数字后面没有 号/日）。

    ★★ 绝对日期优先：用户同时写了「下周三 10月21日」这种，按他写死的那个日子走。

    Returns:
        (start_date, days) 或 None（没点名日期）。
    """
    s = str(goal or "")
    if not s:
        return None

    def _fix(d: _date, explicit_month: bool = False) -> _date:
        """把用户说的日期挪到**未来**。两种写法要分开处理：

        - 说了月份（`explicit_month=True`，如「1月5日」）：过去就翻**年**。
          翻月是错的 —— 「1 月 5 日」翻到 11-05，用户看到的是完全另一个日子。
        - 只说日号（如「20 号」「5 号」）：指的是**最近的那个 20 号**，过去就翻月。
          ★ 这里必须**循环**：只加一次月的话，「5 号」（今天是 10-18）会停在
            11-05 —— 那反而是对的；但「20 号」若碰上月末会停在过去，
            于是 `run_study_agent` 当成"没点名"，起点退回今天，
            症状和完全没解析一模一样（问题记录：「点名日期久了就不行」）。
        """
        if explicit_month:
            if d < today - timedelta(days=1):
                try:
                    d = _date(d.year + 1, d.month, d.day)
                except ValueError:
                    pass
            return d
        for _ in range(24):          # 24 个月足够翻到明年；也是死循环保险
            if d >= today - timedelta(days=1):
                break
            y, m = d.year, d.month + 1
            if m > 12:
                y, m = y + 1, 1
            try:
                d = _date(y, m, d.day)
            except ValueError:
                break                # 2 月 30 日这种不存在的日子，原样留给上层判空
        return d

    # ① 带月份的完整区间：10月20日-10月21日 / 10月20-21日
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*[-~—–到至]\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*[日号]?",
                  s)
    if m:
        m1, d1 = int(m.group(1)), int(m.group(2))
        m2 = int(m.group(3)) if m.group(3) else m1
        d2 = int(m.group(4))
        try:
            a = _date(today.year, m1, d1)
            b = _date(today.year, m2, d2)
        except ValueError:
            return None
        a, b = _fix(a, True), _fix(b, True)
        if b < a:
            return None
        return a, (b - a).days + 1

    # ② 只写日号：20-21 号 / 20 到 21 日
    m = re.search(r"(\d{1,2})\s*[-~—–到至]\s*(\d{1,2})\s*[日号]", s)
    if m:
        d1, d2 = int(m.group(1)), int(m.group(2))
        try:
            a = _date(today.year, today.month, d1)
            b = _date(today.year, today.month, d2)
        except ValueError:
            return None
        a, b = _fix(a), _fix(b)
        if b < a:                       # 20 号到 3 号这种跨月的写法
            try:
                b = _date(a.year + (1 if a.month == 12 else 0), (a.month % 12) + 1, d2)
            except ValueError:
                return None
        return a, (b - a).days + 1

    # ③ 单个日期：20 号 / 10月20日
    m = re.search(r"(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*[日号]", s)
    if m:
        mon = int(m.group(1)) if m.group(1) else today.month
        day = int(m.group(2))
        try:
            a = _date(today.year, mon, day)
        except ValueError:
            return None
        a = _fix(a, bool(m.group(1)))     # 写了月份 → 翻年；只写日号 → 翻月
        return a, 1

    # ③.5 裸写「10-24」「10/24」（2026-10-20 补）。
    #     用户确实会这么打（手机键盘打「10月24日」要切三次），原来一个都不认。
    #     ⚠️ 必须和「18-22 点」这种钟点区间分开，判据是**数值范围**：
    #        月份只可能 1~12，所以「18-22」自然不成立；
    #        「2-3 天」「1-2 周」由后面那个负向断言挡掉。
    m = re.search(r"(?<![\d:])(\d{1,2})\s*[-/]\s*(\d{1,2})(?![\d:])"
                  r"(?!\s*[点时:分天周月日个])", s)
    if m:
        mon, day = int(m.group(1)), int(m.group(2))
        if 1 <= mon <= 12 and 1 <= day <= 31:
            try:
                a = _date(today.year, mon, day)
            except ValueError:
                a = None
            if a:
                return _fix(a, True), 1

    # ④ 中文相对日期（下周 / 下周三 / 周末 / 明天 …）。
    #    放在最后：用户同时写了绝对日期时，一律以他写死的那个日子为准。
    #
    # ★★ 「昨天 / 前天」只有**问课**那条路要用（问"昨天有课吗"）。
    #    排计划时拿到一个已经过去的日子是有害的：一句「昨天没学完」
    #    会让计划从昨天开始排。所以默认 allow_past=False。
    rel = _parse_relative_dates(s, today)
    if rel:
        if not allow_past and rel[0] < today:
            return None
        return rel
    return None


def _hhmm(minutes: int) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60)


# 句首那些连接词/动词，剥掉之后剩下的才是"做什么"
_CLAUSE_LEAD = re.compile(r"^(?:先|再|然后|接着|之后|最后|开始|首先|其次|并且|同时)+")
# 时间窗：18-22点 / 18:00-22:00 / 晚上18点到22点
_WIN_DOT = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})?\s*[-~—–到至]\s*(\d{1,2})\s*[:：]?\s*(\d{2})?\s*点?")
_WIN_RNG = re.compile(r"(\d{1,2})\s*[-~—–到至]\s*(\d{1,2})\s*点")


def parse_time_recipe(goal: str):
    """认出输入里说的「时间配方」：时间窗 + 每段学多久 + 中间休息多久 + 剩下的时间干什么。

    ★★ 为什么必须在后端算（2026-10-18 实测，第 10 个反馈）：
       他说「下周晚上 18-22 点，每次学习完休息 10 分钟，**先学大学英语 60 分钟**，
       再学**高等数学 30 分钟**，剩余时间练习 WPS」。这是个**算术题**，不是创作题。

       实测模型排出来（同一天）：
           18:00-19:00 大学英语 / 19:10-19:20 休息 / 19:30-20:00 高等数学 / 20:30-21:00 WPS
       ① 19:00-19:10 凭空消失，19:20-19:30 也没有着落；
       ② 高等数学被挪到 19:30，而不是 19:10；
       ③ WPS 只写到 21:00 —— 用户明明说到 22 点，剩下一小时没了；
       ④ 而且**七天里写法还不一样**（另一天是 19:50-22:00 才对）。
       根因不是模型"不听话"，是让它**心算一串加法**：60+10+30+10+…。它会算，但不会天天算对。

       所以这里直接把时间算成绝对时刻表，让它**抄**，而不是算。

    Returns:
        dict（见下方各键）或 None（用户没给配方，正常流程不受影响）。
    """
    s = str(goal or "")
    if not s:
        return None

    # ① 时间窗
    start_min = end_min = None
    m = _WIN_RNG.search(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= a <= 23 and 0 <= b <= 24 and b > a:
            start_min, end_min = a * 60, b * 60
    if start_min is None:
        m = _WIN_DOT.search(s)
        if m:
            a = int(m.group(1)); ah = int(m.group(2) or 0)
            b = int(m.group(3)); bh = int(m.group(4) or 0)
            a_min, b_min = a * 60 + ah, b * 60 + bh
            if 0 <= a_min < b_min <= 24 * 60:
                start_min, end_min = a_min, b_min
    if start_min is None:
        return None

    # ② 每段之间休息多久（用户不说就 0）
    rest = 0
    m = re.search(r"休息\s*(\d{1,3})\s*分钟", s)
    if m:
        rest = max(0, min(60, int(m.group(1))))

    # ③ 按标点切句，认「<做什么><N>分钟」；「休息10分钟」这一句不算任务
    tasks = []
    for clause in re.split(r"[，,。；;、\n]", s):
        mc = re.search(r"(\d{1,3})\s*分钟", clause)
        if not mc:
            continue
        body = clause[:mc.start()]
        if "休息" in body:
            continue
        body = _CLAUSE_LEAD.sub("", body.strip())
        body = re.sub(r"^(?:学习|练习|复习|背|做|刷|写|读|看|听)", "", body).strip()
        body = _CLAUSE_LEAD.sub("", body) or body
        dur = int(mc.group(1))
        if len(body) >= 2 and 5 <= dur <= 240:
            tasks.append({"name": body, "minutes": dur})
    if len(tasks) < 2:
        return None

    # ④ 剩余时间做什么
    filler = ""
    m = re.search(r"(?:剩余|剩下|其余|多余|剩下的)的?\s*(?:时间|时段)?\s*([^\s，,。；;]+)", s)
    if m:
        filler = _CLAUSE_LEAD.sub("", m.group(1).strip())

    # ⑤ 算绝对时刻（这就是那几个加法，一次算对，七天照抄）
    blocks = []
    t = start_min
    truncated = False
    for i, task in enumerate(tasks):
        if t + task["minutes"] > end_min:
            truncated = True
            break
        blocks.append({"start": _hhmm(t), "end": _hhmm(t + task["minutes"]),
                       "what": "学习" + task["name"], "kind": "study"})
        t += task["minutes"]
        # ★ 休息要插在"后面还有内容"的时候 —— 包括**下一项是「剩余时间」那一项**。
        #   输入里说的是「每次学习完中间休息 10 分钟」，不是"两个任务之间"：
        #   漏掉最后一个学习段后面的休息，WPS 就会提前 10 分钟起（19:40 而不是 19:50），
        #   而他截图里第一天写的正是 19:50 起 —— 自己和自己对不上。
        has_next = (i < len(tasks) - 1) or bool(filler)
        if has_next and rest and t + rest <= end_min:
            blocks.append({"start": _hhmm(t), "end": _hhmm(t + rest),
                           "what": "休息", "kind": "rest"})
            t += rest
    if filler and t < end_min:
        blocks.append({"start": _hhmm(t), "end": _hhmm(end_min),
                       "what": filler + "（剩余时间）", "kind": "filler"})
        t = end_min
    if not blocks:
        return None
    return {"start": _hhmm(start_min), "end": _hhmm(end_min),
            "rest": rest, "blocks": blocks,
            "filler": filler, "truncated": truncated}


def format_time_recipe(recipe: dict, days: int) -> str:
    """把算好的时刻表写成让模型**照抄**的几行字。"""
    lines = "\n".join("   {} - {}  {}".format(b["start"], b["end"], b["what"])
                      for b in recipe["blocks"])
    tail = ""
    if recipe["filler"]:
        tail = (f"   · 最后一项「{recipe['filler']}」要一直排到 {recipe['end']}，"
                f"不要提前收尾。\n")
    return (
        f"\n★ 用户给了**明确的时间配方**。下面这张表我已经替他算好，"
        f"**时间一个字都不要改，也不要自己重新分配**：\n"
        f"{lines}\n"
        f"{tail}"
        f"   要求：\n"
        f"   ① 上面 {days} 天里的**每一天**都按这张表排，一天都不许漏；\n"
        f"   ② 不许写「自由学习」「机动时间」「自行安排」这类占位词 —— "
        f"每一项都要有明确内容；\n"
        f"   ③ 只在时间窗 {recipe['start']}-{recipe['end']} 内排，窗外不排；\n"
        f"   ④ 若某天这个时段确实有课（工具返回的 busy 里有），就在那天写「此时段有课，跳过」，"
        f"**不要为了凑数把时间挪到别处**；\n"
        f"   ⑤ 用户点名要这么排，所以「每周留半天机动」「拆成 1~2 小时」这两条**不适用**。\n"
    )


def build_user_message(today: _date, days: int, goal: str, who_line: str = "",
                       context: str = "", start: _date = None) -> str:
    """拼「用户这一轮说了什么」。

    ★ 为什么要单独抽成一个函数：**它必须包含锚点日期**。

    这里曾经漏掉日期，结果模型把「今天」猜成了 2023-04-15（真实是 2026-10-14），
    接着拿这个错误日期去调 `get_timetable` / `find_free_slots`，查到 0 门课、
    算出 42 小时空档，最后交出一份和真实课表毫无关系的计划 ——
    更糟的是**从输出上完全看不出来错在哪**，计划本身写得很漂亮。

    「未来 N 天」这种相对表述，模型没有任何办法自己落到绝对日期上，
    必须由调用方给出。抽成独立函数是为了让这条不变量**可以被测试**，
    而不是埋在两百行的 async 循环里只能靠人肉 review 发现。

    Args:
        today: 锚点日期（一般是服务器当天）。
        days: 计划覆盖天数（含今天）。
        goal: 用户这一轮写下的目标。
        who_line: 学生画像那一句，可为空。
        context: 前几轮原句（对话页才用），可为空。

    Returns:
        直接塞进 `messages[1]["content"]` 的字符串。
    """
    days = max(1, min(30, int(days or 1)))
    # ★ start 与 today 是**两个不同的东西**，别合并：
    #     today = 真正的今天（模型判断「今晚」「明天」要用它）；
    #     start = 用户点名的那天（"20-21 号"）。计划从 start 起算，但"今天几号"仍要说实话。
    start = start or today
    end_day = start + timedelta(days=days - 1)
    weekday_cn = "一二三四五六日"[today.weekday()]
    # ★ 每一天的**日期 + 星期**都写出来，让模型照抄。
    #
    # 为什么不只给首尾：语言模型算星期是出了名的不准。实测（2026-10-15 那天）
    # 锚点写的是「今天的日期是 2026-10-15（周四）」，模型却把 10-15 排成了「（周六）」，
    # 后面 10-16（周日）、10-17（周一）**整串跟着错**。
    # 计划里每天都顶着星期，错一天就是全篇错 —— 而它自己完全意识不到。
    # 把「日期（周X）」逐条列出来，模型就从"算"变成了"抄"，成本只有几十个 token。
    day_lines = "、".join(
        "{}（周{}）".format(d.isoformat(), "一二三四五六日"[d.weekday()])
        for d in (start + timedelta(days=i) for i in range(days))
    )
    # 用户点名日期时，必须**明说**不要从今天开始排 —— 实测不写这句，
    # 模型会老老实实按"今天起 N 天"排（用户要 20-21 号，它排 18-19 号）。
    when_line = (
        f"★ 用户这次点名了日期：**只排 {start.isoformat()} 到 {end_day.isoformat()} "
        f"这 {days} 天**，不要从今天（{today.isoformat()}）开始排，也不要多排一天。\n"
        if start != today else
        f"请为 {start.isoformat()} 到 {end_day.isoformat()} 这 {days} 天（含首尾）制定学习计划。\n"
    )
    # ★ 用户给了「时间配方」时，时刻表由后端算好，模型只管抄（见 parse_time_recipe 的注释）。
    recipe = parse_time_recipe(goal)
    recipe_txt = format_time_recipe(recipe, days) if recipe else ""
    # 有配方时条目天然多于 3-4 条，那条"紧凑"要求得让位；但**仍然不许解释理由**。
    compact = (
        "计划写成**紧凑清单**：就按上面那张表逐日列，一天一段，"
        "不要解释理由、不要复述课表、不要客套话。\n"
        if recipe else
        # ★ 输出必须紧凑：线上网关约 60 秒就断，模型写得越长越可能排不完。
        #   实测 7 天逐条解释会多花 20 秒以上，直接把人推到超时降级。
        "计划写成**紧凑清单**：每天最多 3-4 条，只写时间段 + 做什么，"
        "不要解释理由、不要复述课表、不要客套话。\n"
    )
    return (
        (f"{who_line}\n" if who_line else "") +
        (f"{context}\n" if context else "") +
        f"今天的日期是 {today.isoformat()}（周{weekday_cn}）。\n"
        f"我的目标是：{goal}\n"
        f"{when_line}"
        f"本次要覆盖的日期（**下面每一行的星期都是核过的，直接照抄，不要自己推算**）："
        f"{day_lines}\n"
        "先用工具把我的时间表和空闲时段查清楚，再排。\n"
        + compact +
        recipe_txt +
        "⚠️ 查时间表和空档时**必须用上面给出的日期**，不要自己推测今天是几号。"
    )


async def run_study_agent(db, user, courses: list, goal: str, days: int,
                          who_line: str = "", context: str = "") -> tuple[str, list]:
    """跑一轮 Agent，返回 (计划正文, 工具调用轨迹)。

    轨迹 `trace` 是给前端展示「Agent 干了什么」用的，形如：

        [{"tool": "find_free_slots", "args": {...}, "ok": true,
          "summary": "算出 7 天共 31.5 小时空档"}]

    这是 Agent 与"黑盒问答"最直观的区别：调用过程对用户可见。

    Args:
        context: 前几轮原句（对话页才传），拼进用户消息帮模型接住追问。

    Returns:
        (plan, trace)。AI 不可用时返回 **("", trace)**，由调用方降级到本地计划。
    """
    days = max(1, min(30, int(days or 1)))
    today = _date.today()
    # ★ 用户点名日期就以他说的为准："帮我排 20-21 号" → 起点 10-20、共 2 天。
    #   没点名才退回「从今天起 N 天」。
    rng = parse_date_range(goal, today)
    start = rng[0] if rng else today
    if rng:
        days = max(1, min(30, rng[1]))
    # 计划窗口（含首尾）。工具层用它做防幻觉兜底：模型自己编日期时把请求夹回这段区间。
    window = (start.isoformat(), (start + timedelta(days=days - 1)).isoformat())
    ctx = {"db": db, "user": user, "courses": courses or [], "window": window}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": build_user_message(today, days, goal, who_line, context, start=start)},
    ]
    trace: list = []
    t0 = time.monotonic()
    exhausted = False

    for round_no in range(MAX_ROUNDS):
        left = BUDGET_S - (time.monotonic() - t0)
        # 预算不够再跑一轮了：**别再等**，直接走本地降级。
        # 线上网关 60 秒就 504，用户在页面上只会看到「生成失败」——
        # 与其让他等一个注定失败的结果，不如马上给一份朴素但能用的计划。
        if left < 10:
            exhausted = True
            break
        # 最后一轮 / 预算见底：强制作答，不允许再调工具，避免无限循环
        last = round_no == MAX_ROUNDS - 1
        force_answer = last or left < 18
        msg = await glm_chat_tools(
            messages, TOOLS,
            temperature=0.5,
            # 单轮超时也不能超过剩下的预算，否则一轮就把网关等穿
            timeout=min(TOOL_TIMEOUT, max(8.0, left - 3)),
            tool_choice="none" if force_answer else "auto",
        )
        if not msg:
            logger.info("Agent 第 %d 轮拿不到模型响应，中止并降级。", round_no + 1)
            return "", trace

        calls = msg.get("tool_calls") or []
        if not calls:
            plan = (msg.get("content") or "").strip()
            if plan:
                return plan, trace
            # 有响应但没内容也没工具调用 —— 当作不可用
            return "", trace

        messages.append(msg)
        for c in calls:
            fn = (c.get("function") or {})
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:                   # 模型偶尔会给出坏 JSON
                args = {}
            result, summary = await _run_tool(name, args, ctx)
            trace.append({"tool": name, "args": args,
                          "ok": "error" not in result, "summary": summary})
            messages.append({
                "role": "tool",
                "tool_call_id": c.get("id") or name,
                "content": json.dumps(result, ensure_ascii=False)[:6000],
            })

    # 预算耗尽 / 用满轮次仍未收敛：交给调用方降级（返回空串 = 用本地规则排）
    if exhausted:
        logger.warning("Agent 用满 %.0f 秒预算仍未收敛，降级到本地计划（避免网关 504）", BUDGET_S)
        return "", trace

    # 走到这里说明 MAX_ROUNDS 用尽仍未收敛，兜底再说一次（不带工具）。
    # 这次也**不能超过预算**，否则前面省下的时间会在这里一次性花光。
    left = BUDGET_S - (time.monotonic() - t0)
    if left < 8:
        logger.warning("最终作答只剩 %.0f 秒，降级到本地计划", left)
        return "", trace
    plan = await glm_chat(messages, temperature=0.5,
                          timeout=min(FINAL_TIMEOUT, left), max_tokens=1600)
    return (plan or "").strip(), trace

# ══════════════════════════════════════════════════════════════════
#   中文钟点 + 「往时间表里加一条」（2026-10-20 从开发线移植）
# ══════════════════════════════════════════════════════════════════
#
# 原句：「周六18-19点上钢琴课，排2周」→ 收到**一整天**学习计划
# （钢琴课练习 / 复习高等数学 / 预习线性代数 / 自习）。
# 根因：模型手上**没有写课表的工具**，听懂了也落不了库，只能退回「排计划」。
# 所以后端认出这种结构化句子直接写库，一秒结束，不用赌模型听不听话。
#
# 判据是句子长什么样，不是关键词：**星期 + 钟点区间 + 一件事**，三条齐了才算。
# 「周六18-19点复习高数」有强学习动词 → 仍然是学习计划，不抢。

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(tok: str) -> int:
    """「3」「两」「十一」「二十三」→ 整数；认不出返回 0。"""
    tok = (tok or "").strip()
    if tok.isdigit():
        return int(tok)
    if tok in _CN_NUM:
        return _CN_NUM[tok]
    m = re.fullmatch(r"([一二三四五六七八九]?)十([一二三四五六七八九]?)", tok)
    if m:
        tens = _CN_NUM.get(m.group(1), 1) if m.group(1) else 1
        return tens * 10 + (_CN_NUM.get(m.group(2), 0) if m.group(2) else 0)
    return 0


_NUM_TOKEN = r"(\d+(?:\.\d+)?|[一两二三四五六七八九十]{1,3})"
# 「每天最多两小时」「别超过一小时」「两小时以内」
_CAP_PAT = re.compile(
    r"(?:每天|每日|一天|每晚|天天)?\s*"
    r"(?:最多|至多|不超过|别超过|不要超过|不能超过|控制在)\s*"
    + _NUM_TOKEN + r"\s*个?\s*(?:小时|钟头)")
_CAP_WITHIN = re.compile(_NUM_TOKEN + r"\s*个?\s*(?:小时|钟头)\s*(?:以内|之内|以下)")

# 「晚上不要排到十一点以后」「别超过十点」「十一点以后不要排」
_LATEST_1 = re.compile(r"(?:不要|别|不能|不)\s*(?:再)?\s*排到?\s*" + _NUM_TOKEN + r"\s*点")
_LATEST_2 = re.compile(r"(?:不要|别)\s*超过\s*" + _NUM_TOKEN + r"\s*点")
_LATEST_3 = re.compile(_NUM_TOKEN + r"\s*点\s*(?:以后|之后)\s*(?:不要|别|不)\s*(?:再)?\s*排")
# 「十点前收工」「十一点之前结束」
_LATEST_4 = re.compile(_NUM_TOKEN + r"\s*点\s*(?:前|之前|以前)\s*(?:收工|结束|睡|停|不学|打住|关电脑)")


def _cn_clock(tok: str, frac: str) -> int:
    """「八」+「半」→ 8*60+30；认不出返回 -1。"""
    h = _cn_to_int(tok)
    if not 0 <= h <= 24:
        return -1
    if not frac:
        mi = 0
    elif frac == "半":
        mi = 30
    else:
        mi = _cn_to_int(frac)
    if not 0 <= mi < 60:
        return -1
    return h * 60 + mi


def _fix_clock_range(a_min: int, b_min: int, s: str):
    """把口语钟点落到一天里的真实时刻。两个注意点都要修：

    ① **跨中午**：「十二点半到一点半」= 12:30 → **13:30**。
       按字面算 b(1:30) < a(12:30)，区间不成立，原来直接判"没有时间窗" → 配方丢。
    ② **晚上语境**：「晚上七点半到九点半」= **19:30-21:30**，不是早上。
       用户不会把"晚上"两个字白说；不修的话，模型可能真排到 07:30。

    下午同理（「下午一点到三点」= 13:00-15:00）。没有时段词就原样返回。
    """
    if b_min <= a_min:                       # 跨中午 / 跨整点
        cand = b_min + 12 * 60
        if cand > a_min:
            b_min = cand
    if re.search(r"晚上|今晚|明晚|后晚|昨夜|夜里|夜间|傍晚|睡前", s):
        if 1 * 60 <= a_min < 12 * 60 and b_min <= 12 * 60:
            a_min += 12 * 60
            b_min += 12 * 60
    elif re.search(r"下午|午后", s):
        if 1 * 60 <= a_min < 7 * 60 and b_min <= 12 * 60:
            a_min += 12 * 60
            b_min += 12 * 60
    return a_min, b_min


_SA_WD_IDX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# 「周六」「下周三」「这周六」「星期日」「礼拜天」——也接**一串**：周二四 / 周一三五。
# ★ 2026-10-20：「每周二四8-9点晨跑」原来只吃掉「周二」，剩下一个「四」跟进了名字
#   （名字成了「四 晨跑」），日期也只算了周二。改成吃一串之后，名字干净、日期两天都有。
_SA_WD_RE = re.compile(r"(?<![一两几])(下下|下|这|本)?\s*个?\s*(?:周|星期|礼拜)\s*([一二三四五六日天]+)")

# 「排2周」「连续两周」「共 3 周」
# ★ 只认「数字 + 周」，「周六」「下周三」不会被误吃（数字必须在"周"前面）。
# ★ 前面的动词要**一起吃掉**，否则名称会剩一个「排」字（实测：「钢琴课 排」）。
#   长的写在前面：「连续」「重复」若排在「连」后面，只会吃掉「连」，剩「续」。
_SA_WEEKS_RE = re.compile(
    r"(?:连续|重复|持续|继续|再排|排|连)?\s*"
    r"([0-9]{1,2}|[一两二三四五六七八九十]{1,3})\s*个?\s*周")

# 三种时间写法，按精确度从高到低试
_SA_HHMM_RE = re.compile(
    r"(\d{1,2})\s*[:：]\s*(\d{1,2})\s*[-~—–－至到]\s*(\d{1,2})\s*[:：]\s*(\d{1,2})")
_SA_NUMDOT_RE = re.compile(
    r"(\d{1,2})\s*点?\s*(半)?\s*[-~—–－至到]\s*(\d{1,2})\s*点\s*(半)?")
_SA_CN_RE = re.compile(
    r"([零一二三四五六七八九十两]{1,3})\s*点\s*(半)?\s*[-~—–－至到]\s*"
    r"([零一二三四五六七八九十两]{1,3})\s*点\s*(半)?")

# 强学习动词：命中说明这是"学习安排"，交给计划流程。
# ★ 刻意不用 `_STUDY_ACTIONS` —— 它含「练 / 写 / 看 / 学」这些单字，
#   「练琴」「学吉他」「看话剧」都会被误伤，而那些恰恰是课外活动。
_SA_STUDY_WORDS = ("复习", "预习", "备考", "刷题", "冲刺", "突击", "做题",
                   "写作业", "背单词", "看网课", "上网课", "自习", "复盘",
                   "默写", "整理笔记", "赶作业")

# 名称提取时要抹掉的空壳词（时间/星期/周数由正则单独抹）
_SA_FILLERS = ("查一下", "看一下", "查查", "看看", "查下", "看下", "问一下",
               "记一下", "记下来", "记个", "帮我", "帮忙", "麻烦", "给我",
               "我要", "我想", "加个", "加一个", "加一节", "加一堂", "加两节",
               "一节", "一堂", "添加", "排进", "排到",
               "安排", "重复", "连续", "持续", "每个", "每周", "每", "一次", "共")

_SA_TIME_WORDS = ("早上", "上午", "中午", "下午", "傍晚", "晚上", "夜里", "凌晨")
# 「明天下午3-5点体检」里，日期词也得从名字里剥掉，否则名字会变成「明天下午 体检」
_SA_DAY_WORDS = ("大后天", "后天", "明早", "明晚", "明天", "明日", "明儿",
                 "今晚", "今早", "今天", "今日", "今儿")
_SA_VERB_HEAD = ("上", "去", "参加", "有", "在", "到", "约", "开始", "弄")

# 名称不能是这些 —— 它们说明"是什么事"没说出来（「周六18-19点有课」）
_SA_NAME_BAD = {"课", "上课", "有课", "一节课", "节课", "活动", "事情", "事", "安排"}


def _sa_clock_range(s: str):
    """从这句话里认出钟点区间，返回 (起分钟, 止分钟)。认不出返回 None。"""
    def _hm(h, m):
        return int(h) * 60 + int(m or 0)

    m = _SA_HHMM_RE.search(s)
    if m:
        a, b = _hm(m.group(1), m.group(2)), _hm(m.group(3), m.group(4))
        return _fix_clock_range(a, b, s)

    m = _SA_NUMDOT_RE.search(s)
    if m:
        a = int(m.group(1)) * 60 + (30 if m.group(2) else 0)
        b = int(m.group(3)) * 60 + (30 if m.group(4) else 0)
        return _fix_clock_range(a, b, s)

    m = _SA_CN_RE.search(s)
    if m:
        a = _cn_clock(m.group(1), m.group(2) or "")
        b = _cn_clock(m.group(3), m.group(4) or "")
        if a >= 0 and b >= 0:
            return _fix_clock_range(a, b, s)

    return None


def _sa_clean_name(s: str) -> str:
    """把时间/星期/周数/动词都抹掉，剩下那个"要干什么"。"""
    t = s
    for rx in (_SA_HHMM_RE, _SA_NUMDOT_RE, _SA_CN_RE, _SA_WD_RE, _SA_WEEKS_RE):
        t = rx.sub(" ", t)
    for w in _SA_FILLERS:
        t = t.replace(w, " ")
    t = re.sub(r"[，,。.、；;:：！!？?（）()\[\]【】\s]+", " ", t).strip()
    # 开头的状语和动词要一层层剥（"早上" 剥完可能露出"上"）
    changed = True
    while changed:
        changed = False
        for w in _SA_DAY_WORDS + _SA_TIME_WORDS + _SA_VERB_HEAD:
            if t.startswith(w) and len(t) > len(w):
                t = t[len(w):].strip()
                changed = True
    t = re.sub(r"(?:吧|啊|呢|了|呀|哈|嘛|的)+$", "", t).strip()
    return t


def _sa_weeks(s: str) -> int:
    """重复几周。没写就当 1 周（只排最近这一次），最多 30 周。"""
    m = _SA_WEEKS_RE.search(s or "")
    if not m:
        return 1
    return max(1, min(30, _cn_to_int(m.group(1)) or 1))


def parse_schedule_add(goal: str):
    """这句话是不是「往时间表里加一条固定安排」；是就返回结构化结果。

        「周六18-19点上钢琴课，排2周」
            -> {"weekday": 5, "start": "18:00", "end": "19:00",
                "name": "钢琴课", "weeks": 2}
        「下周三下午2点到4点开班会」
            -> {"weekday": 2, "start": "14:00", "end": "16:00",
                "name": "开班会", "weeks": 1}

    返回 None 的三种情况（都要回落成「先问一句」，绝不硬排）：
        · 缺星期 / 缺钟点      ——「周六复习高数」没给时间，那是计划
        · 有强学习动词          ——「周六18-19点复习高数」是学习安排
        · 名称抽不干净          ——「周六18-19点有课」说不清是什么课

    ★ 不接 `date` 参数：这个函数只判"是不是这件事、是什么事"，
      具体落到哪几天由 `schedule_add_dates` 算 —— 纯函数才好测。
    """
    s = (goal or "").strip()
    if not s or len(s) > 80:
        return None

    # ★★ 2026-10-20：除了「周X」，也要认「今天 / 明天 / 后天 / 大后天」。
    #    实测「明天下午3-5点体检」被判成"说不清哪天"，整句落给模型又变成排计划。
    base_off = None
    # ★ 2026-10-20：「明早八点到九点背单词」原来漏了 —— 表里只有「明天」，没有「明早/明晚」，
    #   于是 base_offset 取不到 → 整句落到模型排计划。
    for _kw, _off in (("大后天", 3), ("后日", 2), ("后天", 2),
                      ("明早", 1), ("明晚", 1), ("明天", 1), ("明日", 1), ("明儿", 1),
                      ("今晚", 0), ("今早", 0), ("今天", 0), ("今日", 0), ("今儿", 0)):
        if _kw in s:
            base_off = _off
            break

    mw = _SA_WD_RE.search(s)
    wds = []
    week_pre = ""
    if mw:
        week_pre = mw.group(1) or ""
        for _ch in mw.group(2):
            _i = _SA_WD_IDX.get(_ch)
            if _i is not None and _i not in wds:
                wds.append(_i)
        wd = wds[0] if wds else None
    elif base_off is not None:
        wd = base_off                 # 占位：真正的日期在 schedule_add_dates 里按偏移算
    else:
        return None
    if wd is None:
        return None
    rng = _sa_clock_range(s)
    if not rng:
        return None
    if any(w in s for w in _SA_STUDY_WORDS):
        return None
    name = _sa_clean_name(s)
    if len(name) < 2 or len(name) > 16 or name in _SA_NAME_BAD:
        return None
    if not re.search(r"[\u4e00-\u9fa5A-Za-z]", name):
        return None
    a, b = rng
    if not (0 <= a < b <= 24 * 60) or b - a < 10:
        return None                      # 少于 10 分钟的多半不是真安排
    spec = {
        "weekday": wd,
        "start": "{:02d}:{:02d}".format(a // 60, a % 60),
        "end": "{:02d}:{:02d}".format(b // 60, b % 60),
        "name": name,
        "weeks": _sa_weeks(s),
    }
    if base_off is not None and not mw:
        spec["base_offset"] = base_off
    if len(wds) > 1:
        spec["weekdays"] = wds          # 周二四 / 周一三五
    if week_pre:
        spec["week_pre"] = week_pre     # 下 / 下下 / 这 / 本
    return spec


def schedule_add_dates(spec: dict, today: _date) -> list:
    """把「周六，排 2 周」落到**具体日期**。

    第一个日期取**最近的未来那个星期几**（含今天），之后每 7 天一次。
    今天正好是周六就算今天 —— 输入里说"周六"时人就在这一天，没必要等到下周。

        today=2026-10-20（周二）, spec={weekday:5, weeks:2}
            -> [2026-10-24, 2026-10-31]
    """
    def _first_of(wd: int) -> _date:
        """某个星期几的**第一次**。★ 必须认「下 / 下下 / 这」——
        原来一律按"最近的未来那个星期几"算，于是「下周三开组会」被写到了**明天**。"""
        pre = spec.get("week_pre") or ""
        next_mon = today + timedelta(days=7 - today.weekday())
        if pre == "下下":
            return next_mon + timedelta(days=7 + wd)
        if pre == "下":
            return next_mon + timedelta(days=wd)
        if pre in ("这", "本"):
            d = today - timedelta(days=today.weekday()) + timedelta(days=wd)
            return d if d >= today else d + timedelta(days=7)
        return today + timedelta(days=(wd - today.weekday()) % 7)

    if spec.get("base_offset") is not None:
        # 「明天 / 后天」这种给的是具体哪天，不再按星期几往后找
        first = today + timedelta(days=int(spec["base_offset"]))
    elif spec.get("weekdays"):
        n_each = max(1, min(30, int(spec.get("weeks") or 1)))
        out = []
        for wd in spec["weekdays"]:
            base = _first_of(int(wd) % 7)
            out += [base + timedelta(days=7 * i) for i in range(n_each)]
        return sorted(set(out))
    else:
        first = _first_of(int(spec.get("weekday") or 0) % 7)
    n = max(1, min(30, int(spec.get("weeks") or 1)))
    return [first + timedelta(days=7 * i) for i in range(n)]
