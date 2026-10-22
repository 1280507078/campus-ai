"""管理后台：全站数据统计 + 反馈列表 + 回复反馈。

## 为什么统计口径都放后端算
前端逐个拉列表再自己 count，看着简单，但会被 `limit` 截断 —— 用户表里
200 个人，前端只拿到一页 50 条，统计就永远是 50。所以计数一律在库里算。

## 为什么这个文件里没有任何「写用户数据」的接口
管理员在这里只做两件事：**看数字**、**回反馈**。不提供改课表、删账号、
看某个人情绪记录的能力 —— 统计页里连用户名都只在反馈列表出现，
因为反馈是用户主动递给管理员的，看它不算越权；课表和情绪记录不是。

## 和开发树的差别（本树是精简包）
开发树的看板还有「伴学对话」「待办完成率」两组数字，本树的伴学对话存在
浏览器 localStorage、也没有待办功能，库里查不到对应记录。与其编一个
查不到的数字上大屏，不如**不显示**这两组 —— 看板上的每个数都必须
能在库里指到出处，这是这个页面的底线。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models.orm import User, Course, Assessment, ChatSession, Feedback
from models.schemas import FeedbackOut, FeedbackReplyIn
from routers.auth import get_admin_user
from routers.feedback import feedback_to_out, iso_utc, kind_label, FEEDBACK_KINDS

router = APIRouter(dependencies=[Depends(get_admin_user)])

# 服务器本地时区相对 UTC 的偏移。用「本机现在」减「UTC 现在」得到，
# **不写死 +8**：换个时区的机器跑也不会把「今天」算错。
# 为什么需要它：created_at 全部存 UTC，直接按 UTC 日期分桶的话，
# 东八区 0:00-8:00 的操作会被算进「前一天」，看板上今天的数字会偏小。
_OFFSET = datetime.now() - datetime.utcnow()

REPLY_MAX = 500
DAILY_DAYS = 7


def _count(db: Session, model, *criteria) -> int:
    from sqlalchemy import func
    q = db.query(func.count(model.id))
    if criteria:
        q = q.filter(*criteria)
    return int(q.scalar() or 0)


def _only_users(q, model, admin_ids):
    """把**管理员自己的记录**排掉。

    需求：「数据不要识别管理员，识别的是用户的数据」。只把「注册用户 /
    活跃 / 有课表」三处排掉是不够的 —— 课程条数、测评、情绪对话、反馈、
    每日走势**还带着管理员自己点出来的数据**，答辩时上面写着「反馈 5 条」，
    其实 4 条是自己点着试的，数字不可信。所以统一在这里排：
    凡是带 user_id 的表，一律只算普通用户。

    ⚠️ `admin_ids` 为空时**不能**拼 `NOT IN ()`：SQLAlchemy 会渲染成
       `NOT IN (NULL)`，整个结果集变空 —— 一个管理员都没建时看板全 0。
       所以判空后原样返回。
    """
    if admin_ids and hasattr(model, "user_id"):
        return q.filter(~model.user_id.in_(admin_ids))
    return q


def _users_of(db: Session, model, since=None, col=None, exclude=None) -> set:
    """某一类记录里出现过的**去重用户**。

    ⚠️ 时间列不能默认写 `model.created_at`：有些表可能没有 created_at
       （比如「一人一条、原地覆盖」的表只有 updated_at）。按
       「created_at → updated_at」顺序找，找不到才报错 —— 默认值在函数开头
       求值，即使 not since 也会先 AttributeError，统计接口直接 500。
    """
    if col is None:
        col = getattr(model, "created_at", None) or getattr(model, "updated_at", None)
    q = db.query(model.user_id).distinct()
    if exclude:
        q = q.filter(~model.user_id.in_(exclude))
    if since is not None:
        if col is None:
            raise AttributeError(f"{model.__name__} 既没有 created_at 也没有 updated_at")
        q = q.filter(col >= since)
    return {int(uid) for (uid,) in q.all() if uid}


def _daily_buckets(db: Session, model, col, since, exclude=None) -> dict:
    """按**服务器本地日期**分桶计数：{"2026-10-19": 3, ...}。"""
    out: dict = {}
    q = _only_users(db.query(col), model, exclude or set())
    for (dt,) in q.filter(col >= since).all():
        if not dt:
            continue
        key = (dt + _OFFSET).date().isoformat()
        out[key] = out.get(key, 0) + 1
    return out


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """全站概览。数字全部是**真实库里的**，没有演示用的假数据。

    为什么分成 users / courses / emotion / feedback 四组：
    答辩时最常被问的三个问题是「有多少人用」「有多少真实数据」「AI 真被用了吗」，
    分组之后一眼能答；平铺十几个数字反而没人看。

    ⚠️ `active7d` 的口径写清楚：近 7 天**产生过动作**（测评 / 情绪对话 /
    反馈）的去重用户数。它不等于「打开过页面」——
    项目没有埋点，也刻意不做（埋点会记 IP 和停留时长，对学生项目没必要）。
    """
    now = datetime.utcnow()
    since7 = now - timedelta(days=DAILY_DAYS)
    since30 = now - timedelta(days=30)

    # ★ 统计**不含管理员账号**：admin 是系统内置的，把它算进「注册用户」
    #   会让看板永远多 1 个人，答辩时说「有 1 个真实用户」对不上数字。
    #   活跃用户也要扣掉 —— 管理员自己进来看看板不该算「有人在用」。
    admin_ids = {int(i) for (i,) in
                 db.query(User.id).filter(User.is_admin == True).all()}   # noqa: E712

    # ★★ **所有**计数统一排掉管理员自己的记录（见 _only_users）。
    from sqlalchemy import func

    def cnt(model, *criteria) -> int:
        q = _only_users(db.query(func.count(model.id)), model, admin_ids)
        if criteria:
            q = q.filter(*criteria)
        return int(q.scalar() or 0)

    users_total = _count(db, User, User.is_admin == False)                # noqa: E712
    users_new7 = _count(db, User, User.is_admin == False,                 # noqa: E712
                        User.created_at >= since7)

    # 活跃口径：近 7 天产生过动作的去重用户（_users_of 里已经把 admin 排掉了）
    active = set()
    active |= _users_of(db, Assessment, since7, exclude=admin_ids)
    active |= _users_of(db, Feedback, since7, exclude=admin_ids)
    active |= _users_of(db, ChatSession, since7, exclude=admin_ids)

    course_users = {int(uid) for (uid,) in db.query(Course.user_id).distinct().all() if uid}
    course_users -= admin_ids

    # ---- 情绪：近 30 天的两个维度均值（只陈述数字，不做判断）----
    recent = _only_users(db.query(Assessment), Assessment, admin_ids) \
        .filter(Assessment.created_at >= since30).all()
    dep = [int((r.result or {}).get("depression") or 0) for r in recent]
    anx = [int((r.result or {}).get("anxiety") or 0) for r in recent]
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0

    # ---- 反馈：按类型分布 + 待回复数 ----
    by_kind = [{"kind": k, "label": v, "count": cnt(Feedback, Feedback.kind == k)}
               for k, v in FEEDBACK_KINDS.items()]
    unclassified = _only_users(db.query(func.count(Feedback.id)), Feedback, admin_ids) \
        .filter(~Feedback.kind.in_(list(FEEDBACK_KINDS.keys()))).scalar() or 0
    if unclassified:
        by_kind.append({"kind": "other", "label": kind_label("other"),
                        "count": by_kind[-1]["count"] + int(unclassified)})

    fb_total = cnt(Feedback)
    fb_replied = int(_only_users(db.query(func.count(Feedback.id)), Feedback, admin_ids)
                     .filter(Feedback.reply != "", Feedback.reply.isnot(None)).scalar() or 0)

    # ---- 近 7 天每日走势 ----
    d_users = _daily_buckets(db, User, User.created_at, since7, exclude=admin_ids)
    d_assess = _daily_buckets(db, Assessment, Assessment.created_at, since7, exclude=admin_ids)
    d_fb = _daily_buckets(db, Feedback, Feedback.created_at, since7, exclude=admin_ids)
    d_chats = _daily_buckets(db, ChatSession, ChatSession.created_at, since7, exclude=admin_ids)
    daily = []
    for i in range(DAILY_DAYS - 1, -1, -1):
        day = (now + _OFFSET) - timedelta(days=i)
        key = day.date().isoformat()
        daily.append({
            "date": key,
            "label": f"{day.month}/{day.day}",
            "users": d_users.get(key, 0),
            "assessments": d_assess.get(key, 0),
            "feedback": d_fb.get(key, 0),
            "chats": d_chats.get(key, 0),
        })

    return {
        "generatedAt": iso_utc(now),
        "users": {
            "total": users_total,
            "new7d": users_new7,
            "active7d": len(active),
            "withCourses": len(course_users),
        },
        "courses": {
            "total": cnt(Course),
            "users": len(course_users),
        },
        "emotion": {
            "assessments": cnt(Assessment),
            "users": len(_users_of(db, Assessment, exclude=admin_ids)),
            "chats": cnt(ChatSession),
            "avgDepression30d": avg(dep),
            "avgAnxiety30d": avg(anx),
            "assessed30d": len(recent),
        },
        "feedback": {
            "total": fb_total,
            "new7d": cnt(Feedback, Feedback.created_at >= since7),
            "replied": fb_replied,
            "pending": max(0, fb_total - fb_replied),
            "byKind": by_kind,
        },
        "daily": daily,
    }


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0),
                  only: str = Query("", pattern="^(|pending|replied)$"),
                  db: Session = Depends(get_db)):
    """反馈列表（最近的在前），带提交人和回复。

    `only=pending` 是管理员最常用的视图：只看待回复的。
    为什么不在前端过滤：前端过滤要先拉全量，反馈一多就是几百条，
    而「待回复」恰好是索引能直接筛掉的。
    """
    q = db.query(Feedback, User.username).outerjoin(User, User.id == Feedback.user_id)
    if only == "pending":
        q = q.filter((Feedback.reply == "") | (Feedback.reply.is_(None)))
    elif only == "replied":
        q = q.filter(Feedback.reply != "", Feedback.reply.isnot(None))
    rows = (q.order_by(Feedback.created_at.desc(), Feedback.id.desc())
             .offset(offset).limit(limit).all())
    return [feedback_to_out(row, uname or "") for row, uname in rows]


@router.patch("/feedback/{fid}", response_model=FeedbackOut)
def reply_feedback(fid: int, data: FeedbackReplyIn,
                   admin: User = Depends(get_admin_user),
                   db: Session = Depends(get_db)):
    """回复一条反馈；传空串 = 撤回回复。

    ★ 回复存进反馈本身，**不另建 messages 表**：一来一回在这里是常态之外的
      少数情况（要追问就再提一条），为它建会话表只会让「有没有回复」
      这个最简单的判断变成一次 join。
    ★ `replied_by` 记的是管理员 id：出问题时能查到是谁回的，
      但页面上不显示（对用户没有意义）。
    """
    row = db.query(Feedback).get(fid)
    if not row:
        raise HTTPException(status_code=404, detail="这条反馈不存在，可能已被删除")

    txt = (data.reply or "").strip()
    if len(txt) > REPLY_MAX:
        raise HTTPException(status_code=400, detail=f"回复最多 {REPLY_MAX} 字")

    if txt:
        row.reply = txt
        row.replied_at = datetime.utcnow()
        row.replied_by = int(admin.id)
    else:
        # 撤回：把三处一起清掉，否则会留下「没有回复内容但有回复时间」的脏数据
        row.reply = ""
        row.replied_at = None
        row.replied_by = None
    db.commit()
    db.refresh(row)

    uname = ""
    if row.user_id:
        u = db.query(User).get(int(row.user_id))
        uname = (u.username if u else "")
    return feedback_to_out(row, uname)
