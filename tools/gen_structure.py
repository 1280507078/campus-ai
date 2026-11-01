"""重建「包内结构.txt」（Windows 记事本可读）。

为什么要做成项目里的正式工具，而不是一次性脚本
--------------------------------------------
这份清单是**用户排查问题时的第一手资料**：它列了每个文件的大小、本包的构建号、
以及「改了代码没生效时该看哪里」。它必须随每次打包更新，否则会误导用户
（清单说构建 g、实际包是 h）。以前它是临时脚本 + 手工清单，容易漏。

用法：
    python tools/gen_structure.py            # 按文件系统实际内容重建清单

⚠️ 编码选择（曾出现问题，修改前须知）：
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
#    README 与「docs/使用说明.pdf」一直承诺「压缩包里 backend/.env 已经填好全部 Key、
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
    L.append("      详见 docs/使用说明.pdf。")
    L.append("")
    L.append(f"本版变更（构建 {tag}）：")
    L.append("  ① 【技术文档】23 页 PDF，八章：项目概述、需求分析、系统架构、AI 技术方案、")
    L.append("     核心功能实现、测试与部署、总结与展望、创新点说明；含 6 张图与三级书签。")
    L.append("  ② 【README】按大赛材料要求重写：项目介绍、功能一览、架构与选型、安装运行、")
    L.append("     环境配置、测试说明、开发过程、团队信息。")
    L.append("  ③ 【评审账号】启动时自动创建演示账号 demo（预置 7 条示例课程）与管理员账号；")
    L.append("     两者均为幂等创建，已存在则跳过，不重置密码、不覆盖已修改的课表。")
    L.append("  ④ 【提交包】-ss 包顶层仅保留 README.md 与技术文档.pdf 两份说明；")
    L.append("     操作手册位于 docs/使用说明.pdf，逐文件清单与重复的启动说明不进入该包。")
    L.append(f"  ⑤ 【构建号】{tag}：14 个页面的 ?v= 与登录页页脚同步。")
    L.append("")
    L.append("版本沿革：")
    L.append("  2026-11-01-a  参赛材料包：技术文档、README、评审账号")
    L.append("  2026-10-25-b  情绪对话写入 chat_sessions，管理端统计不再恒为 0")
    L.append("  2026-10-24-c  注释与文档表述整理（仅注释，AST 逐节点比对代码零差异）")
    L.append("  2026-10-23-a  语料扩至 29 篇；手册问答加入三道反幻觉闸（分数/动作词/对象）")
    L.append("  2026-10-22-c  部署鉴权修正：网关会改写 Authorization，改读 X-Campus-Token")
    L.append("  2026-10-21-*  地图导航：自建校园步道图、贴路指标核验、移除浏览器定位")
    L.append("  2026-10-20-*  课表导入容错（四种表头与星期写法、连堂归并）；伴学三种人格")
    L.append("  2026-10-18-*  时间表改为按周次建模（锚点 + 偏移），不再按日期展开")
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
    L.append("  页面经 https 访问时前端以同源方式调用 /api，不会拼出 http:// 而触发混合内容拦截。")
    L.append("")
    L.append("安装后确认依赖完整：")
    L.append("  双击 check-windows.bat，看第 5 项 Backend dependencies")
    L.append("  显示 ALL OK 表示依赖完整；显示 MISSING 则重新运行 start-windows.bat")
    L.append("")
    L.append("导入课表只需要 xlrd + openpyxl，不需要 pandas。")
    L.append("")
    L.append("关键文件：")
    L.append("  backend/build.txt                     构建号唯一来源（接口/横幅都读它）")
    L.append("  backend/main.py                       FastAPI 入口（读 PORT，可公网部署）")
    L.append("  backend/requirements.txt              后端依赖（pandas 已标为可选）")
    L.append("  backend/services/timetable_excel.py   Excel 解析（自带无 pandas 降级路径）")
    L.append("  backend/services/study_agent.py       AI 伴学 Agent：6 个只读工具的 schema + 实现 + 调用循环")
    L.append("  backend/services/data_export.py       数据导出：删除前将库与向量库数据打印并写入 JSON（换包取全量，注销取个人）")
    L.append("  start-windows.bat                     启动脚本（含向上找目录的告警 + 手机连通性排查）")
    L.append("  tools/bump_build.py                   单点修改构建号，全项目同步")
    L.append("  tools/sync_launcher.py                两份启动脚本整文件同步（不一致即报错）")
    L.append("  tools/check_bat.py                    启动脚本自检（含依赖清单一致性）")
    L.append("  frontend/js/campus-graph.js           校园路网（折线几何 + Dijkstra + 文字指引）")
    L.append("  frontend/js/nav.js                    导航交互（吸附、文字指引、起点兜底）")
    L.append("  tools/test_campus_graph.mjs           校园路网回归（81 项；核心是折线几何不得丢失）")
    L.append("  tools/qa_campus_graph.py              贴路质量指标（将路网投影到底图道路像素上量测距离）")
    L.append("  tools/refdata/campus_shot.png         上述脚本使用的标定底图（高德干净底图）")
    L.append("  tools/test_scan_login_contract.py     扫码登录与起点兜底契约（27 项，含移除定位的负向守卫）")
    L.append("  tools/test_auth_token.mjs             鉴权 token 契约与扫码文案口径（12 项，含网关改写 Authorization 的复现）")
    L.append("  tools/test_timetable_freeze.mjs       课表冻结 + 按周显示回归")
    L.append("  tools/test_xls_import.mjs             Excel 导入回归（第 8 节验证缺 pandas 时的降级路径）")
    L.append("  tools/test_campus_places.mjs          校园点位数据契约（27 项，纯 Node，无需浏览器）")
    L.append("  tools/test_plan_chat.mjs             AI 伴学对话框契约（56 项：旧表单不得复现、天数从语句识别、发送须带课表）")
    L.append("  tools/test_term_timetable.mjs        「时间表」术语契约（51 项：三页文案统一、旧词不得复现、内部名不得修改）")
    L.append("  frontend/js/planner-plan.js          AI 伴学对话框逻辑（发消息 / 天数识别 / 等待提示 / 调用记录渲染）")
    L.append("  tools/test_study_agent.py             AI 伴学 Agent 契约（82 项：锚点日期、逐天星期清单、多轮 context、防幻觉兜底）")
    L.append("  tools/test_data_export.py            数据导出契约（22 项：换包与注销两条路径，输出须先于删除的顺序守卫）")
    L.append("")

    # ⚠️ GBK + 无 BOM（原因见文件顶部说明），换行 CRLF
    data = ("\r\n".join(L) + "\r\n").encode("gbk")
    OUT.write_bytes(data)

    print(f"已写入 {OUT.relative_to(ROOT)}（GBK 无 BOM，{len(data):,} 字节）")
    print(f"文件数 = {len(items)}，合计 {total:,} 字节，构建号 = {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
