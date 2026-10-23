"""校园文档 RAG：把 `backend/data/campus_docs/*.md` 切块、向量化、写进 Qdrant。

与情绪记忆的区别（这是两套东西，别混）：

|            | 情绪记忆（services/qdrant.py 的 emotion_memory） | 校园文档（campus_docs） |
|------------|-----------------------------------------------|------------------------|
| 内容       | 用户自己说过的话                                | 学校公开的办事流程、校历 |
| 隔离       | 按 payload.user_id 强制过滤                     | 全库共享，不过滤          |
| 谁写入     | 对话过程中自动写                                | 由本模块一次性导入        |
| 清个人数据 | 整集合删除                                      | **不动**（见 clear_all_memory 的注释） |

语料来源：阳光学院教务处官网（jwc.ygu.edu.cn）公开的「学生办事指南」与「校历」，
抓取脚本见项目外 `gtest/scrape_jwc_docs.py`。每篇 Markdown 开头都带原文链接与抓取日期，
检索结果会把 `source` 一起返回，答案里能标出处 —— 这是「不编」的前提。

用法：
    # 导入（可重复执行，同内容覆盖，不会重复堆叠）
    cd backend && python -m services.rag_docs
    # 只看要切多少块、不写库
    cd backend && python -m services.rag_docs --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/data/campus_docs（本文件在 backend/services/ 下）
DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "campus_docs"

CHUNK_SIZE = 400      # 单块目标字数。400 字 ≈ 一段流程说明，够回答一个具体问题
CHUNK_OVERLAP = 60    # 相邻块重叠。跨块的句子不会被切断后两边都读不懂
MIN_CHUNK = 40        # 太短的块（「流程图」这种独立成行的词）没有检索价值，丢掉


# 语料指纹存这里（运行时产物，不进包 —— 见 .dockerignore 与 tools/gen_structure.py）
FINGERPRINT_FILE = DOCS_DIR.parent / ".docs_fingerprint"


def fingerprint(docs_dir: Path | str | None = None) -> str:
    """语料指纹：文件名 + 大小 + 修改时间 的哈希。

    ★ 为什么需要它：
      启动自检原来只判「集合是不是空的」。语料**改了**但集合非空时，
      的文档永远进不去库 —— 表现为「学生问图书馆几点开门，系统答
      '手册里没写'」，而手册里明明写了。这种错不报异常、日志也干净。
      所以判据升级成「集合为空 或 指纹变了」。
    """
    d = Path(docs_dir or DOCS_DIR)
    if not d.is_dir():
        return ""
    h = hashlib.sha1()
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            # ★ 只算内容，**不算 mtime**：mtime 会随拷贝、解压、git checkout 变化，
            #   同一份语料在开发机和服务器上会算出不同指纹 → 每次部署都白导一遍
            #   （91 块要调 8 次 embedding，白花钱白等待）。判据要的是「内容变没变」。
            h.update(p.name.encode())
            h.update(b"\0")
            h.update(p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:16]


def read_fingerprint() -> str:
    try:
        return FINGERPRINT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_fingerprint(fp: str) -> None:
    try:
        FINGERPRINT_FILE.write_text(fp, encoding="utf-8")
    except OSError as e:
        logger.warning("写语料指纹失败（不影响检索）：%s", e)


def load_docs(docs_dir: Path | str | None = None) -> list:
    """读目录下所有 .md，返回 `[{"name","text"}]`。

    ★ 跳过以 `_` 开头的文件：那是给人看的说明（如 `_语料说明.md`），
      不该被当成知识回答用户。
    """
    d = Path(docs_dir or DOCS_DIR)
    if not d.is_dir():
        logger.warning("校园文档目录不存在：%s", d)
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取 %s 失败：%s: %s", p.name, type(e).__name__, e)
            continue
        if text.strip():
            out.append({"name": p.stem, "text": text})
    return out


def _split_sections(md: str) -> list:
    """按二级标题切段，返回 `[(section_title, section_text)]`。

    文档的 `# 标题` 与开头那段 `> 来源 / 原文 / 抓取日期` 属于元信息，
    拼进每个块的正文里只会浪费检索额度 —— 来源单独作为 payload 存。
    """
    lines = md.splitlines()
    sections, cur_title, buf = [], "", []
    for ln in lines:
        if ln.startswith("> "):          # 元信息块，跳过
            continue
        if re.match(r"^#{1,3}\s+", ln):
            title = re.sub(r"^#{1,3}\s+", "", ln).strip()
            if re.match(r"^#\s", ln) and not cur_title:
                cur_title = title       # 一级标题当默认段名
                continue
            if buf:
                sections.append((cur_title, "\n".join(buf)))
            cur_title, buf = title, []
            continue
        buf.append(ln)
    if buf:
        sections.append((cur_title, "\n".join(buf)))
    return [(t, s) for t, s in sections if s.strip()]


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """把一段文本切成 ~size 字的块，块间重叠 overlap。

    切分优先落在「句子边界」（。！？；换行），实在找不到才硬切 ——
    硬切出来的半句话，检索命中后模型也读不懂，等于白召回。
    """
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return []
    if len(text) <= size:
        return [text]

    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("。"), window.rfind("！"), window.rfind("？"),
                      window.rfind("；"), window.rfind("\n"))
            if cut >= size * 0.55:          # 离末尾太近就不值当切（会切出极短块）
                end = start + cut + 1
        piece = text[start:end].strip()
        if len(piece) >= MIN_CHUNK:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def build_chunks(docs_dir: Path | str | None = None) -> list:
    """把整个语料目录切成 `[{"text","source","section"}]`。"""
    chunks = []
    for doc in load_docs(docs_dir):
        for title, body in _split_sections(doc["text"]):
            for piece in chunk_text(body):
                chunks.append({
                    "text": piece,
                    "source": doc["name"],
                    "section": title or doc["name"],
                })
    return chunks


async def ingest(rebuild: bool = False, docs_dir: Path | str | None = None) -> dict:
    """导入语料。返回 `{ok, files, chunks, written, ...}`，不抛异常。

    `rebuild=True` 会先删掉整个 campus_docs 集合再重建 —— 只在「语料大改、
    想彻底清掉旧块」时用。日常增删单篇文档**不需要**它：
    点的 id 是内容哈希，同内容覆盖、删掉的文件才需要 rebuild 清理。
    """
    from services import qdrant as q

    chunks = build_chunks(docs_dir)
    files = len(load_docs(docs_dir))
    if not chunks:
        return {"ok": False, "files": files, "chunks": 0,
                "error": "语料为空（检查 backend/data/campus_docs/）"}
    if not q.is_configured():
        return {"ok": False, "files": files, "chunks": len(chunks),
                "error": "未配置 QDRANT_URL / QDRANT_API_KEY，文档库不可用"}

    if rebuild:
        q.clear_docs()

    written = await q.add_docs(chunks)
    stats = q.docs_stats()
    return {
        "ok": written > 0,
        "files": files,
        "chunks": len(chunks),
        "written": written,
        "points": stats.get("points"),
    }


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="导入校园文档语料到 Qdrant")
    ap.add_argument("--rebuild", action="store_true", help="先清空 campus_docs 再导入")
    ap.add_argument("--dry-run", action="store_true", help="只切块并打印统计，不写库")
    args = ap.parse_args(argv)

    if args.dry_run:
        files = load_docs()
        chunks = build_chunks()
        print(f"文件 {len(files)} 篇，切出 {len(chunks)} 块")
        for c in chunks[:3]:
            print("-" * 60)
            print(f"[{c['source']} / {c['section']}] {len(c['text'])} 字")
            print(c["text"][:120].replace("\n", " "))
        return 0

    res = asyncio.run(ingest(rebuild=args.rebuild))
    print(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
