"""Qdrant Cloud 封装：情绪陪伴的长期记忆（RAG）。

做什么：
  把用户说过的每句话向量化后存进 Qdrant，下次对话时按语义相似度
  检索出最相关的历史片段，作为上下文喂给 GLM —— 这样 AI 能记住
  「用户上周说期末压力大」「用户提过家里的事」，而不是每轮都从零开始。

设计要点：
  1. **全程降级**：没装 qdrant-client、没填 URL/Key、网络不通、检索超时……
     任何一环出问题都只是「拿不到记忆」，绝不影响正常对话。
  2. **按用户隔离**：所有向量都带 payload.user_id，检索时强制过滤，
     保证 A 同学的情绪记录绝不会被 B 同学检索到。
     ⚠️ 注销账号时**必须**调 `delete_user_memory()` 把该用户的向量一并删掉：
     因为 users.id 没有 AUTOINCREMENT，id 会被新注册的用户复用，
     不清就会导致「新用户继承旧用户的隐私记忆」。详见该函数的注释。
  3. **集合按需创建**：首次写入时自动建集合。维度由实际用的 embedding
     模型决定（embedding-3 = 2048 维，embedding-2 = 1024 维），
     建好后若维度不符会重建（开发期常见，避免卡住）。
  4. 客户端是**单例**，避免每次请求都新建连接。
"""
import logging
import re
import time
import uuid

from config import settings

logger = logging.getLogger(__name__)

COLLECTION = "emotion_memory"        # 用户情绪记忆：按 payload.user_id 隔离
DOCS_COLLECTION = "campus_docs"      # 校园公共文档：全库检索，不带 user_id

# embedding 模型 -> 向量维度（与 services/glm.py 的候选顺序保持一致）
EMBED_DIMS = {"embedding-3": 2048, "embedding-2": 1024}

_client = None
_client_failed = False      # 连不上就别每次请求都重试，省点时间
# 每个集合各自记住「已确认过哪个维度」。
# ⚠️ 不要退回成一个全局布尔量：两个集合的维度可能不同（换过 embedding 模型时），
#    共用一个结论会让后建的集合跳过真正的校验，检索时静默返回 0 条。
_dim_state: dict = {}


def is_configured() -> bool:
    """URL 和 Key 都填了才算配置好。"""
    return bool((settings.QDRANT_URL or "").strip()
                and (settings.QDRANT_API_KEY or "").strip())


def get_qdrant():
    """返回 Qdrant 客户端单例；不可用时返回 None。"""
    global _client, _client_failed
    if _client is not None:
        return _client
    if _client_failed or not is_configured():
        return None
    try:
        from qdrant_client import QdrantClient  # 延迟导入：可选依赖

        _client = QdrantClient(
            url=settings.QDRANT_URL.strip(),
            api_key=settings.QDRANT_API_KEY.strip(),
            timeout=15.0,
            check_compatibility=False,
        )
        logger.info("Qdrant 已连接：%s", settings.QDRANT_URL)
        return _client
    except ImportError:
        logger.warning(
            "未安装 qdrant-client，情绪记忆检索已降级（不影响其他功能）。"
            "如需启用：python -m pip install qdrant-client"
        )
        _client_failed = True
        return None
    except Exception as e:
        logger.warning("Qdrant 连接失败，记忆检索降级：%s: %s", type(e).__name__, e)
        _client_failed = True
        return None


def _ensure_collection(dim: int, collection: str = COLLECTION) -> bool:
    """确保集合存在且维度正确。返回是否可用。

    `collection` 默认是情绪记忆，**老调用点一律不用改**；
    校园文档走 `DOCS_COLLECTION`，它不需要 user_id 过滤，所以不建那个索引。
    """
    client = get_qdrant()
    if client is None:
        return False

    if _dim_state.get(collection) == dim:
        return True

    try:
        from qdrant_client.models import Distance, VectorParams

        existing = None
        try:
            info = client.get_collection(collection)
            # 不同版本字段名略有差异，稳妥起见两种都试
            vec = getattr(info.config.params, "vectors", None)
            existing = getattr(vec, "size", None)
            if existing is None and isinstance(vec, dict):
                existing = next(iter(vec.values())).get("size")
        except Exception:
            existing = None      # 集合不存在

        if existing == dim:
            _dim_state[collection] = dim
            if collection == COLLECTION:
                _ensure_user_index(client, collection)  # 老集合可能缺索引，补建
            return True

        if existing is not None and existing != dim:
            # 维度不匹配（换过 embedding 模型）：重建集合
            logger.warning("Qdrant 集合 %s 维度 %s 与当前 %s 不符，重建集合。",
                           collection, existing, dim)
            client.delete_collection(collection)

        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info("Qdrant 集合 %s 已就绪（维度 %s）。", collection, dim)
        _dim_state[collection] = dim
        if collection == COLLECTION:
            _ensure_user_index(client, collection)
        return True
    except Exception as e:
        logger.warning("Qdrant 建集合失败（%s）：%s: %s", collection, type(e).__name__, e)
        return False


def clear_all_memory() -> dict:
    """**清空全部向量记忆**（把整个集合删掉，下次用到时自动重建）。

    什么时候需要它：
      · 演示/交付前要把环境恢复成"没有任何真实数据"；
      · 数据库被整体重建过（例如用 `CAMPUS_RESET_DB=1` 清库）——
        ⚠️ 这种情况下**必须一起清向量库**：`users.id` 没有 AUTOINCREMENT，
        清库后第一个新用户会拿到 id=1，而 id=1 这个人的历史向量还在集合里躺着，
        新用户就会检索到**上一个人的隐私片段**。这正是 auth.py 注销账号时
        反复强调"必须调 delete_user_memory"的同一个理由，只是清库这条路更容易被忘掉。

    返回：
        {"ok": bool, "before": 向量条数或 None, "error": 可选}
        —— 未配置 Qdrant 时返回 {"ok": False, "skipped": True}，不抛异常。

    ⚠️ **只清 `emotion_memory`，不碰 `campus_docs`**。
    校园文档是「公共知识库」，不是任何人的隐私数据；清库/换包时把它删掉，
    AI 伴学立刻无法回答任何办事流程问题，而且下次启动不会自动重建（需要重新导入）。
    要重置文档库请显式调用 `clear_docs()`。
    """
    if not is_configured():
        logger.info("未配置 Qdrant，跳过清空向量记忆。")
        return {"ok": False, "skipped": True}
    try:
        client = get_qdrant()
        before = None
        try:
            before = getattr(client.get_collection(COLLECTION), "points_count", None)
        except Exception:
            pass                      # 集合本来就不存在，不算错
        client.delete_collection(COLLECTION)
        logger.info("已清空 Qdrant 集合 %s（原有向量 %s 条）。", COLLECTION, before)
        # 立刻重建一个空集合，省得下一次对话时因为"集合不存在"报一次 warning
        _ensure_collection(2048)
        return {"ok": True, "before": before}
    except Exception as e:
        logger.warning("清空 Qdrant 失败：%s: %s", type(e).__name__, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _ensure_user_index(client, collection: str = COLLECTION) -> None:
    """给 payload.user_id 建整数索引。

    Qdrant Cloud 有一条硬性要求：**只要检索时带 filter，被过滤的字段就必须先建索引**，
    否则查询直接返回 400（Index required but not found）。
    按 user_id 过滤做用户隔离，所以这个索引是必需的。
    """
    try:
        from qdrant_client.models import PayloadSchemaType

        client.create_payload_index(
            collection_name=collection,
            field_name="user_id",
            field_schema=PayloadSchemaType.INTEGER,
        )
        logger.info("Qdrant payload 索引 user_id 已就绪（%s）。", collection)
    except Exception as e:
        # 索引已存在时会抛错，这属于正常情况，不必升级为警告噪音
        logger.debug("创建 user_id 索引时返回：%s: %s", type(e).__name__, e)


async def add_memory(user_id: int, text: str, role: str = "user", dim: int = 2048) -> bool:
    """把一条对话写入长期记忆。失败返回 False（不抛异常）。"""
    text = (text or "").strip()
    if not text or not is_configured():
        return False
    client = get_qdrant()
    if client is None or not _ensure_collection(dim):
        return False

    from services.glm import glm_embed

    try:
        vecs = await glm_embed([text])
        if not vecs or len(vecs[0]) != dim:
            return False
        from qdrant_client.models import PointStruct

        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=str(uuid.uuid4()),
                vector=vecs[0],
                payload={
                    "user_id": int(user_id),
                    "text": text[:500],
                    "role": role,
                    "ts": int(time.time()),
                },
            )],
        )
        return True
    except Exception as e:
        logger.warning("写入 Qdrant 失败：%s: %s", type(e).__name__, e)
        return False


async def search_memory(user_id: int, query: str, top_k: int = 3,
                        min_score: float = 0.35, dim: int = 2048) -> list:
    """按语义检索该用户的历史记忆。

    Returns:
        相关片段文本列表（0~top_k 条）。任何异常都返回 []，不影响对话。
    """
    query = (query or "").strip()
    if not query or not is_configured():
        return []
    client = get_qdrant()
    if client is None or not _ensure_collection(dim):
        return []

    from services.glm import glm_embed

    try:
        vecs = await glm_embed([query])
        if not vecs or len(vecs[0]) != dim:
            return []

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        user_filter = Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=int(user_id)))
        ])

        # 兼容不同版本的 qdrant-client：
        #   1.19+ 移除了 client.search()，改用 client.query_points()（返回 .points）。
        #   在旧版本上则回退到 search()。两条路都试，避免版本差异导致检索静默失效。
        hits = None
        if hasattr(client, "query_points"):
            resp = client.query_points(
                collection_name=COLLECTION,
                query=vecs[0],
                limit=top_k,
                score_threshold=min_score,
                query_filter=user_filter,
            )
            hits = getattr(resp, "points", resp)
        else:  # pragma: no cover - 旧版本兼容
            hits = client.search(
                collection_name=COLLECTION,
                query_vector=vecs[0],
                limit=top_k,
                score_threshold=min_score,
                query_filter=user_filter,
            )

        out = []
        for h in hits or []:
            t = (h.payload or {}).get("text", "").strip()
            if t and t not in out:
                out.append(t)
        return out
    except Exception as e:
        logger.warning("检索 Qdrant 失败，本次不带记忆：%s: %s", type(e).__name__, e)
        return []


async def delete_user_memory(user_id: int) -> bool:
    """删除某个用户的**全部**向量记忆（注销账号时必须调用）。

    ⚠️⚠️ 为什么这个方法必须存在（注意点，别再删掉它）：
      SQLite 的 `users.id` 是自增主键，但**没有 AUTOINCREMENT**，删掉最大的一行后
      新插入会**复用同一个 id**。而 Qdrant 里所有向量靠 payload.user_id 关联用户，
      如果注销时只删 SQLite、不删 Qdrant：
          张三(id=1) 注销 -> 李四注册又拿到 id=1 -> 检索到张三的全部情绪记忆
      新用户直接「继承」了前一个人的隐私记录。已复现（残留 3 条含学号的记忆）。
      **"AI 没说出来" ≠ "数据不在"** —— 提示词约束只能挡住嘴，挡不住库，必须在存储层真删。

    Returns:
        True 表示确实执行了删除（可能 0 条命中也算成功）；False 表示不可用/失败。
        失败不抛异常 —— 账号该注销还是要注销，不能因为云服务抽风就卡住用户。
    """
    if not is_configured():
        return False
    client = get_qdrant()
    if client is None:
        return False

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector

        client.delete(
            collection_name=COLLECTION,
            points_selector=FilterSelector(filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=int(user_id)))
            ])),
        )
        logger.info("已清空 user_id=%s 的全部向量记忆。", user_id)
        return True
    except Exception as e:
        # 集合不存在也算「清干净了」
        logger.warning("清空 user_id=%s 的向量记忆失败：%s: %s", user_id, type(e).__name__, e)
        return False


async def count_user_memory(user_id: int) -> int | None:
    """数一下某个用户在向量库里还有多少条记忆（拿不到就返回 None）。

    给 `services/data_export.py` 用：注销账号前要把这个人的数据**输出**出来，
    只报 SQLite 那几张表是不够的，向量库存了什么同样是用户数据的一部分。

    与 `delete_user_memory` 用**同一个过滤字段**（payload.user_id）——
    字段名必须一致，写错会得到一个恒为 0 的"看起来很正常"的答案。
    """
    if not is_configured():
        return None
    client = get_qdrant()
    if client is None:
        return None
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        res = client.count(
            collection_name=COLLECTION,
            count_filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=int(user_id)))
            ]),
            exact=True,
        )
        return int(getattr(res, "count", 0) or 0)
    except Exception as e:
        logger.warning("统计 user_id=%s 的向量条数失败：%s: %s", user_id, type(e).__name__, e)
        return None


def stats() -> dict:
    """给 /api/emotion/memory/stats 用的健康信息。"""
    info = {"configured": is_configured(), "reachable": False, "points": 0}
    if not is_configured():
        return info
    client = get_qdrant()
    if client is None:
        return info
    try:
        # 构造客户端不会真正联网，所以这里必须实际发一次请求，
        # 否则「地址填错/域名解析失败」也会被误报成 reachable。
        c = client.get_collection(COLLECTION)
        info["reachable"] = True
        info["points"] = int(getattr(c, "points_count", 0) or 0)
    except Exception as e:
        logger.debug("Qdrant 健康检查失败：%s: %s", type(e).__name__, e)
    return info


# ============================================================================
# 校园公共文档（RAG）—— 与情绪记忆**分集合存放**
# ============================================================================
# 为什么不塞进 emotion_memory：那一套按 payload.user_id 强制过滤，且清库时要整集合删除。
# 校园文档是「所有人共享的同一份知识」，既不该按人过滤，也不该被「清个人数据」波及。
# 两个集合各管一件事，代价只是多一次 API 调用，换来的是清库时不会误删知识库。

def docs_stats() -> dict:
    """文档库健康信息：配没配、通不通、有多少块。"""
    info = {"configured": is_configured(), "reachable": False, "points": 0}
    if not is_configured():
        return info
    client = get_qdrant()
    if client is None:
        return info
    try:
        c = client.get_collection(DOCS_COLLECTION)
        info["reachable"] = True
        info["points"] = int(getattr(c, "points_count", 0) or 0)
    except Exception as e:
        logger.debug("文档库健康检查失败：%s: %s", type(e).__name__, e)
    return info


async def add_docs(chunks: list, dim: int = 2048, batch: int = 12) -> int:
    """把切好的文档块写入 campus_docs，返回实际写入条数。

    每块形如 `{"text": ..., "source": ..., "section": ...}`。

    ★ 点 id 用 **uuid5(source|section|text)** 而不是随机 uuid4：
      这样重复导入同一份语料是**覆盖**而不是叠加，不会越导越多、也不会
      因为一次失败重跑就把知识库灌成 3 份（检索时前 4 条全是同一段话）。
    """
    chunks = [c for c in (chunks or []) if (c.get("text") or "").strip()]
    if not chunks or not is_configured():
        return 0
    client = get_qdrant()
    if client is None or not _ensure_collection(dim, DOCS_COLLECTION):
        return 0

    from services.glm import glm_embed
    from qdrant_client.models import PointStruct

    written = 0
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        try:
            vecs = await glm_embed([c["text"] for c in part])
        except Exception as e:
            logger.warning("文档块向量化失败（第 %s 批）：%s: %s", i // batch + 1, type(e).__name__, e)
            continue
        if not vecs or len(vecs) != len(part) or any(len(v) != dim for v in vecs):
            logger.warning("文档块向量维度不符（期望 %s），本批跳过。", dim)
            continue
        points = []
        for c, v in zip(part, vecs):
            key = "%s|%s|%s" % (c.get("source", ""), c.get("section", ""), c["text"])
            points.append(PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                vector=v,
                payload={
                    "text": c["text"][:1200],
                    "source": c.get("source", ""),
                    "section": c.get("section", ""),
                    "ts": int(time.time()),
                },
            ))
        try:
            client.upsert(collection_name=DOCS_COLLECTION, points=points)
            written += len(points)
        except Exception as e:
            logger.warning("写入文档块失败：%s: %s", type(e).__name__, e)
    if written:
        logger.info("校园文档已入库 %s 块。", written)
    return written


# 语义分数到这个值以上，视为「确实在讲同一件事」，不再要求字面重合
DOCS_STRONG_SCORE = 0.56

# 二字词里剔除虚词与**泛动作词**。
# 「申请」「办理」「流程」这类词几乎出现在每一篇办事指南里，
# 只靠它们匹配等于没匹配（问「怎么申请奖学金」会命中「缓考申请」）。
_LEX_STOP = {
    "怎么", "什么", "怎样", "如何", "请问", "可以", "需要", "一下", "我的", "我要",
    "想要", "哪些", "哪个", "哪里", "多少", "是不", "不是", "能不", "不能", "有没",
    "没有", "了吗", "了吧", "这个", "那个", "我们", "他们", "现在", "时候", "一个",
    "的话", "还是", "或者", "然后", "因为", "所以", "但是", "如果", "就是",
    "申请", "办理", "流程", "手续", "材料", "帮我", "帮忙", "知道", "告诉", "是否",
    "具体", "相关", "一些", "有关", "去到", "去哪", "在哪", "要带", "带上",
}


def _bigrams(text: str) -> set:
    """取中文二字词（去掉标点与虚词）。没有分词依赖，也不需要它。"""
    s = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} - _LEX_STOP


def _lex_anchored(query: str, text: str) -> bool:
    """问句与文档块之间有没有**字面**交集。

    只要有一处二字词重合就算锚定 —— 阈值定得松是故意的：
    这一步只是用来挡「语义上像、但根本不是同一件事」的假命中
    （校园卡补办 ↔ 毕业证明补办），不是为了做精确排序，排序仍由向量分数负责。
    """
    qb = _bigrams(query)
    return bool(qb and (qb & _bigrams(text)))


async def search_docs(query: str, top_k: int = 4, min_score: float = 0.40,
                     dim: int = 2048) -> list:
    """检索校园文档。

    Returns:
        `[{"text","source","section","score"}]`，按分数降序；0~top_k 条。
        任何异常都返回 []（不抛），调用方据此决定「说不知道」还是「照常回答」。

    ★ 这里**没有** user_id 过滤 —— 文档是公共知识。

    ★★ **为什么光靠向量分数不够**（遇到的）：
      问「校园卡丢了怎么补」，向量检索给出的最高分是 0.458 —— 命中的是
      「毕业、学位证明书办理流程」，因为两者都带「补办证件」的语义。
      语料里根本没有校园卡这件事，但分数足够高，模型就会照着毕业证明的流程回答。
      纯阈值也没法救：真正该命中的「什么时候放寒假」只有 0.440，
      比那条错命中还低 —— 把阈值提到 0.46，先被切掉的反而是正确答案。

      所以这里加一层**字面锚点**（hybrid）：把问句切成二字词（去掉「怎么/什么/
      可以」这类虚词），要求命中的文档块里至少出现过其中一个。
      · 语义强相关（≥0.56）→ 直接放行，不要求字面重合；
      · 语义中等（0.40~0.56）→ 必须有字面锚点，否则丢掉；
      · 语义低于 0.40 → 直接丢。
      这样「校园卡丢了」（与毕业证明零字面重合）被拦下，
      而「什么时候放寒假」（含「寒假」二字）即使只有 0.44 也能留下。
    """
    query = (query or "").strip()
    if not query or not is_configured():
        return []
    client = get_qdrant()
    if client is None or not _ensure_collection(dim, DOCS_COLLECTION):
        return []

    from services.glm import glm_embed

    try:
        vecs = await glm_embed([query])
        if not vecs or len(vecs[0]) != dim:
            return []

        if hasattr(client, "query_points"):
            resp = client.query_points(
                collection_name=DOCS_COLLECTION,
                query=vecs[0],
                limit=max(top_k * 3, 8),      # 多取一些，给下面的字面校验留余量
                score_threshold=min_score,
            )
            hits = getattr(resp, "points", resp)
        else:  # pragma: no cover - 旧版本兼容
            hits = client.search(
                collection_name=DOCS_COLLECTION,
                query_vector=vecs[0],
                limit=max(top_k * 3, 8),
                score_threshold=min_score,
            )

        out, seen = [], set()
        for h in hits or []:
            p = h.payload or {}
            t = (p.get("text") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            score = float(getattr(h, "score", 0.0) or 0.0)
            if score < DOCS_STRONG_SCORE and not _lex_anchored(query, t):
                logger.debug("丢弃无字面锚点的文档块（%.3f）：%s", score, t[:30])
                continue
            out.append({
                "text": t,
                "source": (p.get("source") or "").strip(),
                "section": (p.get("section") or "").strip(),
                "score": score,
            })
            if len(out) >= top_k:
                break
        return out
    except Exception as e:
        logger.warning("检索校园文档失败：%s: %s", type(e).__name__, e)
        return []


def clear_docs() -> dict:
    """清空文档库（**只在显式要重建语料时调用**，不在清库流程里）。"""
    if not is_configured():
        return {"ok": False, "skipped": True}
    client = get_qdrant()
    if client is None:
        return {"ok": False, "skipped": True}
    try:
        before = None
        try:
            before = getattr(client.get_collection(DOCS_COLLECTION), "points_count", None)
        except Exception:
            pass
        client.delete_collection(DOCS_COLLECTION)
        _dim_state.pop(DOCS_COLLECTION, None)
        logger.info("已清空文档库 %s（原有 %s 块）。", DOCS_COLLECTION, before)
        return {"ok": True, "before": before}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
