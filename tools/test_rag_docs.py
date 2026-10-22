#!/usr/bin/env python3
"""校园文档 RAG（学生手册问答）的守卫测试。

跑法：python tools/test_rag_docs.py

## 为什么值得单独测

RAG 这套东西最阴险的地方是：**它永远都能给出一个看起来像答案的答案**。
检索错了、语料抓空了、模型开始自由发挥，接口照样 200、文字照样通顺，
只有真正拿着材料去教务处办事的人才会发现对不上。靠人眼试几条是抓不住的
（本机实测就真的踩过：问「校园卡丢了怎么补」，向量检索最高分 0.458 命中的
是「毕业、学位证明书办理流程」—— 语料里压根没有校园卡这件事）。

所以这里钉四类东西：

  A. **语料本身**：抓空了、抓到旧学年、头部丢了原文链接，都要当场失败。
     语料是这套功能的**地基**，地基坏了上面全白搭，而且从接口层面看不出来。
  B. **切块**：不能丢字、不能切出超长块（embedding 截断会让后半段查不到）。
  C. **检索判据**：假命中必须被拦住，真答案不能被顺手切掉 —— 这两条互相拉扯，
     改任何一边都可能弄坏另一边，所以两个方向都要断言。
  D. **拒答**：检索为空时**不许调模型**。这是从机制上保证"不知道就说不知道"，
     一旦有人为了"体验更顺"把那次模型调用加回来，这条会立刻失败。

⚠️ 全程不连 Qdrant、不调模型：用 monkeypatch 换掉 `search_docs` 和 `glm_chat`，
   在断网机器 / CI 上照样跑完。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# ★ 先改数据库指向再 import backend 模块（config 在导入时就实例化 settings）。
#   这个测试其实用不到数据库，但 study_agent -> config 这条链会建库文件；
#   让 backend/campus_ai.db 冒出来是部署事故（会被原样推上线上覆盖真实数据）。
_TMP_DB = Path(tempfile.gettempdir()) / "campus_ai_rag_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB.as_posix()

sys.path.insert(0, str(BACKEND))

from services import rag_docs as R                          # noqa: E402
from services import qdrant as Q                             # noqa: E402
from services import study_agent as A                        # noqa: E402

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


def section(name: str) -> None:
    print(f"\n{name}")


def _run(coro):
    """跑一个协程。不用 get_event_loop（3.12 起会告警，3.13 已弃用）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===========================================================================
# A. 语料本身
# ===========================================================================
section("A. 语料（backend/data/campus_docs）")

DOCS = R.load_docs()
check("语料目录能被读到", bool(DOCS), f"{len(DOCS)} 篇")
check("语料篇数够用（>= 10 篇）", len(DOCS) >= 10, f"实际 {len(DOCS)} 篇")

# 每篇都要能追溯回官网：没有原文链接的语料，等于让学生无从核对
no_src, no_url = [], []
for d in DOCS:
    body = d["text"]
    if "来源：" not in body[:400]:
        no_src.append(d["name"])
    # ★ 2026-10-21 放宽：语料从「只有教务处」扩到「教务处 + 图书馆 + 后勤 + 信息化中心」，
    #   原来写死 `jwc.ygu.edu.cn` 会把新增的 15 篇全判成「无出处」。
    #   要求没变：**必须能回到学校官方站点上核对**，不是随便一个链接都算。
    if not re.search(r"https?://(?:[a-z0-9\-]+\.)?ygu\.edu\.cn/", body[:600]):
        no_url.append(d["name"])

# 「抓空」不能靠字数判：官网有几篇正文总共就 46 个字，但那句
# 「申请路径：一站式服务平台→教务服务→考务服务→缓考申请」就是学生要的答案。
# 真正该拦的是**只有图片占位、没有任何可执行信息**的页面 —— 那种留下只会
# 让检索命中一段废话，模型照样答不出，还占掉 top_k 名额。
#
# ★ 2026-10-21 扩表：前一组是教务处语料的词（办事流程），后一组是生活服务类语料的词
#   （图书馆/校园卡/校园网）。不分成两张表，是因为判据只有一条 ——
#   **这段正文里有没有一句能直接照做的信息**。
_SUBSTANCE = ("申请路径", "流程", "材料", "条件", "时间", "地点", "办公室",
              "学院", "教务", "提交", "办理", "截止", "周", "楼",
              # 生活服务类：图书馆、校园卡、校园网、后勤
              "借阅", "归还", "续借", "预约", "座位", "自习", "密码", "充值",
              "步骤", "点击", "输入", "登录", "电话", "咨询", "报修", "账号", "系统")

# 允许留着的「薄页」—— 官网原文就这么短，但**不是抓空**，逐篇写上理由。
# 新增条目前先问自己：这段正文能独立回答一个问题吗？不能就别列进来。
_THIN_OK = {
    # 全文就是「新教务系统已上线 X/Y/Z 模块，其他陆续更新，具体请查看附件」。
    # 它回答不了「怎么操作」，但能回答「新教务系统有哪些功能」——
    # 而这正是 search_campus_docs 的工具描述里承诺覆盖的范围，删了就对不上。
    "【全体阳光学子】新教务系统操作指南": "模块清单页，正文明细在附件里",
}

hollow, thin = [], []
for d in DOCS:
    plain = "\n".join(l for l in d["text"].splitlines()
                      if not l.lstrip().startswith((">", "#")))
    chars = len(re.sub(r"\s", "", plain))
    if chars == 0:
        hollow.append(d["name"])
    elif not any(k in plain for k in _SUBSTANCE) and d["name"] not in _THIN_OK:
        hollow.append(f"{d['name']}({chars}字，无实质信息)")
    if 0 < chars < 120:
        thin.append(f"{d['name']}({chars}字)")

check("★ 每篇开头都带「来源：」", not no_src, "缺失：" + "、".join(no_src) if no_src else "")
check("★ 每篇都带教务处原文链接（可追溯）", not no_url, "缺失：" + "、".join(no_url) if no_url else "")
check("★ 没有只有图占位、零可执行信息的空壳", not hollow,
      "、".join(hollow) if hollow else "")
if thin:
    # 薄页不判失败（官网原文就短），但要打出来让人复核 —— 悄悄留着才是问题
    print(f"  [INFO] 薄页（正文 < 120 字，官网原文如此）：{'、'.join(thin)}")

# 校历只认当前学年：留着往年校历，RAG 会用错年份的日子回答（比不答更糟）
xl = [d["name"] for d in DOCS if "校历" in d["name"]]
check("★ 校历只剩当前学年一份（往年校历会答错日期）", len(xl) == 1, "、".join(xl) or "一份都没有")
check("当前校历标的是 2026-2027 学年", any("2026-2027" in n for n in xl), "、".join(xl))

# 抓取脚本曾经把「官网发布：时间：2023-05-23」这种残缺字段留在头部
bad_meta = [d["name"] for d in DOCS if "官网发布：时间：" in d["text"][:400]]
check("语料头部没有「官网发布：时间：」这种残缺字段", not bad_meta, "、".join(bad_meta))

# 问课/办事都得能查到这两类，缺了就说明抓取范围跑偏了
names = "".join(d["name"] for d in DOCS)
for kw in ("休学", "复学", "重修", "缓考", "转专业", "辅修", "校历"):
    check(f"语料覆盖「{kw}」", kw in names)

# ===========================================================================
# B. 切块
# ===========================================================================
section("B. 切块（chunk）")

chunks = R.build_chunks()
check("能切出块", bool(chunks), f"{len(chunks)} 块")

over = [c for c in chunks if len(c["text"]) > R.CHUNK_SIZE * 1.3]
check("没有超长块（超了会被 embedding 截断，后半段查不到）", not over,
      f"最长 {max((len(c['text']) for c in chunks), default=0)} 字" if not over else f"{len(over)} 块超标")

tiny = [c for c in chunks if len(c["text"]) < R.MIN_CHUNK]
check("没有低于 MIN_CHUNK 的碎块", not tiny, f"{len(tiny)} 块")

miss = [c for c in chunks if not c.get("source") or not c.get("section")]
check("★ 每块都带 source（答案要标出处，没 source 就没法标）", not miss, f"{len(miss)} 块缺")
check("每块都带 section（片段定位到小节，模型才知道上下文）",
      all(c.get("section") for c in chunks))

# 切块不能丢字：拿每篇里最长的一行，必须能在某块里找到
lost = []
for d in DOCS:
    lines = [l.strip() for l in d["text"].splitlines()
             if len(l.strip()) >= 20 and not l.lstrip().startswith((">", "#"))]
    if not lines:
        continue
    probe = max(lines, key=len)[:30]
    hit = any(probe in c["text"] for c in chunks if c["source"] == d["name"])
    if not hit:
        lost.append(d["name"])
check("★ 切块不丢正文（抽查每篇最长一行）", not lost, "、".join(lost))

# 元信息行（`> 来源：…`）不该进正文块 —— 它会把检索分数带偏
leak = [c for c in chunks if c["text"].lstrip().startswith(">")]
check("引用块（`> 来源：…`）没有混进正文块", not leak, f"{len(leak)} 块")

# ===========================================================================
# C. 检索判据（hybrid：语义分数 + 字面锚点）
# ===========================================================================
section("C. 检索判据")

check("DOCS_STRONG_SCORE 落在 0.4~0.7 之间（太低会放过假命中）",
      0.40 <= Q.DOCS_STRONG_SCORE <= 0.70, f"{Q.DOCS_STRONG_SCORE}")

# 泛动作词必须被剔除：否则「怎么申请奖学金」会命中「缓考申请」
for w in ("申请", "办理", "流程", "材料", "怎么", "什么"):
    check(f"停用词含「{w}」（泛词当锚点等于没锚点）", w in Q._LEX_STOP)

# ★ 真踩过的假命中：校园卡补办 vs 毕业证明补办
FALSE_HIT = ("毕业、学位证明书办理流程。学生遗失毕业证书或学位证书，"
             "可申请补办证明书，需提交申请表、身份证复印件。")
check("★ 假命中被拦：「校园卡丢了怎么补」不应锚定到毕业证明",
      not Q._lex_anchored("校园卡丢了怎么补", FALSE_HIT))

# ★ 真答案不能被顺手切掉：寒假那条向量分只有 0.44，低于强阈值
TRUE_HIT = ("第 20 周为期末考试周，寒假自 2027 年 1 月 21 日起放假。"
            "学生应在寒假开始前完成宿舍清退。")
check("★ 真答案留得住：「什么时候放寒假」应有字面锚点",
      Q._lex_anchored("什么时候放寒假", TRUE_HIT))

check("锚点对完全无关的内容为假",
      not Q._lex_anchored("怎么借教室", "食堂二楼营业时间 11:00-13:00。"))
check("问句为空时不做锚定（返回 False，由调用方决定拒答）",
      Q._lex_anchored("", TRUE_HIT) is False)

# 空问题 / 未配置都不能抛异常，必须返回 []（调用方靠它决定拒答）
# ★ 不能靠「本机没配 QDRANT_URL」来测：开发机的 .env 里是有真实 Qdrant 的，
#   那种写法在别人机器上会走去查真库、结果不可预期。直接钉住 is_configured。
_orig_conf = Q.is_configured
try:
    Q.is_configured = lambda: False
    check("未配置向量库时 search_docs 返回 []（不抛）",
          _run(Q.search_docs("休学需要什么材料")) == [])
finally:
    Q.is_configured = _orig_conf
check("空问题直接返回 []（连 embedding 都不算）",
      _run(Q.search_docs("")) == [])

# ★ 公共知识库不许按 user_id 过滤（它是公共知识，不是隐私）
src_qdrant = (BACKEND / "services" / "qdrant.py").read_text(encoding="utf-8")
i_search = src_qdrant.find("async def search_docs")
i_clear = src_qdrant.find("def clear_docs")
seg = src_qdrant[i_search:i_clear] if 0 <= i_search < i_clear else ""
# 去掉文档字符串与注释再看 —— 那段说明本身就在讲「这里没有 user_id 过滤」，
# 不剔掉的话这条断言永远是假失败（比不写还糟）。
seg_code = re.sub(r'""".*?"""', "", seg, flags=re.S)
seg_code = re.sub(r"#[^\n]*", "", seg_code)
check("★ search_docs 的代码里没有 user_id 过滤（文档是公共知识）",
      bool(seg_code) and "user_id" not in seg_code,
      f"代码段 {len(seg_code)} 字符")

# ★ 清库流程（clear_all_memory）只许删 emotion_memory。
#   把 campus_docs 一起删掉的话，AI 立刻答不出任何办事流程，而且下次启动
#   **不会自动重建**（要手工跑 rag_docs.ingest）—— 线上表现为"RAG 突然哑了"。
i_clr = src_qdrant.find("def clear_all_memory")
clr_body = src_qdrant[i_clr:src_qdrant.find("\ndef ", i_clr + 10)] if i_clr > 0 else ""
check("★ clear_all_memory 不删 campus_docs",
      bool(clr_body) and "DOCS_COLLECTION" not in clr_body,
      "clr_body 长度 %d" % len(clr_body))
check("★ 文档库另有显式入口 clear_docs（要重置只能手工调）",
      "def clear_docs" in src_qdrant)

# ===========================================================================
# D. 拒答：检索为空时**不调模型**
# ===========================================================================
section("D. 拒答机制")

# 探针：把模型调用换成计数器。如果它被调了，说明"不编"这条是靠模型自觉的
_calls = {"n": 0}


async def _fake_chat(*a, **kw):
    _calls["n"] += 1
    return "编出来的流程：先去教务处领表……"


_orig_chat = A.glm_chat
_orig_search = Q.search_docs
_orig_noconf = Q.is_configured


async def _empty_search(*a, **kw):
    return []


try:
    A.glm_chat = _fake_chat
    Q.search_docs = _empty_search
    ans = _run(A.ask_docs("校园卡丢了怎么补")) or ""
finally:
    A.glm_chat = _orig_chat
    Q.search_docs = _orig_search

check("★ 检索为空时不调模型（调了就说明「不编」全靠模型自觉）",
      _calls["n"] == 0, f"模型被调用了 {_calls['n']} 次")
check("★ 拒答口径明说「没有写」", "没有写" in ans, ans[:40])
check("拒答口径给出教务处电话（让学生有下一步）", "0591-83969194" in ans)
check("拒答时不假装有出处（没有「来源：《」）", "来源：《" not in ans)

# 反过来：检索有结果时**必须**调模型，否则就是把功能做坏了
_calls["n"] = 0


async def _fake_search(*a, **kw):
    return [{"text": TRUE_HIT, "source": "阳光学院2026-2027学年校历",
             "section": "寒假", "score": 0.9}]


try:
    A.glm_chat = _fake_chat
    Q.search_docs = _fake_search
    ans2 = _run(A.ask_docs("什么时候放寒假")) or ""
finally:
    A.glm_chat = _orig_chat
    Q.search_docs = _orig_search

check("检索有结果时确实调用了模型", _calls["n"] == 1, f"{_calls['n']} 次")
check("检索有结果时把答案透传出来", "教务处" in ans2, ans2[:40])

# 模型挂了要降级成原文摘录，不能抛、也不能空手
_boom_called = {"n": 0}


async def _boom_chat(*a, **kw):
    _boom_called["n"] += 1
    raise RuntimeError("upstream 502")


try:
    A.glm_chat = _boom_chat
    Q.search_docs = _fake_search
    ans3 = _run(A.ask_docs("什么时候放寒假")) or ""
finally:
    A.glm_chat = _orig_chat
    Q.search_docs = _orig_search

check("模型异常时不抛（对话不能因为这个挂掉）", _boom_called["n"] == 1)
check("模型异常时降级为原文摘录并标出处",
      "原文" in ans3 and "来源：《" in ans3, ans3[:40])

# ===========================================================================
# E. 路由：文档问答必须走 docs，不能被当成排表
# ===========================================================================
section("E. 路由（route_of）")

try:
    from routers.planner import route_of
except Exception as e:                                    # noqa: BLE001
    route_of = None
    print(f"  [SKIP] 导入 routers.planner 失败，跳过路由断言：{type(e).__name__}: {e}")

if route_of:
    CASES = [
        # (问句, 期望路由, 说明)
        ("休学需要准备什么材料", "docs", "办事流程"),
        ("怎么申请缓考", "docs", "办事流程"),
        ("转专业要什么条件", "docs", "办事流程"),
        ("什么时候放寒假", "docs", "校历时间"),
        ("第10周是哪几天", "docs", "校历周次"),
        ("校园卡丢了怎么补", "docs", "手册没写的事务也要走 docs（好让 AI 拒答，而不是排表）"),
        ("今天有什么课", "local_ask", "问课本地答"),
        ("帮我排三天计划", "plan", "排表"),
        ("我该怎么复习高数", "explain", "学习教练"),
        ("周六18-19点上钢琴课", "schedule_add", "往时间表加一条（星期几 + 起止钟点 + 一件事）"),
        # ★ 边界：只说了起点、没有终点 —— 没法算课时，不能塞进时间表，
        #   退回 plan 让 Agent 问一句「到几点」。这条挡的是"半句就硬加课"。
        ("周六下午三点加一节钢琴课", "plan", "只有起点没有终点，不该硬加"),
    ]
    for q, want, why in CASES:
        got = route_of(q)
        check(f"路由「{q}」-> {want}（{why}）", got == want, f"实际 {got}")

    # ★★★ 寒暄短路：纯寒暄不许进 Agent。
    #   在线评测实测过代价 —— 「你好」进 Agent 后要 34 秒、1 万 token、
    #   调用两次排表工具，最后回一句「你好」。判据必须**极窄**：
    #   带着任务的句子（「你好，帮我排个计划」）要照常进 Agent。
    from routers.planner import small_talk

    for q in ("你好", "您好！", "hi", "在吗", "谢谢", "多谢啦", "好的", "收到"):
        hit = small_talk(q)
        check(f"寒暄短路命中「{q}」", bool(hit), (hit or "")[:24])

    for q in ("你好，帮我排个计划", "谢谢，那周三呢", "好的那再排两天",
              "帮我排三天计划", "今天有什么课"):
        check(f"带任务的话不许被寒暄短路拦掉「{q}」", small_talk(q) is None,
              small_talk(q) or "")

    # -----------------------------------------------------------------
    # ★★★ 「同主题的另一件事」：扩语料时踩的最狠一次。
    #   语料加进《校园一卡通线上充值指南》之后，问「校园卡丢了怎么补」，
    #   检索命中了这份**充值**指南（都有「校园卡」），模型就照着编了一套
    #   **补办**流程 ——「去校园卡服务中心、带学生证、交补办费」，还标了出处。
    #   三句话没有一句是真的。这不是检索分数的问题，是"同主题 ≠ 同动作"。
    #   修法：把「用户问的动作在片段里没出现」算出来，直接告诉模型。
    section("G. 同主题不同动作（防「拿充值指南答补办」）")

    from services.study_agent import _missing_actions

    fake_recharge = [{"text": "校园一卡通线上充值操作指南。绑定校园卡，填写姓名、"
                              "卡号、校园卡密码，选择工行聚合支付宝通道完成充值。"}]
    check("★ 「丢了怎么补」能识别出「丢了」在片段里没有",
          "丢了" in _missing_actions("校园卡丢了怎么补", fake_recharge),
          str(_missing_actions("校园卡丢了怎么补", fake_recharge)))
    check("★ 「怎么充值」不误报（片段讲的就是充值）",
          _missing_actions("校园卡怎么充值", fake_recharge) == [],
          str(_missing_actions("校园卡怎么充值", fake_recharge)))
    # ⚠️ 反向：不能把「报修」也判成缺失，否则本来答得对的题会答成不知道
    fake_repair = [{"text": "校园报修系统使用指南。进入报修系统，填写报修内容并提交。"}]
    check("★ 「怎么报修」不误报（片段里真有「报修」）",
          _missing_actions("宿舍坏了怎么报修", fake_repair) == [],
          str(_missing_actions("宿舍坏了怎么报修", fake_repair)))

    # -----------------------------------------------------------------
    # ★★★ 启动自检：语料没进库时，系统必须**自己补**，不能等人手工跑命令。
    #   防的是这个真实事故形态（2026-10-21 发现）：
    #   换 Qdrant 实例部署后集合是空的，检索返回空 → 走「手册里没写」拒答口径。
    #   学生看到「手册里没写」，运维看到「一切正常」——不报错的错最难查。
    # -----------------------------------------------------------------
    section("F. 启动自检（防「RAG 静默哑掉」）")

    main_src = (BACKEND / "main.py").read_text(encoding="utf-8")
    check("main.py 定义了 _warm_docs", "async def _warm_docs" in main_src)
    check("★ lifespan 里真的调了它", "create_task(_warm_docs())" in main_src)
    check("★ 后台跑、不 await（否则健康检查会超时）",
          "await _warm_docs()" not in main_src)
    # 自检失败绝不能拖垮启动：整个函数体必须包在 try 里，且 except 只打印
    i_warm = main_src.find("async def _warm_docs")
    i_body = main_src.find("async def lifespan")
    seg = main_src[i_warm:i_body] if 0 <= i_warm < i_body else ""
    check("★ 自检异常不会抛出去（不拖垮启动）", "except Exception" in seg)
    check("★ 未配置向量库时只提示、不报错",
          "is_configured()" in seg and "return" in seg)

    # 指纹要稳定：同一份语料连算两次必须相同，否则每次启动都会重导一遍
    fp1, fp2 = R.fingerprint(), R.fingerprint()
    check("★ 语料指纹稳定（同一份语料两次一致）", fp1 == fp2, f"{fp1} / {fp2}")
    check("指纹不是空串（目录里确实读到了文件）", bool(fp1), fp1)

    # 指纹要能往返（写进去读出来一致），否则「已就绪」这条分支永远走不到
    old_fp = R.read_fingerprint()
    try:
        R.write_fingerprint("TEST-FP-123")
        check("★ 指纹可写入并原样读回", R.read_fingerprint() == "TEST-FP-123",
              R.read_fingerprint())
    finally:
        if old_fp:
            R.write_fingerprint(old_fp)
        else:
            try:
                R.FINGERPRINT_FILE.unlink()
            except OSError:
                pass

    # 指纹文件是运行时产物，不许进交付包
    _docker = BACKEND.parent / ".dockerignore"
    dockerignore = _docker.read_text(encoding="utf-8") if _docker.exists() else ""
    gen_src = (BACKEND.parent / "tools" / "gen_structure.py").read_text(encoding="utf-8")
    if _docker.exists():
        check("★ .docs_fingerprint 不进 docker 镜像",
              "docs_fingerprint" in dockerignore or "data/.*" in dockerignore,
              "（.dockerignore 里没显式排除）")
    else:
        # 开发树暂时不带容器产物（Dockerfile / .dockerignore 是竞赛树的工程化交付），
        # 没这个文件就没这条规则可查 —— 报 SKIP 而不是 FAIL，
        # 免得「本树根本没有的东西」被算成失败（假失败比不查更糟）。
        print("  [SKIP] 本树没有 .dockerignore（无容器产物），跳过镜像排除检查")
    check("★ 包内结构清单也不统计它",
          "docs_fingerprint" in gen_src,
          "（清单脚本没排除，会出现在 包内结构.txt 里）")

# ===========================================================================
print("\n" + "=" * 60)
print(f"  结论: {_passed} 通过 / {_failed} 失败")
print("=" * 60 + "\n")
sys.exit(1 if _failed else 0)
