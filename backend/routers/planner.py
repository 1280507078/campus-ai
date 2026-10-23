"""学业规划助手：时间槽查询 + 课程/活动 CRUD（按账号关联）+ 沿用上周 + AI伴学。

时间维度用真实日期 date（"YYYY-MM-DD"），因为前端要同时浏览本周及后两周共三周，
同一个「周三第三节」在三周里是三组不同的数据。旧的 day(1..7) 字段仅作历史兼容。
"""
import base64
import json
import logging
import re
from datetime import date as ddate, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# 识别链路里有几处「自动判定」的结果值得留痕（比如 cells 的对齐偏移），
# 出问题时能直接在 uvicorn 控制台看到判到了多少，不用再靠猜。
logger = logging.getLogger(__name__)

from database import get_db
from models.orm import User, Course, TimeSlot
from models.schemas import (TimetableUpdate, InheritRequest, PlanRequest,
                            PlanResponse, TimeSlotOut, CourseIn,
                            ImportImageIn, ImportImageOut, ImportedCourse,
                            ImportSheetIn, ImportSheetOut)
from routers.auth import get_current_user
from services.study_agent import (run_study_agent, ask_coach, local_coach, ask_docs,
                                  _is_explain, _is_docs_question, _course_on_date,
                                  parse_date_range, parse_time_recipe,
                                  parse_schedule_add, schedule_add_dates,
                                  _week_number, SEM_TOTAL_WEEKS,
                                  quick_kind, quick_answer, _run_tool)
from services.timetable_excel import parse_timetable_excel, parse_period
from services import usage as _usage
from services.llm_json import (
    extract_json as _extract_json,
    salvage_headers as _salvage_headers,
    salvage_objects,
    weekday_from_header as _weekday_from_header,
)
from services.vision import recognize_timetable

router = APIRouter(dependencies=[Depends(get_current_user)])


def _slot_out(s: TimeSlot) -> TimeSlotOut:
    return TimeSlotOut(
        order_no=s.order_no, label=s.label,
        start=s.start_time.strftime("%H:%M"), end=s.end_time.strftime("%H:%M"),
        period_type=s.period_type, region=s.region or "both",
        start_south=s.start_south or "", end_south=s.end_south or "",
        start_north=s.start_north or "", end_north=s.end_north or "",
    )


def _course_out(c: Course) -> dict:
    return {
        "id": c.id, "date": c.date, "type": c.type or "course",
        "slot_order": c.slot_order or 0,
        "start": c.start_time, "end": c.end_time, "name": c.name,
        "teacher": c.teacher, "location": c.location, "building": c.building,
        "weeks": c.weeks or "",           # 上课周次原始串，空 = 每周都上
        "lng": c.lng, "lat": c.lat,
    }


@router.get("/slots", response_model=list[TimeSlotOut])
def get_slots(db: Session = Depends(get_db)):
    """返回 6:00-23:00 的空白时间表槽位（含南北区作息，前端据此渲染课表网格）。"""
    slots = db.query(TimeSlot).order_by(TimeSlot.order_no).all()
    return [_slot_out(s) for s in slots]


@router.get("/timetable")
def get_timetable(start: str | None = None, end: str | None = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """按日期区间读取课程+活动。不传区间则返回全部（兼容旧调用）。"""
    q = db.query(Course).filter(Course.user_id == user.id)
    if start:
        q = q.filter(Course.date >= start)
    if end:
        q = q.filter(Course.date <= end)
    rows = q.order_by(Course.date, Course.slot_order).all()
    return {"courses": [_course_out(c) for c in rows]}


def _write_courses(db: Session, user: User, courses: list[CourseIn]) -> int:
    """写入课程/活动。一个 (date, slot_order) 只保留一条，后来者覆盖前面的。"""
    written = 0
    seen: set[tuple[str, int]] = set()
    for c in courses:
        key = (c.date, c.slot_order or 0)
        if key in seen:
            continue          # 同一时间点的重复提交，只留第一条
        seen.add(key)
        db.add(Course(
            user_id=user.id, date=c.date, type=c.type or "course",
            slot_order=c.slot_order or 0,
            start_time=c.start, end_time=c.end, name=c.name,
            teacher=c.teacher, location=c.location, building=c.building,
            weeks=c.weeks or "",
            lng=str(c.lng) if c.lng is not None else "",
            lat=str(c.lat) if c.lat is not None else "",
        ))
        written += 1
    return written


# ══════════════════════════════════════════════════════════════════
#      「往时间表里加一条」—— 后端直接落库，不经过模型
# ══════════════════════════════════════════════════════════════════
#
# 「周六18-19点上钢琴课，排2周」→ 收到一整天学习计划
# （钢琴课练习 / 复习高数 / 预习线代 / 自习），而他要的只有一行。
#
# 为什么不交给模型做：工具表里**没有"写课表"的工具**，模型就算听懂了也落不了库，
# 只能退回它熟悉的"排计划"。与其给它加工具再赌它这一轮肯调用，不如后端认出
# 这种结构化的句子直接写 —— 「周六 + 18-19点 + 一件事」本来就没什么歧义。
#
# 解析在 services/study_agent.py 的 `parse_schedule_add`（纯函数，单测覆盖）。

_WD_CN = "一二三四五六日"

# 「这句话问的是课表吗」—— `_local_answer` 只在命中时才用课表作答。
# 否则「明天带伞吗」也会被回一句"10-21 没有课"，又是一次答非所问。
_LOCAL_ASK_HINTS = ("课", "时间表", "课表", "空档", "空闲", "有空", "安排", "自习")


def _hm_to_min(t) -> int | None:
    """"18:00" -> 1080；解析不出来返回 None。"""
    try:
        h, m = str(t or "").strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _slot_for_time(db: Session, start: str) -> int:
    """18:00 这种自定时间，落到时间表的哪一格。

    ★ 必须落在**真实存在的节次**上：前端是按 `slot_order === 节次.order_no`
      逐格渲染的，`0` 不在任何格子里 —— 写进去页面上一条都看不到，
      而且不报错（自己遇到过这种情况，所以这里宁愿多查一次）。
      优先取"开始时间落在这节里"，都落不进就取时间最接近的一节。
    """
    from models.orm import TimeSlot
    slots = db.query(TimeSlot).order_by(TimeSlot.order_no).all()
    if not slots:
        return 1
    t = _hm_to_min(start)
    if t is None:
        return slots[0].order_no
    best, best_d = slots[0].order_no, None
    for s in slots:
        a = _hm_to_min(s.start_time.strftime("%H:%M") if s.start_time else "")
        b = _hm_to_min(s.end_time.strftime("%H:%M") if s.end_time else "")
        if a is None or b is None:
            continue
        if a <= t < b:
            return s.order_no
        d = abs(t - a)
        if best_d is None or d < best_d:
            best, best_d = s.order_no, d
    return best


def _apply_schedule_add(db: Session, user: User, goal: str, today):
    """把「周六18-19点上钢琴课，排2周」真正写进时间表。

    Returns:
        (handled, text, trace)。`handled=False` 表示这句话不是加课，交给后面的流程。
        `handled=True` 时一定会给一段说明，**不会再排计划**（哪怕是冲突/学期外）。
    """
    spec = parse_schedule_add(goal)
    if not spec:
        return False, "", []

    days = schedule_add_dates(spec, today)
    wks = [_week_number(d) for d in days]
    if any(w is None for w in wks):
        return True, (
            f"这句是「{spec['name']}」的固定安排，我认出来了，"
            f"但 {days[0].isoformat()} 不在本学期（第 1–{SEM_TOTAL_WEEKS} 周）里。\n"
            "时间表只显示学期内的安排，所以这次没有写进去。"
        ), []

    slot = _slot_for_time(db, spec["start"])
    anchor = days[0]
    clash = (db.query(Course)
             .filter(Course.user_id == user.id,
                     Course.date == anchor.isoformat(),
                     Course.slot_order == slot).first())
    if clash:
        return True, (
            f"{anchor.strftime('%m-%d')}（周{_WD_CN[anchor.weekday()]}）"
            f"这一格已经有「{clash.name}」了，同一格只能放一条。\n"
            "换个时间，或者先去时间表把那条删掉，再说一次。"
        ), []

    # ★ 只写**一条**，用 weeks 表达"哪几周上" —— 这是时间表本来的口径
    #   （一条记录 = 某个星期几 + 一组周次），逐日期写会变成 N 条重复记录，
    #   之后用户在时间表里改一次，另外几条不会跟着变。
    lo, hi = min(wks), max(wks)
    weeks_txt = str(lo) if lo == hi else f"{lo}-{hi}"
    db.add(Course(
        user_id=user.id, date=anchor.isoformat(), type="course", slot_order=slot,
        start_time=spec["start"], end_time=spec["end"], name=spec["name"],
        teacher="", location="", building="",
        weeks=weeks_txt, lng="", lat="",
    ))
    db.commit()

    lines = [f"已写进时间表：{spec['name']}，{spec['start']}–{spec['end']}。", ""]
    for d, w in zip(days, wks):
        lines.append(f"· {d.strftime('%m-%d')}（周{_WD_CN[d.weekday()]}，第 {w} 周）")
    lines += [
        "",
        f"一共 {len(days)} 次。要改时间或删掉，去「学习规划 → 时间表」点那一格。",
    ]
    trace = [{
        "tool": "add_schedule",
        "args": {"name": spec["name"], "start": spec["start"], "end": spec["end"],
                 "weeks": spec["weeks"], "dates": [d.isoformat() for d in days]},
        "ok": True,
        "summary": f"写入时间表：{spec['name']} {spec['start']}-{spec['end']}，"
                   f"共 {len(days)} 次",
    }]
    return True, "\n".join(lines), trace


# ★★ 「每天 / 天天 / 每晚」不是某个具体日子，是**一整周**。
#
# 为什么只在问课时接管、不放进 `parse_date_range`：
#   那句话在排计划那条路上也生效，而「排 3 天，每天 2 小时」里的「每天」
#   说的是**作息频率**，不是"排 7 天" —— 放进去会把用户点名的 3 天顶成 7 天。
_WEEK_ALL = re.compile(r"每天|天天|每晚|每日")

# 相对说法 → 偏移天数。只用来在回答里**回显**输入里说的是哪天。
#
# ★ 为什么要回显（2026-10-20 「一问后天就不行了」）：
#   今天是周二，「后天」和「周四」**本来就是同一天**（都是 10-22）。
#   只答「10-22（周四）没有课。」时，用户没法判断它是真把「后天」算对了，
#   还是把上一句的答案又贴了一遍 —— 看着就是"没听懂"。
#   而且上一句如果是模型答的（会把查的那天说成"今天"），两句放一起更乱。
#   所以：用相对说法问的，回答里把那个词原样带上。
_REL_WORDS = (
    (("大后晚", "大后天"), 3),
    (("后晚", "后天", "后日"), 2),
    (("明晚", "明早", "明天", "明日", "明儿"), 1),
    (("今天", "今日", "今晚", "今早", "今儿"), 0),
    (("昨晚", "昨儿", "昨天", "昨日"), -1),
    (("前晚", "前天", "前日"), -2),
)


def _course_in(goal: str, courses: list) -> str:
    """这句话在问课表里的哪门课？没有就返回空串。

    ★★ 「高数在哪上」「计网几点上」以前一律交给模型 ——
       课表就在手上，按课名查一下就有一行答案。

    ★ 两个注意点：
      1) 用户说**简称**：课表里「高等数学」，他打「高数」；「计算机网络」-> 「计网」。
         所以不能只做 `name in goal`，要按**子序列**比（高…数是高等数学的子序列）。
      2) 中文句子里没有空格，整句是一个连续串 —— 不能把整串当候选，
         要在句子里**滑窗取子串**当候选，从长到短试。
    ★ 再加一道闸：句子里必须带"问课"的迹象（在哪/几点/上课/有课/节…），
      否则「大学有几个楼」会因为「大学」二字误命中「大学英语」。
    """
    g = re.sub(r"[^\u4e00-\u9fa5A-Za-z]", "", goal or "")
    _asked = bool(re.search(r"在哪|哪里|几点|什么时候|上课|有课|没课|上不上|课表|课程|节|教室|几次", g))
    # 「明天有高数吗」这种没有"有课"两个字，但有「有 … 吗」的疑问形。
    # ⚠️ 不能只看「有」——「大学有几个楼」也有「有」，会误命中「大学英语」。
    if not _asked and re.search(r"[吗呢？?]", goal or "") and "有" in g:
        _asked = True
    if not g or not _asked:
        return ""
    best, best_len = "", 0
    for c in courses or []:
        name = (c.get("name") or "").strip()
        if len(name) < 2:
            continue
        for L in range(min(len(name), len(g)), 1, -1):
            if L <= best_len:
                break
            hit = False
            for k in range(len(g) - L + 1):
                tok = g[k:k + L]
                it = iter(name)
                if all(ch in it for ch in tok):
                    hit = True
                    break
            if hit:
                best, best_len = name, L
                break
    return best


def _rel_label(goal: str, d, today) -> str:
    """用户是用相对说法（明天/后天…）问的，就把那个词取出来；否则空串。"""
    off = (d - today).days
    # 「明后天」是一句话点了两天：第一天叫明天，第二天叫后天
    if "明后" in (goal or ""):
        if off == 1:
            return "明天"
        if off == 2:
            return "后天"
    for words, o in _REL_WORDS:
        if o != off:
            continue
        for w in words:
            if w in (goal or ""):
                return w
    return ""


def _local_ask(courses: list, goal: str, today, force: bool = False):
    """本地答「哪天有没有课」这类问题。

    Returns:
        (text, trace)；这句话跟课表无关时返回 None（交给模型去答）。

    ★ 他问「周四有没有课」，AI 超时后走了兜底，
      兜底**无条件**排了一份「未来 1 天安排」，还顶着「大学生没有排课的一天」
      的通用作息（上午专业课、下午英语…）。用户要的只有一句"没有课"。
      这类问题根本不需要模型：课表就在手上，算一下就有答案。

    ⚠️ 但这条路**只在问课表时**用。问「明天带伞吗」也判成 ask，
      拿课表去答就是又一次答非所问 —— 所以先看这句话跟课表有没有关系。
    """
    # ★ force=True 时跳过这道"这句子跟课表有关吗"的粗筛：
    #   `_local_ask_first` 已经用更细的 `_LOCAL_CLASS_ASK` 判过了。
    #   不跳的话，「明天上几门」「哪天有空」这种一个"课"字都没有的说法会被挡掉
    #   —— 明明口径已经认出来了，却又在下一关被拒。
    if not force and not any(h in (goal or "") for h in _LOCAL_ASK_HINTS):
        return None
    # ★ 问课这条路允许过去的日子：「昨天有课吗」「前天那节课」都要能答。
    #   （排计划那条路不允许 —— 见 parse_date_range 的 allow_past）
    rng = parse_date_range(goal, today, allow_past=True)
    # ★ 没点日期、但问的是"这几天哪天空 / 课表"，就按**这周剩下的几天**答。
    #   只答今天会漏掉后面几天的课（「查课表」尤其明显）。
    #   ⚠️ 这段必须在 _local_ask 里：_local_ask_first 算出的 rng 不会传进来，
    #      只写在那边的话，「哪天没课」还是只答一天（出现过）。
    if not rng and re.search(r"课表|课程表|哪天空|哪天没课|哪天有课|哪天有空|什么时候空",
                             goal or ""):
        rng = (today, max(1, 7 - today.weekday()))
    if rng:
        start, n = rng
        days = [start + timedelta(days=i) for i in range(max(1, min(7, n)))]
    elif _WEEK_ALL.search(goal or ""):
        days = [today + timedelta(days=i) for i in range(7)]
    else:
        days = [today]

    named = [c for c in courses if (c.get("name") or "").strip()]

    # ★★ 句子里点名了某门课 -> 直接答那门课（不要列别的课）。
    #   ★ 补：课名 + **具体日期**（「周四高数在哪上」「明天有高数吗」）时，
    #     只在他说的那几天里找；没说日期才按"今天起 7 天"扫一遍。
    _hit = _course_in(goal, courses)
    if _hit:
        _rng = parse_date_range(goal, today, allow_past=True)
        if _rng:
            _s0, _n0 = _rng
            _scope = [_s0 + timedelta(days=_k) for _k in range(max(1, min(7, _n0)))]
        else:
            _scope = [today + timedelta(days=_k) for _k in range(7)]
        items = []
        for _d in _scope:
            for _c in named:
                if (_c.get("name") or "") == _hit and _course_on_date(_c, _d):
                    items.append((_d, _c))
        _tr2 = [{"tool": "get_timetable",
                 "args": {"date_from": _scope[0].isoformat(),
                          "date_to": _scope[-1].isoformat()},
                 "ok": True, "summary": f"读到 {len(named)} 条课程/活动"}]
        _span = (f"{_scope[0].strftime('%m-%d')}（周{_WD_CN[_scope[0].weekday()]}）"
                 if len(_scope) == 1 else
                 f"{_scope[0].strftime('%m-%d')} ~ {_scope[-1].strftime('%m-%d')}")
        if not named:
            return (f"你的时间表还是空的，查不到「{_hit}」—— "
                    f"去「学习规划 → 时间表」导入一下再来问。"), _tr2
        if not items:
            return f"{_span} 没有「{_hit}」这门课。", _tr2
        _parts = []
        for _d, _c in items[:4]:
            _p = (f"周{_WD_CN[_d.weekday()]}（{_d.strftime('%m-%d')}）"
                  f"{_c.get('start', '')}-{_c.get('end', '')}")
            if _c.get("location"):
                _p += f"，{_c['location']}"
            _parts.append(_p)
        if len(_scope) == 1:
            return f"{_hit} 在 {_parts[0]}。", _tr2
        _tail = "（后面还有）" if len(items) > 4 else ""
        return f"{_hit}：" + "；".join(_parts) + "。" + _tail, _tr2

    # ★★★ 「到几点 / 第一节 / 在哪上」问的是**一个数或一个地方**，
    #    这时候把整天课表列一遍很笨 —— 他要的是"最后一节 15:40 下课"。
    _ask_end = re.search(r"课上到|上到几点|到几点|几点下课|几点放学|最后一节", goal or "")
    _ask_first = re.search(r"第一节|几点开始上课|最早", goal or "")
    _ask_where = re.search(r"在哪上|在哪里上|在哪个教室|哪个楼|教室在哪", goal or "")
    # ★★ 问"几节课 / 课满吗"也别甩列表 —— 一行报完：
    #    明天 3 门课：08:10 高数、10:05 计网、14:00 实验。
    _ask_count = re.search(r"几节|几门|上几门|课多|课满|满吗|课紧", goal or "")
    if len(days) == 1 and (_ask_end or _ask_first or _ask_where or _ask_count) and named:
        d0 = days[0]
        hits = sorted([c for c in named if _course_on_date(c, d0)],
                      key=lambda c: str(c.get("start") or ""))
        tag = f"{d0.strftime('%m-%d')}（周{_WD_CN[d0.weekday()]}）"
        _tr = [{"tool": "get_timetable",
                "args": {"date_from": d0.isoformat(), "date_to": d0.isoformat()},
                "ok": True, "summary": f"读到 {len(named)} 条课程/活动"}]
        if not hits:
            return f"{tag} 没有课。", _tr
        more = f"（当天共 {len(hits)} 门）" if len(hits) > 1 else ""
        if _ask_end:
            last = hits[-1]
            return (f"{tag} 最后一节是 {last.get('name', '')}，"
                    f"{last.get('end', '')} 下课。{more}"), _tr
        if _ask_first:
            first = hits[0]
            return (f"{tag} 第一节是 {first.get('name', '')}，"
                    f"{first.get('start', '')} 开始。{more}"), _tr
        if _ask_count:
            one = "、".join(f"{c.get('start', '')} {c.get('name', '')}" for c in hits)
            return f"{tag} 共 {len(hits)} 门课：{one}。", _tr
        place = "；".join(f"{c.get('name', '')} {c.get('location') or '（没填教室）'}"
                          for c in hits)
        return f"{tag} 上课地点：{place}。{more}", _tr

    # ★★ 「这周哪天空 / 哪天没课」问的是**哪几天**，
    #    逐天罗列虽然不算错，但只算半个答案 —— 用户还得自己数哪几天是"没有课"。
    #    这里直接归纳成一句：「没课的有：周三、周六、周日」。
    if re.search(r"哪天空|哪天没课|哪天有空|什么时候空|哪天休息|哪天闲", goal or ""):
        span = f"{days[0].strftime('%m-%d')} ~ {days[-1].strftime('%m-%d')}"
        if not named:
            return (f"（{span}）你的时间表还是空的，所以每天都没课 —— "
                    f"课其实是有的，只是还没导入。"), [{
                "tool": "get_timetable", "args": {"date_from": days[0].isoformat(),
                                                  "date_to": days[-1].isoformat()},
                "ok": True, "summary": "读到 0 条课程/活动"}]
        free = [d for d in days if not any(_course_on_date(c, d) for c in named)]
        busy = [d for d in days if d not in free]
        if not free:
            return f"（{span}）这几天每天都有课，没有整天空的一天。", []
        txt = (f"（{span}）没课的有："
               + "、".join(f"周{_WD_CN[d.weekday()]}（{d.strftime('%m-%d')}）" for d in free)
               + "。")
        if busy:
            txt += ("其余 " + "、".join(f"周{_WD_CN[d.weekday()]}" for d in busy)
                    + " 有课。")
        return txt, [{
            "tool": "get_timetable", "args": {"date_from": days[0].isoformat(),
                                              "date_to": days[-1].isoformat()},
            "ok": True, "summary": f"读到 {len(named)} 条课程/活动"}]

    lines = []
    # 用相对说法问的，先把"今天是哪天"摆在最上面。
    # 问题记录：口径不对时（例如记成今天是周四），一眼就能看出差在哪，
    # 不用再来回问一轮「后天到底是几号」。
    if days[0] != today and any(w in (goal or "")
                                for words, _o in _REL_WORDS for w in words):
        lines.append(f"（今天是 {today.strftime('%m-%d')}，"
                     f"周{_WD_CN[today.weekday()]}）")
    for d in days:
        hits = [c for c in named if _course_on_date(c, d)]
        lab = _rel_label(goal, d, today)
        tag = (f"{lab}（{d.strftime('%m-%d')}，周{_WD_CN[d.weekday()]}）" if lab
               else f"{d.strftime('%m-%d')}（周{_WD_CN[d.weekday()]}）")
        if not hits:
            lines.append(f"{tag} 没有课。")
            continue
        lines.append(f"{tag} 有 {len(hits)} 门：")
        for c in sorted(hits, key=lambda x: str(x.get("start") or "")):
            place = f"（{c['location']}）" if c.get("location") else ""
            lines.append(f"  · {c.get('start', '')}-{c.get('end', '')} "
                         f"{c.get('name', '')}{place}")
    if not named:
        lines += ["", "（你的时间表还是空的。课其实是有的，只是还没导入 —— "
                      "去「学习规划 → 时间表」加一下，我再按真实课表回答。）"]
    # trace 跟模型那条路保持同一个形状：页面上「调用记录」看起来一样，
    # 用户不用关心这句是本地算的还是模型答的（他也确实不需要关心）。
    trace = [{
        "tool": "get_timetable",
        "args": {"date_from": days[0].isoformat(), "date_to": days[-1].isoformat()},
        "ok": True,
        "summary": f"读到 {len(named)} 条课程/活动",
    }]
    return "\n".join(lines), trace


# ★★ 「问这节课」的窄口径。**不能**拿 _LOCAL_ASK_HINTS 当本地优先的判据：
#    那里面还有「空档 / 空闲 / 安排」，于是「这周哪天空」也会被本地接管，
#    而它要的是一句"哪几天没课"，本地那份"逐天列课"只算半个答案。
_LOCAL_CLASS_ASK = re.compile(
    r"有没有课|有课吗|有课么|有课没|有.{0,4}课吗|上不上课|几点上课|几点的课|"
    r"上什么课|什么课|哪几门|哪几节|几节课|几门课|有哪些课|有没有安排|"
    r"有安排吗|有没有活动|课表|"
    # ★★ 补：「查询本周五的课程」这类**看一眼课**的说法原来一个都不认 ——
    #    既没有「有没有课」，也没有「课表」，于是整句落到模型；模型按"排计划"办，
    #    回了「2026-10-23（周五）的学习计划」。用户只是想把那天的课列出来。
    #    ⚠️ 动词只收"看一眼"那一类（查/看/问/列），而且离「课」不超过 6 个字：
    #      「帮我排一下课程」「排一下周五的课」不会被抢走 —— 那是要排，不是要查。
    r"(?:查|查询|查一下|看|看看|看一下|问|问下|问一下|知道|列|列出).{0,6}课|"
    # ★★ 体检：一批"换种说法问课"的都漏了，落给模型就变成排计划。
    #    这里**只补具体说法**，不裸加「有课 / 上课」—— 那两个字太泛，
    #    「明天上课前把作业写完」这种也会被抢去答"哪天有课"。
    r"上课吗|要上课|用不用.{0,3}上课|去不去上课|第几节|"
    # ★ 扩口径：「明天有早八吗」「今晚有晚课吗」是**看课表**，
    #   不是排计划。原来这两句一个不认，落 plan 直接给了一天的作息表。
    r"早八|晚课|还剩几节|还有几节|剩几节|第一节是什么|今天几节课|"
    # ★ 评测 D 组暴露 ——「这周哪几天有课」原来判不出来（既没「有没有课」
    #   也没「课表」），整句落到模型就变成一份计划。这里补「哪几天 + 有课」这一族。
    r"哪几天?.{0,2}(?:有课|没课|上课|有空|没安排)|"
    r"课上到|上到几点|几点下课|几点放学|几点开始上课|"
    r"是不是有课|有课不|在哪上|在哪里上|在哪个教室|哪个楼|几天课|"
    # 「下周的课程安排」也是"要看"，不是"要排"——加「的」限定，
    # 免得把「帮我安排课程」（那确实是要排）抢过来。
    r"的课程安排|的课程表|的课表|的课|"
    r"没课吧|没课吗|没课不|空闲|空档|"
    # ★★ 第三轮：口语/省略式。需求：「口径大点」，这里按"能问课"补，
    #    但下面 _local_ask_first 里留了两道闸（要点了日期 / 不许带"安排"这类动词）。
    r"课多吗|课多不多|课多|课满|课紧|上几门|几门|第一节|最后一节|晚课|早课|晚自习|早自习|"
    r"有课吧|有课对吧|有课对不对|"
    r"哪天空|哪天没课|哪天有课|哪天有空|什么时候空"
)


def _local_ask_first(courses: list, goal: str, today):
    """「周四有没有课」这类问题**本地先答**，答不了返回 None 交给模型。

    ★★★ 为什么必须本地先答，而不是"AI 挂了才本地答"（第二轮，2026-10-20）：
      此前只做了后者，于是 **AI 在线时**这句话仍然由模型自由发挥。
      它答的是
      「今天（2026年10月20日，周二）没有安排课程」—— **问的是周四、答的是今天**，
      而且它自己那一步工具调用明明查的是 10-22。答案就在手上还能答歪，
      说明这类问题压根不该让它参与：哪天有课是**确定的事实**，算一下就有。
    """
    g = goal or ""
    # ★ 点名了课表里的某门课（「高等数学几点上」）也算问课
    if not _LOCAL_CLASS_ASK.search(g) and not _course_in(g, courses):
        return None
    # ★ 闸一：带"安排/规划/制定"动词的是"要排"，不是"要查"。
    #   「安排一下这周的课程」必须留给模型；而「下周的课程安排」是名词短语（要看）。
    # ★ 评测漏判 ——「周末想补一下这周的课，帮我理一下时间」里
    #   「理一下」不在闸里，于是被当成问课，答了「周末有/没有课」。
    #   把「要排」那一族动作词补全（与 _PLAN_ACTION_VERBS 同一口径）。
    if re.search(r"安排|规划|制定|梳理|理一下|拆一下|拆成|拆开|列个|列一下|写一份|"
                 r"排(?=一下|个|到|出|进|[0-9一二三四五六七八九十])", g) \
            and not re.search(r"的课程安排|的课程表|的课表", g):
        return None
    rng = parse_date_range(g, today, allow_past=True)
    # ★ 闸二：没点日期就不猜 —— 「我有课吗」交给模型顺着上下文理解。
    #   两个例外：① 「每天/天天」点了范围；② 只说「查课表/看课表」——
    #   这种默认按**这周剩下的几天**答（只答今天会漏掉后面的课）。
    if not rng and re.search(r"课表|课程表|哪天空|哪天没课|哪天有课|哪天有空|什么时候空", g):
        rng = (today, max(1, 7 - today.weekday()))
    if not (_WEEK_ALL.search(g) or rng or _course_in(g, courses)):
        return None
    return _local_ask(courses, goal, today, force=True)


def _place_text(goal: str) -> str:
    """从「博文楼在哪」「从知行楼到博文楼怎么走」里取出**目的地**楼名。

    ★ 为什么取目的地：问路的人关心的是终点。「从知行楼到博文楼怎么走」把整句
      丢给 `get_building_info` 只会得到「不是已收录的教学楼」——一句正确的废话。
    """
    s = goal or ""
    # 「从 A 到 B 怎么走」→ 取 B
    m = re.search(r"从(.{2,14}?)(?:到|去|走到)(.{2,14}?)(?:怎么|咋|如何|要多久|$)", s)
    if m:
        s = m.group(2)
    s = re.sub(r"^(?:请问|问一下|问下|帮我看看|帮我查查|我想知道)", "", s.strip())
    # ⚠️ 「在哪儿」必须排在「在哪」前面：交替分支是**先匹配先赢**，
    #    反过来的话「知行楼B栋在哪儿」只会被切掉「在哪」，剩一个「儿」交给楼名表。
    s = re.sub(r"(?:在哪儿|在哪|怎么走|怎么去|怎么到|哪个楼|哪栋|几号楼|哪个校区)", "", s)
    s = re.sub(r"[\s，,。.？?！!、~～]", "", s)
    return s.strip()


def route_of(goal: str, courses: list = None, today=None) -> str:
    """把一句话归到六条路之一：
    `schedule_add` / `quick` / `docs` / `explain` / `local_ask` / `plan`。

    ★ 存在的意义：**路由器与评测脚本共用同一份判据**。
      如果评测自己另写一套判断，那测出来的就不是线上跑的那套规则，
      数字再好看也没意义（这是「循环论证」的另一种形式）。

    顺序不是随便排的，每一条都挡过一类真实错误：
      ① 加一条固定安排 —— 结构化解析，命中就必须只写一条，绝不能排成计划；
      ② 单工具直答     —— 「今天天气怎么样」「博文楼在哪」只查一个工具就答得完。
                          必须排在 docs **前面**：「图书馆怎么走」要的是导航，
                          落到文档路会回一句"手册里没写"，那是答非所问；
                          但「图书馆几点开门」问的是规定，`_WHERE_NOT` 把它挡回来，
                          仍然走文档路 —— 这两句只差一个字，判据里写清了为什么；
      ③ 学校文档       —— 「休学要什么材料」既不像排表也不像讲方法，
                          但只查手册能答；放在 explain 前面，否则「什么是学分认定」会被教练接走；
      ④ 学习教练       —— 求方法/求讲解，不调工具、不排表；
      ⑤ 本地问课       —— 「周四有课吗」是确定事实，算一下就有，不该让模型参与；
      ⑥ 课程细节       —— 「计算机组成原理是谁教」课表里没有这门课时 local_ask 接不住，
                          但仍只该查一次课表，不该顺带排计划；
      ⑦ 其余           —— 规划 Agent。
    """
    today = today or ddate.today()
    courses = courses or []
    g = goal or ""
    if parse_schedule_add(g):
        return "schedule_add"
    _qk = quick_kind(g)
    if _qk in ("weather", "place", "campus"):
        return "quick"
    if _is_docs_question(g):
        return "docs"
    if _is_explain(g):
        return "explain"
    if _local_ask_first(courses, g, today):
        return "local_ask"
    if _qk == "course":
        return "quick"
    return "plan"


# ---------------------------------------------------------------------------
# 寒暄短路
# ---------------------------------------------------------------------------
# ★★★ 加（评测逼出来的）：
#   在线评测里「你好」「谢谢」这两条**干扰题**的表现是：34 秒、1 万 token、
#   调用 get_timetable + find_free_slots 各一次，最后回一句「你好」。
#   原因是它们连 route_of 的五条路都不匹配，只能落到 `plan` —— 于是被当成
#   「要排计划」，Agent 规规矩矩先把课表和空档查了一遍。
#
#   先试过在 system prompt 里加一条「寒暄不要调工具」，**无效**
#   （7 条干扰题，多余调用率 100% → 100%，X05 还丢了 get_class_schedule）。
#   模型对"任务是什么"的认知来自整段提示词（你是规划 Agent、基于真实时间表…），
#   末尾一条禁令压不过它。所以改成**代码层短路**：纯寒暄根本不进 Agent。
#
#   判据要**极窄**：整句话只有寒暄/道谢/应声，才短路。
#   「你好，帮我排个计划」带着任务，必须照常走 Agent；
#   所以用 `^…$` 全串匹配，不做"包含"判断。
_SMALL_TALK = [
    (re.compile(r"^(?:你好|您好|hi|hello|嗨|哈喽|在吗|在不在|早|早上好|中午好|"
                r"下午好|晚上好|早安|晚安)[\s!！。.~～?？、啦咯哈]*$", re.I),
     "你好，我是校园 AI 伴学。想让我做什么？一句「帮我排这周的计划」"
     "或者「什么时候放寒假」都可以。"),
    (re.compile(r"^(?:谢谢|多谢|感谢|谢谢啦|辛苦了|thx|thanks|thank\s*you|3q)"
                r"[\s!！。.~～?？、啦咯哈]*$", re.I),
     "不客气。还想再调整哪一部分，直接说就行。"),
    (re.compile(r"^(?:好的|好|嗯+|哦|收到|行|可以|ok|okay)"
                r"[\s!！。.~～?？、啦咯哈]*$", re.I),
     "好。还有要改的地方就说；没有的话，这份计划就可以照着走了。"),
]


def small_talk(goal: str) -> str | None:
    """纯寒暄/道谢/应声时给一句固定回复，**不调模型、不调工具**。

    Returns:
        命中就返回该回的话；没命中返回 None（交给后面的正常流程）。
    """
    g = (goal or "").strip()
    if not g or len(g) > 12:        # 稍长一点的话多半带着任务，别猜
        return None
    for pat, reply in _SMALL_TALK:
        if pat.match(g):
            return reply
    return None


_LOCAL_ASK_MISS = ("这段时间 AI 服务没有响应，这个问题没答上来。"
                   "课表、空档、今天有没有课这类问题我可以直接查，随时问。")


def _local_answer(courses: list, goal: str, today) -> str:
    """AI 不可用时的兜底：能本地答就答，答不了就说清楚没答上来。"""
    got = _local_ask(courses, goal, today)
    return got[0] if got else _LOCAL_ASK_MISS


@router.post("/timetable")
def save_timetable(payload: TimetableUpdate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """保存课表：**只覆盖 payload 涉及的日期区间**，不动其它周的数据。

    早期版本直接 delete 该用户全部课程，改成三周视图后那样会互相覆盖，
    所以这里只删「本次提交日期范围内的那些天」，再写回新数据。
    """
    days = sorted({c.date for c in payload.courses if c.date})
    if days:
        db.query(Course).filter(
            Course.user_id == user.id,
            Course.date >= days[0], Course.date <= days[-1],
        ).delete(synchronize_session=False)
    count = _write_courses(db, user, payload.courses)
    db.commit()
    return {"ok": True, "count": count}


@router.post("/timetable/inherit")
def inherit_last_week(payload: InheritRequest, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """「沿用上周」：把上周的课程+活动复制到目标周，同名同时段已存在则跳过。"""
    try:
        src_start = ddate.fromisoformat(payload.source_start)
        tgt_start = ddate.fromisoformat(payload.target_start)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")

    rows = (db.query(Course)
            .filter(Course.user_id == user.id,
                    Course.date >= payload.source_start,
                    Course.date <= payload.source_end)
            .order_by(Course.date, Course.slot_order).all())
    if not rows:
        return {"ok": True, "created": 0, "skipped": 0,
                "message": "上一周没有已保存的安排。如果上周时间表还没点过「保存时间表」，请先切到上一周保存一次。"}

    # 目标周已占用的 (date, slot_order) 集合，用于判重（一个时间点只放一条）
    existing = {
        (c.date, c.slot_order or 0)
        for c in db.query(Course)
        .filter(Course.user_id == user.id,
                Course.date >= payload.target_start,
                Course.date <= payload.target_end).all()
    }

    created = skipped = 0
    for c in rows:
        offset = (ddate.fromisoformat(c.date) - src_start).days
        new_date = (tgt_start + timedelta(days=offset)).isoformat()
        key = (new_date, c.slot_order or 0)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        db.add(Course(
            user_id=user.id, date=new_date, type=c.type or "course",
            slot_order=c.slot_order or 0,
            start_time=c.start_time, end_time=c.end_time, name=c.name,
            teacher=c.teacher, location=c.location, building=c.building,
            # ⚠️ weeks 必须跟着复制。漏了的话复制出来的课 weeks 变空，
            #    会被前端当成「每周都上」—— 比源周显示得**更多**，等于凭空多课。
            weeks=c.weeks or "",
            lng=c.lng, lat=c.lat,
        ))
        created += 1
    db.commit()
    return {"ok": True, "created": created, "skipped": skipped}


@router.delete("/timetable/{course_id}")
def delete_course(course_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """按主键删除单条课程 / 活动（只能删自己的）。"""
    n = (db.query(Course)
         .filter(Course.id == course_id, Course.user_id == user.id)
         .delete(synchronize_session=False))
    db.commit()
    if not n:
        raise HTTPException(status_code=404, detail="找不到这条记录，可能已被删除")
    return {"ok": True}


@router.post("/plan", response_model=PlanResponse)
async def generate_plan(req: PlanRequest, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """生成学习计划。

    ## 课表从哪来（这是此处修的一个主要问题）

    早期这个接口**只从数据库读** `Course` 表：

        rows = db.query(Course).filter(Course.user_id == user.id).all()

    于是出现两类「课表明明是空的」的假象：
      1. 用户刚导入课表、还没点「保存课表」—— 数据只在前端内存里，库里是空的；
      2. 用户导入失败（识别没读出来），库里自然也没有。

    两种情况 AI 都会一本正经地回答「你还没有添加任何课程，目前你的课表是空的」，
    用户看到的是「我刚填的课表白填了」。

    现在改成**优先用前端传上来的 courses**，库里那份只在「前端没传」时才兜底 ——
    这样「所见即所得」，页面上显示的课表就是 AI 看到的课表。

    ## 课表为空也要能生成

    需求：「得是大学生的学习计划」。大学生的时间结构和中学生完全不同
    （没晚自习、有社团/实习/竞赛、自学占比高、四六级/考研/求职多条线），
    所以**课表为空不是拒绝的理由**，反而是最需要一份通用大学学习计划的时候。
    这里把「空课表」当成一种正常输入，交给下面的提示词处理。
    """
    # ---- 课表来源：前端优先，数据库兜底 ----
    if req.courses:
        courses = [{
            "date": c.date, "type": c.type or "course",
            "start": c.start, "end": c.end, "name": c.name,
            "location": c.location or c.building or "",
            # ★ weeks 必须带上：Agent 算空档要按「星期 + 周次」算（见
            #   study_agent._course_on_date）。少了它就只能按 date 相等判断，
            #   结果是把上课时间当成空闲时段排进计划。
            "weeks": c.weeks or "",
        } for c in req.courses]
    else:
        rows = db.query(Course).filter(Course.user_id == user.id).all()
        courses = [{"date": c.date, "type": c.type or "course", "start": c.start_time,
                    "end": c.end_time, "name": c.name, "location": c.location,
                    "weeks": c.weeks or ""}
                   for c in rows]

    goal = (req.goal or "").strip() or "全面提升成绩"
    days = max(1, min(30, req.days or 7))
    # 学生画像拼成一句，没有就不提
    who = "".join(x for x in [req.grade.strip(), req.major.strip()] if x)
    who_line = f"我是一名{who}的学生。" if who else "我是一名大学生。"

    # ---- 对话上下文（2026-10-15：AI伴学页改成对话框，要接得住追问）----
    # 用户现在是在**聊天**，第二句往往很短：「改成两周」「再详细一点」「那周末呢」。
    # 这些话单独看没有信息量，必须带上他前面说过什么才能理解。
    #
    # ⚠️ 只取 **user 原话**，且只取最近 3 轮、每条截断 200 字：
    #    计划正文动不动两三千字，把此前的 assistant 全文回灌进 prompt，
    #    既贵又没用 —— 模型需要知道的是「他还提过什么要求」，不是再读一遍自己写的计划。
    prev = [str((m or {}).get("content") or "").strip()
            for m in (req.history or [])
            if isinstance(m, dict) and m.get("role") == "user"]
    prev = [p[:200] for p in prev if p and p != goal][-3:]
    ctx_line = ""
    if prev:
        ctx_line = ("这是连续对话。在这条之前，用户还说过："
                    + "；".join(f"「{p}」" for p in prev)
                    + "。请结合前后语境理解这一次的要求"
                      "（例如「改成两周」是在改天数，「再细一点」是在改详略）。")

    # ---- 交给 Agent（function calling）去查、去排 ----
    #
    # ★ 起这里**不再自己拼一大段提示词**，改成「给模型工具、让它自己查」。
    #   它手上有 5 个工具：查课表 / 查节次作息 / 算空闲时段 / 查地点归属 / 查天气，
    #   先调工具拿到事实、再动笔排计划。实现见 services/study_agent.py。
    #
    #   为什么必须改（这不是"显得高级"，是治一个真毛病）：
    #   旧写法把课表塞进提示词后直接问，于是「未来 7 天哪天第几节有空」这种
    #   **日期算术 + 区间合并**全压在语言模型身上 —— 而这是它最不擅长的部分。
    #   结果就是计划里经常出现"上课时间和自习时间打架"。
    #   现在空档是 find_free_slots **算出来的**，跟课表页用的是同一套节次数据。
    #
    #   注意「课表为空」依然不是拒绝生成的理由：Agent 的 system 提示里写死了这一点，
    #   工具返回空课表时它按「没排课的大学生的一天」给通用节奏。
    # ★★★ （从开发线移植）：
    #   ① 「往时间表里加一条」先判 —— 只写一条，绝不排计划；
    #   ② 问「哪天有没有课」**本地先答**，不给模型自由发挥的机会。
    #      模型会把"查的那天"说成"今天"（问周四答今天），而答案就在手上。
    #
    #   ⚠️ h 版没有 intent_of 那套三档判定（那是 10-19 之后加的），
    #      这里用**窄口径**直接判，不影响别的说法：加点靠结构化解析，
    #      问课靠 _local_ask_first（它自己会判"这句话跟课表有没有关系、点没点日期"）。
    def _resp(plan_text: str, steps=None):
        """本接口唯一的出口。

        顺手把这次请求消耗的 token 带出去（`usage.take()` 会清空本请求的累计值，
        所以只能在出口处取一次；取完不带出去，这批数字就丢了）。
        评测脚本与成本核算都依赖它 —— 没有它，「一次对话多少钱」只能靠估。
        """
        return PlanResponse(plan=plan_text, trace=steps or [],
                            usage=_usage.summary(_usage.take()))

    # ★ 判定抽成 `route_of()`，**路由与评测脚本共用同一份判据**。
    _route = route_of(goal, courses, ddate.today())

    # ★★★ 寒暄/道谢/应声：一句话直接回，**不进 Agent**。
    #   放在所有分支最前面 —— 「你好」既不查课表、也不查文档、更不排计划。
    #   不短路时这一句要 34 秒、1 万 token、两次工具调用。
    _st = small_talk(goal)
    if _st:
        return _resp(_st)

    if _route == "schedule_add":
        _done, _txt, _tr = _apply_schedule_add(db, user, goal, ddate.today())
        if _done:
            return _resp(_txt, _tr)

    # ★★★ 只查**一个**工具就能答完的问题（天气 / 楼在哪 / 某门课谁教）。
    #   在这之前这三类连路由都不匹配，只能落到 `plan` —— 而 plan 的入口就是
    #   「先把课表和空档查一遍」，于是「博文楼在哪」被当成「要排计划」，
    #   最后交出一份作息表（在线评测实录，X03/X04）。
    #   现在判出来之后**只跑一个工具 + 模板化回答**，不调模型：
    #   天气和楼名是确定数据，没有理由让它等 20 秒，更没有理由给模型编的机会。
    if _route == "quick":
        _qk = quick_kind(goal) or ""
        if _qk == "campus":
            # 楼名表是常量，一个工具都不用调 —— 连 trace 都留空，
            # 前端就不会显示「Agent 查了课表」这种根本没发生的事。
            return _resp(quick_answer("campus", {}), [])
        if _qk == "weather":
            _qtool, _qargs = "get_weather", {}
        elif _qk == "place":
            _qtool, _qargs = "get_building_info", {"text": _place_text(goal)}
        else:                                    # course
            _qtool, _qargs = "get_timetable", {}
        _qres, _qsum = await _run_tool(
            _qtool, _qargs,
            {"db": db, "user": user, "courses": courses or [], "window": ""})
        _qtxt = quick_answer(_qk, _qres, goal)
        if _qtxt:
            return _resp(_qtxt, [{"tool": _qtool, "args": _qargs,
                                  "ok": not _qres.get("error"), "summary": _qsum}])

    # ★★★ 学校规定 / 办事流程 / 学期时间 -> 查文档再答。
    #   必须排在 explain 之前：问「什么是学分认定」既像求解释，也是文档问题，
    #   但只有文档能给出「走哪个系统、找谁」这一步 —— 教练人格只会讲方法。
    #   也必须排在排计划之前：「休学需要准备什么材料」会返回一张作息表。
    if _route == "docs":
        _doc = await ask_docs(goal)
        if _doc:
            return _resp(_doc)

    # ★★★ 求方法 / 求讲解 -> 学习教练（不调工具、不排表）。
    #   改之前「高数怎么复习」会被当成 plan，问的是"怎么学"，收到一张时间表。
    if _route == "explain":
        _coach = await ask_coach(goal, who_line=who_line, context=ctx_line)
        return _resp(_coach or local_coach(goal))

    if _route == "local_ask":
        _la = _local_ask_first(courses, goal, ddate.today())
        if _la:
            return _resp(_la[0], _la[1])

    plan, trace = await run_study_agent(
        db=db, user=user, courses=courses, goal=goal, days=days, who_line=who_line,
        context=ctx_line,
    )
    if not plan:
        # ★ 问事实不能拿一份计划来回答：AI 超时后兜底无条件排了「未来 1 天安排」，
        #   还顶着「大学生没有排课的一天」的通用作息，而问的只是一句"有没有课"。
        if _local_ask(courses, goal, ddate.today()):
            _t, _tr = _local_ask(courses, goal, ddate.today())
            return _resp(_t, _tr)
        # 两级降级：AI 不可用时，落到本地规则拼接，保证功能始终可用。
        # 此时 trace 为空（一个工具都没真跑过），前端不会渲染「调用记录」那一段。
        # ★ 兜底计划也必须尊重用户点名的日期，否则「AI 超时降级」之后
        #   又变回「从今天起 N 天」，用户刚点的那两天就白说了。
        _rng = parse_date_range(goal, ddate.today())
        plan = _local_plan(courses, goal, _rng[1] if _rng else days,
                           start=_rng[0] if _rng else None)
    return _resp(plan, trace)


def _local_plan(courses: list, goal: str, days: int, start=None) -> str:
    """无 AI 时的兜底计划：按课表占用情况，把剩下的空档排成学习时段。

    不依赖任何外部服务，纯本地推导，保证「AI 没配 / 网络不通」时页面也有内容。

    ⚠️ 与早期版本的区别：**课表为空时也照样出一份完整计划**。
    旧版 busy 表是空的，于是每天只剩两行通用的「专注时段 / 复习时段」，
    看起来像「没课表就没内容」。现在无论有没有课表，都按大学生的典型作息
    排出「上午 / 下午 / 晚上」三段，课表只用来**避开已占用的时间**。
    """
    # 有没有课 —— 只影响开头那句说明的措辞（下面每天按 _course_on_date 现算）
    has_courses = any((c.get("name") or "").strip() for c in courses)
    lines = [
        f"【学习目标】{goal}",
        "",
        f"【未来 {days} 天安排（本地生成·未接入 AI）】",
        ("说明：当前 AI 服务未配置或暂时不可用，以下为按你的时间表空档自动排出的计划框架，"
         "可先照着执行。") if has_courses else
        ("说明：当前 AI 服务未配置或暂时不可用。你的时间表还是空的，下面按**大学生没有排课的"
         "一天**排出了通用作息框架 —— 等你导入时间表后重新生成，会按真实空档重排。"),
        "",
        "【每天的基本节奏】",
        "  · 08:00-11:30 上午段：状态最好，留给最难的专业课 / 需要深度思考的内容",
        "  · 14:00-17:00 下午段：做题、编程、实验这类需要动手的内容",
        "  · 19:00-22:00 晚间段：回顾白天内容 + 英语（四六级）/ 竞赛 / 项目",
        "  · 22:30 前收工，别熬夜 —— 第二天的效率比今晚多刷两道题重要",
        "",
    ]
    # ★ 用户给了「时间配方」（18-22 点、英语 60、高数 30、休息 10、剩下 WPS）时，
    #   兜底也必须照他的配方排 —— 否则 AI 一超时，他看到的又是一份"没按我说的排"的模板，
    #   和当初报的那个问题一模一样。时刻表同样是 study_agent 算好的那一份，不另写一套。
    _rc = parse_time_recipe(goal)
    if _rc:
        base = start or ddate.today()
        out = [
            f"【学习目标】{goal}",
            "",
            f"【未来 {days} 天安排（本地生成·未接入 AI）】",
            "说明：这次 AI 没在时限内返回，下面按**你自己定的时间安排**逐日排好了。",
            f"固定时间窗：{_rc['start']}-{_rc['end']}",
            "",
        ]
        for i in range(1, days + 1):
            day = base + timedelta(days=i - 1)
            wd = "一二三四五六日"[day.weekday()]
            out.append(f"· 第 {i} 天（{day.isoformat()} 周{wd}）")
            clash = [
                f"{c.get('start','')}-{c.get('end','')} {c.get('name','')}"
                for c in courses
                if _course_on_date(c, day)
                and c.get("start") and c.get("end")
                and c["start"] < _rc["end"] and c["end"] > _rc["start"]
            ]
            if clash:
                # 有课就如实说，绝不把配方时间挪走（挪了就不是他要的东西了）
                out.append("    - ⚠️ 这个时段有课：" + "、".join(clash[:2])
                           + " —— 有课的那几段跳过，其余照下面的时间执行")
            for b in _rc["blocks"]:
                out.append(f"    - {b['start']}-{b['end']} {b['what']}")
        out += ["", "【执行建议】",
                "1. 到点就换，别把 30 分钟的一段拖成 1 小时 —— 休息那 10 分钟也别省。",
                "2. 前一天没做完的，第二天不要叠加，跳过继续。"]
        return "\n".join(out)

    for i in range(1, days + 1):
        # ★ 逐天按「星期 + 周次」取当天真实课程（_course_on_date）。
        #   原来这里写 `busy.get("", [])` —— 键是空串，永远不会命中，
        #   等于每天都印同一份模板，用户看到的"计划"跟课表毫无关系。
        # ★ 起点优先用「用户点名的日期」（"排 20-21 号"）；没点名才是今天。
        #   两个分支都用同一个 base，避免 AI 版和兜底版排出不同的日子。
        day = (start or ddate.today()) + timedelta(days=i - 1)
        occ = [
            f"{c.get('start','')}-{c.get('end','')} {c.get('name','')}"
            for c in sorted(
                (c for c in courses if _course_on_date(c, day)),
                key=lambda c: c.get("start") or "",
            )
        ]
        wd = "一二三四五六日"[day.weekday()]
        lines.append(f"· 第 {i} 天（{day.isoformat()} 周{wd}）")
        if occ:
            for item in occ[:4]:
                lines.append(f"    - 已有安排：{item}")
        lines.append("    - 上午 08:00-11:30：攻难点 / 写作业（最难的事放在最前面）")
        lines.append("    - 下午 14:00-17:00：刷题 / 实验 / 项目实践")
        lines.append("    - 晚间 19:30-21:30：专注时段（2 小时，英语或专业课复习）")
        lines.append("    - 睡前 21:30-22:00：回顾当天错题与笔记")
    lines += [
        "",
        "【执行建议】",
        "1. 每天先做最难的那件事，别从简单的刷起。",
        "2. 每个时段 50 分钟休息 10 分钟，别硬扛。",
        "3. 每周留半天机动、留一晚完全休息 —— 排太满的计划一定执行不下去。",
        "4. 周末花 30 分钟复盘，比多刷两套卷子有用。",
        "5. 大学生的时间要分给多条线：专业课、四六级、竞赛/项目、社团，",
        "   别只顾一头；每周检查一次各条线的进度。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 课表截图导入：用智谱视觉模型（glm-4v 系列）直接读图，产出结构化课表。
#
# 为什么不上 OCR 而是直接看图：
#   课表截图是「表格 + 彩色块 + 小字」，通用 OCR 拿到的是一堆散字，还得自己
#   还原行列关系；视觉大模型能直接把「周三第三节 = 高等数学 @ 博文楼301」这种
#   语义关系读出来，省掉一整套版面分析。
#
# 识别一定会有错（模型不是 100% 准），所以设计上有两道保险：
#   1. 后端做「结果清洗」：楼名对不上校园表、节次超范围、时间格式不对，
#      全部标进 uncertain，而不是猜一个填进去；
#   2. 前端把 uncertain 的格染色 + 弹框列出待补全项，让用户自己确认。
# 宁可少填一格让用户补，也不要静默填错让他上课跑错楼。
# ---------------------------------------------------------------------------

# 校园里真实存在的**教学楼**名（与 frontend/js/campus.js 的 BUILDINGS 保持一致：南区 4 + 北区 6）
#
# ⚠️ 这里只放「教学楼」，是**故意的**，别因为导航页能选更多点位就跟着加。
#    原因：本表有两个用途，都是围绕「上课地点」的 ——
#      1. 课表截图识别时当白名单，把模型的幻觉收敛到真实楼名上；
#      2. 从教室编号里反推楼栋（如「知行楼A203」-> 知行楼），判断该按南区还是北区作息。
#    课表里出现的地点永远是上课的地方，不会写「东区餐厅」「菜鸟驿站」。
#    把这些生活设施加进来，只会让 AI 更容易把教室编号误认成某栋楼，有害无益。
#
#    导航目的地是另一回事：那边用前端的 CAMPUS_PLACES（含宿舍/食堂/操场等 44 个点位，
#    精简过），不经过本表。所以「能导航的点位」比「教学楼」多是正常的。
#
# 注意：操场**不在**本表里，但要和「地图上有没有操场」区分清楚 ——
#   需求：「标记为操场放入其他类」，它现在**是**一个可导航的目的地
#   （在 campus.js 的 CAMPUS_PLACES 里，type=other），但它**不是教学楼**，
#   所以仍然不进 KNOWN_BUILDINGS。否则 AI 会把课表截图里的「田径场」当成一栋
#   可导航的教学楼，而体育课的地点本来就不需要按楼栋算南北区作息。
KNOWN_BUILDINGS = {
    "商德楼", "博文楼", "雅颂楼", "正德楼",
    "知行楼", "载德楼", "润知楼", "启智楼", "致远楼", "平远楼",
}

# 楼栋**别名表**：教务表格里写的是简称 / 旧称 / 带后缀的写法，统一规范成正式楼名。
#
# 为什么要有这张表：
#   教务系统导出的课表用的是**口语简称**（「商教514」而不是「商德楼514」），
#   而 KNOWN_BUILDINGS 里只有正式名。没有这张表时，「商教514」匹配不上任何一栋楼，
#   后果是**这栋楼没有坐标、不能导航**，而且核对弹窗里会把它标黄 ——
#   用户以为是自己填错了，其实是系统不认识这个叫法。
#
# ⚠️ 两个必须遵守的规则（写错了会误伤别的楼）：
#   1. **键要短于值，且必须是「不会出现在别的楼名里」的串**。
#      比如不能把 "德" 当成别名 —— 「正德/载德/商德」都含它，会互相抢。
#      所以这里一律用完整的两字简称（商教 / 文教…），不用单字。
#   2. 替换要用**整串替换**（下面的 _normalize_building_alias 用的是 replace），
#      不是"包含判断"。因为原文是「商教514」，要把「商教」换成「商德楼」，
#      变成「商德楼514」，而不是简单地把 building 设成商德楼、丢掉教室号。
#
# 目前只收录**确认过**的写法。以后再遇到新简称，往这里加一行即可 ——
# 别去改 _norm_building 的匹配逻辑，那会影响图片识别那条路（两边口径不同）。
BUILDING_ALIASES = {
    "商教": "商德楼",     # 用户确认：课表上的「商教513/514」就是商德楼
}

# 别名替换后仍要能识别出的正式名（顺序无关，_norm_building 自己按长度排）
assert all(v in KNOWN_BUILDINGS for v in BUILDING_ALIASES.values()), \
    "别名表的「值」必须是 KNOWN_BUILDINGS 里的正式楼名，否则替换完还是认不出"


def _normalize_building_alias(text: str) -> str:
    """把地点文字里的**简称**换成正式楼名，返回替换后的整串。

    只做替换、不做判断 —— 认不认得出这栋楼是 _norm_building / 调用方的事。

    ⚠️ 用 replace 而不是"整体赋值"是有意的：
      原文「商教514」里，「商教」是楼、「514」是教室号。
      整体赋值成 "商德楼" 会把 514 丢掉，用户就看不到自己在哪个教室了。
      所以这里是**串内替换**，保持其余文字不动。
    """
    if not text:
        return text
    out = text
    for alias, full in BUILDING_ALIASES.items():
        if alias in out:
            out = out.replace(alias, full)
    return out

# 北区楼栋（作息比南区早 5-10 分钟）。已知楼名时按对应区套上课时间。
NORTH_BUILDINGS = {"知行楼", "载德楼", "润知楼", "启智楼", "致远楼", "平远楼"}

# 视觉模型识别课表的指令。要点：
#   - 强制只要 JSON，方便解析（尾部带 ``` 也容忍，见 _extract_json）
#   - ⚠️ **示例里绝对不能出现具体课名**（见下方「为什么示例全是占位符」）
#   - 明确「没看清就留空 + 记进 uncertain」，别让模型硬猜
#   - 给死楼名白名单和节次范围，把幻觉收敛到可校验的空间里
#
# ---------------------------------------------------------------
# 为什么示例里全是 `<课名>` 这样的占位符（注意，别改回去）
# ---------------------------------------------------------------
# 旧版这里写的是 `"cells": ["高等数学A", null, "大学英语（读写）", ...]`。
# 看着很直观，但出了**最致命的一类 bug**：GLM-4V 把示例里的课名
# 当成了「答案」，直接照抄输出。
#
# 拿一张真实的手机课表截图（内容是「数据库原理与应用-邱烨@知行楼B」）去调，
# glm-4v-flash 和 glm-4v-plus 返回的都是：
#     col_headers = ["周一".."周五"]，rows=6，
#     内容是「高等数学A」「线性代数」「大学英语（读写）」「程序设计基础」「大学物理」
# —— 全是示例里的词，跟图里内容**毫无关系**，而且 JSON 合法、不报错、不标存疑。
# 用户看到的就是「你还没有添加任何课程」，白忙一场还找不到原因。
#
# 这就是典型的 few-shot 泄漏：具体的示例值会被小模型当成待填内容。
# 所以示例里的**每个可变位置都必须是占位符**，让它模仿不了具体值。
#
# 另外发现：示例越短，模型越倾向于**编造**内容（给一张字体渲染乱码的图，
# 它会笃定地说图上写着「基础化学实验B」）。所以必须**主动要求它「读不清就留空」**，
# 否则它宁可编一个看起来合理的课名，也不愿意输出空。
VISION_PROMPT = """这是一张中文大学课程表截图。请把它读成一个**二维数组 grid**。

格式就是这样（下面每个尖括号都是**占位符**，请替换成你实际看到的字）：

{
  "grid": [
    ["<表头第1格>", "<表头第2格>", "<表头第3格>"],
    ["<第1行节次>", "<该行第1格内容>", "<该行第2格内容>"],
    ["<第2行节次>", "<同上>", "<同上>"]
  ]
}

## 怎么填这个 grid

1. **第 0 行 = 表头那一行**，从左到右照抄。
   表头常见写法是「周一 21」「周二 22」这种「星期 + 日期」，
   也可能有「09月」这种月份格 —— **一律整格原样照抄**，
   不要只保留「周一」、不要补全日期、也不要跳过任何一格。
2. **第 1 行起 = 表格的每一行**，一行一个子数组。
   每个子数组的**第 1 个元素是节次列**（表格最左边那一列），
   **后面依次是各列的内容，从左到右**。
3. ⚠️⚠️ **每一行的长度必须完全一样**（都等于表头的格数）。
   这一条最重要 —— 行内少写或多写一个元素，会让后面所有格子左右错位。
   哪一格没课就写**空字符串 `""`**，**用空串占位，不要省略、不要写 null**。

   ★★ 检查方法（请在输出前**逐行**做一遍）：★★
     数一数表头有几个格子（比如 7 个星期列 → 7 格）；
     然后**每一行都要数出同样多个格子**。
     某行最前面几格没课，就写几个 `""` 打头，**不能直接跳过开头**。
     举例：表头 4 格，某行是「第2格有课、其余空」，
     必须写成 ["节次文字", "", "课名", "", ""]（共 5 个元素 = 节次 + 4 格），
     **不能**写成 ["节次文字", "课名"]（少写会让课名被当成第 1 格）。
     宁可多写几个 ""，也**绝不能少写**。

   ⚠️ 特别注意「表格左边几列没课」的行：这种行最容易丢开头的空位。
     一定要把开头的 `""` 补齐，让有课的那一格落在**它真正所在的列**上。
4. 节次列的写法（照抄原文，不要改写）：
     · `第一节` `第二节`（单节）
     · `一二节 01,02` `三四节 03,04` `九十节 09,10`（连堂，一格占两节）
     · `08:00` `12:00` 这类纯时间行 —— 它们也占一行，**不要漏**
   连堂写法是两个节次数字连写，「一二节」就是「一」和「二」两个字，
   不是「一节课」。照抄，不要理解、不要润色。
   连堂行**只占一行**，不要拆成两行。

## 格子里写什么

5. 有课的格子，**照抄格子里的原文**。课表格子常印成一整串：
       `数据库原理与应用-邱烨@知行楼B`
   请**原样照抄整串**，不要自己拆、不要删掉教师和教学楼、不要改顺序 ——
   程序会用分隔符把它拆开，你擅自删减反而让我无从下手。
6. 一格里上下叠了两门课，写成 `"课名1|课名2"`，中间用竖线分隔。

## 看不清怎么办（非常重要）

- **绝对禁止编造课程名、教师名、教学楼名。**
- 一个字都看不清的格子，写**空字符串 `""`**。
- 只认得一部分的，**只写你真正看清的那部分**，认不出的字用 `?` 代替
  （只看清「数据库原理」就写 `"数据库原理?"`）。
- 宁可交一个带空格的诚实结果，也**不要**为了「填满」而写一个
  看起来合理、但图上其实没有的课程名。程序会把可疑的标出来让我人工核对。

## 最容易犯的错（务必避免）

- ❌ **不要照抄我这里给的占位符例子**（`<表头第1格>` 这种），
  它们只是格式说明，不是内容。
- ❌ **不要把某一行的空单元格省掉**。空着也要写 `""`，
  否则整行会向左塌缩、所有课错位到别的星期。
- ❌ **不要漏掉整行**。哪怕这一行（或后面几行）全校都没课，
  也要把节次写出来、后面全填 `""`。
- ❌ **不要漏掉节次列里的纯时间行**（`06:00` / `12:00` 等）。
- ❌ 不要输出任何解释文字、不要 Markdown 代码块标记，
  只输出这一个 JSON 对象。

现在开始识别："""

# 第二轮的「补读」指令：把网格里读到的课名，补上教师 / 教学楼 / 教室。
# 让模型一次性输出「网格 + 每门课的全部字段」会超出输出长度并串位；
# 拆成一维网格（短、准）再单独补详情，两轮都比一轮全字段强。
#
# 同第一轮：示例里**不能出现真实课名 / 教师名 / 楼名**，否则会被照抄。
# 楼名白名单是唯一允许出现具体值的地方 —— 因为那是「闭集」，
# 照抄它反而无害（照抄出来的楼名一定是合法楼名）。
DETAIL_PROMPT = """这是一张中文大学课程表截图。

我已经从图里读出了每节课的课程名，但还需要补齐**教师、教学楼、教室**这三项。
下面是已知的课程格子（格式：第K个星期列 节次 = 格子原文）：

{grid}

⚠️ 上图（{grid}）里每一行是一格课。请对照截图，为**每一格**补齐这三项，
输出**只包含一个 JSON 对象**，不要任何解释文字：

{{
  "details": [
    {{
      "weekday": 1,
      "slot": 1,
      "name": "<照抄上面给你的课程名>",
      "teacher": "<图上写的教师姓名，没有就写空字符串>",
      "building": "<从下面白名单里选，选不出写空字符串>",
      "location": "<教室号，没有就写空字符串>"
    }}
  ]
}}

规则：
- ⚠️⚠️ **weekday 必须由你自己看着图数出来**（这条最重要，请严格照做）：
  我会告诉你这一格是「第K个星期列」。请你**在截图上找到表头那一行**，
  从**表头里第一个星期列**开始数：第 1 个星期列 = 周一(1)，第 2 个=周二(2)……
  **数到第 K 个**，那就是这一格的 weekday。
  ⚠️ 注意别把表头左边的**月份、周次**那些格子数进去 —— 只数「周一/周二/…」这些。
  如果数出来和我给的 K 对不上，**以你在图上亲眼看到的为准**。
- slot 是该课所在节次。**这个照抄上面给你的节次文字，别改。**
- name **直接照抄上面给你的课程名原文**，不要改写、不要翻译、不要补全简称、
  也不要把教师名和楼名并进来。上面给你什么就写什么。
- building 教学楼名，**只能**从下面这个列表里选，选不出就留 ""：
  商德楼、博文楼、雅颂楼、正德楼、知行楼、载德楼、润知楼、启智楼、致远楼、平远楼
  注意：本列表里**没有**「操场」「体育馆」「图书馆」「宿舍」这类场所，
  遇到这些一律把 building 留 ""，把原名写进 location 即可。
  例：图上写「博文301」→ building="博文楼", location="301"；
      写「知行楼A203」→ building="知行楼", location="A203"。
- location 只填教室号（如 "301"、"A203"），教学楼自己单列，别塞进 location。
- teacher 任课教师；图上没写或看不清就留 ""。
- **三项都看不清时就都留 ""，没关系，程序会标黄让我手动补。绝对不要编造。**
  编一个看起来合理的教师名或教室号，比留空**有害得多**。
- 必须为上面列出的**每一格**都输出一条，一格都不能漏。

现在开始，只输出 JSON："""

# ---------------------------------------------------------------
# 【旧版提示词·仅作回滚备份，不要启用】
# 旧版示例里写了具体课名（"高等数学A" / "线性代数" / "陈立群" / "商德楼"），
# 会被 GLM-4V 直接照抄成答案，导致「识别不到课表」。
# 保留在此仅为出问题时对照，见文件顶部 VISION_PROMPT 的注释。
# ---------------------------------------------------------------
# VISION_PROMPT_OLD = '''这是一张中文大学课程表截图。请把它读成一个**二维网格**。
# ...（含 "高等数学A" 等具体示例，已废弃）...'''



def _salvage_rows(text: str) -> list:
    """从**被截断的网格 JSON**里，救回已经写完整的行对象。

    背景：第一轮让模型输出 `{"rows":[{"slot":1,"label":"...","cells":[...]}, ...]}`。
    当 rows 很多（16 行课表）或 cells 很长时，输出可能被 max_tokens 截断，
    于是 JSON 尾部不合法，`_extract_json` 返回空 —— 前面已经读对的十几行
    全都白读了，用户看到「没能从这张图里读出课程」。

    这里做的就是在「半截 JSON」里按大括号配对，把**结构完整**的 `{...}`
    对象一个个抠出来，其中带 `cells` 的就是一行可用的网格数据。

    ⚠️ 为什么 require_key 是 "cells" 而不是 "name"：
       网格格式的行对象**根本没有 name 字段**（只有 slot / label / cells），
       早期沿用课程格式的 require_key="name" 导致**一个都救不回来**，
       整个函数等于死代码。判据必须跟着数据格式走。

    Returns:
        行对象列表（可能为空）。每行会打上 `_truncated` 标记，
        供上层知道「这是救回来的，可能缺最后几行」。
    """
    out = salvage_objects(text, require_key="cells")
    for o in out:
        o["_truncated"] = True
    return out


def _norm_time(v) -> str:
    """把模型给的时间统一成 "HH:MM"；给不出合法值就返回空串。

    模型偶尔会写 "8:10"、"8:10-8:55"、"0810" 这类，这里尽量掰回来，
    掰不回来就当没给（前端会按节次套作息兜底），绝不硬编一个时间。
    """
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    # "8:10-8:55" 这种取前半段
    s = s.split("-")[0].split("~")[0].strip()
    m = re.match(r"^(\d{1,2})\s*[:：.]\s*(\d{1,2})$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
        return ""
    # 纯数字 "0810" / "810"
    m = re.match(r"^(\d{3,4})$", s)
    if m:
        d = m.group(1).zfill(4)
        h, mi = int(d[:2]), int(d[2:])
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    # "8点10分"
    m = re.match(r"^(\d{1,2})\s*[点时]\s*(\d{1,2})?\s*分?$", s)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    return ""


def _strip_building_suffix(s: str) -> str:
    """从「楼301」「-302」「B101」这类尾巴里剥出教室号。

    只剥开头结尾的「楼/栋」和连接符，不用 str.strip("楼")——
    那是按字符集剥离，会把教室号里的字符也误删。
    """
    s = str(s or "").strip()
    s = re.sub(r"^[楼栋]\s*", "", s)          # 楼名粘过来时开头可能剩个「楼」
    s = re.sub(r"[楼栋]\s*$", "", s)
    return s.strip(" -—·－_").strip()


def _is_virtual_place(tail: str) -> bool:
    """判断 @ 后面这串是不是「虚拟地点」（不是教学楼）。

    课表里 @ 后面除了楼名，还常见这类：
        线上 / 慕课 / 网课 / 网络 / 网络工 / 云课堂 / 线上教学 / 腾讯会议…
    它们**不是教学楼**，不能进 building（那个字段要拿去导航，
    填错了会把人导到某栋不相干的楼）。统一放进 location 让用户看着办。

    ⚠️ 必须放在 _norm_building 之前判断：_norm_building 用的是「楼名包含在文字里」
       的宽松匹配，「网络工」会被它当成某个含「网络」的楼名匹配上。
    """
    t = str(tail or "").strip()
    if not t:
        return False
    # 纯虚拟地点关键词，命中即算
    VIRTUAL = ("线上", "网课", "慕课", "网络", "云课堂", "腾讯会议", "钉钉",
               "直播", "录播", "雨课堂", "学习通", "超星", "智慧树", "虚拟")
    if any(k in t for k in VIRTUAL):
        return True
    # ⚠️ 另外一类：**校园里的露天/场馆场地**（田径场、操场、体育馆、
    #    游泳池、篮球场…）。它们不是「教学楼」，导航时也不该走
    #    教学楼那条路（校园 POI 里另有对应点位）。
    #    体育课就写「赵鹏@田径场」—— 若不拦，_norm_building 匹配不上
    #    就会把它当「存疑楼名」标黄，用户看到一格莫名其妙的黄色提示。
    #    放进 location（当备注）最合适：既保留了信息，又不误导导航。
    VENUE = ("田径场", "操场", "体育馆", "游泳馆", "游泳池", "篮球场",
             "足球场", "羽毛球场", "网球场", "运动场", "球场")
    if any(k in t for k in VENUE):
        return True
    # 「第X实验室」这类不是教学楼但也不是虚拟 —— 交给 _norm_building 处理即可
    return False


# ---- 「像楼名但不是已知教学楼」的识别 ----
#
# 课表里出现、而 KNOWN_BUILDINGS 里**没有**的楼名：
#     「大学英语 语德楼-105」
#     「大学物理 实验楼-203」
#     「马克思主义基本原理 人文社科楼-102」
# 它们确实是校园里的楼，只是不在这 10 栋「教学楼」清单里
# （可能是实验楼、院系楼，或者没收录到的楼）。
#
# 不处理的后果：整串「大学英语 语德楼-105」原样留在 course.name 里 ——
#   · 课表格子放不下，用户看不到教室（抱怨过这一点）；
#   · 「大学英语」这个课名还带着一串楼名，显示上很怪。
#
# 所以这里做一个**保守的形态识别**：`2~6 个汉字 + 楼/栋/苑/馆/中心`
# 就认为是「楼名形态」。识别出来后：
#   · 因为**对不上校园楼栋表**，绝不能写进 building（那个字段要拿去导航，
#     填一个查不到的楼名等于骗用户）——只放进 location 当备注。
#   · 课名因此被切干净，格子里显示得下。
_PLACE_TAIL_RE = re.compile(r"^[\u4e00-\u9fa5]{2,6}(?:楼|栋|苑|馆|堂|中心|教学楼|实验楼)$")


def _looks_like_place_name(tail: str) -> bool:
    """这串是不是「像楼名但不是已知教学楼」的地点（语德楼 / 实验楼 / 人文社科楼）。

    注意：**已知楼名不算**（那种情况走 _norm_building，要进 building 去导航）。
    这里只兜底「楼名形态但对不上表」的那些，让它们从课名里切出来。
    """
    t = str(tail or "").strip()
    if not t or len(t) < 3:
        return False
    # 剥掉可能的教室号再判：`语德楼-105` / `语德楼105` -> `语德楼`
    core = re.split(r"[-—–－\s]", t)[0]
    core = re.sub(r"[A-Za-z]?\d+$", "", core).strip()
    if not core:
        return False
    # 已知楼名交给 _norm_building，不在这里拦
    if any(b in core for b in KNOWN_BUILDINGS):
        return False
    return bool(_PLACE_TAIL_RE.match(core))


def _looks_like_subject_tail(tail: str) -> bool:
    """判断「尾部这 2~4 个字」更像是学科词尾，而不是人名。

    用来避免把课名里的连字符误当「课名-教师」的分隔：
        「计算机-网络技术」  网络技术 -> 学科词，不拆 ✅
        「计算机-郑晔」      郑晔     -> 人名，拆 ✅

    判据：以这些**学科通用后缀**结尾的，一律不当人名。
    宁可漏拆（回到「一整串当课名」，用户能看见原文自行改），
    也不要误拆 —— 误拆会把课程名改错，用户还不容易发现。
    """
    t = str(tail or "").strip()
    if not t:
        return False
    TAILS = ("技术", "工程", "原理", "设计", "导论", "概论", "基础", "实验",
             "理论", "方法", "系统", "网络", "程序", "结构", "分析", "应用",
             "数学", "物理", "化学", "英语", "语文", "历史", "地理", "生物",
             "体育", "实践", "实训", "研讨", "专题", "讲座", "课程")
    return t.endswith(TAILS)


def _split_cell_text(cell: str) -> list[dict]:
    """把一格课表文字拆成「课名 / 教师 / 教学楼 / 教室」。

    为什么需要这个：很多学校的课表格子印的是**一整串**，形如

        数据库原理与应用-邱烨@知行楼B
        └──── 课名 ────┘ └师┘ └─楼─┘
        计算机网络基础实验-黄劲@网络工
        大学英语(专升本)-田红@博文楼
        人工智能技术与应用-许庆龙@知行

    第一轮提示词虽然说了「只写课程名」，但模型看到格子里就是这一串，
    多数时候会照抄 —— 结果课程名变成 20 多个字（课表格里显示不下、
    点开才看得全），而教师和教学楼两栏全是空的，等于白识别。

    与其反复跟模型较劲，不如在后端顺手拆开：这是纯字符串处理，
    确定性强、不依赖模型发挥，拆错了也只是回到「一整串当课名」的原状。

    分隔符约定（这套写法在高校课表里很常见）：
        「@」  分课名与地点     课程名-教师@教学楼教室
        「-」  分课名与教师     （半角/全角、也容忍 · ・ 等）
        「,，;；」  也当课名与教师的分隔（有些系统用逗号）

    ⚠️ 拆不出来就**原样返回**，绝不硬拆。
       「计算机组成原理-郑晔@平远楼313」能拆，
       但「马克思主义基本原理概论」这种自带连字符/点号的课名不能误伤 ——
       所以只有整串**同时**满足「有 @」或「分隔符后是 2~4 字人名」才动手。

    @param cell 单元格原始文字（可能已含 "|" 分隔的多门课，调用方负责先拆）
    @returns [{name, teacher, building, location}]，至少一条
    """
    s = str(cell or "").strip()
    if not s:
        return []

    teacher = ""
    building = ""
    location = ""
    name = s

    # ---- 第一步：用 @ 切出「地点」部分 ----
    # @ 是课表里最可靠的分隔标记，先处理它，剩下的再谈教师
    if "@" in s:
        head, _, tail = s.partition("@")
        head, tail = head.strip(), tail.strip()
        if head:
            name = head
        if tail:
            # tail 形如「知行楼B」「博文楼301」「平远楼313」「网络工」「线上」
            #
            # ⚠️ 先挡掉「虚拟地点」：线上 / 网络 / 网课 / 慕课 这些不是教学楼，
            #    但 _norm_building 的宽松匹配会把「网络工」当成带「网络」二字的
            #    某个楼 —— 它们应该老实地进 location，别去碰 building
            #    （building 是要拿去导航的，填错了会把人导到错误的楼）。
            if _is_virtual_place(tail):
                location = tail
            elif _looks_like_place_name(tail):
                # 楼名形态但对不上校园楼栋表（`大学英语@语德楼105`）。
                # 切出来当 location 备注，课名才干净 —— 不进 building（查不到坐标）。
                location = _strip_building_suffix(tail)
            else:
                b, loc, unc = _norm_building(tail, "")
                # ⚠️ 只有**真匹配上**已知楼名才写 building。
                #    _norm_building 有个兜底：匹配不上时会把原文原样当 building
                #    返回、并用第三个值 True 表示存疑 —— 所以必须看 unc。
                #    否则「操场」「实验楼」这类会混进 building，
                #    而 building 是要拿去查坐标导航的，填个查不到的楼名等于骗用户。
                if b and not unc:
                    building, location = b, loc
                else:
                    location = _strip_building_suffix(tail)    # ---- 第二步：从课名里剥出教师 ----
    # 形如「数据库原理与应用-邱烨」，分隔符后是 2~4 个字的人名
    if name:
        for sep in ("-", "—", "–", "－", "·", "・", ",", "，", ";", "；"):
            if sep not in name:
                continue
            head, _, tail = name.rpartition(sep)
            head, tail = head.strip(), tail.strip()
            # 人名判定（三条都要满足，避免「计算机-网络技术」被误拆）：
            #   1. 尾部是 2~4 个纯汉字（字母/数字/括号都不算人名）
            #   2. 尾部**不是**学科词尾（网络技术/程序设计/原理…）
            #   3. 头部至少 2 个字（别把「计算机」这种切出来当课名）
            if (head and len(head) >= 2
                    and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", tail or "")
                    and not _looks_like_subject_tail(tail)):
                name, teacher = head, tail
                break

    # ---- 第三步：用**空格**再试一次（模型最常用的写法）----
    #
    # 注意：图上是「课名－教师@楼」，但模型输出时**把「－教师」整段丢了**，
    # 只留下「课名 教学楼-教室」，中间用**一个半角空格**分隔，原文：
    #     "高等数学 商德楼-302"        <- 教师没了，且楼名与教室是半角「-」
    #     "数据结构 知行楼A-508"
    #     "体育 赵鹏@田径场"           <- 这格教师保住了（因为后面还有 @）
    #
    # 所以空格也是合法的「课名 / 地点」分隔符，必须认。判定要**严**：
    #   · 空格后整段必须**含已知楼名**（否则「马克思主义基本原理概论」这种
    #     本身就带空格的课名会被误切）；
    #   · 或空格后是 2~4 个纯汉字且不像学科词尾（教师写法）。
    # 顺序放在分隔符之后：先试更可靠的「-」，再用空格兜底。
    if name and not building:
        head, _, tail = name.rpartition(" ")
        head, tail = head.strip(), tail.strip()
        if head and len(tail) >= 2:
            # 情况 A：空格后是楼名/教室（`商德楼-302`、`知行楼A-508`）
            b2, loc2, unc2 = _norm_building(tail, "")
            if b2 and not unc2:
                name = head
                building, location = b2, loc2
            elif _is_virtual_place(tail):
                name = head
                location = tail
            # 情况 A2：空格后是**楼名形态但不在校园楼栋表**里的地点
            #         （`语德楼-105`、`实验楼-203`、`人文社科楼-102`）。
            #
            #   这三条原本整串留在 course.name 里（"大学英语 语德楼-105"），
            #   后果是课名又长又怪、格子里放不下教室。既然形态上明确是楼名，
            #   就把课名切干净；但因为**查不到坐标**，只能进 location 当备注，
            #   绝不进 building（building 要拿去导航，填假楼名会把人导错）。
            elif _looks_like_place_name(tail):
                name = head
                location = tail
            # 情况 B：空格后是教师名（`高等数学 陈立群`）
            elif (re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", tail)
                    and not _looks_like_subject_tail(tail)
                    and len(head) >= 2):
                name, teacher = head, tail

    # 兜底：teacher 若在 tail 里空着但 @ 前有教师，已被上面处理
    return [{
        "name": name.strip(),
        "teacher": teacher,
        "building": building,
        "location": location,
    }]


def _norm_building(raw: str, location: str) -> tuple[str, str, bool]:
    """把模型给的楼名/教室名对齐到校园真实楼栋表。

    模型经常把楼名塞进 location（"操场"、"博文301"），或写得不全（"博文"），
    这里统一拉到 building 字段，剩下的才当教室号。

    Returns:
        (building, location, uncertain_building)
        building 为空表示「没能对上任一栋楼」，此时 uncertain 为 True，
        前端会把这一格标黄让用户自己选。
    """
    b = str(raw or "").strip()
    loc = str(location or "").strip()

    # 找一个「出现在 b 或 loc 里」的校园楼名（长的优先，避免「博文」被「博」类误配）
    def match(text: str) -> str:
        if not text:
            return ""
        for name in sorted(KNOWN_BUILDINGS, key=len, reverse=True):
            if name in text:
                return name
            # 允许简写：模型写「博文」时也能对上「博文楼」
            short = name.rstrip("楼栋")
            if len(short) >= 2 and short in text:
                return name
        return ""

    # 1) building 为空 -> 从 location 里挖
    if not b:
        hit = match(loc)
        if hit:
            idx = loc.find(hit) if hit in loc else loc.find(hit.rstrip("楼栋"))
            cut = (idx + len(hit)) if hit in loc else (idx + len(hit.rstrip("楼栋")))
            loc = _strip_building_suffix(loc[cut:])
            return hit, loc, False
        # ⚠️⚠️ 挖不出来时**不能一律标黄**，这是遇到的误报：
        #
        #     `体育 赵鹏@田径场` 里 location="田径场"，上游 _split_cell_text
        #     已经正确判定它不是教学楼（走 _is_virtual_place → location=田径场），
        #     可这一层又调了一次 _norm_building，而这里 `bool(loc)` 恒为 True，
        #     于是 `uncertain=["building"]` 又被加回去 —— 用户看到一格莫名的黄。
        #
        #     根因：「挖不出楼名」有三种截然不同的原因 ——
        #        · location 本来就**不是教学楼**（田径场/线上/体育馆）—— 正常，不该黄；
        #        · location 是**楼名形态但不在校园楼栋表**（语德楼/实验楼/人文社科楼）
        #          —— 已知它是什么楼，只是没收录、查不到坐标，也不该黄
        #          （黄格是在说「我看不懂」，而这里看得懂）；
        #        · location 是**教室号但没有楼名**（"302"）—— 真的缺信息，才该黄。
        #
        #     前两类都放行，只有第三类标黄。判据复用上游那两个函数，
        #     保证「是否教学楼」的判断**全局只有一套标准**。
        if _is_virtual_place(loc) or _looks_like_place_name(loc):
            return "", loc, False
        # 真的只挖到个教室号（如「302」）却不知道是哪栋楼 —— 这才该标黄
        return "", loc, bool(loc)

    # 2) building 有值 -> 对齐到标准楼名
    hit = match(b)
    if hit:
        if not loc:
            # 楼名里粘了教室号（"博文301"），拆出来
            idx = b.find(hit) if hit in b else b.find(hit.rstrip("楼栋"))
            cut = (idx + len(hit)) if hit in b else (idx + len(hit.rstrip("楼栋")))
            tail = _strip_building_suffix(b[cut:])
            if tail and not match(tail):
                loc = tail
        else:
            # location 里又写了别的楼名（模型把楼名写两遍），以 building 为准
            other = match(loc)
            if other:
                # 写的是同一栋 -> 这格本来就是楼名，location 清空
                loc = "" if other == hit else loc
        return hit, loc, False

    # 3) building 有值但对不上任何楼 —— 学校新楼或模型看错了，保留原文并标黄
    return b, loc, True



def _norm_name_key(s: str) -> str:
    """课程名的「宽松归一」，用于跨轮匹配。

    两轮之间模型对同一个课程名的写法会飘：全角/半角括号（（读写） vs (读写)）、
    夹带的空格、「A」和「Ａ」。这些差异不该导致补详情失败，所以匹配前统一抹平。
    但**不**做模糊匹配（别把「高等数学A」和「高等数学B」混起来）——
    课名差了字母就是不同的课，宁可匹配不上让它标黄。
    """
    s = str(s or "").strip()
    # 全角括号/字母数字 -> 半角
    s = s.translate(str.maketrans("（）【】　ＡＢＣＤＥＦＧ", "()[] ABCDEFG"))
    # 抹掉所有空白与常见连接符
    s = re.sub(r"[\s\-—·_]", "", s)
    return s.lower()


def _align_courses(raw_courses: list, slots: list, monday: str,
                   col_headers: list | None = None) -> tuple[list, list]:
    """清洗模型输出：对齐楼名/节次/日期，收集所有不确定项。

    Args:
        col_headers: 模型回读的表头（["周一","周二",...]）。用它把课程列号换算成星期，
                     比直接相信模型的星期编号可靠得多 —— 见下面 weekday 那段的注释。
                     ⚠️ 传**原始表头**（可以含 `09月` 这类装饰格）；
                     `raw_courses` 里的 `col_index` 必须已经是**表头下标**。

    Returns:
        (courses, warnings) —— courses 里每条都带 uncertain 字段，
        warnings 是整体性提示（如「有 3 处楼名对不上」）。
    """
    max_order = max((s.order_no for s in slots), default=12)
    out: list = []
    warn_counter: dict[str, int] = {}

    # 节次 -> (start, end)，用于模型没给时间时套学校作息
    slot_map = {s.order_no: s for s in slots}

    # ---- 表头 -> 列号换算表 ----
    # 为什么绕这一圈：模型直接输出 weekday 很容易整体串位（比如把周一的课
    # 一律写成 4），但它**读表头是准的**，而且「这格在第几列」是它数出来的
    # 可观察事实。所以让它先写 col_headers 当标尺，再用「列号 -> 表头星期」
    # 换算，把容易出错的「印象值」换成「查表」。表头不是从周一开始时
    # （如「周三 周四 周五」）这套还天然支持。
    # ⚠️⚠️ `col_to_weekday` 的键必须与 `col_index` **同一套坐标**，否则整表错位。
    #
    # 这里出过三次问题，结论：**上游负责把 cells 下标折算成表头下标**（idx - col_shift），
    # 本函数只按「col_headers 的下标」建表，两边统一，不再各建各的。
    # 之所以能这样简化，是因为「cells 到底带不带 `09月` 装饰位」这个歧义
    # 已经在上游用内容命中法（col_shift）解掉了 —— 本函数不需要再操心。
    # ⚠️ 键必须与上游传来的 `col_index` **同一套坐标**。
    #    上游已经把 cells 下标折算成**表头下标**（idx - col_shift）再存进 col_index，
    #    所以这里只需按「col_headers 的下标」建表即可，一一对应。
    #    早期这里曾按「过滤掉装饰格后的课程列序号」建表，而上游给的是原始下标，
    #    两套坐标错位 -> 星期整体右移一天。现在两边统一到表头下标，不会再错。
    col_to_weekday: dict[int, int] = {}
    for i, h in enumerate(col_headers or [], start=1):
        wd = _weekday_from_header(str(h))
        if wd:
            col_to_weekday[i] = wd

    for rc in raw_courses:
        if not isinstance(rc, dict):
            continue
        uncertain: list = list(rc.get("uncertain") or [])
        uncertain = [str(u).strip() for u in uncertain if str(u).strip()]
        # 被截断救回来的那条：结束时间/存疑标记都没来得及写，统一标黄
        if rc.get("_truncated"):
            for k in ("time", "slot_order"):
                if k not in uncertain:
                    uncertain.append(k)

        name = str(rc.get("name") or "").strip()
        if not name:
            # 整条都空 = 模型瞎凑的空条目，直接丢掉，别拿去烦用户。
            # 只有「真读了点东西但名字没认出来」才有保留价值（至少能提示那格有课）。
            others = [rc.get(k) for k in ("teacher", "building", "location", "start", "end")]
            if not any(str(o or "").strip() for o in others):
                continue
            if "name" not in uncertain:
                uncertain.append("name")

        # ---- 节次 ----
        slot_order = rc.get("slot_order")
        try:
            slot_order = int(slot_order) if slot_order is not None else None
        except (TypeError, ValueError):
            slot_order = None
        if slot_order is None or not (1 <= slot_order <= max_order):
            if slot_order is None:
                # 模型没给节次：用开始时间去最近的槽位兜底
                st = _norm_time(rc.get("start"))
                slot_order = _nearest_slot(st, slots)
                if "slot_order" not in uncertain:
                    uncertain.append("slot_order")
            else:
                # 给了但明显越界（模型把「第 13 节」这种也读出来了）
                slot_order = max(1, min(max_order, slot_order))
                if "slot_order" not in uncertain:
                    uncertain.append("slot_order")

        # ---- 教学楼 / 教室 ----
        # 先解析楼名：下面的时间要按「南区还是北区」套作息，得先知道是哪栋楼。
        # 详情轮（第二轮）给的值放在 _raw_* 里，优先用它；
        # 没有详情时退回 rc 自身的 building/location 字段。
        building, location, b_uncertain = _norm_building(
            rc.get("_raw_building", rc.get("building")),
            rc.get("_raw_location", rc.get("location")))
        if b_uncertain and "building" not in uncertain:
            uncertain.append("building")

        # ---- 时间：优先用模型给的；给不出就按节次套学校作息 ----
        # 网格方案下模型不再输出时间（它只报课名），所以基本都走「按节次套作息」
        # 这条路 —— 反而比让模型看书上的小字更稳。
        start = _norm_time(rc.get("start"))
        end = _norm_time(rc.get("end"))
        if not start or not end:
            s = slot_map.get(slot_order)
            if s:
                # 楼名已知就用该区作息（南北区差 5-10 分钟）。
                # ⚠️ 楼名未知 / 线上课（building 为空）一律按**南区** ——
                #    作息是「哪栋楼决定的」，没有楼就没理由套北区时间；
                #    南区是基准作息（start_south 就是默认 start），取它最稳。
                #    前端 toLocalCourse() 走的是同一套口径，别各写各的。
                north = bool(building) and building in NORTH_BUILDINGS
                if not start:
                    start = (s.start_north if north else s.start_south) or s.start
                if not end:
                    end = (s.end_north if north else s.end_south) or s.end
            # 这里不标黄：按节次套作息是**预期的正常路径**，不是识别失败。
            # （只有连节次都认不出来，才会在下面把 slot_order 标黄。）

        # ---- 星期 ----
        # 网格方案下 weekday 是上游按「cells 下标 + 表头」查表算出来的，
        # 属于**程序推导的可验证事实**，不是模型凭印象写的数字，所以默认可信。
        # 只有下面两种情况才需要标黄：
        #   a) 上游压根没算出星期（表头没读到、格子数对不上）
        #   b) 模型同时给了 col_index 且与推导结果矛盾（它自己都没把握）
        weekday = rc.get("weekday")
        try:
            weekday = int(weekday) if weekday is not None else None
        except (TypeError, ValueError):
            weekday = None
        if weekday is not None and not (1 <= weekday <= 7):
            weekday = None

        col_index = rc.get("col_index")
        try:
            col_index = int(col_index) if col_index is not None else None
        except (TypeError, ValueError):
            col_index = None

        if col_index and col_index in col_to_weekday:
            mapped = col_to_weekday[col_index]
            if weekday is not None and mapped != weekday and col_headers:
                # 模型列号与推导结果打架 -> 它自己没把握，标黄（以推导值为准）
                weekday = mapped
                if "weekday" not in uncertain:
                    uncertain.append("weekday")
            elif weekday is None:
                weekday = mapped

        # 到这里 weekday 仍然没值才算「真没认出来」，标黄。
        # ⚠️ 早期这里还叠了一条「if "weekday" not in uncertain: append」的兜底，
        #    结果是把**上游已经算好 weekday 的课**也一起标黄了（因为上游
        #    往 uncertain 里预置了 weekday 占位）。满屏黄格完全是误报。
        #    现在只认「确实没值」这一种情况。
        if not (weekday and 1 <= weekday <= 7):
            if "weekday" not in uncertain:
                uncertain.append("weekday")
        else:
            # 有确定值就把上游可能预置的占位标记撤掉
            if "weekday" in uncertain and col_index and col_index in col_to_weekday:
                uncertain.remove("weekday")

        # ---- 该行格数与表头对不上：星期不可信，标黄要求核对 ----
        # 放在上面之后，因为上面的逻辑会 remove("weekday")，
        # 而这一条是**独立于「有没有算出星期」的事实**：
        # 行格数错了，算出来的星期就不可靠，必须让用户看一眼。
        # （悄悄挪位置猜对猜错都不知道，标黄至少用户会去确认。）
        if rc.get("_row_weekday_unsure"):
            if "weekday" not in uncertain:
                uncertain.append("weekday")

        # ---- 星期交叉校验：第一轮（按位置推） vs 第二轮（模型独立看图）----
        # 两个来源都是确定的数字却不一致 -> 至少有一个错，标黄让用户对照原图定夺。
        #
        # ⚠️⚠️ 这里**去掉了「只在格数可疑的行上查」的前置条件**（曾经有，现已废弃）。
        #
        #   为什么当初收窄：怕误报 —— 第二轮「独立看图」自己也会判错，
        #   如果每行都比对，正确的结果反而被误标黄，核对表被噪声淹没，
        #   用户就再也不认真看黄格了（"狼来了"）。
        #   收窄之后，判定条件是「raw_len 比别人短」，可**这个条件几乎不触发**：
        #   模型漏格之后会自己把末尾补空串，于是每行 raw_len 都等于表头列数，
        #   判据失效 —— 错位照样静默进库（问题的"还是会出错"就是这个）。
        #
        #   为什么现在敢放宽：先量化过代价，不是拍脑袋。
        #   同一张课表图连跑 10 轮、逐格比对真值：
        #       第一轮（按位置推）   96%   (106/110)
        #       第二轮（独立看图）   90%   ( 99/110)
        #       第一轮独错、第二轮对  7 处  <- 放宽后能兜住的
        #       第二轮独错、第一轮对  4 处  <- 放宽后会误标黄的
        #       两轮都错              4 处  <- 谁也救不了，只能靠提醒用户
        #   7 : 4，净收益为正。而且两类错误的**代价不对称**：
        #       · 误标黄 = 用户多看一格，改不改由他定（不损失正确性）；
        #       · 漏标黄 = 错的课表静默进库，等上课才发现（损失严重）。
        #   代价不对称时，宁可偏向「多标」。
        #
        # ⚠️⚠️ **这套校验的能力边界，别高估它**（当前10 轮里约 8 轮全对、
        #   2 轮静默错位）。抓不住的是「两轮错得一模一样」那种情况 ——
        #   同一个模型看同一张图，第一轮把某行整体平移一格，第二轮**也**平移一格。
        #
        #   试过并且**失败**了的补救方向（记下来，别再走一遍）：
        #     · 换成「完全不给参考位置」的提问 -> 模型自己数 8 列的表数不准，
        #       误标黄从 0 暴涨到 16 格，比不校验还糟（现改为给「第K个星期列」，
        #       即给位置、不给结论，见 services/vision.py 的 _grid_to_text）。
        #     · 用「某行首个星期列为空」当错位特征 -> 召回率仅 50%、
        #       误报率 29%，等于既抓不住又添噪声，直接否决。
        #     · 用过「内容命中数」猜偏移量 -> 目标函数天然偏向大偏移，
        #       且会改坏本来正确的行（详见下面「已废弃」那段）。
        #
        #   结论：**这类错位没有便宜的事后检测**。诚实的做法是把局限讲清楚，
        #   让用户知道"黄格只是辅助、仍需对一眼原图"，而不是伪称已经解决。
        #
        # ⚠️ 判据是「星期**不在候选里**」，不是「候选不等于星期」。
        #   候选是个列表：同一门课一周上两次（周一+周四）时，
        #   两次的「节次+课名」完全相同，没法区分是哪一次，
        #   所以第二轮给出的 1 和 4 都算是这门课的合法答案。
        #   用「不在候选里」判断，才不会把这种正常的重复课误标黄。
        #   （一开始写成 `check_wd != weekday`，每张图固定误报 2 格 —— 就是出错在这。）
        check_wd = rc.get("_wd_check")
        if (isinstance(check_wd, list) and check_wd
                and weekday and 1 <= weekday <= 7
                and weekday not in check_wd):
            if "weekday" not in uncertain:
                uncertain.append("weekday")
            warn_counter["weekday_check"] = warn_counter.get("weekday_check", 0) + 1

        # ---- 日期：weekday(1..7) + 参考周一 -> 真实日期 ----
        date = str(rc.get("date") or "").strip()
        if not date:
            if weekday and 1 <= weekday <= 7 and monday:
                try:
                    date = (ddate.fromisoformat(monday)
                            + timedelta(days=weekday - 1)).isoformat()
                except ValueError:
                    date = ""
            if not date and "weekday" not in uncertain:
                # 缺参考周一时算不出具体日期，但星期是准的 —— 这属于「信息不全」
                # 而不是「认错了」，所以只加一条轻提示，不把格子标黄。
                uncertain.append("date")

        # ---- 教学楼 / 教室 ----
        # 已在上面（时间之前）解析过了，这里不再重复 —— 时间要靠它判南北区作息。
        if "building" in uncertain and building:
            warn_counter["building"] = warn_counter.get("building", 0) + 1
        if "name" in uncertain:
            warn_counter["name"] = warn_counter.get("name", 0) + 1
        if "weekday" in uncertain:
            warn_counter["weekday"] = warn_counter.get("weekday", 0) + 1

        out.append(ImportedCourse(
            date=date, weekday=weekday, slot_order=slot_order,
            start=start, end=end, name=name,
            teacher=str(rc.get("teacher") or "").strip(),
            building=building,
            location=location or str(rc.get("location") or "").strip(),
            uncertain=sorted(set(uncertain)),
        ))

    warnings = []
    label = {"building": "教学楼名对不上校园楼栋表", "name": "课程名没认出来",
             "weekday": "星期没认准（对照原图核对一下）",
             "weekday_check": "上课星期存在两种判断结果（对照原图核对一下）"}
    for key, n in warn_counter.items():
        if key == "weekday_check":
            warnings.append(
                f"有 {n} 门课的星期，AI 两次看图给出的结果不一致，"
                "已标黄 —— 请对照原图确认这几门课到底在星期几再保存。")
            continue
        warnings.append(f"有 {n} 处的{label.get(key, key)}，已标黄，请核对后手动补全。")
    return out, warnings


def _nearest_slot(hhmm: str, slots: list) -> int:
    """按开始时间找最接近的节次；给不出时间就默认第一节。"""
    if not hhmm:
        return slots[1].order_no if len(slots) > 1 else 1

    def to_min(t: str) -> int:
        try:
            h, m = t.split(":")
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return -1

    target = to_min(hhmm)
    best, best_diff = slots[0].order_no if slots else 1, 10 ** 9
    for s in slots:
        t = s.start_south or s.start
        d = abs(to_min(t) - target)
        if d < best_diff:
            best, best_diff = s.order_no, d
    return best


@router.post("/import-image", response_model=ImportImageOut)
async def import_timetable_image(payload: ImportImageIn,
                                 user: User = Depends(get_current_user),
                                 db: Session = Depends(get_db)):
    """把课表截图交给视觉模型识别，返回**结构化但未落库**的课程列表。

    刻意不直接写库：识别结果需要用户过一眼（标黄的那些格），
    确认/补全后再走已有的 POST /timetable 保存，避免脏数据直接进课表。
    """
    slots = db.query(TimeSlot).order_by(TimeSlot.order_no).all()
    slot_items = [_slot_out(s) for s in slots]

    img = (payload.image or "").strip()
    if not img:
        raise HTTPException(status_code=400, detail="没收到图片数据")
    # 剥掉 dataURL 前缀，模型只要纯 base64
    if img.startswith("data:"):
        _, _, img = img.partition(",")

    # 两轮识别：先读课程名网格（短、准、不串位），再补教师/教学楼/教室。
    # 详细取舍见 services/vision.py 顶部注释。
    #
    # ⚠️ grid_data 是 vision 层**已经归一化好**的结果（统一成 rows + col_headers，
    #    每行都带 weeks）。这里直接用它，**不要再自己解析一遍 grid_text** ——
    #    两处各解析一遍，只要对「表头行算不算数据行」的理解有一点偏差，
    #    整表星期就会错位。解析只应发生在一个地方。
    grid_text, detail_text, warnings0, grid_data = await recognize_timetable(
        img, VISION_PROMPT, DETAIL_PROMPT)
    warnings: list = list(warnings0)

    # ---- 解析第二轮的 details，做「星期交叉校验」----
    #
    # 为什么需要这一步（此处新加）：
    #   第一轮的星期是「按列位置查表头」推出来的，前提是模型把每行的格子写齐。
    #   glm-4v-flash 常把「左边几列没课」的行写成不带头部空位的短行，
    #   于是那一行**整体左移一格**：周一的课被判成周二。
    #   加强提示词 + 追问补齐能把这种错位从 50% 降到 ~12%，但没法到 0，
    #   而且剩下那部分**静默出错**（不报错、不标黄），用户很可能直接存进课表。
    #
    #   第二轮（补教师/教学楼）本来就要模型再看一遍原图。这版提示词让它
    #   **自己独立判断每格在第几列**（不许照抄给的星期），
    #   于是就有了**第二个独立的星期来源**，可以和第一轮的结果对账：
    #     两边一致 -> 高置信，不动；
    #     两边不一致 -> 至少有一个错，**标黄让用户确认**（不擅自选一个）。
    #   这样即使第一轮错位，用户也会在核对表里看到黄色提示，不会默默存错。
    detail_by_key: dict = {}
    if detail_text:
        d_data, _d_trunc = _extract_json(detail_text)
        for d in (d_data.get("details") if isinstance(d_data, dict) else None) or []:
            if not isinstance(d, dict):
                continue
            try:
                dwd = int(d.get("weekday"))
            except (TypeError, ValueError):
                continue
            if not (1 <= dwd <= 7):
                continue
            # 键用「节次 + 课名」—— 比行号稳（行号在两种格式下含义不同）。
            #
            # ⚠️⚠️ 值必须是**列表**，不能直接存单个数字。出现过的 bug：
            #   同一门课一周上两次很常见（例：「数据库原理与应用」周一和周四各一次），
            #   它们 period 相同、课名相同 -> 键撞车 -> 后一条把前一条**覆盖掉**。
            #   于是周一那门查到的是周四的星期（1 vs 4），明明两边都对，
            #   却被判定为「不一致」而误标黄 —— 每张图固定误报 2 格，
            #   黄格一旦变成常驻噪声，用户就再也不看它了（"狼来了"）。
            #   存成列表后，校验退化为「候选里有没有一个等于我算出来的星期」：
            #     同名课在不同星期 -> 候选 [1, 4]，周一查到含 1 -> 一致，不标黄。
            #   而真正的错位（第一轮算成 2、第二轮是 3）依然查不到 -> 照常标黄。
            key = (str(d.get("slot") or "").strip(), str(d.get("name") or "").strip())
            detail_by_key.setdefault(key, []).append(dwd)
        if detail_by_key:
            logger.info("第二轮独立判定了 %d 格的星期，将用于交叉校验",
                        sum(len(v) for v in detail_by_key.values()))

    def _lookup_wd_check(by_key: dict, period, name: str) -> Optional[list]:
        """查第二轮为这门课独立判定的星期**候选**；查不到返回 None。

        键设计成「节次数字 + 课名」的**宽松匹配**：第二轮给的 slot 可能是
        行号（"3"）也可能是节次文字（"第三节"），课名可能有细微空白差异，
        所以这里两边都抽成数字和去空白的名字再比。

        返回值是**列表**（不是单个数字）—— 见上面 detail_by_key 的注释：
        同一门课一周上两次时，这两次除了星期以外完全一样，
        光靠「节次+课名」没法区分是哪一次。与其赌一个，不如把两个候选都带回去，
        让调用方判断「我算出来的星期在不在候选里」。
        """
        if not by_key or period is None:
            return None
        nm = re.sub(r"\s+", "", str(name or ""))
        if not nm:
            return None
        for (k_slot, k_name), dwds in by_key.items():
            if re.sub(r"\s+", "", k_name) != nm:
                continue
            # 节次：把 "3" / "第三节" / "3,4" 都归一到起始节
            kp = _period_from_label(k_slot)
            if kp is None:
                try:
                    kp = int(k_slot)
                except (TypeError, ValueError):
                    kp = None
            if kp == period:
                return list(dwds) if isinstance(dwds, list) else [dwds]
        return None

    if not grid_text:
        return ImportImageOut(
            ok=False, courses=[], model="",
            warnings=warnings or ["识别失败，请稍后重试。"],
        )

    if not (grid_data.get("rows")):
        # 网格本身没读全就别往下走了 —— 位置错的数据比没有数据更糟。
        # 具体提示语已由 recognize_timetable 按「截断 / 真没读到」分别给出。
        return ImportImageOut(
            ok=False, courses=[], model=settings_vision_model(),
            warnings=_dedupe_keep_order(warnings) or ["没能从这张图里读出课程，请换一张更清晰的时间表截图重试。"],
            raw=grid_text[:4000],
        )

    # ---- 统一从归一化结果里取行与表头 ----
    #
    # vision 层已经把新格式（二维 grid）和旧格式（col_headers + rows）
    # **统一归一化成同一种形状**（rows 里每行带 cells 和 weeks），
    # 所以这里只认这一种形状即可 —— 不再需要 `if grid ... elif rows ...` 分叉。
    # 分叉解析是列错位的常见源头：两处只要对「表头行算不算数据行」理解不同，
    # 整表星期就会整体错位。解析只应发生在一个地方。
    col_headers = grid_data.get("col_headers") or []
    if not (isinstance(col_headers, list) and col_headers):
        col_headers = _salvage_headers(grid_text)

    # ---- 「cells 的第 i 格」对应「表头第几格」----
    #
    # 归一化之后，cells 的 idx 与表头下标**一一对应**：
    # vision 层生成 rows 时，cells 取的是「去掉第 1 个节次元素」之后的剩余列，
    # 而表头行本身也**不含**节次列 —— 两边都是从「第一个课程/表头列」开始数，
    # 所以不需要任何偏移。
    #
    # ⚠️⚠️ 但「一一对应」有个前提：**这一行的 cells 格数必须等于星期列数**。
    #   出现过的真实 bug（用户截图 + 自建测试图都复现）：
    #     七八节那行正确内容是「周一有课、周三有课」，
    #     模型却输出成 ["七八节 07,08", "", "计算机组成原理…", "", "面向对象…", ""]
    #     —— **开头的空位被它吞了**，整行左移一格。
    #     于是「计算机组成原理」（周一的课）被按位置判成了周二。
    #   这类「模型漏写开头空格」在跨列多的行上很常见（它倾向只写非空格）。
    #
    #   ⚠️ 判据必须用 `raw_len`（vision 层在补齐**之前**记下的原始格数），
    #      不能用 len(cells) —— 因为 vision 层会把短行末尾补空串对齐，
    #      补齐后 length 一律等于表头列数，**信号被抹掉了**（这就是
    #      早期判定永远不触发的原因）。
    #
    #   注意：这里**不做自动挪位**，只标记。位置错了该往左还是往右挪，
    #   光看这一行数据判定不了（模型漏的可能是开头、也可能是末尾），
    #   试过用「内容命中数」猜偏移，结果必然偏向大偏移、还会挪坏本来对的行。
    #   所以策略是：**如实报告 + 标黄让用户核对**。
    rows_raw = [r for r in (grid_data.get("rows") or []) if isinstance(r, dict)]
    n_week_cols = sum(1 for h in col_headers if _weekday_from_header(str(h)))
    n_cols_total = len(col_headers)

    # 本次识别里，模型给到的**最大原始格数**。
    # 判据是「比最长的那行短」而不是「不等于表头列数」——
    # 因为模型常常每一行都少写末尾的空格（只要没有课就不写），
    # 于是**所有行**都比表头短，那是常态、不是错误。
    # 真正可疑的是：**别的行都够长，就这一行特别短** ——
    # 说明它自己漏格了，这一行按位置推出来的星期整体是歪的。
    max_raw_len = max((r.get("raw_len") or 0) for r in rows_raw) if rows_raw else 0

    def _row_gap(r) -> bool:
        """这一行的原始格数是否**明显少于**同一张图里最长的行（= 列对齐不可信）。

        还要求「这一行确实有课」—— 一周全空的行（比如「九十节」没排课）
        本来就短，标黄它纯属噪声。
        """
        raw_len = r.get("raw_len")
        if not isinstance(raw_len, int) or not max_raw_len:
            return False
        if raw_len >= max_raw_len:
            return False
        cells = r.get("cells")
        if not isinstance(cells, list):
            return False
        return any(str(c).strip() for c in cells if c is not None)

    col_shift = 0

    def _wd_for(idx: int, row: dict | None = None) -> Optional[int]:
        """cells 的下标（从 1 开始）-> 星期。取不到返回 None。

        新版 grid 格式下 `weeks` 是直接由表头行生成的一一对应列表，最可靠；
        旧格式没有 weeks，才退回「按位置查表头」（可能带 col_shift 偏移）。

        ⚠️⚠️ 用 `weeks` 有一个**必须遵守的前提**：这一行的 cells 格数
        要和 weeks 一样长。出现过的真实 bug：
          模型把「周一有课、周二空」的行输出成开头丢了空位的样子
          （["", "课A", "课B", ""] 而不是 ["课A", "", "课B", ...]），
          cells 比 weeks 短了一截。
          此时若还按 `weeks[idx-1]` 取，整行会**左移一格**：
          周一的课被判成周二 —— 位置对了、星期错了，比读不出来更危险
          （用户不会怀疑，直接保存就进库了）。
        所以长度对不上时**不用 weeks**，改走下面的内容命中法（含逐行右移）。"""
        if row is not None:
            weeks = row.get("weeks")
            cells = row.get("cells") or []
            if isinstance(weeks, list) and len(weeks) == len(cells):
                try:
                    w = int(weeks[idx - 1])
                    if 1 <= w <= 7:
                        return w
                except (TypeError, ValueError, IndexError):
                    pass
        j = idx - col_shift                      # 换成表头下标
        if not (1 <= j <= len(col_headers)):
            return None
        return _weekday_from_header(str(col_headers[j - 1])) or None

    # ---- 【已废弃】逐行右移补偿 ----
    #
    # 曾经想用「内容命中法」猜出模型漏了几格、再把整行右移对齐。
    # **不可行、而且有害**，别再走这条路：
    #   · 「命中数」这个目标函数天然偏向大偏移（右移越多，非空格越是
    #     全部落在合法星期列上，命中数只增不减），根本选不出正确答案；
    #   · 更糟的是它会**改坏本来正确的行**：一二节那行本来就是
    #     「第1格=周一」，它却算出要右移1格 → 周一的课被判成周二。
    # 位置错了到底该往左还是往右挪，**光看这一行数据是无法判定的**
    # （模型漏的是开头还是末尾，从结果里看不出来）。
    # 所以正确做法是：**不动数据**，只把「这一行格数异常」如实告诉用户
    # （见下面 _row_gap 的使用处），让他核对 ——
    # 这比悄悄挪一下、挪错了还进库要好得多。

    # ---- 节次文字 -> 课程序号 / 连堂跨度 ----
    #
    # 模型报的 slot 是「节次列的第几行」这个**行号**，而库里 time_slots.order_no
    # 中间夹着 06:00/07:00/12:00 这些整点行；截图上的节次列本身也可能夹着整点行。
    # 所以「行号」跟「第几节课」根本不是一回事 —— 直接拿行号去套 class_slots
    # 会让第五、六节之后的课整体上移一节。
    # 因此**不信任 slot 数字，改为解析模型照抄回来的 label 文字**（"第五节" -> 5）。
    class_slots = [s for s in slot_items if s.period_type == "class"]
    n2order = {i: s.order_no for i, s in enumerate(class_slots, start=1)}

    def _period_from_label(label: str) -> Optional[int]:
        """从节次列的文字里解析出课程序号 1..N；解析不出返回 None。

        实际见到的写法（阳光学院课表就是这么印的）：
            "第一节" / "第5节" / "第一节课"       -> 1 / 5
            "一二节" / "三四节" / "九十节"        -> 1 / 3 / 9   ★连堂的合并写法
            "5"                                  -> 5
            "06:00" / "6:00"                     -> None（整点行，不是一节课）

        ⚠️ 「一二节」这种**不带「第」字**的合并写法最容易漏。
           它一格顶两节课（第 1、2 节连着上），是全周课表最常见的排版。
           早期只认「第X节」，导致这种行解析失败 -> 退回用行号 -> 整周节次全错位。
           这里取**起始节**（"三四节" -> 3），因为 order_no 是每节课一个槽，
           连续两节是两条记录，从起始节开始往下排才对得上。
        """
        s = str(label or "").strip()
        if not s:
            return None

        CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
              "十一": 11, "十二": 12}

        def _cn2int(t: str) -> Optional[int]:
            """把「一」「十二」「11」都换成数字。"""
            t = str(t or "").strip()
            if not t:
                return None
            if t.isdigit():
                return int(t)
            return CN.get(t)

        # 纯时间行（06:00 / 6:00）不是一节课 —— 必须先挡掉，
        # 否则下面的数字提取会把 "06:00" 里的 06 当成第 6 节
        if re.match(r"^\d{1,2}\s*[:：.]\s*\d{1,2}$", s):
            return None

        # 1) 「第X节」/「第X节课」
        #    量词 {1,4} 与下面连堂写法保持一致，覆盖「第十一二节」这种写法
        m = re.search(r"第\s*([一二三四五六七八九十]{1,4}|\d{1,2})\s*节", s)
        if m:
            body1 = m.group(1)
            v = _cn2int(body1)
            if v:
                return v
            # 「十一二」这种合并写法不在 CN 表里 —— 和规则 2 一样，
            # 从左往右贪心取最长能识别的前缀（「十一二」-> 11）
            i = 0
            while i < len(body1):
                two = body1[i:i + 2]
                one = body1[i]
                if two in CN:
                    return CN[two]
                if one in CN:
                    return CN[one]
                i += 1
        # 2) 连堂合并写法「一二节」/「十一二节」/「十一十二节」（不含「第」字）
        #
        # ⚠️⚠️ 量词必须是 {1,4}，不能是 {1,2}。出现过的真实 bug：
        #   「十一二节」= 第 11、12 节连堂，节次标签是 **3 个汉字**（十一二）；
        #   「十一十二节」则是 **4 个汉字**（十一十二）。
        #   写成 {1,2} 时 `^([一二三四五六七八九十]{1,2})节` 根本匹配不上——
        #   因为 3/4 个汉字超出了量词上限，于是整行解析返回 None，
        #   第 11、12 节的课**全部落不到正确槽位**（或整行被丢弃）。
        #   这两种写法在周课表里很常见（不少学校一天排 12 节、
        #   并把连堂合并成一个格子），所以必须认。
        #   取起始节（"十一二节" -> 11），因为 order_no 是每节课一个槽。
        #
        # 匹配到之后**不直接查 CN**（「十一二」不在表里），而是从左往右
        # 贪心取最长能识别的前缀：先试「十X」，再试单字。
        m = re.match(r"^([一二三四五六七八九十]{1,4})节", s)
        if m:
            body = m.group(1)
            v = _cn2int(body)
            if v:
                return v
            # 最长前缀优先：「十一二」-> 先命中「十一」(11)，返回 11
            i = 0
            while i < len(body):
                two = body[i:i + 2]
                one = body[i]
                if two in CN:
                    return CN[two]
                if one in CN:
                    return CN[one]
                i += 1
        # 数字写法：「1 2 节」/「01,02节」
        m = re.match(r"^(\d{1,2})\s*[、,，]?\s*\d{1,2}\s*节", s)
        if m:
            return int(m.group(1))

        # 3) 只有序号（"5"）；「01,02」这种取第一个
        m = re.match(r"^(\d{1,2})", s)
        if m:
            return int(m.group(1))
        # 4) 光一个汉字数字（"五"）
        if s in CN:
            return CN[s]
        return None

    def _period_span(label: str) -> int:
        """这一行**盖了几节课**（连堂合并写法时 > 1），解析不出返回 1。

        为什么需要它：阳光学院课表的行标题是「一二节」「三四节」——
        一**行**代表连着上的两**节**课。而库里 order_no 是每节课一个槽
        （第 1、2 节是两个槽），所以这种行要展开成两条记录，
        否则第 2 节就空着，课表看着像缺了一节。

        判据：看 label 里能不能解析出**两个不同的序号**
            「一二节」   -> 1,2  -> 2 节
            「三四节」   -> 3,4  -> 2 节
            「第一节」   -> 1    -> 1 节
            「九十节」   -> 9,10 -> 2 节
        """
        s = str(label or "").strip()
        if not s:
            return 1
        if re.match(r"^\d{1,2}\s*[:：.]\s*\d{1,2}$", s):
            return 1
        CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
              "十一": 11, "十二": 12}

        def _cn(t):
            t = str(t or "").strip()
            return int(t) if t.isdigit() else CN.get(t)

        # 汉字数字串：「一二节」-> ['一','二']「九十节」-> ['九','十']
        m = re.match(r"^([一二三四五六七八九十]{2,6})\s*节", s)
        if m:
            body = m.group(1)
            # 先试双字（十一/十二），再退单字 —— 保证「十一十二节」拆成 11、12
            parts, i = [], 0
            while i < len(body):
                two = body[i:i + 2]
                if two in CN:
                    parts.append(CN[two]); i += 2
                elif body[i] in CN:
                    parts.append(CN[body[i]]); i += 1
                else:
                    i += 1
            if len(parts) >= 2:
                return max(1, parts[-1] - parts[0] + 1)
        # 数字写法「1 2 节」/「01,02」
        m = re.match(r"^(\d{1,2})\s*[、,，]?\s*(\d{1,2})\s*节", s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > a:
                return max(1, b - a + 1)
        return 1


    raw_courses: list = []
    for r in rows_raw:
        try:
            slot_n = int(r.get("slot")) if r.get("slot") is not None else None
        except (TypeError, ValueError):
            slot_n = None
        # 优先用 label 文字定位节次；解析不出才退回 slot 行号（可能带整点行偏移）
        period = _period_from_label(r.get("label"))
        if period is None:
            period = slot_n
        # 这一行盖了几节课（「一二节」-> 2）。课表一行两节时要展开成两条，
        # 否则第 2 节空着，用户看着像少了一节课。
        span = _period_span(r.get("label"))
        # 课程序号 -> 真实 order_no；对不上就留空，下面 _align_courses 会兜底
        slot_order = n2order.get(period)
        cells = r.get("cells")
        if not isinstance(cells, list):
            continue
        # ---- 行对齐可信度检查 ----
        # 判据用 vision 层在**补齐之前**记下的原始格数（raw_len），
        # 不能看补齐后的 len(cells)（那时一律等于表头列数，信号已被抹掉）。
        #
        # ⚠️ 为什么不在这里「自动挪一下对齐」：
        #   位置错了到底该往左还是往右挪，**光看这一行数据判定不了** ——
        #   模型漏的可能是开头的空位，也可能是末尾的空位，从结果里看不出来。
        #   试过用「内容命中数」当目标函数去猜偏移，结果它**必然偏向大偏移**
        #   （右移越多非空格越全落在合法列上），而且会把本来正确的行也挪坏。
        #   所以这里选择**如实报告 + 标黄**，让用户核对 ——
        #   悄悄挪错还进了库，比让他多点两下有害得多。
        row_gap = bool(col_headers) and _row_gap(r)
        if row_gap:
            warnings.append(
                f"第 {period if period is not None else slot_n} 节这一行的格子数与表头对不上，"
                "该行课程的星期可能整体错位，请在核对表里重点看一眼（已标黄）。"
            )
        for idx, cell in enumerate(cells, start=1):
            if not cell or str(cell).strip().lower() in ("null", "none"):
                continue
            wd = _wd_for(idx, r)
            # 这一格不对应任何星期列就跳过（例如那格月份装饰位）
            if wd is None:
                continue
            # 一格多课用 | 分隔
            for one in str(cell).split("|"):
                one = one.strip()
                if not one:
                    continue
                # ★ 先把「课名-教师@教学楼教室」这串拆开 ★
                # 见 _split_cell_text 的说明：很多课表格子印的是一整串，
                # 不拆的话课程名会长达二十几个字（表格里显示不下），
                # 而教师/教学楼两栏全空，等于白识别。
                for part in _split_cell_text(one):
                    # ★ 连堂展开 ★
                    # 行标题「一二节」= 第 1、2 节连着上同一门课，
                    # 库里是每节一个槽，所以要往下连排 span 条。
                    for k in range(span):
                        p_n = (period + k) if period is not None else None
                        p_order = n2order.get(p_n) if p_n is not None else slot_order
                        raw_courses.append({
                            # 星期来自表头行（grid 格式）或位置查表（旧格式），
                            # 都是「程序推导的可验证事实」，不是模型的印象值。
                            "weekday": wd,
                            # ⚠️ col_index 只用于**旧格式**下让 _align_courses
                            #    拿列号查表头复核星期（那时 weekday 是按位置推的）。
                            #    新格式的 weekday 直接来自表头行，是权威值；
                            #    此时传 None，避免下游用另一套坐标把它改错。
                            "col_index": None,
                            "slot_order": p_order if p_order is not None else slot_order,
                            # 课程序号（1=第一节），详情轮用它对齐。
                            "slot_n": period,
                            "name": part["name"],
                            # 这一行的格数与表头对不上 -> 星期不可信，标黄让用户核对。
                            # 这里传的是**程序发现的事实**（格数不匹配），不是模型的印象，
                            # 所以不会像乱标黄那样制造满屏误报。
                            "_row_weekday_unsure": bool(row_gap),
                            # 第二轮模型「独立看图」判定的星期（可能为空表示没拿到）。
                            # 下游会拿它和上面按位置推的 wd 对账，不一致就标黄。
                            "_wd_check": _lookup_wd_check(detail_by_key, period, part["name"]),
                            # 本地拆出来的教师/教学楼先填上；
                            # 第二轮详情识别若拿到值会覆盖它（见下面的贴详情逻辑）
                            "teacher": part["teacher"],
                            "building": part["building"],
                            "location": part["location"],
                            "_from_cell": True,   # 标记：这些值来自本地拆解，不是模型给的
                            "_span_from_label": span > 1,  # 这行本来就是一格两节
                            "uncertain": [],
                        })

    courses, align_warnings = _align_courses(
        raw_courses, slot_items, payload.monday or "", col_headers)
    warnings.extend(align_warnings)

    return ImportImageOut(
        ok=True, courses=courses, warnings=_dedupe_keep_order(warnings),
        slots=sorted({c.slot_order for c in courses if c.slot_order}),
        model=settings_vision_model(), raw=grid_text[:4000],
    )


# 表格里写的「第几节」是**作息序号**（1..12），和库里 TimeSlot.order_no 不是一回事 ——
# order_no 里还夹着若干「整点」占位槽（06:00/07:00/12:00/13:00/18:00/21:00/22:00），
# 所以第 1 节实际是 order_no=3、第 5 节是 9、第 9 节是 14。
# 这张表把「作息第几节 -> 库里 order_no」钉死，避免靠时间去 nearestSlot 猜
# （猜的话「第 1 节 08:10」会正确落到 3，但「傍晚 18:30 的第 9 节」在
#  18:00 整点槽和 18:30 上课槽之间很容易落错）。
_PERIOD_TO_ORDER = {1: 3, 2: 4, 3: 5, 4: 6, 5: 9, 6: 10, 7: 11, 8: 12, 9: 14, 10: 15, 11: 16, 12: 17}

# 「大节」模板专用映射：课名所在的**小节起始号**（1、3、5、7…）-> 该大节的起始 order_no。
#
# 怎么来的（对着真实表格数的，别凭直觉改）：
#   这份模板的节次列仍然写着 1..12（小节号），但**课程单元格是上下合并的**，
#   课名只落在奇数行（1、3、5、7…），偶数行是合并下来的空行：
#
#       节次列  1  <- 课名在这行，实际占「第 1-2 小节」
#       节次列  2  <- 空（被上面合并了）
#       节次列  3  <- 课名，实际占「第 3-4 小节」
#       节次列  4  <- 空
#
#   所以：表格写 1 -> 库里 order_no 3（第一节），再靠 _NEXT_CLASS_ORDER 补出 4（第二节）。
#
# ⚠️ 键是**表格里真实出现的数字**（1,3,5,7,9,11），不是 1..6 的大节序号。
#    第一版我按「大节序号 1..6」写了这张表，结果表格写的 7 查不到，
#    全部落到 _nearest_slot 兜底，五门课被挂到了 order_no=2（07:00 占位槽）上 ——
#    界面上那几门课跑到了「07:00」那一行，而且不报任何错。
_BIG_PERIOD_TO_ORDER = {1: 3, 3: 5, 5: 9, 7: 11, 9: 14, 11: 16}

# 「上一个是小节」的下一个小节 order_no。
#
# 为什么要单独一张表、而不是写 `order + 1`：
#   TimeSlot.order_no 里**夹着整点占位槽**（06:00=1/07:00=2/12:00=7/13:00=8/
#   18:00=13/21:00=18/22:00=19），所以小节序号并不是连续的 ——
#   第 1 节=3、第 2 节=4（相邻，+1 可以），但第 4 节=6、第 5 节=9（+1 得 7，
#   而 7 是「12:00」那个占位槽）。
#   写 +1 的后果：连堂的下半节被挂到「12:00」那一行 —— 界面上那行没有课名标签，
#   用户看到的是"上午的课有一半跑到中午去了"，很难联想到是 +1 的锅。
#
# 这张表只描述**小节之间**的相邻关系，不含占位槽，所以不会走错。
_NEXT_CLASS_ORDER = {3: 4, 4: None, 5: 6, 6: None, 9: 10, 10: None,
                     11: 12, 12: None, 14: 15, 15: None, 16: 17, 17: None}


def _next_class_order(order: int):
    """同段落内的「下一小节」order_no；已是该段落最后一节（或不是小节槽）返回 None。

    用 None 而不是抛异常：调用方（导入时展开连堂）拿不到下一节就不展开，
    退化成单节显示，而不是让整张表导入失败。
    """
    return _NEXT_CLASS_ORDER.get(order)


@router.post("/import-sheet", response_model=ImportSheetOut)
async def import_timetable_sheet(payload: ImportSheetIn,
                                 user: User = Depends(get_current_user),
                                 db: Session = Depends(get_db)):
    """解析教务系统导出的课表表格（.xls / .xlsx），返回**未落库**的课程列表。

    为什么单独开一个接口、而不是让前端解析：
        前端是零依赖零构建的（不引 npm、不打包），拿不到 SheetJS 这种库；
        而 .xls 是老版 OLE2/BIFF 二进制，手写解析器又重又容易出错。
        后端本来就有 pandas + xlrd + openpyxl（识别链路用得上），
        加一个接口的成本远低于在前端重造一个 Excel 解析器。

    和 /import-image 一样**刻意不写库**：结果要用户过一眼再确认，
    复用同一套「核对弹窗 -> 确认导入 -> POST /timetable」流程。
    """
    import binascii

    raw = (payload.data or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="没收到表格数据")
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="表格数据不完整，请重新选一次文件") from exc
    if not data:
        raise HTTPException(status_code=400, detail="表格是空的，请检查文件")

    try:
        parsed = parse_timetable_excel(data, payload.name or "", payload.monday or "")
    except ValueError as exc:
        # 解析器抛的都是**已经写好给用户看**的文案（见 services/timetable_excel.py）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:                                   # noqa: BLE001
        logger.exception("课表表格解析失败: %s", exc)
        # ⚠️ 兜底文案里必须带上异常类型。
        #
        # 这条兜底曾经三次让排查陷入僵局：用户截图只有一句「这个表格读不了」，
        # 而这句话对应的真实异常只写在服务器日志里，用户根本拿不到 ——
        # 只能反复猜「是不是文件问题」「是不是旧版本」。
        #
        # 现在在文案末尾追加 `[类型名]`，用户截图时就把真凶一起带过来了，
        # 一眼能分清是编码/依赖问题（ModuleNotFoundError）还是数据问题（KeyError）。
        # 对普通用户依然是一句人话，对排查则是决定性线索。
        raise HTTPException(
            status_code=400,
            detail="这个表格读不了，可能是文件损坏或格式太特殊。"
                   f"（问题类型：{type(exc).__name__}）",
        ) from exc

    slots = db.query(TimeSlot).order_by(TimeSlot.order_no).all()
    slot_map = {s.order_no: s for s in slots}
    max_order = max((s.order_no for s in slots), default=17)
    valid_orders = set(slot_map)

    courses: list[ImportedCourse] = []
    warnings: list[str] = list(parsed.get("warnings") or [])
    missing_loc = 0

    for c in parsed.get("courses") or []:
        name = str(c.get("name") or "").strip()
        if not name:
            continue

        # ⚠️ 用 parse_period() 而不是 int()。
        #    这里曾经写的是 `_PERIOD_TO_ORDER.get(int(period))`，只要格子里不是纯数字
        #    （「第1节」「1-2」「上午」这种在各校教务模板里都很常见）就抛 ValueError，
        #    被最外层兜底 except 抓住 -> 用户看到「这个表格读不了，可能是文件损坏或
        #    格式太特殊」。文件明明好好的，用户却被告知文件坏了，完全无从下手。
        #    现在解析失败只会让这一条标黄，不影响整张表导入。
        period = parse_period(c.get("slot_order"))
        # ⚠️ 按解析器给的 slot_span 选映射表，别自己猜。
        #    span=2 表示这份表是「一大节两行」模板，节次列的数字是大节序号：
        #    表格写 1 -> 该大节起始 order_no = 3（占 3、4 两格）。
        #    用错表不会报错，只会让课落到隔壁节次上 —— 属于「看着像数据问题」
        #    的那类 bug，所以这里显式分支。
        span = int(c.get("slot_span") or 1)
        if period:
            order = (_BIG_PERIOD_TO_ORDER if span > 1 else _PERIOD_TO_ORDER).get(period)
        else:
            order = None
        uncertain: list = []
        if order is None or order not in valid_orders:
            # 表格里写了超出作息范围的节次：就近挂一个合法槽并标黄，
            # 让用户自己在核对表里改 —— 静默丢一条课比标黄麻烦得多。
            order = _nearest_slot("", slots) if slots else 3
            order = max(1, min(max_order, order))
            uncertain.append("slot_order")

        # 连堂：这一门课要挂到**哪几个 order_no** 上。
        #
        # 大节模板（span=2）里，表格的一格等于库里的两格（如第一节+第二节），
        # 所以这里**展开成两个相邻的 order_no**，各生成一条记录。
        #
        # 为什么不留一个"跨 2 格"的标记让前端自己画：
        #   课表的渲染、冲突检测、拖拽、AI伴学全都按「一条 = 一个节次」工作，
        #   让它们都去理解"这条要占两格"会渗透到十几个地方。
        #   展开成两条则**下游一行都不用改**，而且这两条天然满足
        #   「同一天 + 同课名 + 节次相邻」——正好命中前端已有的**连堂合并显示**
        #   逻辑（computeSpanMap），最终画出来就是一张合并的宽卡片。
        #
        # ⚠️ 相邻性用 _next_class_order() 判断，不能写 order+1：
        #    库里小节之间夹着整点占位槽（第4节=6 的下一个是第5节=9，不是 7），
        #    写 +1 会把课挂到「12:00」这种占位槽上，界面上那一行是空标签。
        orders = [order]
        if span > 1:
            nxt = _next_class_order(order)
            if nxt is not None:
                orders.append(nxt)

        # ⚠️ 星期是表格列头**数出来**的（第几列就是星期几），属于可验证事实，
        #    不像识别图片那样需要标黄。解析器拿不到表头时才会是 0。
        #    ⚠️ 用 try 包住 int()：解析器正常时 weekday 一定是 1..7 的整数，
        #    但这里多一道防线 —— 「一个格子的脏值不该让整张表导入失败」。
        try:
            weekday = int(c.get("weekday") or 0)
        except (TypeError, ValueError):
            weekday = 0
        if not (1 <= weekday <= 7):
            uncertain.append("date")

        s0 = slot_map.get(order)
        north = bool(c.get("building")) and c.get("building") in NORTH_BUILDINGS
        start = end = ""
        if s0:
            start = (s0.start_north if north else s0.start_south) or s0.start_time.strftime("%H:%M")
            end = (s0.end_north if north else s0.end_south) or s0.end_time.strftime("%H:%M")

        # ⚠️ 这里**不能**直接把 location 丢给 _norm_building()。
        #
        # _norm_building 是给「模型输出」用的：它假定 building/location 是两个
        # 已经分好的字段，当 building 为空时会去 location 里**从头找楼名、把
        # 楼名之前的部分切掉**。这套逻辑喂给结构化数据会出事 ——
        # 表格里的教室原文是「软件工程实验室二(启智楼306)」这种**楼名在中间**的
        # 形式，一挖就会切成 building=启智楼、location="306)"，把楼名前缀吃掉。
        #
        # 表格数据本身是准的，缺的只是「这串文字属于哪栋楼」。所以这里自己
        # 用同一张楼名表做**包含判断**（不切割原字符串），把 location 原样保留；
        # 只有确实找不出楼名时，才退回去用 _norm_building 的第三类判断
        # （虚拟地点/已知地点放行，纯教室号才标黄）。
        raw_loc = str(c.get("location") or "").strip()
        # ⚠️ 先把**简称**规范成正式楼名，再做后面的包含判断。
        #    必须在这一步做，而不是在判断里加分支：原文「商教514」里
        #    「商教」是楼、「514」是教室号，规范化的结果是「商德楼514」——
        #    这样 building 能认出来、location 也还留着教室号，两个都对。
        #    顺带好处：存进库的就是正式名，以后查数据/导航口径统一，
        #    不会出现同一栋楼在库里既有「商教」又有「商德楼」两种写法。
        raw_loc = _normalize_building_alias(raw_loc)
        building = ""
        for nm in sorted(KNOWN_BUILDINGS, key=len, reverse=True):
            if nm in raw_loc:
                building = nm
                break
        location = raw_loc
        if not building:
            # 复用 image 那条路已经调好的判断：不是「缺楼名」，只是这地点本来
            # 就不属于任何一栋楼（线上/田径场），或者是没收录的楼 —— 都不标黄。
            if raw_loc and not (_is_virtual_place(raw_loc) or _looks_like_place_name(raw_loc)):
                b2, loc2, b_unc = _norm_building("", raw_loc)
                building, location = b2, loc2
                if b_unc:
                    uncertain.append("building")
        if not raw_loc:
            missing_loc += 1

        # 一个大节展开成 2 条时，两条的 start/end 要按**各自的槽**取，
        # 否则第一、二节会显示成同一个时间（用户看到两张卡片时间一样会以为重复了）。
        for oi in orders:
            si = slot_map.get(oi)
            st, en = start, end
            if si:
                st = (si.start_north if north else si.start_south) or si.start_time.strftime("%H:%M")
                en = (si.end_north if north else si.end_south) or si.end_time.strftime("%H:%M")
            courses.append(ImportedCourse(
                date="", weekday=int(weekday) or None, slot_order=oi,
                start=st, end=en, name=name,
                teacher=str(c.get("teacher") or "").strip(),
                building=building, location=location,
                # 周次原样透传给前端（前端渲染时才知道「当前是第几周」）。
                # 解析器读不到周次时这里是空串，前端按「每周都上」处理。
                weeks=str(c.get("weeks") or "").strip(),
                uncertain=uncertain,
            ))

    if missing_loc:
        warnings.append(
            f"有 {missing_loc} 门课没在表格里找到教室，导入后可以手动补上。"
        )
    warnings.append("请核对下面的课程，确认无误后点「确认导入这些课程」。")

    return ImportSheetOut(
        ok=True, courses=courses, warnings=_dedupe_keep_order(warnings),
        sheet=str(parsed.get("sheet") or ""),
        slots=sorted({c.slot_order for c in courses if c.slot_order}),
    )


def _weeks_from_headers(col_headers: list) -> list:
    """把表头行（["周一 21", "周二 22", ...]）翻成星期数组（[1, 2, ...]）。

    新版 grid 格式下，表头就是数组的第 0 行，所以「第几列是星期几」是**现成的**，
    不需要模型再心算、也不需要事后猜偏移。解析不出的格（如 `09月`）写 0，
    调用方会把 0 当「不是课程列」跳过。
    """
    out = []
    for h in (col_headers or []):
        out.append(_weekday_from_header(str(h)))
    return out


def _weekday_from_header(text: str) -> int:
    """把表头文字（"周一"/"星期一"/"周二 (3/4)"）解析成 1..7；解析不出返回 0。"""
    if not text:
        return 0
    WD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
    m = re.search(r"周\s*([一二三四五六日天])|星期\s*([一二三四五六日天])", text)
    if m:
        return WD.get(m.group(1) or m.group(2), 0)
    return 0



def _dedupe_keep_order(items: list) -> list:
    """按内容去重且保持原顺序（警告文案去重用）。"""
    seen, out = set(), []
    for x in items:
        k = str(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def settings_vision_model() -> str:
    """实际使用的视觉模型名（仅用于把信息透出给前端排查）。"""
    try:
        from config import settings
        return getattr(settings, "GLM_VISION_MODEL", "") or "glm-4v-flash"
    except Exception:
        return "glm-4v-flash"
