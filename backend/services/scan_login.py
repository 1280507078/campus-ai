"""扫码登录（演示级）：把「手机扫码 → 网页自动登录」做成一条可用的链路。

## 这个功能是干什么的

登录页给两个入口：**账号密码** 和 **扫码登录**。选扫码后，页面显示一个二维码，
用手机扫一下，网页就自动登录进去了 —— 和你在电脑上登微信/招商银行 App 的体验一样。

## ⚠️ 先说清楚边界（避免误解）

本项目的扫码是**演示级实现**，不是真的对接了某个校园 App：
  · 二维码里放的是一个**一次性、有时效的随机票据**（ticket），不是账号密码；
  · 「谁来扫/谁来确认」由服务端记录，前端通过**轮询**接口得知票的状态；
  · 手机端扫码后打开的确认页（`/app/scan-confirm.html`）由本项目自己提供，
    真正上线时要换成校园 App 里的一个原生页面。

这样设计的好处是**链路是真的**（票据、有效期、一次性、防重放都实现了），
只是"扫码的那一端"用了网页代替原生 App —— 竞赛演示足够，也便于讲清楚原理。

## 安全设计（虽然是演示，但这几条是真的）

1. **票据一次性**：用过立刻作废，重放无效。
2. **强时效**：默认 120 秒过期，过期后即使被截获也用不了。
3. **票据不落前端 localStorage**，只存在内存字典里，进程重启即失效。
4. **扫码确认需要已登录**：手机端必须先登录自己的账号，才能给电脑端授权。
   也就是说，扫别人的码只会用**你自己的**账号登进去，不会泄露别人信息。
5. **不出账号密码**：二维码、轮询响应里都只有票据 ID，没有密码、没有 token。
   真正的 token 只在「电脑端轮询到已确认」那一刻，由服务端签发一次。
"""
import logging
import secrets
import time
import threading
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# 票据有效期（秒）。手机上扫码 → 确认通常几秒内完成，120 秒足够宽裕；
# 再长就纯粹是给攻击者留窗口了。
TICKET_TTL = 120

# 清理阈值：票据攒到这么多就顺手清一次过期项，避免长期运行内存缓慢增长
_CLEAN_THRESHOLD = 200

# 票据存储：{ticket_id: {...}}
# 用内存字典而不是数据库，原因有两个：
#   1) 票据是**秒级**生命周期的临时数据，没必要落盘、也没必要持久化；
#   2) 进程重启后所有票据自动失效，这本身就是一道安全兜底。
# 代价是**多进程部署时会失效**（每个 worker 各存一份）——
# 本项目是单进程 uvicorn，不构成问题；真要上多进程需换成 Redis。
_tickets: Dict[str, dict] = {}
_lock = threading.Lock()


def _cleanup_locked() -> int:
    """清掉过期票据。**调用前必须已持有 _lock**。返回清理条数。"""
    now = time.time()
    dead = [t for t, v in _tickets.items() if v["expires_at"] <= now]
    for t in dead:
        _tickets.pop(t, None)
    return len(dead)


def create_ticket() -> dict:
    """新建一张扫码票据。

    Returns:
        {"ticket": str, "expires_in": int}
        ticket 是给二维码编码用的随机串，**不含任何用户信息**。
    """
    with _lock:
        if len(_tickets) > _CLEAN_THRESHOLD:
            _cleanup_locked()
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        _tickets[ticket] = {
            "status": "pending",      # pending -> scanned -> confirmed / expired
            "user_id": None,          # scanned/confirmed 后填入
            "created_at": now,
            "expires_at": now + TICKET_TTL,
            "confirmed_at": None,
        }
    logger.info("扫码票据已创建：%s…（%d 秒内有效）", ticket[:8], TICKET_TTL)
    return {"ticket": ticket, "expires_in": TICKET_TTL}


def peek_status(ticket: str) -> Optional[dict]:
    """查询票据状态（不修改）。不存在或已过期返回 None。"""
    with _lock:
        item = _tickets.get(ticket)
        if not item:
            return None
        if item["expires_at"] <= time.time():
            _tickets.pop(ticket, None)
            return None
        return {
            "status": item["status"],
            "user_id": item["user_id"],
            "expires_in": max(0, int(item["expires_at"] - time.time())),
        }


def mark_scanned(ticket: str, user_id: int) -> bool:
    """手机端扫码后标记「已扫描」（此时还没有授权登录）。

    注意：只允许从 pending 迁移到 scanned，**不允许覆盖已确认的票据**，
    否则一个已完成的登录会被后来的扫码"劫持"。
    """
    with _lock:
        item = _tickets.get(ticket)
        if not item or item["expires_at"] <= time.time():
            return False
        if item["status"] != "pending":
            return False
        item["status"] = "scanned"
        item["user_id"] = user_id
    logger.info("票据 %s… 已被 user_id=%s 扫描", ticket[:8], user_id)
    return True


def confirm_ticket(ticket: str, user_id: int) -> bool:
    """手机端点击「确认登录」：把票据标记为已完成。

    ⚠️ 只有**同一个 user_id** 才能确认自己扫的那张票 ——
    防止 A 扫了码、B 拿票号去别处确认（票据 ID 泄露时的兜底）。
    """
    with _lock:
        item = _tickets.get(ticket)
        if not item or item["expires_at"] <= time.time():
            return False
        if item["status"] == "confirmed":
            # 已经确认过，幂等返回，避免手机端重复点导致报错
            return item["user_id"] == user_id
        if item["status"] == "scanned" and item["user_id"] != user_id:
            logger.warning("票据 %s… 的确认者与扫描者不一致，已拒绝", ticket[:8])
            return False
        item["status"] = "confirmed"
        item["user_id"] = user_id
        item["confirmed_at"] = time.time()
    logger.info("票据 %s… 已由 user_id=%s 确认登录", ticket[:8], user_id)
    return True


def consume_ticket(ticket: str) -> Optional[int]:
    """电脑端拿到「已确认」后，**一次性消费**票据并返回 user_id。

    消费即作废 —— 同一张票永远只能换一次登录态。
    返回 None 表示票据无效/未确认/已用过。
    """
    with _lock:
        item = _tickets.get(ticket)
        if not item:
            return None
        if item["expires_at"] <= time.time():
            _tickets.pop(ticket, None)
            return None
        if item["status"] != "confirmed" or not item["user_id"]:
            return None
        uid = int(item["user_id"])
        _tickets.pop(ticket, None)        # 一次性：立刻销毁
    logger.info("票据 %s… 已消费，签发 user_id=%s 的登录态", ticket[:8], uid)
    return uid


def revoke_ticket(ticket: str) -> None:
    """作废票据（用户点「刷新二维码」时调用，防止旧码被继续使用）。"""
    with _lock:
        _tickets.pop(ticket, None)


def stats() -> dict:
    """当前票据概况（给状态接口/排查用，不含票据 ID 本身）。"""
    with _lock:
        _cleanup_locked()
        by_status: Dict[str, int] = {}
        for v in _tickets.values():
            by_status[v["status"]] = by_status.get(v["status"], 0) + 1
        return {"total": len(_tickets), "by_status": by_status, "ttl": TICKET_TTL}
