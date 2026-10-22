"""意见反馈：用户提交 + 查看自己提交过的（含管理员回复）。

## 为什么单独一个页面，而不是「我的」页里嵌一个输入框
用户 2026-10-19 明确要求：**在「我的」页底部点按钮跳到新页面填写**。理由站得住：
「我的」页已经有昵称、改密码、使用引导、注销四张卡，再塞一个多行输入框，
整页会变成设置表单；而反馈是要写字的事，单独一页能给出足够的空间和说明。

## 为什么回复只由管理员写（用户不能回帖）
一旦允许来回对话，它就变成了一个没有通知、没有已读、没有关闭状态的私信系统 ——
用户不会知道管理员什么时候回，管理员也不知道用户有没有看到。
所以这里的模型是**单向**的：用户提一条，管理员回一条，用户下次打开
「我的反馈」就能看到回复。要追问就再提一条新的。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.orm import User, Feedback
from models.schemas import FeedbackIn, FeedbackOut
from routers.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

# 类型白名单。未知值回落成 other，而不是 422 —— 见 schemas.FeedbackIn 的说明。
FEEDBACK_KINDS = {
    "suggest": "功能建议",
    "bug": "问题反馈",
    "ux": "使用体验",
    "other": "其他",
}
CONTENT_MIN = 4          # 至少 4 个字，否则「不好用」这种反馈等于没写
CONTENT_MAX = 1000
CONTACT_MAX = 64
PAGE_MAX = 32


def kind_label(kind: str) -> str:
    return FEEDBACK_KINDS.get(kind or "", FEEDBACK_KINDS["other"])


def iso_utc(dt) -> str:
    """datetime -> ISO8601（带 Z）。

    库里存的是 UTC（`datetime.utcnow`），前端 `new Date(iso)` 会按本地时区
    还原。**必须带 Z**：不带的话浏览器会把它当本地时间，东八区会整整差 8 小时
    （这个坑在情绪趋势接口上踩过，注释也留在那儿）。
    """
    d = dt or datetime.utcnow()
    return d.isoformat() + "Z"


def feedback_to_out(row: Feedback, username: str = "") -> FeedbackOut:
    """ORM -> 出参。用户侧和管理员侧共用同一个转换，避免两边字段口径漂移。"""
    return FeedbackOut(
        id=int(row.id),
        kind=row.kind or "other",
        kindLabel=kind_label(row.kind or "other"),
        content=row.content or "",
        contact=row.contact or "",
        page=row.page or "",
        createdAt=iso_utc(row.created_at),
        username=username,
        reply=row.reply or "",
        repliedAt=iso_utc(row.replied_at) if row.replied_at else "",
    )


@router.post("", response_model=FeedbackOut, status_code=201)
def submit(data: FeedbackIn, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """提交一条反馈。

    ★ 校验必须在这里做（而不是只靠前端 `maxlength`）：前端限制只是提示，
      F12 改一下就能绕过。下限 4 个字是为了挡住「不好用」这类没法处理的反馈 ——
      这不是刁难用户，是「一句话反馈」既帮不到他，也帮不到我们。
    """
    content = (data.content or "").strip()
    if len(content) < CONTENT_MIN:
        raise HTTPException(
            status_code=400,
            detail=f"再多写几个字吧（至少 {CONTENT_MIN} 个字），这样才看得懂你遇到的问题",
        )
    if len(content) > CONTENT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"太长了（最多 {CONTENT_MAX} 字），把最关键的一段留下就好",
        )

    kind = data.kind if data.kind in FEEDBACK_KINDS else "other"
    row = Feedback(
        user_id=user.id,
        kind=kind,
        content=content,
        contact=(data.contact or "").strip()[:CONTACT_MAX],
        page=(data.page or "").strip()[:PAGE_MAX],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return feedback_to_out(row, user.username)


@router.get("/mine", response_model=list[FeedbackOut])
def mine(limit: int = 20, user: User = Depends(get_current_user),
         db: Session = Depends(get_db)):
    """我提交过的反馈（最近的在前），带管理员回复。

    为什么不让前端自己按 id 查：那样要开一个「按 id 取反馈」的公开接口，
    一旦漏了 `user_id == 当前用户` 的过滤就是越权读取。这里从查询起点
    就锁死在当前用户上，前端拿不到别人的。
    """
    limit = max(1, min(int(limit or 20), 50))
    rows = (
        db.query(Feedback)
        .filter(Feedback.user_id == user.id)
        .order_by(Feedback.created_at.desc(), Feedback.id.desc())
        .limit(limit)
        .all()
    )
    return [feedback_to_out(r) for r in rows]
