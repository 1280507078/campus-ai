"""智谱 GLM 调用封装（OpenAI 兼容协议）。

设计要点：
  1. **未配置 key 时自动降级**：直接返回空串，由上层走占位文案。
     这样别人拿到项目不填 key 也能跑通全流程，不会一打开就报错。
  2. **所有异常都被吞掉并降级**：网络不通、key 失效、额度用尽……
     任何情况都不应该让页面白屏。出错时打印一行日志便于排查。
  3. **超时要给够**（见下方 TIMEOUT 说明）。这是踩过的坑，别再调小。

关于模型：
  glm-4-flash 是官方承诺「永久免费、不限量」的对话模型，适合本项目。
  想换更强模型时改 .env 里的 GLM_MODEL 即可（如 glm-4-flashx / glm-4-plus）。

## 超时为什么是 180 秒

原先设 60 秒，结果「生成计划」实测要 **69.6 秒**（生成 7 天、每天若干时段，
输出近 1900 字），正好卡在超时线上 —— 表现为每次都降级成本地占位计划，
日志里刷「GLM 请求超时（60s）」，用户看到的是「生成计划用不了」。

这里有个容易忽略的点：**超时不是「网络慢」，而是「输出长」**。
大模型是一个字一个字往外吐的，输出越长总耗时越长，跟网速关系不大。
7 天计划 ≈ 70 秒；用户如果把天数调到 14 天，就要 140 秒以上。

所以：
  - 默认超时从 60s 提到 **180s**，覆盖 14 天计划的量级；
  - httpx 的 timeout 用元组分别设置（连接 / 读取），
    让「连不上」能快速失败，而「在慢慢输出」不被打断。

注意 httpx 的 timeout 语义：它衡量的是**两次数据到达之间的间隔**，
不是整个请求的总时长。所以 read=180 表示「超过 180 秒没吐出新数据才算超时」，
只要模型在持续输出就不会误杀 —— 这正是我们要的行为。
"""
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

# 单次对话的超时上限（秒）。
# 连接阶段给 10 秒快速失败；读取阶段给 180 秒，容忍长文本生成。
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 180.0


def is_configured() -> bool:
    """是否填了 GLM_API_KEY。"""
    return bool((settings.GLM_API_KEY or "").strip())


async def glm_chat(messages: list, temperature: float = 0.8,
                   timeout: float = DEFAULT_READ_TIMEOUT,
                   max_tokens: int | None = None) -> str:
    """调用 GLM 对话接口，返回回复文本。

    Args:
        messages: OpenAI 格式的消息列表，如
                  [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
        temperature: 采样温度。情绪陪伴建议 0.8（更有温度），学业规划建议 0.5（更稳）。
        timeout: 读取超时秒数（两次数据到达的间隔），默认 180 秒。
        max_tokens: 限制输出长度。生成计划时可给个上限，避免模型"刹不住"
                    导致又慢又啰嗦。

    Returns:
        模型回复的纯文本；**未配置 key 或调用失败时返回空串**，
        调用方据此走占位文案。
    """
    if not is_configured():
        logger.info("未配置 GLM_API_KEY，AI 对话降级为占位文案。")
        return ""

    # base_url 结尾必须带斜杠，否则拼接会少一层路径
    base = settings.GLM_BASE_URL.rstrip("/") + "/"
    url = base + "chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": settings.GLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    msg = await _post_chat(url, headers, payload, timeout)
    return (msg.get("content") or "").strip()


async def _post_chat(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """POST 一次 chat/completions，返回 assistant 的 **message 字典**；失败返回 {}。

    为什么单独抽出来：`glm_chat` 只要 content 纯文本，而 **function calling 必须拿到
    完整的 message**（`tool_calls` 就挂在 message 上，只取 content 会把调用请求丢掉）。
    两边共用同一套超时与降级策略，避免"两条路径行为不一致"这种最难查的 bug。

    所有异常一律吞掉并返回 {}（与 glm_chat 的降级哲学一致：任何情况都不该让页面白屏）。
    """
    # 连接 10 秒快速失败，读取按传入值（长文本生成要等得起）
    to = httpx.Timeout(timeout, connect=DEFAULT_CONNECT_TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=to) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.warning("GLM 返回 %s：%s", resp.status_code, resp.text[:200])
                return {}
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                logger.warning("GLM 返回体没有 choices：%s", str(data)[:200])
                return {}
            # ★ 2026-10-21：usage 以前直接丢掉，导致「这次对话花了多少 token / 多少钱」
            #   在系统里无从得知。现在每次成功返回都记一笔（见 services/usage.py），
            #   评测报告里的成本数字就是从这儿来的，不是估的。
            try:
                from services import usage as _usage
                _u = data.get("usage") or {}
                _usage.record(
                    model=data.get("model") or payload.get("model") or "",
                    prompt_tokens=_u.get("prompt_tokens") or 0,
                    completion_tokens=_u.get("completion_tokens") or 0,
                    total_tokens=_u.get("total_tokens"),
                )
            except Exception:                   # noqa: BLE001 —— 埋点失败不影响对话
                logger.debug("记录 token 用量失败", exc_info=True)
            return choices[0].get("message") or {}
    except httpx.TimeoutException:
        logger.warning("GLM 请求超时（读取 %.0fs 无新数据），降级为占位文案。", timeout)
        return {}
    except Exception as e:
        logger.warning("GLM 调用失败，降级为占位文案：%s: %s", type(e).__name__, e)
        return {}


async def glm_chat_tools(messages: list, tools: list,
                         temperature: float = 0.5,
                         timeout: float = DEFAULT_READ_TIMEOUT,
                         max_tokens: int | None = None,
                         tool_choice: str = "auto") -> dict:
    """带**工具（function calling）**的一轮对话，返回 assistant 的 message 字典。

    这是「AI Agent」的基础能力：模型看到 `tools` 里有哪些函数可用后，可以**不直接回答**，
    而是回一个 `tool_calls` 请求（"我需要先调用 get_timetable 看看课表"），
    由上层执行完再把结果喂回去，多轮之后才给最终答案。

    Args:
        messages: OpenAI 格式消息列表。**回灌工具结果时必须是完整的 messages**
                  （含之前的 assistant.tool_calls 与随后的 role="tool" 消息），
                  否则模型会反复请求同一个工具。
        tools: OpenAI 格式的工具定义列表（type=function，含 name/description/parameters）。
        temperature: 规划类任务建议 0.5。
        timeout: 读取超时（两次数据到达之间的间隔），与 glm_chat 同口径。
        max_tokens: 输出上限。

    Returns:
        assistant 的 message 字典（可能只有 `tool_calls`、`content` 为 None）；
        未配置 key 或调用失败时返回 **{}**，调用方据此走降级。
    """
    if not is_configured():
        logger.info("未配置 GLM_API_KEY，Agent 降级为占位文案。")
        return {}

    base = settings.GLM_BASE_URL.rstrip("/") + "/"
    url = base + "chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": settings.GLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "tools": tools,
        # "auto"  = 模型自己决定要不要调工具；
        # "none"  = **只准作答、不许再调**。Agent 循环的最后一轮用它收口，
        #           否则模型可能一直"再查一次"，永远给不出最终计划。
        "tool_choice": tool_choice,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    return await _post_chat(url, headers, payload, timeout)


async def glm_embed(texts: list, timeout: float = 30.0) -> list:
    """把一批文本向量化（智谱 Embedding 接口，OpenAI 兼容）。

    主要用于情绪陪伴的长期记忆检索：把聊过的话转成向量存进 Qdrant，
    下次对话时按语义相似度找回相关片段，让 AI 记得用户此前说过什么。

    Args:
        texts: 待向量化的文本列表。空列表直接返回 []。
        timeout: 请求超时秒数。

    Returns:
        与输入等长的向量列表；**未配置 key 或调用失败时返回 []**（长度不匹配也返回 []），
        调用方据此跳过记忆检索，不影响正常对话。
    """
    if not texts:
        return []
    if not is_configured():
        return []

    base = settings.GLM_BASE_URL.rstrip("/") + "/"
    url = base + "embeddings"
    headers = {
        "Authorization": f"Bearer {settings.GLM_API_KEY}",
        "Content-Type": "application/json",
    }

    # embedding-3 是 2048 维；失败则退回 embedding-2（1024 维）。
    # 两个模型维度不同，所以集合维度必须与实际使用的模型匹配（见 qdrant.py）。
    for model in ("embedding-3", "embedding-2"):
        payload = {"model": model, "input": list(texts)}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.warning(
                        "GLM Embedding(%s) 返回 %s：%s",
                        model, resp.status_code, resp.text[:200],
                    )
                    continue
                data = resp.json()
                items = data.get("data") or []
                if not items:
                    continue
                # 按 index 排序，保证与输入顺序一致
                items = sorted(items, key=lambda x: x.get("index", 0))
                vecs = [it.get("embedding") or [] for it in items]
                if len(vecs) == len(texts) and all(vecs):
                    return vecs
        except httpx.TimeoutException:
            logger.warning("GLM Embedding(%s) 超时（%.0fs）。", model, timeout)
        except Exception as e:
            logger.warning("GLM Embedding(%s) 失败：%s: %s", model, type(e).__name__, e)
    return []


async def glm_vision(image_b64: str, prompt: str, timeout: float = 90.0,
                     models: tuple | None = None, max_tokens: int = 0) -> str:
    """让视觉模型看图并返回文本（用于课表截图识别）。

    Args:
        image_b64: 图片的 base64（**不带** data:image/...;base64, 前缀）。
        prompt: 给模型的指令。
        timeout: 超时秒数。识别课表图比普通对话慢，默认放宽到 90 秒。
        models: 依次尝试的视觉模型。默认取 .env 的 GLM_VISION_MODEL 打头，
                后面跟 glm-4v-flash / glm-4v 兜底。
        max_tokens: 输出上限。**0 表示不传该参数、用服务端默认值**。
                    GLM 的视觉模型对上限卡得很死（glm-4v-flash 最多 1024、
                    其他最多 2048），传大了直接 400，所以这里保守地不传，
                    长课表改由「切图分两次识别」来解决（见 vision.py）。

    Returns:
        模型输出文本；未配置 key 或全部失败时返回空串。
    """
    if not image_b64:
        return ""
    if not is_configured():
        logger.info("未配置 GLM_API_KEY，图片识别不可用。")
        return ""

    # 组装候选模型：配置项优先，其余按「免费→付费」补位，去重保序
    if models is None:
        preferred = (getattr(settings, "GLM_VISION_MODEL", "") or "").strip()
        chain = [preferred, "glm-4v-flash", "glm-4v", "glm-4v-plus"]
        models = tuple(dict.fromkeys([m for m in chain if m]))

    base = settings.GLM_BASE_URL.rstrip("/") + "/"
    url = base + "chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    content = [
        {"type": "image_url", "image_url": {"url": image_b64}},
        {"type": "text", "text": prompt},
    ]

    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,      # 识别任务要稳定，别发挥
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.warning("GLM 视觉(%s) 返回 %s：%s",
                                   model, resp.status_code, resp.text[:200])
                    continue
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    continue
                txt = ((choices[0].get("message") or {}).get("content") or "").strip()
                if txt:
                    return txt
        except httpx.TimeoutException:
            logger.warning("GLM 视觉(%s) 超时（%.0fs）。", model, timeout)
        except Exception as e:
            logger.warning("GLM 视觉(%s) 失败：%s: %s", model, type(e).__name__, e)
    return ""
