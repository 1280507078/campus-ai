"""按「一次请求」累计 GLM 的 token 用量。

为什么需要它（这是指标能不能写的前提）：
  原来 `services/glm.py` 拿到响应后只取 `choices[0].message`，**`usage` 字段直接丢掉**。
  于是「一次对话花了多少 token、折合多少人民币」这件事在系统里根本不存在 ——
  想写评测报告时只能估算，而估算出来的成本数字评委一问就露。

怎么用：
  1. `glm._post_chat()` 每次成功返回都会自动 `record(...)`；
  2. 一个请求处理完毕后调用 `take()` 取走这一批记录并清空（随取随清，
     不会把上一个用户的用量算到下一个人头上）；
  3. `summary()` 做汇总（总 token、按模型分组）。

为什么用 ContextVar 而不是模块级列表：
  FastAPI 是并发处理请求的。用模块级全局列表，两个同时进行的对话会把用量
  混进同一个列表，`take()` 还可能把别人的记录取走。
  ContextVar 天然按协程上下文隔离，每个请求看到的是自己那一份。
"""
from __future__ import annotations

import contextvars
from typing import List

# 智谱 GLM 定价（元 / 千 token）。用于把 token 折算成钱，只做量级参考。
# 价格会变，改这里即可；报告里必须写明「按写入时的官方价折算」。
PRICE_PER_KTOKEN = {
    "glm-4-plus": (0.05, 0.05),
    "glm-4": (0.10, 0.10),
    "glm-4-air": (0.001, 0.001),
    "glm-4-flash": (0.0, 0.0),
}
DEFAULT_PRICE = (0.05, 0.05)

_buf: contextvars.ContextVar = contextvars.ContextVar("glm_usage", default=None)


def _get_buf() -> List[dict]:
    b = _buf.get()
    if b is None:
        b = []
        _buf.set(b)
    return b


def record(model: str = "", prompt_tokens: int = 0, completion_tokens: int = 0,
           total_tokens: int = None) -> None:
    """记一次调用。任何字段缺失都当 0，绝不抛异常（埋点不许影响主流程）。"""
    try:
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        t = int(total_tokens) if total_tokens is not None else p + c
        _get_buf().append({
            "model": model or "",
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
        })
    except Exception:          # noqa: BLE001 —— 埋点出错不能拖垮对话
        pass


def take() -> List[dict]:
    """取走当前上下文里累计的用量并清空。"""
    b = _get_buf()
    items = list(b)
    b.clear()
    return items


def peek() -> List[dict]:
    return list(_get_buf())


def cost_of(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """按模型单价折算成元。未收录的模型按 DEFAULT_PRICE 估。"""
    key = (model or "").strip().lower()
    pin, pout = PRICE_PER_KTOKEN.get(key, DEFAULT_PRICE)
    # 前缀匹配兜底：glm-4-plus-0111 这类带日期的模型名
    if key not in PRICE_PER_KTOKEN:
        for k, v in PRICE_PER_KTOKEN.items():
            if key.startswith(k):
                pin, pout = v
                break
    return (prompt_tokens / 1000.0) * pin + (completion_tokens / 1000.0) * pout


def summary(items: List[dict] = None) -> dict:
    """把一批记录汇总成报告里要用的几个数。"""
    items = peek() if items is None else items
    total = sum(i.get("total_tokens", 0) for i in items)
    pin = sum(i.get("prompt_tokens", 0) for i in items)
    pout = sum(i.get("completion_tokens", 0) for i in items)
    money = sum(cost_of(i.get("model", ""), i.get("prompt_tokens", 0),
                        i.get("completion_tokens", 0)) for i in items)
    models = sorted({(i.get("model") or "unknown") for i in items})
    return {
        "calls": len(items),
        "prompt_tokens": pin,
        "completion_tokens": pout,
        "total_tokens": total,
        "cost_cny": round(money, 4),
        "models": models,
    }
