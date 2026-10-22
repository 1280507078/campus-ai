"""重建「包内结构.txt」（Windows 记事本可读）。

为什么要做成项目里的正式工具，而不是一次性脚本
--------------------------------------------
这份清单是**用户排查问题时的第一手资料**：它列了每个文件的大小、本包的构建号、
以及「改了代码没生效时该看哪里」。它必须随每次打包更新，否则会误导用户
（清单说构建 g、实际包是 h）。以前它是临时脚本 + 手工清单，容易漏。

用法：
    python tools/gen_structure.py            # 按文件系统实际内容重建清单

⚠️ 编码选择（踩过问题，别改回去）：
  最初用 UTF-8 + BOM（`utf-8-sig`）写这个文件，理由是「记事本需要 BOM」。
  但 tools/check_encoding.py 会**正确**地把它判为错误：
      .txt 这类会被 cmd / pip 等按系统 ANSI 编码（cp936）读取的文件里，
      UTF-8 的 BOM 字节 EF BB BF 不是合法的 GBK 序列，
      在中文 Windows 上会显示成开头的乱码方块。
  本项目里中文字符全部在 GBK 范围内（已校验），所以直接写 **GBK + 无 BOM**：
  记事本、type、findstr 都能正常读，也和项目其它 .txt 保持一致。

⚠️ 换行必须是 CRLF（记事本否则会显示成一大坨）。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "包内结构.txt"

# 打包时排除的东西
EXCLUDE_DIRS = {
    ".git", ".codebuddy", "node_modules", "__pycache__", "screenshots",
    ".pytest_cache", ".mypy_cache", ".venv", "venv", "dist", "build",
    # 历史遗留：曾经有一版会生成 backend/certs（自签证书）。那条 https / 定位
    # 链路已整体删除，但这一条保留着 —— 万一谁的目录里还留着上次生成的
    # certs（含私钥），也不该被打进交付包。⚠️ 要和 gtest/pack_zip.py 保持一致。
    "certs",
    # 数据「输出」目录（services/data_export.py 写的 JSON）：里面是**真实用户数据**
    # （账号名、课表、情绪对话原文、向量统计）。打进交付包就是隐私事故。
    # ⚠️ 必须和 gtest/pack_zip.py 的 EXCLUDE_DIRS 保持一致。
    "data_export",
}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", "包内结构.txt", "campus-ai.zip"}
# ⚠️ .package_marker 也不打包：它由 database.py 在每次启动时用源码 mtime
#    **自动生成**，用来检测「换了新包」并清库。把一个本机生成的指纹打进包里，
#    反而可能让新机器首次启动就误判「换过包」而清掉用户数据。
#
# ⚠️ backend/.env **要**打包（2026-10-13 起）。
#    README 与「如何启动.md」一直承诺「压缩包里 backend/.env 已经填好全部 Key、
#    开箱即用」，所以它必须真的在包里 —— 此前这里把它排除了，文档与包对不上，
#    用户解压后就撞上「27 个接口全在、天气和 AI 却全是占位文案」。
#    ⚠️ 代价：交付包带着真实 Key，**不要转发给别人、不要上传到公开仓库**。
#    （.gitignore 仍然排除 .env —— git 仓库里不该有 Key。两条规则各管一段，不冲突。）
#
# ⚠️ backend/campus_ai.db 仍然不打包：它是 SQLite 里的**用户数据**，属于本机
#    运行时产物。之前这里漏了这条：开发机上跑过测试后它就存在了，于是清单会把
#    「本机才有的东西」列进「包内结构」，而用户解压后对照清单却找不到，白白怀疑
#    "是不是少文件了"。清单的意义是**如实描述这个包**，所以必须和打包排除规则一致。
EXCLUDE_REL = {
    # 语料指纹（services/rag_docs.py 写）：运行时产物，进包没意义
    "backend/data/.docs_fingerprint",
    "backend/.package_marker",
    "backend/campus_ai.db",
}
# ⚠️ 还要按**前缀**排除：2026-10-14 build q 起「换包」不再删库，而是把旧库另存成
#    backend/campus_ai.db.bak-<旧标识>。名字带后缀，上面那个精确匹配逮不住 ——
#    而那是**用户数据**（账号 / 课表 / 情绪记录），绝不能进包、也不该列进清单。
#    要和 gtest/pack_zip.py 的 EXCLUDE_REL_PREFIX 保持一致。
EXCLUDE_REL_PREFIX = ("backend/campus_ai.db",)


def collect() -> list[tuple[str, int]]:
    """返回 [(相对路径, 字节数), ...]，按路径排序。"""
    items: list[tuple[str, int]] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(ROOT).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        if p.name in EXCLUDE_NAMES:
            continue
        if p.relative_to(ROOT).as_posix() in EXCLUDE_REL:
            continue
        if p.relative_to(ROOT).as_posix().startswith(EXCLUDE_REL_PREFIX):
            continue
        if p.suffix == ".pyc":
            continue
        # ⚠️ 任何 .zip 都不进清单 —— 和 gtest/pack_zip.py 一致。交付包本身也是
        #    .zip，若有人把上一版包留在项目目录里，清单会把它当成「包内文件」列
        #    出来，用户对照清单自然找不到它。清单必须如实描述**打包的那一批**。
        if p.suffix == ".zip":
            continue
        items.append((p.relative_to(ROOT).as_posix(), p.stat().st_size))
    return items


def build_tag() -> str:
    """构建号从 backend/build.txt 读 —— 唯一来源。"""
    try:
        return (ROOT / "backend" / "build.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "UNKNOWN"


def main() -> int:
    items = collect()
    total = sum(sz for _, sz in items)
    tag = build_tag()

    # 按顶层目录分组，根目录文件放最后
    groups: dict[str, list[tuple[str, int]]] = {}
    order: list[str] = []
    for rel, sz in items:
        top = rel.split("/")[0] if "/" in rel else "（根目录）"
        if top not in groups:
            groups[top] = []
            order.append(top)
        groups[top].append((rel, sz))
    order = [g for g in order if g != "（根目录）"]
    if "（根目录）" in groups:
        order.append("（根目录）")

    L: list[str] = []
    L.append("Campus AI Assistant —— 包内结构")
    L.append("=" * 56)
    L.append("")
    L.append(f"共 {len(items)} 个文件，合计 {total:,} 字节")
    L.append(f"本包构建号：{tag}")
    L.append("")
    L.append("说明：本包零依赖即用，不需要 Node.js / npm / 构建。")
    L.append("      双击 start-windows.bat 即可（首次会自动装 Python 依赖）。")
    L.append("      详见 如何启动.md。")
    L.append("")
    L.append(f"本次更新（构建 {tag}）：")
    L.append("  ── 本版（build 2026-10-22-c，扩伴学口径 + 修文档路误抢）────")
    L.append("  ① ★★★【实测发现】问天气、问楼在哪时，伴学会去调排表工具并交出一份计划：")
    L.append("     6 条标注里错 3 条，工具选择准确率只有 50%。根因是路由只分五类，凡是不走")
    L.append("     课表、不走学校规定、不走学习教练的句子，全都落到 plan，而 plan 的主流程就是排表。")
    L.append("  ② ★★★【新路由】路由改六类，新增 quick：问天气、问楼在哪、学校有几个楼、")
    L.append("     某一节课几点上，这四类只跑一个工具（或零工具）再套模板回答，完全不调模型。")
    L.append("     理由三条：确定的数据不该让人等二十秒；不给模型编造的机会（楼在南北区是查表，")
    L.append("     不是推理）；token 恒为 0。实测 4 条干扰题 0.0 至 2.5 秒，多余工具调用 0 次。")
    L.append("     修后工具选择准确率 100%（68/68），多余调用从 50% 降到 0%。")
    L.append("  ③ ★★★【语料】学校手册从 14 篇加到 29 篇（7682 字增至 23168 字）：图书馆 11 篇、")
    L.append("     后勤 2 篇（公众号的《校园报修系统使用指南》《校园一卡通线上充值指南》）、")
    L.append("     信息化 1 篇（校园网准入系统密码找回）。抓不到的一律不收：列表页、纯图片页、")
    L.append("     整页只有一个下载按钮的，全部剔除，宁缺勿滥。每篇仍带原文链接与抓取日期。")
    L.append("  ④ ★★★【同主题不等于同动作】语料一多就出新问题：问「校园卡丢了怎么补」会命中")
    L.append("     《一卡通线上充值指南》（两件事都含「校园卡」三个字），模型照着充值步骤")
    L.append("     编出一整套补办流程。现在先从问句里抽动作词（补办/挂失/报修/预约/打印…），")
    L.append("     再到检索片段里逐个核对，一个都没出现就直接写进提示词告警：检索结果里")
    L.append("     没有问的这个操作。把判断从模型手里拿走，改后明确回答「手册里没写这条流程」。")
    L.append("  ⑤ ★★★【静默哑掉的根因】以前只在部署那一刻导一次语料，一旦失败或漏导，")
    L.append("     检索恒为空、伴学永远拒答，而日志里什么线索都没有。现在启动时后台自检：")
    L.append("     集合为空、或语料指纹变了，就重新导入；指纹只算文件内容不算修改时间，")
    L.append("     同一份语料在开发机和服务器上算出同一个值，不会每次部署白导一遍。")
    L.append("  ⑥ 【守卫】手册问答 83/83、伴学 82/82、鉴权 27/27、导出 22/22、前端静态 250/250、")
    L.append("     路由离线 68/68 与留出集 21/21、在线 15/15；真浏览器验收无报错。")
    L.append("  ⑦ ★★★【CI 真的跑起来了】github.com/1280507078/campus-ai 上三个作业全绿：")
    L.append("     后端契约测试 + 覆盖率、前端纯逻辑守卫、e2e + 无障碍审计（11 页逐页打开，")
    L.append("     控制台错误 0；axe 扫浅色 13 页 + 黑夜 3 页，serious/critical 为 0）。")
    L.append("     跑绿的过程中修掉两处只在 Linux 上才炸的写法：写盘失败用例原来拿不存在的")
    L.append("     Z 盘当死路，Linux 里 Z: 只是普通目录名，反而真写进去了；几个脚本把浏览器")
    L.append("     路径写死成 /root/.cache 下的 chromium-1208，而 GitHub runner 的家目录是")
    L.append("     /home/runner 且版本号会变。两处都改成不依赖平台。")
    L.append("")
    L.append("  ⑧ ★★★【扩口径】一批最常见的口语说法原来全落 plan，去排了一张作息表：")
    L.append("     问天气不说「天气」而说「今天多少度」「明天要带伞吗」「雾霾大不大」；")
    L.append("     问某门课说「计组几点上」「高数什么时候上」；看课表说「明天有早八吗」")
    L.append("     「今晚有晚课吗」；说状态用「学不下去」「三分钟热度」「上课总走神」。")
    L.append("     现在分别走 quick（0.7 秒、0 token）、local_ask（本地查课表）、explain（讲方法）。")
    L.append("  ⑨ ★★★【反向修复】`上课` / `考试` 这两个对象词太泛，把「下周一开始上课吗」")
    L.append("     拖进文档路、把「下周要考试了，帮我排个计划」也拖进文档路。现在先排掉再判：")
    L.append("     命中状态词（走神/拖延/效率低）不进文档路，命中「帮我排计划」也不进；")
    L.append("     对象词收窄成「考试安排/考试时间/补考/缓考/考务」，并补「四六级/报名」。")
    L.append("     评测集从 68 条加到 78 条，开发集 78/78、留出集 21/21。")
    L.append("  ── build 2026-10-21-h（学生手册问答 + 无障碍 + 工程化产物）────")
    L.append("  ① ★★★【新能力 RAG】AI 伴学能回答学校规定的办事流程了：语料取自教务处官网")
    L.append("     公开的办事指南 13 项 + 2026-2027 学年校历，共 14 篇，放在 backend/data/campus_docs，")
    L.append("     每篇开头带原文链接与抓取日期。检索走独立集合 campus_docs，不跟情绪记忆混。")
    L.append("     ★ 查不到就不调模型：直接说手册里没写 + 教务处电话，从机制上杜绝编流程；")
    L.append("     答案末尾标出处，前端把出处单独排一行、灰一档（它是证据，不是结论）。")
    L.append("  ② ★★★【修体验】说「你好」「谢谢」不再走 Agent：代码层全串匹配短路，")
    L.append("     实测从 49 秒 / 2 次工具调用 / 9 千 token 降到 0.03 秒 / 零工具。")
    L.append("     判据故意极窄，带任务的话（你好，帮我排个计划）照常进 Agent。")
    L.append("  ③ ★★★【无障碍】axe-core 扫 13 页 + 黑夜 3 页，serious 违规 13 处全部清零：")
    L.append("     顶栏账号链接的读屏名字与可见文字对齐；品牌色当小字用对比度不够，拆出 --brand-text")
    L.append("     只给文字用；课表次要时刻原本 opacity 0.5（2.05 比 1），改用校过对比度的 --ink-faint；")
    L.append("     危险区红字拆出 --danger-text；另有一页 viewport 禁用了缩放，已放开。")
    L.append("  ④ 【工程化产物】Dockerfile（非 root + HEALTHCHECK）、.dockerignore（排掉 .env 与真实库）、")
    L.append("     .github/workflows/ci.yml（后端契约 + 覆盖率 / 前端静态 / e2e + 无障碍三作业）、")
    L.append("     tools/audit_a11y.mjs、tools/test_rag_docs.py、tools/eval_agent.py 与 backend/eval 评测集、")
    L.append("     docs/agent-eval.md（评测方法 + 实测指标 + 失败样例 + 成本），reports/ 存原始报告。")
    L.append("  ⑤ 【守卫】RAG 70/70、伴学 82/82、鉴权 27/27、导出 22/22、前端静态 250/250、")
    L.append("     全站 e2e 11/11 页、课表冻结 106/106、路由离线 67/67 与留出 17/17、在线 15/15。")
    L.append("")
    L.append("  ── build 2026-10-21-f（地图页换成开发包同款版式）────")
    L.append("  ① ★★★【需求】地图页与开发包 e 包保持一致：又大又清楚的地图 + 右侧导航设置，")
    L.append("     路面、楼名、类型筛选（全部/教学楼/宿舍/食堂餐饮/其他）都回来了。")
    L.append("  ② 【无食堂入口】竞赛包没有食堂模块，所以只保留「清除路线」，不出现食堂档口按钮。")
    L.append("  ③ 【可访问性】页面主体改用 main，起终点输入框的标签真正关联（点文字能聚焦输入框），")
    L.append("     点位胶囊可按 Tab 键选中，键盘用户也能用。")
    L.append("  ④ 【天气】本页自己取数（后端 -> 免费公开接口兜底），图标与配色跟着实况变。")
    L.append("  ⑤ 【守卫】verify_h 72/72、自带伴学 76/76、静态契约 27/27；真浏览器验收无报错。")
    L.append("")
    L.append("  ── build 2026-10-20-y（伴学：加了「学习教练」人格）────")
    L.append("  ① ★★★【实测问题】「问的容易出错、没有学习性」—— 根因是只有一个规划 Agent 人格。")
    L.append("  ② ★★★【新能力】「怎么学/记不住/听不懂/什么是…」-> 走 COACH_PROMPT：不调工具、")
    L.append("     不排表，讲卡点 + 2-3 条能落地的做法 + 一个例子 + 今天的第一步；AI 不可用时按话题兜底。")
    L.append("  ③ 【判据】排除只挑动作词（排/安排/计划…），不能用「复习/提高」这些话题词。")
    L.append("  ④ 【守卫】gtest/verify_h.py 63 -> 72 条；自带 test_study_agent 76/76。")
    L.append("")
    L.append("  ── build 2026-10-20-v（课名 + 具体日期）────")
    L.append("  ① ★★★【组合问法】「周四高数在哪上」「明天有高数吗」：点了日期就只在那几天找，")
    L.append("     没点才扫 7 天；没有就直说「10-21（周三） 没有「高等数学」这门课」。")
    L.append("  ② ★★【有…吗 也算问课】补：句子里有 吗/呢/？ 且有「有」就算问课迹象。")
    L.append("  ③ 【守卫】gtest/verify_h.py 60 -> 63 条。")
    L.append("")
    L.append("  ── build 2026-10-20-t（伴学：按课名问，认简称）────")
    L.append("  ① ★★★【按课名问】「高数在哪上」「计网几点上」现在直接答：")
    L.append("     高等数学：周四（10-22）08:10-08:55，博文楼-301。")
    L.append("  ② ★★★【认简称】「高数」-> 高等数学、「计网」-> 计算机网络，用**子序列**比 + 滑窗取候选。")
    L.append("  ③ ★★【防误命中】句子里必须带问课迹象，否则「大学有几个楼」会答成「大学英语」。")
    L.append("  ④ 【守卫】gtest/verify_h.py 56 -> 60 条。")
    L.append("")
    L.append("  ── build 2026-10-20-r（口径 155 句全绿）────")
    L.append("  ① ★★【又补的口语】「明天有实验课吗」这类任意「有…课吗」。")
    L.append("     语料扩到 141 句，跑偏 0。")
    L.append("  ② 【体检口径】「太满了 / 差不多了」要带历史才判 plan，不进无历史语料。")
    L.append("")
    L.append("  ── build 2026-10-20-p（伴学：细节问题直接给答案）────")
    L.append("  ① ★★★【问细节别列整表】「今天课上到几点」「第一节几点」「在哪上」现在直接答：")
    L.append("       周四课上到几点 -> 最后一节是 计网，15:40 下课。")
    L.append("       周四第一节几点 -> 第一节是 高数，08:10 开始。")
    L.append("       周四在哪上课   -> 上课地点：高数 博文楼-301；计网 商德楼-513。")
    L.append("  ② ★★★【分词陷阱】「查一下周四的课表」曾被读成「一下周」= 下周（10-29）。")
    L.append("     下/这/本 + 周X 的正则都加了 (?<![一两几])，现在 = 本周四 10-22。")
    L.append("  ③ 【加课名字】补了 查一下/看一下 这类口语前缀，名字不再带它们。")
    L.append("  ④ 【守卫】gtest/verify_h.py 50 -> 56 条；h 自带 test_study_agent 76/76。")
    L.append("")
    L.append("  ── build 2026-10-20-n（伴学：又挖出两个真 bug 修掉）────")
    L.append("  ① ★★★【一周上几天】「每周二四8-9点晨跑」原来只认了「周二」：日期少一天，")
    L.append("     剩下的那个「四」还混进了名字（课名叫「四 晨跑」）。现在认一串星期字，")
    L.append("     每个星期几各写一条，名字干净。")
    L.append("  ② ★★★【「下周三」写到了明天】今天是周二，本周三就是明天 —— 加课解析")
    L.append("     一直按「最近的未来那个星期几」算，「下」「下下」「这」全被扔了。")
    L.append("     现在认前缀：下周三 = 10-28，这周五 = 10-23。")
    L.append("  ③ 【包名】校赛提交包后缀 -xs 改成 -ss。")
    L.append("  ④ 【守卫】gtest/verify_h.py 47 -> 50 条；h 自带 test_study_agent 76/76。")
    L.append("")
    L.append("  ── build 2026-10-20-k（伴学：口径再放宽 + 空档改成归纳回答）────")
    L.append("  ① ★★★【需求】「继续测试，让伴学更智能，口径大点」。体检语料 59 -> 107 句，")
    L.append("     挖出 22 处跑偏全部修掉，现在 107/107。")
    L.append("  ② ★★【又补的口语】课多吗、上几门、第一节、最后一节、晚课、没课吧、")
    L.append("     的课（明天的课 / 明天的课几点开始）、空闲、空档、哪天空、哪天没课…")
    L.append("  ③ ★★★【空档改成归纳】「这周哪天空」现在直接回一句：")
    L.append("     没课的有：周三（10-21）、周六（10-24）、周日（10-25）。其余 周二、周四、周五 有课。")
    L.append("     以前是逐天罗列，用户还得自己数哪几天没课。")
    L.append("  ④ ★★【两道闸】带 安排/规划/制定/排一下 的是「要排」留给模型；")
    L.append("     没点日期就不猜，只有「课表 / 哪天空」这类默认按这周剩下的几天答。")
    L.append("  ⑤ 【守卫】gtest/verify_h.py 35 -> 47 条；h 自带 test_study_agent 76/76。")
    L.append("")
    L.append("  ── build 2026-10-20-i（伴学：换一种问法也认得出）────")
    L.append("  ① ★★★【实测】「本周五有课吗」答得对，换成「查询本周五的课程」就变成")
    L.append("     排了一整天学习计划。根因：本地答课只看「有没有课 / 课表」几个词，")
    L.append("     「动词 + 课」的说法一个都不匹配 → 整句交给模型 → 模型按「排计划」办。")
    L.append("  ② ★★【口径补全】查/看/问/列 + 课、上课吗、要上课、用不用…上课、第几节、")
    L.append("     课上到几点、几点下课、是不是有课、有课不、在哪上、几天课、的课程安排…")
    L.append("     ★ 刻意不裸加「有课 / 上课」——「明天上课前把作业写完」会被误抢。")
    L.append("  ③ ★★【加课也认「明天」】「明天下午3-5点体检」原来被判成「说不清哪天」，")
    L.append("     因为加课解析只认周X。现在认 今天/明天/后天/大后天，落到具体那天，")
    L.append("     名字里的日期词也剥掉（否则叫「明天下午 体检」）。")
    L.append("  ④ 【守卫】gtest/verify_h.py 21 -> 35 条（含 9 种换法问课 + 3 种不许抢的排计划）。")
    L.append("")
    L.append("  ── build 2026-10-20-h（AI 伴学：只按要求排 + 常见时间说法）────")
    L.append("  ① ★★★【原句】「答非所问……只按要求排，不要莫名其妙又输出什么线性代数」，")
    L.append("     以及「一问后天就不行了」。本版把开发线 2026-10-20 的伴学修复移植进 h 线。")
    L.append("  ② ★★★【说一条就写一条】「周六18-19点上钢琴课，排2周」以前被当成「排一份两周计划」，")
    L.append("     模型于是排出一整天（练琴 / 复习高数 / 预习线代 / 自习）。现在后端识别")
    L.append("     「星期 + 钟点区间 + 一件事」，只写一条课表记录（weeks 记 7-8），")
    L.append("     秒回一句「已写进时间表：钢琴课，18:00-19:00」；同格已有只提示、不新建。")
    L.append("  ③ ★★★【问哪天有没有课，本地先答】以前由模型自由发挥，实测会把「查的那天」")
    L.append("     说成「今天」（问周四答今天）。现在课表在手上，本地直接算：")
    L.append("     10-22（周四）没有课。")
    L.append("  ④ ★★【常见时间说法补齐】今天/明天/后天/大后天/明早/明晚/后晚、昨天/前天、")
    L.append("     明后天（两天）、周六日（两天）、周三到周五、周末、这周/下周/下下周、")
    L.append("     工作日、这两天、这几天、每天、24号/10月24日、裸写 10-24 和 10/24。")
    L.append("  ⑤ ★★【相对说法要回显】用「后天」问的，回答里带上那个词，顶上注明今天是几号：")
    L.append("       （今天是 10-20，周二）")
    L.append("       后天（10-22，周四）没有课。")
    L.append("     因为「后天」和「周四」常常是同一天，不回显就像把上一句又贴了一遍。")
    L.append("  ⑥ ★★【「昨天」只给问课用】排计划时拿到已经过去的日子是有害的（一句")
    L.append("     「昨天没学完」会让计划从昨天开始排），所以默认不认，只有问课那条路打开。")
    L.append("  ⑦ ★【前端】调用记录多一条「写入我的时间表」；加完课自动回读时间表，")
    L.append("     页脚不再显示「当前时间表为空」。")
    L.append("")
    L.append("  ── 上一版（build w，2026-10-15 晚）──────────────────────")
    L.append("  ① ★★【本版·需求】学习规划三个子页里，界面上这个功能**统一叫「时间表」**，")
    L.append("     不再叫「课表」。改的是**给人看的字**：三页胶囊标签、标题「我的时间表」、")
    L.append("     按钮「导入时间表」「保存时间表」、toast / 空态 / 等待语、Agent 工具名")
    L.append("     （「读取我的时间表」），以及后端展示给用户的文案与提示词用词。")
    L.append("     【注意】代码里的名字一个都没动，也不许动：localStorage 的 timetable_local、")
    L.append("     loadTimetable / saveTimetable、DOM 锚点 #tt / #tt-body / #file-import、")
    L.append("     接口 /planner/timetable、Agent 工具名 get_timetable、表名 courses。")
    L.append("     一句话：**「时间表」只活在文案里，代码里继续叫 timetable / courses。**")
    L.append("  ② 【本版】新增 tools/test_term_timetable.mjs（51 项，纯 Node 秒级）做双向守卫：")
    L.append("     正向（新词都在：胶囊 / 标题 / 按钮 / toast / 工具名 / 后端文案）")
    L.append("     + 反向（旧文案一个字都不许回来、上面那批内部名字一个都不许被顺手改掉）。")
    L.append("     【注意】HTML 一律**先剥掉注释再断言** —— 注释里写着「以前叫课表」是故意的。")
    L.append("  ③ 【本版】tools/test_xls_import.mjs 的按钮选择器跟着改成「保存时间表」。")
    L.append("     文案和断言是一对，改了文案不同步改断言，就会把**正确实现判成失败** ——")
    L.append("     这类问题已经出现过三次（扫码文案两次、AI伴学等待语一次）。")
    L.append("")
    L.append("  ── 上一版（build v，2026-10-15 白天）──────────────────")
    L.append("  ① ★★★【本版·主线·需求】「AI伴学」页从**表单**改成**对话框**。")
    L.append("     原句：「这里使用 AI agent，对话框方式，其他样式去掉」。")
    L.append("     原来是一张「生成设置」表（学习目标 / 计划天数 / 年级 / 专业 +")
    L.append("     「生成计划」按钮）+ 结果框 + 单独挂着的「Agent 调用记录」一块。")
    L.append("     问题是**它看起来像个表单工具，不像一个 Agent** —— 哪怕后台真在调工具，")
    L.append("     用户感受到的也只是「填表 → 等 → 出结果」。")
    L.append("     现在整页只剩一个对话框，跟着变的几件事：")
    L.append("       · 说目标 = 打字，不再是填输入框；")
    L.append("       · **天数从话里认**（「排 3 天」「排两周」「半个月」都认，都没说就")
    L.append("         沿用上一轮，默认 7 天）—— 下拉框撤了，这是天数的唯一来源；")
    L.append("       · 调用记录**挂在它自己那条回答里**（谁答的就贴在谁下面），")
    L.append("         而不是飘在页面别处；这正是旧版最看不出「这是 Agent」的地方；")
    L.append("       · 支持**多轮**：前面说过的话会一起发上去，所以「改成两周」")
    L.append("         「再细一点」这类短追问接得上（后端只回灌 user 原话，计划正文不回灌）。")
    L.append("     等待提示沿用「AI 倾听对话」那套口径：不带省略号 + 给出大概还要多久 + 有温度。")
    L.append("  ② ★【本版·需求】「五期食堂」改名为「五期餐厅」（类别/坐标都不动）。")
    L.append("     截图「食堂餐饮 5」里其余 4 条都叫「XX餐厅」，只有它叫「食堂」——")
    L.append("     同一个类别里两套叫法，看着像漏改的。")
    L.append("  ③ 【本版】新增 tools/test_plan_chat.mjs（55 项，纯 Node 秒级）钉住上面这些：")
    L.append("     旧控件不许回来 / 天数只能从话里认 / **发送时必须带上课表**（旧问题）/")
    L.append("     history 必须在 push 本条消息之前取（取反了同一句话会发两遍）。")
    L.append("     其中「旧样式已撤掉」那几条要先**剥掉 HTML 注释**再断言 ——")
    L.append("     plan.html 里那段注释正是在说明「这些控件已经撤掉了」，")
    L.append("     直接匹配源码会把**正确的新实现判成失败**（同一条教训这个项目栽过好几次）。")
    L.append("  ④ 【本版】新增 gtest/e2e_plan_chat.mjs 做真浏览器验收（真 GLM，两轮约 3 分钟）：")
    L.append("     旧表单真的没了 → 回车发送 → 有等待提示 → 出计划 → 调用记录在 AI 气泡里，")
    L.append("     第二轮只说「改成两周」，验 days=14 且 history 里带着上一轮原话。")
    L.append("  ⑤ 【本版】给模型的用户消息里**逐天列出「日期（周X）」**，让它照抄而不是自己算星期。")
    L.append("     实测：锚点写的是「今天的日期是 2026-10-15（周四）」，模型仍把 10-15 排成「（周六）」，")
    L.append("     10-16（周日）、10-17（周一）整串跟着错。计划每天都顶着星期，错一天就是全篇错，")
    L.append("     而它自己完全意识不到。把清单给它，就从「算」变成「抄」。")
    L.append("     tools/test_study_agent.py 加了 6 项钉住（含「天数变了清单也要跟着变」）。")
    L.append("  ── 上一版（build u，2026-10-15 凌晨）────────────────────")
    L.append("  ① ★【本版】地图点位再补 3 处（宿舍类凑齐东/西/五/六四个园区）：")
    L.append("       · 新增「五期宿舍」-> 宿舍类（实测踩点坐标，落在举重中心那片院里，")
    L.append("         与主校区隔一条路约 560 米 —— 用户确认过就是那儿）")
    L.append("       · 新增「五期食堂」-> 餐饮类（紧邻高德「柏斯健身台球(阳光学院店)」）")
    L.append("       · 「六期」改名为「六期宿舍」（与西区/东区/五期宿舍口径一致）")
    L.append("       点位总数 42 -> 44；「六期餐厅」**没有**被误改，食堂餐饮现在是 5 个。")
    L.append("        改名的副作用：`findPlace(「六期」)` 这种**简称**不再命中（原来靠完全匹配）。")
    L.append("       查过为什么不加前缀模糊匹配：「六期」会同时前缀命中「六期宿舍」和")
    L.append("       「六期餐厅」，给出一个不确定的答案，比明确地找不到更糟。")
    L.append("  ② ★★【本版·需求】「数据输出」：删数据之前先把它输出出来。")
    L.append("     原句：「每次换包、用户注销账号都要把个人的或者全部的库里数据，")
    L.append("     向量库数据输出。」两条路径正好对应两种范围 ——")
    L.append("       · **换包** -> 范围 = **全部**（打印涉及哪些账号 + 各表条数 + 向量总数）")
    L.append("       · **注销账号** -> 范围 = **个人**（打印该用户的课表/情绪对话原文/")
    L.append("         测评 + 该用户的向量条数）")
    L.append("     都会落一份完整 JSON 到 `backend/data_export/`，同时在服务端日志打一份")
    L.append("     人能读的清单。理由：这个项目最疼的一次事故就是**静默丢数据**，")
    L.append("     原则是「数据可以删，但不能悄悄删」。")
    L.append("     ★ 新增 `backend/services/data_export.py`；`database.py` 的换包分支与")
    L.append("     `routers/auth.py` 的注销分支都接了它，且**输出一定在删除之前**（有测试钉住）。")
    L.append("      **`data_export/` 必须排除在交付包之外**（里面是真实用户数据：")
    L.append("        账号名、课表、情绪对话原文）—— `gtest/pack_zip.py` 与")
    L.append("        `tools/gen_structure.py` 两处都加了，`.gitignore` 也加了。")
    L.append("  ③ ★【本版】「五期宿舍」坐标修正（用户第二次给的）：原坐标落在校区外的")
    L.append("     「福建省举重中心」院里、离主校区约 560 米，现改为 119.45204,26.005447 ——")
    L.append("     离高德「阳光学院东区20号楼」只有 19 米，两处互相印证。")
    L.append("  ④ ★【本版】清库时**连向量库一起清**（原来只清 SQLite，这是个真隐患）。")
    L.append("     users.id 没有 AUTOINCREMENT，清库后第一个新用户会拿到 id=1，")
    L.append("     而 id=1 的历史向量还在 -> 新用户检索到**上一个人的隐私片段**。")
    L.append("     新增 `_clear_vector_store()`，在 CAMPUS_RESET_DB=1 分支里调用。")
    L.append("  ⑤ 【本版】新增 tools/test_data_export.py（22 项）。其中两条是**顺序守卫**：")
    L.append("     「输出」必须出现在 `db.delete(user)` / `db_file.unlink()` 之前 ——")
    L.append("     位置挪了逻辑照样跑、日志照样打，但打出来的是空数据，没人会发现。")
    L.append("      那两条断言要用 `rfind` 而不是 `find`：**文档字符串里也提到了这两个调用**，")
    L.append("        用 find 会匹配到注释，导致守卫恒失败（第一版就是这么错的）。")
    L.append("  ── 上一版（build s，同一天深夜）────────────────────")
    L.append("  ① ★★★【本版·主线】「AI 计划」改名「AI伴学」，并从**一次性问答**")
    L.append("     升级成**真正的 AI Agent**（function calling / 工具调用）。")
    L.append("     以前是把课表拼进提示词直接问模型，于是「未来 7 天哪天第几节有空」")
    L.append("     这种**日期算术 + 区间合并**全压在语言模型身上 —— 而那是它最不擅长")
    L.append("     的事，结果经常排出「上课时间和自习时间打架」的计划。")
    L.append("     现在给模型 5 个**接在真实数据上**的工具，让它自己去查：")
    L.append("       get_timetable       读这名学生的课表（与前端的课表页同一份数据）")
    L.append("       get_class_schedule  读节次作息表（含南区/北区两套时间）")
    L.append("       find_free_slots     算出某天的空闲时段（08:00-22:00 扣掉占用）")
    L.append("       get_building_info   把「知行楼A203」这类文字规范化成楼名 + 判南北区")
    L.append("       get_weather         校区实时天气（高德）")
    L.append("     空档是**算出来的**，不再靠模型拍脑袋；工具调用循环有上限（4 轮），")
    L.append("     最后一轮强制作答，避免模型「再查一次」停不下来。")
    L.append("     ★ 调用轨迹会**返回给前端并显示出来**（「Agent 调用记录」），")
    L.append("     这是 Agent 与黑盒问答最直观的区别，答辩时比计划本身更有说服力。")
    L.append("     【注意】开发中遇到一个必须记住的问题：**不把「今天是几号」告诉模型，它会自己编。**")
    L.append("     实测它把今天当成 2023-04-15，于是查了 2023 年的课表、拿到 0 门课，")
    L.append("     最后交出一份和真实课表毫无关系的计划，而且**从输出上完全看不出来**。")
    L.append("     现在锚点日期写死在用户消息里，工具层还做了兜底：")
    L.append("     模型若问了一个与计划区间毫无交集的日期，会被**夹回窗口**并附说明。")
    L.append("  ② ★【本版】地图点位按用户指定调整（4 处，都有测试钉住）：")
    L.append("       · 新增「操场」（田径场/足球场）-> 归「其他」")
    L.append("       · 新增「东区宿舍」-> 归「宿舍」（东区 7/8/10 号楼楼群中心）")
    L.append("       · 原「西区」改名「西区宿舍」并换坐标（西区 9/10/11/13 号楼楼群中心）")
    L.append("       · 「食堂餐饮」从 9 条精简到 4 条：只留 东区餐厅/西区餐厅/六期餐厅/")
    L.append("         创四小镇；7-ELEVEn（便利店）挪进「其他」；")
    L.append("         陈家生煎/1点点/益禾堂/0090汉堡工厂 **整条删除**。")
    L.append("     点位总数 44 -> 42。路网地标表（campus-graph.js）里的「西区宿舍」")
    L.append("     坐标同步更新，否则路线文字指引会指在旧位置。")
    L.append("  ③ ★【本版】情绪陪伴的等待体验（需求：不要省略号 / 要有大概耗时 /")
    L.append("     要有温度）。原来等回复时先插一个「……」的占空气泡，现在改成：")
    L.append("     先说一句人话（「我在认真读你的话，想好好回你一句」），下面跟一行")
    L.append("     不断缩短的秒数；超出预估就换成「还在写，别急」这类安抚语。")
    L.append("     预估取自上一轮的真实耗时（localStorage 平滑），首次 12 秒兜底；")
    L.append("     等待气泡只是 DOM 临时节点，**不进对话历史**、不会被持久化。")
    L.append("  ④ ★【本版·隐私】新增 qdrant.clear_all_memory()：**整体清空向量记忆**。")
    L.append("     数据库被重建时（CAMPUS_RESET_DB=1）**必须连向量库一起清** —— "
            "users.id 没有 AUTOINCREMENT，清库后第一个新用户会拿到 id=1，")
    L.append("     而 id=1 的历史向量还在，新用户会检索到**上一个人的隐私片段**。")
    L.append("     这与 auth.py 注销账号时强调「必须删向量」是同一个理由。")
    L.append("  ⑤ 【本版】新增两个测试：tools/test_campus_places.mjs（22 项，点位数据")
    L.append("     契约，纯 Node 秒级）、tools/test_study_agent.py（36 项，Agent 工具")
    L.append("     行为 + 锚点日期 + 防幻觉兜底，用临时库不碰项目里的 db）。")
    L.append("  ── 上一版（build r）及更早的改动 ────────────────────")
    L.append("  1) ★★★【真 bug】修好「部署到公网后一直登录不了」。")
    L.append("     现象：能注册、能登录（接口都 200），但随便点哪个页面都被弹")
    L.append("     「登录状态已过期，请重新登录」，重新登录也还是一样 —— 死循环。")
    L.append("     根因：**托管平台的网关会改写 / 注入 Authorization 请求头**。实测")
    L.append("     证据（用 curl 打线上域名）：① 完全不带 Authorization，后端却收到")
    L.append("     一个解不开的 token；② 带 `Authorization: Basic abcdef`，后端照样按")
    L.append("     「解不开 token」处理，而按后端代码 Basic 开头本该落到「你还没有")
    L.append("     登录」。两件事都证明**客户端发的那一版头根本没到后端**。")
    L.append("     后果：所有需要登录的接口一律 401，前端见 401 就清 token、弹过期、")
    L.append("     跳回登录页 —— 永远进不去。")
    L.append("     【注意】它**只在部署环境出现**，本机怎么测都正常（本机直连后端、")
    L.append("     不经网关），所以前面几轮回归全绿也照样漏掉了它。")
    L.append("     修法：后端优先读一个网关不会碰的自定义头 X-Campus-Token，")
    L.append("     Authorization 降级为回退（本机 / 局域网 / 第三方客户端照旧，")
    L.append("     老路径一个字没砍）；前端 api() 两个头都发，哪条通都能认证。")
    L.append("     ★ 新增 tools/test_auth_token.mjs（21 项）守住这个契约，其中")
    L.append("     「坏 Authorization + 好 X-Campus-Token」那条就是线上死循环的复现。")
    L.append("  2) ★【本版·数据安全】「换了一份新包」不再自动清空数据库。")
    L.append("     原来只要检测到包标识变了就 unlink() 删库。这条规则在**公网部署**时")
    L.append("     是灾难：每次重新发布都会重置源码 mtime -> 判定成「换包」-> 账号全被")
    L.append("     清空，用户浏览器里还留着旧 token，于是每个请求 401 —— 上面那条")
    L.append("     「一直登录不了」里也有它的份；本机「把新包解压到旧文件夹」同样会")
    L.append("     静默丢掉全部课表和情绪记录。")
    L.append("     现在：默认**什么都不删**，只把旧库另存一份备份")
    L.append("     （campus_ai.db.bak-<旧标识>）并说明数据在哪、想从零开始怎么做；")
    L.append("     只有显式设 CAMPUS_RESET_DB=1 才真的删库重建。")
    L.append("     一句话：数据只在用户亲手要求时才消失。")
    L.append("     【注意】backend/campus_ai.db*（含备份）一律不进包 —— 打包与清单")
    L.append("     两处都改成按前缀排除，否则用户数据会随包发出去。")
    L.append("  3) ★【本版】扫码文案收敛成一句话：**用浏览器打开**（需求）。")
    L.append("     同一屏的三处口径全部统一，且去掉了所有「谁不行 / 为什么不行」的解释：")
    L.append("       · 提示语   ：请用手机浏览器扫码打开")
    L.append("       · 副标题 / 图标标签：用手机浏览器扫一扫")
    L.append("       · 底部 App 图标：**只剩「浏览器」一个**（原来是微信 / 百度两个，")
    L.append("         再早是六个：校园 App / 微信 / 支付宝 / QQ / 企业微信 / 钉钉）。")
    L.append("     本机 / 局域网 / 公网**三态同一套话术**，不再按部署形态分叉 —— 同一")
    L.append("     页面里三处说法打架，比说错一句还糟。")
    L.append("     背景（只写在这里，**不写进用户界面**）：实测带微信 UA 请求线上")
    L.append("     域名，托管平台直接返回 403（iOS 的 WKWebView、Android 的 XWeb 都")
    L.append("     一样），页面只有一句「请在浏览器中打开」；而 Safari / Chrome /")
    L.append("     QQ / 钉钉 / 支付宝 / 百度 / 小红书**全部 200**。这是平台策略，")
    L.append("     客户端改不了。")
    L.append("     为什么界面上**不提**这件事（需求）：用户要的是「用什么")
    L.append("     打开」，把平台限制摊在界面上既啰嗦、又容易被误读成「这应用有问题」。")
    L.append("  4) ★ 为「公网部署 / 在线演示」做的三处适配。三处都做到**本机行为")
    L.append("     一个字不变**，只为让同一份代码能在云沙箱里跑起来（云平台只给**一个**")
    L.append("     公网端口，且要求服务读 PORT 环境变量并绑 0.0.0.0）：")
    L.append("     a. backend/main.py 的 _api_port() 增加读** PORT **环境变量，顺序为")
    L.append("        CAMPUS_API_PORT -> PORT -> API_PORT。云平台注入的是 PORT，不认它")
    L.append("        的后果不是「端口差一点」，而是服务听在平台没开放的口子上、整站打")
    L.append("        不开，而且**本机永远复现不出来**（本机没这个变量，照样跑 8000）。")
    L.append("     b. 根路径 / 从「返回一段 JSON」改成 302 跳转到 /app/login.html。本机")
    L.append("        开发时看 JSON 够用，但分享出去的就是**域名根**，点开看到一坨 JSON")
    L.append("        会让人以为站点坏了。")
    L.append("     c. frontend/js/config.js 的后端地址推导改为「**同源优先**」：页面本身")
    L.append("        是 https 时直接用同源的 /api，不再拼 http://host:8000 —— 后者在")
    L.append("        https 页面上属于**混合内容**，会被浏览器直接拦掉（同样本机复现不")
    L.append("        出来，因为本机是 http）。")
    L.append("     顺带：__main__ 入口的 uvicorn reload 改为默认关闭、可用")
    L.append("     CAMPUS_DEV_RELOAD=1 打开 —— 云上长时间跑不需要文件监视。")
    L.append("  5) ★ 扫码说明里**「要不要连同一个网络」这一句跟着部署形态走**。")
    L.append("     （★ 本版更新：动作那句「用手机上的浏览器扫上面这个码」已统一，不再")
    L.append("     分叉；现在跟着形态变的**只剩这个前提条件**，因为它是事实差异、不是")
    L.append("     话术差异。）")
    L.append("     原来写死一句「手机与电脑连同一个 Wi-Fi」：本机/局域网时是对的，")
    L.append("     公网部署后就**错了**（二维码里已是公网域名，手机用流量就能开）。")
    L.append("     现在由 frontend/js/login.js 的 isLocalOnlyOrigin() / scanNoteHtml()")
    L.append("     按当前 origin 决定：本机或局域网 -> 补一句「手机要和电脑连同一个")
    L.append("     网络（同一个 Wi-Fi）」（二维码里放的是局域网 IP，不连同一网络就是")
    L.append("     打不开，属于使用前提）；公网域名 -> 整句不出现。")
    L.append("     留着一句错文案的代价不是「不准确」这么轻：评委看到「要连同一 Wi-Fi」")
    L.append("     很可能直接放弃扫码 —— 一个本来更好用的入口被一句过时文案劝退了。")
    L.append("     两侧都有断言守着：本机形态进 tools/test_scan_flow.mjs，公网形态由")
    L.append("     gtest/e2e_scan_public.mjs 在真实域名上跑。")
    L.append("  6) 【上一版】「开始 / 结束」时间改为**只读**（需求）。时间本来就由")
    L.append("     「你从课表哪一格点开的」唯一决定（选了教学楼再按该楼所在校区换算），")
    L.append("     让它可编辑只会制造「第 1 节的格子里写 10:00」这种自相矛盾的记录。")
    L.append("     要换时间就换个格子点，或者改教学楼。前端加了 .is-locked 样式（底色压深")
    L.append("     一档、去掉聚焦环）+ 一行说明小字。")
    L.append("     ★ 顺带修掉一个旧 bug：原先 onBuildingInput 里那句「只在用户没手动改过")
    L.append("     时间时才覆盖」的守卫，会让人**先选 A 楼、再改选 B 楼时时间卡在 A 区**")
    L.append("     （值已不等于基准时间，守卫误判成「用户改过」而拒绝更新）。只读之后不")
    L.append("     存在「用户改过」这种状态，一律按当前节次 + 当前教学楼重算；清空教学楼")
    L.append("     也会还原成基准（南区）时间。")
    L.append("     「从已添加的课程中选择」也不再带出旧课程的时间（只带教师 / 教学楼 /")
    L.append("     教室），改为带出教学楼后主动重算一次 —— 被复用的课常在别的节次，")
    L.append("     把它的时间套过来就会跟格子对不上。")
    L.append("  7) ★ 打包规则修正：backend/.env 现在**真的在包里**了。README 与")
    L.append("     「如何启动.md」一直写着「压缩包里 backend/.env 已经填好全部 Key、开箱")
    L.append("     即用」，但打包排除规则却把 .env 挡在包外 —— 文档与包对不上，用户解压")
    L.append("     后就撞上「27 个接口全在、天气和 AI 却全是占位文案」。现在按文档说的来：")
    L.append("     解压即用，不用自己配 Key。")
    L.append("     【注意】代价：交付包带着真实 Key，**不要转发给别人、不要上传到公开仓库**。")
    L.append("     .gitignore 仍然排除 .env（git 仓库里不该有 Key），两条规则各管一段。")
    L.append("     backend/campus_ai.db 仍然不打进包 —— 那是本机运行时的用户数据，")
    L.append("     所以解压到**新目录**等于账号/课表从零开始（解压到原目录不受影响）。")
    L.append("  8) ★ 按需求，把「我的位置」/ 定位功能**整个删除**了（代码、文案、")
    L.append("     依赖、测试、启动脚本分支、README 章节）。原因：定位不准，得不偿失。")
    L.append("     删掉的东西：frontend/js/locate.js（多源融合定位模块）、tools/make_cert.py")
    L.append("     （自签证书）、tools/test_locate.mjs、nav.js 里的定位函数与「定位质量」")
    L.append("     面板、nav.html 的「我的位置 / 用我的位置出发」按钮与说明卡、style.css 的")
    L.append("     定位样式、后端 /map/ip-locate 与 amap_ip_locate、启动脚本的 https 实例")
    L.append("     （8443）与证书生成、/api/system/lan-ip 的 https_port 字段。")
    L.append("  9) 【行为变更】起点留空不再尝试定位，直接用**校内默认出发点（图书馆）**，")
    L.append("     并把它写回「起点」输入框 + 明确提示，避免「点了开始导航却没反应」。")
    L.append("  10) 【负向守卫】新增 tools/test_scan_login_contract.py（27 项）：逐个断言")
    L.append("     定位相关的标识符/文案/接口/插件**必须不存在**，同时守住扫码登录这条链")
    L.append("     （局域网 IP 兜底 + 二维码按当前目录拼 + 两份启动脚本逐字节一致）。")
    L.append("     为什么值得单独立一条负向测试：删功能比加功能更容易留残渣 —— 删了面板")
    L.append("     忘了删样式、删了调用忘了删函数、删了 JS 忘了删 HTML 按钮，最后页面看着")
    L.append("     正常、代码里躺一堆死代码。")
    L.append("  11) 【扫码登录排查】实测问题：「手机扫码打不开网页」。逐项排查后确认**不是**")
    L.append("     代码问题：二维码里的地址就是本机 WLAN 真实 IP、服务绑的是 0.0.0.0、")
    L.append("     防火墙对 python.exe 有任意端口入站放行；而把电脑开在 127.0.0.1、用真实")
    L.append("     浏览器跑完整条链路（出码 → 手机打开 → 登录 → 确认 → 电脑端领票进主页）")
    L.append("     全部 200 通过。所以问题在**手机到电脑这条网络路径**上（校园网 / 公共 Wi-Fi")
    L.append("     常见的客户端隔离，或不在同一网段）。启动脚本的自检提示已改写：明确说清")
    L.append("     「本机自测通过只证明网卡和端口没问题」，并给出一定能绕开的办法（手机开")
    L.append("     热点、让电脑连手机热点，再重跑脚本），以及「直接用账号密码登录」备选。")
    L.append("  12) 上一版（build j）仍然有效：校园路网几何推倒重来 —— 折线几何不再被丢，")
    L.append("     节点离路 p90 从 26.1 m 降到 3.3 m，离路 >3 m 的里程占比 5.7%；")
    L.append("     贴路硬指标 tools/qa_campus_graph.py（把折线投到底图真实路像素上量距离）。")
    L.append("     当时那两条「离路网多远」的断言用的是 distanceToNetwork，这个函数随定位")
    L.append("     一起删了，断言改为直接验 planCampusRoute 对校外坐标返回 null。")
    L.append("  13) 更早版本仍然有效：Excel 导入不依赖 pandas、识别「一大节两行」的连堂")
    L.append("     课表、教学楼简称（商教514 -> 商德楼514）自动换成正式名、课表按周显示。")
    L.append("")
    L.append("-" * 56)
    L.append("")

    for g in order:
        files = groups[g]
        L.append("【根目录】" if g == "（根目录）" else f"【{g}/】 （{len(files)} 个）")
        for rel, sz in files:
            L.append(f"    {rel:<52} {sz:>9,} 字节")
        L.append("")

    L.append("-" * 56)
    L.append("")
    L.append("换了新包之后，先确认「跑的是新包」（重要）：")
    L.append("  1) 启动时看横幅里的 Package build，应当等于本清单第 5 行的构建号。")
    L.append("     若不是（或弹出向上找目录的警告），说明启动脚本用的是旧文件夹：")
    L.append("     关掉所有窗口 -> 把旧文件夹整个删掉 -> 重新解压到全新的路径。")
    L.append("  2) 登录页页脚显示「后端构建 …」，应与上面的构建号一致。")
    L.append("  3) 浏览器打开 http://127.0.0.1:8000/api/ping 也能看到同一个号。")
    L.append("")
    L.append("放到服务器 / 云平台上跑（可选）：")
    L.append("  服务读 PORT 环境变量（顺序 CAMPUS_API_PORT -> PORT -> API_PORT），")
    L.append("  并监听 0.0.0.0，所以云平台直接 python backend/main.py 就能起；")
    L.append("  只有**一个**端口，前端由后端在 /app 下一起托管，不需要另外起静态服务。")
    L.append("  页面走 https 时前端会自动同源调用 /api（不会拼出 http:// 触发混合内容拦截）。")
    L.append("")
    L.append("装完先确认依赖齐全：")
    L.append("  双击 check-windows.bat，看第 5 项 Backend dependencies")
    L.append("  显示 ALL OK 就没问题；显示 MISSING 就重新双击 start-windows.bat")
    L.append("")
    L.append("导入课表只需要 xlrd + openpyxl，不需要 pandas。")
    L.append("")
    L.append("关键文件：")
    L.append("  backend/build.txt                     构建号唯一来源（接口/横幅都读它）")
    L.append("  backend/main.py                       FastAPI 入口（读 PORT，可公网部署）")
    L.append("  backend/requirements.txt              后端依赖（pandas 已标为可选）")
    L.append("  backend/services/timetable_excel.py   Excel 解析（自带无 pandas 降级路径）")
    L.append("  backend/services/study_agent.py       ★ AI伴学 Agent：5 个真实工具的 schema + 实现 + 调用循环")
    L.append("  backend/services/data_export.py       ★ 数据输出：删数据前把库/向量库数据打印并落 JSON（换包=全部，注销=个人）")
    L.append("  start-windows.bat                     启动脚本（含向上找目录的告警 + 手机连通性排查）")
    L.append("  tools/bump_build.py                   一处改构建号，全项目同步")
    L.append("  tools/sync_launcher.py                两份启动脚本整文件同步（要么一致要么报错）")
    L.append("  tools/check_bat.py                    启动脚本自检（含依赖清单一致性）")
    L.append("  frontend/js/campus-graph.js           校园路网（折线几何 + Dijkstra + 文字指引）")
    L.append("  frontend/js/nav.js                    导航交互（吸附、文字指引、起点兜底）")
    L.append("  tools/test_campus_graph.mjs           校园路网回归（81 项；核心=折线几何不许丢）")
    L.append("  tools/qa_campus_graph.py              ★ 贴路硬指标（把路网投到底图路像素上量距离）")
    L.append("  tools/refdata/campus_shot.png         上面那个脚本用的标定底图（高德干净底图）")
    L.append("  tools/test_scan_login_contract.py     扫码登录 + 起点兜底契约（27 项，含删定位负向守卫）")
    L.append("  tools/test_auth_token.mjs             鉴权 token 契约 + 扫码文案口径（21 项，含网关改写 Authorization 的复现）")
    L.append("  tools/test_timetable_freeze.mjs       课表冻结 + 按周显示回归")
    L.append("  tools/test_xls_import.mjs             Excel 导入回归（第8节测缺 pandas 降级）")
    L.append("  tools/test_campus_places.mjs          校园点位数据契约（27 项，纯 Node 秒级，无需浏览器）")
    L.append("  tools/test_plan_chat.mjs             ★ AI伴学对话框契约（55 项：旧表单不许回来 + 天数从话里认 + 发送必须带课表）")
    L.append("  tools/test_term_timetable.mjs        ★ 「时间表」术语契约（51 项：三页文案统一 + 旧词不许回来 + 内部名不许改）")
    L.append("  frontend/js/planner-plan.js          ★ AI伴学对话框逻辑（发消息 / 天数识别 / 等待提示 / 调用记录渲染）")
    L.append("  tools/test_study_agent.py             AI伴学 Agent 契约（36 项：锚点日期 + 逐天星期清单 + 多轮 context + 防幻觉兜底）")
    L.append("  tools/test_data_export.py            数据输出契约（22 项：换包/注销两条路径 + 输出必须先于删除的顺序守卫）")
    L.append("")

    # ⚠️ GBK + 无 BOM（原因见文件顶部说明），换行 CRLF
    data = ("\r\n".join(L) + "\r\n").encode("gbk")
    OUT.write_bytes(data)

    print(f"已写入 {OUT.relative_to(ROOT)}（GBK 无 BOM，{len(data):,} 字节）")
    print(f"文件数 = {len(items)}，合计 {total:,} 字节，构建号 = {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
