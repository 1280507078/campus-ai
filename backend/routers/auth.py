"""登录 / 注册 / 账号设置。鉴权依赖 get_current_user 供其它路由复用。"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import jwt

from database import get_db
from models.orm import User
from models.schemas import (UserCreate, UserLogin, Token,
                            ProfileUpdate, PasswordChange, AccountDelete,
                            ScanCreateOut, ScanStatusOut, ScanConfirmIn)
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()
ALG = "HS256"


def hash_pwd(p: str) -> str:
    # TODO: 生产环境请用 passlib 做 bcrypt 哈希，这里仅占位
    return p


def create_token(uid: int) -> str:
    exp = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(uid), "exp": exp}, settings.SECRET_KEY, algorithm=ALG)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    校验登录态，取当前用户。

    ★ 为什么不用标准的 `Authorization` 头读 token（2026-10-14 build q 修的真 bug）
    ------------------------------------------------------------------------------
    公网部署之后，**托管平台的网关会改写 / 注入 `Authorization` 头**。实测证据
    （在同一台机器上用 curl 打线上域名）：

      · 客户端**什么都不发** → 后端收到的却不是空 → 走到「解不开 token」分支；
      · 客户端发 `Authorization: Basic abcdef` → 后端照样走到「解不开」分支，
        而按这里的代码，Basic 开头本该落到「你还没有登录」。
      两件事都说明：客户端发的那一版头**根本没到**，后端拿到的是网关塞进来的
      另一个值（响应头里 `Server: CloudStudio Gateway` 就是它）。

    后果不是"偶尔失败"，而是**所有需要登录的接口一律 401**：前端 `api()` 见 401
    就清掉 token、弹「登录状态已过期」、跳回登录页；用户重新登录，token 是新的、
    密码也对，下一次请求还是 401 —— 于是**永远登不进去**（问题记录：
    「一直登录不了」就是这个）。而且它只在部署环境出现，本机怎么测都正常。

    解法：另起一个网关不会去碰的自定义头 `X-Campus-Token`，并**优先**读它；
    `Authorization` 降级为回退 —— 本机 / 局域网 / 其它客户端仍然照旧能用，
    老路径一个字没砍。前端 `api()` 两个头都发，哪条通都能认证。
    """
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="你还没有登录，请先登录后再操作")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALG])
        uid = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="登录状态已过期，请退出后重新登录")
    user = db.query(User).get(uid)
    if not user:
        raise HTTPException(status_code=401, detail="账号不存在或已被删除，请重新注册")
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """管理员依赖：在 `get_current_user` 之上再加一道「是不是管理员」。

    ★ 为什么**返回 403 而不是 404**（「假装这个接口不存在」）：
      403 能让真正的前端分清「没登录（401）」「登录了但不是管理员（403）」，
      admin.js 据此显示不同的提示；404 会让人以为是路径写错，白查半天。

    ★ 为什么读 `is_admin` 时要 `bool(...)` 而不是直接 `if not user.is_admin`：
      老库补列后，历史行的值可能是 NULL（`ALTER TABLE ADD COLUMN` 带 DEFAULT
      在 SQLite 上对已存在行生效，但 MySQL/PG 的语义不完全一样），
      `None` 在布尔上下文里是假，行为正确，但显式写出来省得下次有人怀疑。
    """
    if not bool(getattr(user, "is_admin", False)):
        raise HTTPException(status_code=403, detail="这个页面需要管理员账号")
    return user


# 读取 token 的请求头，**顺序即优先级**。见上面 get_current_user 的说明。
TOKEN_HEADER_PRIMARY = "x-campus-token"


def _token_from_request(request: Request) -> str:
    """从请求里取出 token：自定义头优先，标准 Authorization 回退。取不到返回 ""。"""
    v = (request.headers.get(TOKEN_HEADER_PRIMARY) or "").strip()
    if v:
        return v
    auth = (request.headers.get("authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return ""


def display_name_taken(db: Session, name: str, exclude_uid: int | None = None) -> bool:
    """显示名是否已被占用。

    全站显示名唯一：一个名字要么是某人的用户名、要么是某人的昵称，不能撞车。
    检查时同时比对 username 与 nickname 两列，`exclude_uid` 用于「改自己昵称」时排除自己。
    """
    q = db.query(User).filter(or_(User.username == name, User.nickname == name))
    if exclude_uid is not None:
        q = q.filter(User.id != exclude_uid)
    return db.query(q.exists()).scalar()


@router.post("/register", response_model=Token)
def register(data: UserCreate, db: Session = Depends(get_db)):
    if display_name_taken(db, data.username):
        raise HTTPException(status_code=400, detail="该用户名已被注册，换一个试试吧")
    # 昵称默认等于用户名（注册页不单独填昵称）
    nickname = (data.nickname or "").strip() or data.username
    if nickname != data.username and display_name_taken(db, nickname):
        raise HTTPException(status_code=400, detail="该昵称已被占用，换一个试试吧")
    user = User(
        username=data.username,
        hashed_password=hash_pwd(data.password),
        nickname=nickname,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return Token(access_token=create_token(user.id))


@router.post("/login", response_model=Token)
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user or user.hashed_password != hash_pwd(data.password):
        raise HTTPException(status_code=401, detail="用户名或密码不正确，请重新输入")
    return Token(access_token=create_token(user.id))


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    # is_admin 一定要带上：前端「我的」页据此决定要不要显示「管理后台」入口。
    # 这只是**显示开关**，真正的权限在后端 get_admin_user —— 前端改 localStorage
    # 顶多让按钮出现，进 admin.html 后请求 /api/admin/* 仍然是 403。
    return {"id": user.id, "username": user.username, "nickname": user.nickname,
            "is_admin": bool(getattr(user, "is_admin", False))}


# ---------------- 账号设置：改昵称 / 改密码 / 注销 ----------------

@router.patch("/profile")
def update_profile(data: ProfileUpdate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """改昵称。用户名不可改；昵称要与其他人的用户名/昵称都不重复。"""
    nick = data.nickname.strip()
    if len(nick) < 2:
        raise HTTPException(status_code=400, detail="昵称至少 2 个字符")

    # 昵称和当前一致 → 视为成功，避免用户点一下报错
    if nick == user.nickname:
        return {"ok": True, "nickname": nick, "unchanged": True}

    if display_name_taken(db, nick, exclude_uid=user.id):
        raise HTTPException(status_code=400, detail="该昵称已被占用，换一个试试吧")

    user.nickname = nick
    db.commit()
    db.refresh(user)
    return {"ok": True, "nickname": user.nickname}


@router.post("/password")
def change_password(data: PasswordChange, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """改密码：先验旧密码，再写新密码。"""
    if user.hashed_password != hash_pwd(data.old_password):
        raise HTTPException(status_code=400, detail="原密码不正确，请重新输入")
    if data.new_password == data.old_password:
        raise HTTPException(status_code=400, detail="新密码不能和原密码相同")

    user.hashed_password = hash_pwd(data.new_password)
    db.commit()
    return {"ok": True}


@router.delete("/account")
async def delete_account(data: AccountDelete, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """注销账号：输入密码确认后，删除账号及其**全部**数据，不可恢复。

    要清的东西（漏一个都会出隐私事故）：
      1. SQLite：课表 Course / 对话 ChatSession / 测评 Assessment / 用户 User
      2. Qdrant：该用户的全部向量记忆（情绪陪伴的长期记忆）

    ⚠️ 第 2 条**不能省**：users.id 没有 AUTOINCREMENT，注销后 id 会被下一个
    注册的用户复用；向量记忆靠 payload.user_id 关联，不删就会让新用户
    检索到前一个人的隐私记录。实测复现过（新用户 id=1 检索出旧用户含学号的记忆）。
    ⚠️ 取 user.id 必须在 db.delete(user) **之前** —— 删了就取不到了。
    """
    if user.hashed_password != hash_pwd(data.password):
        raise HTTPException(status_code=400, detail="密码不正确，无法注销账号")

    uid = int(user.id)          # 先留存，后面 db.delete 之后就取不到了

    # --- 0) ★ 先把这个人的数据**输出**出来，再动手删（2026-10-14 需求）---
    #    范围 = 「个人」：SQLite 的课表 / 情绪对话原文 / 测评 + 向量库条数，
    #    打印一份人能读的清单，并落一份 JSON 到 backend/data_export/。
    #    ⚠️ 顺序很重要 —— 必须在 db.delete 之前，删完就什么都读不到了。
    #    ⚠️ 向量条数先 await 出来再交给同步的导出模块：data_export **不开事件循环**
    #       （它同时被 lifespan 里的换包流程调用，那里已经在循环里了，见其文件头）。
    #    ⚠️ 整体 try 住：导出失败不能让用户注销不掉。
    try:
        from services import data_export
        # ⚠️ 必须在这里**自己导入** qdrant：本函数下面的「清向量」块里才写了
        #    `from services import qdrant as memory`，那是一个**函数内局部名**，
        #    在这段代码的位置上还不存在 —— 直接用 `memory` 会抛
        #    `UnboundLocalError: cannot access local variable 'memory'`。
        #    （实测踩到：被下面的 try 吞掉，表现为"注销成功但没有任何输出"，
        #      也就是用户最不想要的那种静默失败。）
        from services import qdrant as memory_svc
        vec_points = await memory_svc.count_user_memory(uid)
        data_export.audit_user(uid, username=user.username,
                               vector_user_points=vec_points,
                               reason="用户主动注销账号")
    except Exception as e:                       # noqa: BLE001
        logger.warning("注销前的数据输出失败（不影响注销）：%s: %s", type(e).__name__, e)

    # --- 1) 清 Qdrant 向量记忆（云端，可能超时/不可用，失败不阻塞注销）---
    try:
        from services import qdrant as memory
        await memory.delete_user_memory(uid)
    except Exception as e:
        logger.warning("注销时清空向量记忆失败（不影响账号注销）：%s: %s", type(e).__name__, e)

    # --- 2) 清 SQLite 关联数据 ---
    # 显式删除，避免老库没有 ON DELETE CASCADE 时留下孤儿行
    from models.orm import Course, ChatSession, Assessment
    for model in (Course, ChatSession, Assessment):
        db.query(model).filter(model.user_id == uid).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    return {"ok": True}


# ---------------- 扫码登录（演示级：链路完整，见 services/scan_login.py） ----------------

@router.post("/scan/create", response_model=ScanCreateOut)
def scan_create():
    """① 电脑端：申请一张扫码票据，前端把它编码成二维码显示。

    这是**公开接口**（还没登录的人才能扫码登录），所以不带鉴权。
    返回的 ticket 里没有任何用户信息，即便被别人看到也无法据此登录
    —— 必须手机端已登录并确认，电脑端才能换到 token。
    """
    from services import scan_login

    info = scan_login.create_ticket()
    return ScanCreateOut(ticket=info["ticket"], expires_in=info["expires_in"])


@router.get("/scan/status", response_model=ScanStatusOut)
def scan_status(ticket: str):
    """② 电脑端：轮询票据状态（pending / scanned / confirmed / expired）。

    ⚠️ 这个接口**不签发 token**，只汇报状态。真正的登录态由
    `/scan/consume` 领取 —— 这样即使有人拿到票号反复查状态，
    也拿不到任何人的登录凭证。
    """
    from services import scan_login

    st = scan_login.peek_status(ticket)
    if st is None:
        # 票据不存在或已过期，统一回 expired（不区分，避免泄露票号是否合法）
        return ScanStatusOut(status="expired", expires_in=0)
    return ScanStatusOut(status=st["status"], expires_in=st["expires_in"],
                         scanned=st["status"] in ("scanned", "confirmed"))


@router.post("/scan/consume", response_model=Token)
def scan_consume(ticket: str, db: Session = Depends(get_db)):
    """③ 电脑端：票据已确认 → **一次性领取**登录 token。

    票据在这里被销毁（见 scan_login.consume_ticket），同一张票换不了第二次。
    另外这里会再查一次用户是否还存在 —— 手机上确认完之后、电脑端来领取之前
    这段时间里账号被注销了，就不该再发出 token。
    """
    from services import scan_login

    uid = scan_login.consume_ticket(ticket)
    if uid is None:
        raise HTTPException(status_code=400, detail="二维码已过期或尚未确认，请刷新后重试")

    user = db.query(User).get(uid)
    if not user:
        raise HTTPException(status_code=401, detail="该账号已被注销，请重新注册")
    return Token(access_token=create_token(user.id))


@router.post("/scan/confirm")
def scan_confirm(data: ScanConfirmIn, user: User = Depends(get_current_user)):
    """④ 手机端：确认用**当前登录的账号**给这张票授权。

    ⚠️ 必须已登录（Depends(get_current_user)）：
    这样「扫了别人的码」只会用你自己的账号登进电脑端，不会造成他人信息泄露；
    反过来也意味着**不要把自己的确认链接发给别人**。
    """
    from services import scan_login

    ticket = (data.ticket or "").strip()
    if not ticket:
        raise HTTPException(status_code=400, detail="缺少二维码参数")

    # 先标记已扫描（第一次调用时），再确认。两步都做，兼容手机端
    # 「打开页面就确认」和「打开页面 -> 点按钮确认」两种交互。
    scan_login.mark_scanned(ticket, user.id)
    ok = scan_login.confirm_ticket(ticket, user.id)
    if not ok:
        raise HTTPException(status_code=400, detail="二维码已过期，请在电脑上刷新后重新扫描")

    return {"ok": True, "nickname": user.nickname or user.username}


@router.get("/scan/stats")
def scan_stats():
    """票据概况：只返回数量统计，不暴露任何票据 ID。用于自检/演示讲原理。"""
    from services import scan_login

    return scan_login.stats()
