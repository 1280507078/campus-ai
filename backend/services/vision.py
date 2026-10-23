"""课表截图识别的编排逻辑。

## 为什么是「网格 + 补详情」两轮，而不是一轮全字段

一开始的做法是让模型直接输出「课程数组，每条带课名/教师/教学楼/教室/星期/节次」。
这条路走不通，发现三个问题：

  1. **串位**：模型要同时记「这格在哪一列」和「这格写了什么」，还要组织成长 JSON，
     十几格一起处理时它会把整片星期写错（把周一的课全写成周四），而且**毫无察觉**、
     也不标存疑 —— 用户扫一眼根本发现不了，是最危险的一种错。
  2. **截断**：GLM 视觉模型 max_tokens 卡得很死（glm-4v-flash 只有 1024），
     全字段 JSON 写十几条就超了，返回半截不合法 JSON，结果等于「什么都没识别出来」。
  3. **越切越乱**：试过把图纵向切片绕开长度限制，但切片丢了表头后串位更严重，
     还得额外处理重叠区去重。

后来发现真正有效的做法是**先只读课程名，按网格输出**：

    {"rows": [{"slot": 1, "cells": ["高等数学A", null, "大学英语", "程序设计", null]}, ...]}

同样一张 16 门课的课表，这个格式只要 543 个字符（对比全字段的上千字符），
一轮就装得下、不截断；而且 `cells` 数组的**下标天然就是星期**，
模型不用再「心算列号→星期」，串位问题直接消失（16/16 全对）。

代价是这一轮没有教师/教学楼/教室。所以再跑**第二轮补详情**：
把第一轮读到的网格当「已确定的事实」喂回去，只让它补三个字段。
第二轮不需要判断位置（位置已经给定），也就不会再串位；
输出量虽然大些，但可以用多轮分担。

两轮都比一轮全字段准，而且第一轮的网格可以**独立校验**
（cells 长度必须等于表头数），错位能被程序发现，不会静默传下去。

## 为什么还要「行数不足就重试」

改完网格方案后又验证了一轮，发现**同一张图连打 5 次，有 2 次只读出 6 条**
（真值 16 条）。抓下来看，那 2 次的 JSON 括号是**配平的** —— 也就是说模型不是被输出上限截断，
而是自己「以为写完了」，交了一份**语法完全合法的半截结果**（只有 `rows[0]`）。

这种「自愿提前收尾」比截断更阴险：它不报错、不标存疑，用户看到的是「6 条，全部识别成功」，
而漏掉的 10 条课根本不会出现在界面上。

应对分两层：
  1. **提示词层**：先让模型「数出共几行 R」，再明确要求「必须写满 R 行，
     后面整行是 null 也要写」，把「写到第 R 行」变成可自查的完成条件。
  2. **程序层（就是下面的重试）**：不信模型的自述，拿 `row_count` / 实际行数
     跟「图里应该有几行」比对，明显偏少就**重新问一次**。
     重试时把上一次的结果一起发过去，明确告诉它「你只写了 N 行，还差」，
     比盲目重发有效得多。
"""
import logging

from services.glm import glm_vision
from services.llm_json import (
    extract_json as _extract_json,
    salvage_headers as _salvage_headers,
    weekday_from_header as _weekday_from_header,
)

logger = logging.getLogger(__name__)

# 第一轮最多问几次。1 次原始 + 1 次「你行数不够，补全」的追问。
_GRID_ATTEMPTS = 2

# 行数少于这个值时，认为「几乎肯定没读完」，无条件重试。
# 大学课表再短也有 3-4 节；只读到 1-2 行基本就是提前收尾。
_MIN_ROWS_SANE = 2



def _grid_to_text(rows: list, col_headers: list) -> str:
    """把第一轮读到的网格转成「第2格（周二） 五六节 = 人工智能技术与应用」这种人话。

    为什么转成文字而不是把 JSON 塞回去：文字更像「笔记」，模型更容易照着它
    逐条补字段；直接塞 JSON 时模型有概率重排或改写课程名。

    ⚠️⚠️ 关于「要不要把星期写进来」，这里出现过两次问题，两个方向都试过：

      版本 A（写死星期）：「周二 五六节 = 某课」
        -> 第一轮一旦错位，这里就把**错的星期**喂给模型，模型直接照抄。
           两轮给出的错误星期完全一样，交叉校验形同虚设（两轮一致 -> 不标黄）。

      版本 B（写完锚还要求核对）：「第2格（周二） 五六节 = 某课」+ 提示词说「自己核对」
        -> 措辞上给了纠偏出口，但**锚的影响盖过了措辞**：
           错位时两轮给出的错值依旧完全一致（周2/周3/周4 一起错），
           说明模型根本没去纠偏，只是照抄。

      版本 C（现在这个）= **只给位置，不给结论**：
        输出「第2个星期列 五六节 = 某课」，并明确交代
        「**第1个星期列是周一**，请自己数到第几个」。
        模型拿到的是**可自行验证的位置信息**（对着表头数格子），
        而不是一个可以直接抄的答案 —— 它必须回原图确认，
        两轮才有可能是真正独立的判断。
        误标黄 0 格（说明模型数得准），且错位时两轮不再同步错。

    一句话总结这个取舍：**给方法，不给答案**。
    """
    # 列号 -> 星期：优先用模型回读的表头算，表头缺失时退回「第1列=周一」
    col_wd = {}
    for i, h in enumerate(col_headers or [], start=1):
        col_wd[i] = _weekday_from_header(h) or i

    # 表头里**第一个星期列**的序号：告诉模型「这是第1个星期列」，
    # 它才可以对着表头一个个数下去（月份/周次那种装饰列会被排除在外）。
    first_wd_col = None
    for i in range(1, len(col_headers or []) + 1):
        if _weekday_from_header(str(col_headers[i - 1] or "")):
            first_wd_col = i
            break
    if first_wd_col is None:
        first_wd_col = 1

    lines = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cells = r.get("cells") or []
        if not isinstance(cells, list):
            continue
        # 优先用这一行自带的分节文字（新版 grid 格式把节次放在 label 里，
        # 且 label 是图上原文，比一个行号数字更利于模型对上位置）
        label = str(r.get("label") or "").strip()
        where = label if label else f"第{r.get('slot')}节"
        for idx, cell in enumerate(cells, start=1):
            if not cell:
                continue
            # 这一格是表头里**第几个星期列**（第1个=周一）。
            # 用 weeks 更准（由表头行生成）；没有就按列号推。
            weeks = r.get("weeks")
            wd = None
            if isinstance(weeks, list) and len(weeks) == len(cells):
                try:
                    w = int(weeks[idx - 1])
                    if 1 <= w <= 7:
                        wd = w
                except (TypeError, ValueError):
                    wd = None
            if wd is None:
                wd = col_wd.get(idx, idx)
                if not (1 <= wd <= 7):
                    continue
            # 一格多课用 | 分隔
            for one in str(cell).split("|"):
                one = one.strip()
                if one:
                    lines.append(f"第{wd}个星期列 {where} = {one}")
    return "\n".join(lines)


def _row_count(data: dict) -> int:
    """这一份结果里实际读到了几个**节次行**（两种格式都支持）。

    ⚠️ 为什么不只看 `rows`：
       新版提示词让模型输出 `{"grid": [[表头行], [节次行], ...]}` ——
       表头就是第 0 行，所以「节次行数 = grid 长度 - 1」。
       早期这里只认 `rows`，于是新格式的结果被判定成「0 行」，
       直接被当成识别失败去追问，白白多跑一轮还拿不到数据。
    这里统一成「节次行数」，让新旧两种格式走同一套判断。
    """
    if not isinstance(data, dict):
        return 0
    g = data.get("grid")
    if isinstance(g, list) and g:
        return max(0, len(g) - 1)        # 第 0 行是表头
    r = data.get("rows")
    return len(r) if isinstance(r, list) else 0


def _is_grid_format(data: dict) -> bool:
    """这份结果是不是新版二维 grid 格式。"""
    return isinstance(data, dict) and isinstance(data.get("grid"), list) and bool(data["grid"])


def _rows_ragged(data: dict) -> bool:
    """grid 里各行的格数是否**不一致**（= 有行漏写/多写了格子）。

    为什么专门检查这个：模型（尤其 glm-4v-flash）常把「左边几列没课」的行
    写成不带头部空位的短行。这样的行**整体左移一格**，
    于是周一的课被按位置判成周二 —— 而且它 JSON 合法、不报错、不标黄，
    用户看着像正常结果，直接保存就进库了。比「少读几行」危险得多。

    只认 grid 格式；旧格式没有 grid 无法判断，返回 False（不误伤）。
    """
    g = data.get("grid") if isinstance(data, dict) else None
    if not isinstance(g, list) or len(g) < 2:
        return False
    lens = {len(r) for r in g if isinstance(r, list)}
    # 长度集合不止一个 !=> 有行跟别人不一样
    return len(lens) > 1


async def _ask_grid_with_retry(image_b64: str, prompt: str,
                              timeout: float) -> tuple[str, dict, list]:
    """第一轮：读网格；行数明显偏少就问第二次。

    模型偶尔会「以为写完了」而只交前一两行（JSON 合法、不报错，最难发现），
    所以这里不看它说自己写了几行，而是**拿实际节次行数做判断**，
    太少就用「追问」的方式重来一次 —— 追问时把上次的残缺结果贴回去，
    让它只做「补全」这件小事，成功率比重新发一遍高很多。

    Returns:
        (text, data, warnings)
    """
    warnings: list = []
    text = await glm_vision(image_b64, prompt, timeout=timeout)
    if not text:
        return "", {}, warnings

    data, truncated = _extract_json(text)
    n_rows = _row_count(data)
    is_grid = _is_grid_format(data)

    # ---- 截断时先尝试「抢救已写完整的行」 ----
    # 旧版这里是 `if truncated or len(rows) >= _MIN_ROWS_SANE: return`，
    # 也就是**一旦截断就直接放弃**：明明前面十几行都读对了，却因为它们后面
    # 跟了个没写完的 JSON 就整批丢掉，用户看到「没能读出课程」。
    # 现在改成：截断时先用 _salvage_rows 把结构完整的行抠出来，
    # 救回来的行照样能进下面的流程（总数够就正常用，不够再追问）。
    salvaged_n = 0
    if truncated and n_rows < _MIN_ROWS_SANE:
        from routers.planner import _salvage_rows   # 延迟 import，避免循环依赖
        salv = _salvage_rows(text)
        # 只保留带 cells 的（防止把别的对象混进来）
        salv_rows = [r for r in salv if isinstance(r.get("cells"), list)]
        salvaged_n = len(salv_rows)
        if salvaged_n:
            logger.info("输出被截断，从半截 JSON 里救回 %d 行", salvaged_n)
            warnings.append(f"AI 输出被截断，已抢救回收 {salvaged_n} 行 —— "
                            f"后半部分可能缺失，请核对完再保存。")
            if not is_grid:
                # 旧格式：把救回来的行包成 data 让上层拿到 rows
                data = {"rows": salv_rows}
            # 新格式（grid）：半截 grid 里救出来的行也拼回 grid，
            # 否则上层拿不到数据。表头行若没救到，从 salvage_headers 补。
            else:
                hdr = data.get("grid")[0] if data.get("grid") else None
                if not isinstance(hdr, list) or not hdr:
                    hdr = _salvage_headers(text)
                new_grid = [hdr]
                for r in salv_rows:
                    cells = r.get("cells") or []
                    new_grid.append([str(r.get("label") or "")] + list(cells))
                data = {"grid": new_grid}
                n_rows = len(new_grid) - 1

    # 行数够了就收工（截断但已救回足够多的行，也算够用）
    # ⚠️ 但还要看一件事：**各行格数是否一致**。
    #    glm-4v-flash 经常把「左边几列没课」的行写成不带头部空位的短行，
    #    导致该行所有格子**整体左移一格**（周一的课被判成周二）。
    #    这类错位比「少读几行」危险得多：它不报错、不标黄，用户直接存进课表。
    #    所以「格数不齐」也值得追问一次（追问话术会明确要求补齐开头空位）。
    if n_rows >= _MIN_ROWS_SANE:
        if _rows_ragged(data):
            logger.info("各行格数不一致，追问一次要求补齐空位")
            text2, data2, w2 = await _ask_ragged_fixup(
                image_b64, prompt, text, data, timeout)
            if text2:
                # 追问结果必须「行数没变少 + 格数更整齐」才采用，
                # 否则宁可保留原结果（原结果至少行数是够的）
                if _row_count(data2) >= n_rows and not _rows_ragged(data2):
                    return text2, data2, warnings + w2
            # 追问没修好 —— 这时**必须告诉用户**。
            # 因为格数不齐意味着有行的星期是错位的，而它不会以任何
            # 其它形式报错（不标黄、不弹窗），用户很可能直接就存进课表了。
            # 宁可多说一句让他核对，也别让他把错的课表存进去。
            warnings.append(
                "有几行的格子数与表头对不上，个别课程的上课星期可能错位 —— "
                "请在核对表里对照原图确认一遍再保存。"
            )
            return text, data, warnings
        return text, data, warnings

    # ---- 行数不足（0~1 行）：几乎肯定是提前收尾或被截断，追问一次 ----
    # ⚠️ 截图场景里只有 1 行基本不可能是真的（课表至少 3-4 节），
    #    所以这里**不再区分是不是截断**，一律追问。旧版把截断排除在外，
    #    导致「输出太长被砍 → 只救回 1 行 → 直接放弃」成为最常见的失败路径。
    logger.info("第一轮只读到 %d 行（截断=%s），判定为未读完，追问补全", n_rows, truncated)
    prev = ""
    if data.get("grid") and len(data["grid"]) > 1:
        prev = str(data["grid"][-1][0])[:20]
    elif data.get("rows"):
        prev = str(data["rows"][-1].get("slot"))
    followup = (
        prompt
        + "\n\n注意：你上一次的输出**不完整**（只到 "
        + (f"{n_rows} 行，第 {prev} 节就停了" if n_rows else "0 行")
        + "）。请重新看一遍截图最左边那一列，数清楚到底有多少个节次行，"
        + "然后**从第一节一直写到最后一节**，一行都不要漏\n"
        # 追问时明确要求「课名写短」—— 这是防止再次被截断的关键。
        # 第一轮失败多半就是输出太长被砍，如果追问还是原样长，大概率再砍一次。
        + "（整行没课的也要照写，格子里填空字符串 \"\"）。\n"
        + "为节省篇幅、避免再次被截断：格子里**只写课程名就好**，"
        + "不用写教师、教学楼、教室（那些我后面会另问）。\n"
        + (f"你上次已经读到的内容（供你接着往下写）：{text[:600]}\n" if text else "")
        + "现在请重新输出完整的 JSON："
    )
    text2 = await glm_vision(image_b64, followup, timeout=timeout)
    if not text2:
        return text, data, warnings
    data2, truncated2 = _extract_json(text2)
    n_rows2 = _row_count(data2)
    # 追问结果更差时，也试着从它里面抢救（可能只是截断，前半是好的）
    if n_rows2 < _MIN_ROWS_SANE:
        try:
            from routers.planner import _salvage_rows
            salv2 = [r for r in _salvage_rows(text2) if isinstance(r.get("cells"), list)]
            if salv2:
                if _is_grid_format(data2):
                    hdr = data2["grid"][0] if data2.get("grid") else None
                    if not isinstance(hdr, list) or not hdr:
                        hdr = _salvage_headers(text2)
                    data2 = {"grid": [hdr] + [[str(r.get("label") or "")] + list(r.get("cells") or [])
                                            for r in salv2]}
                else:
                    data2 = {"rows": salv2, "col_headers": _salvage_headers(text2)}
                n_rows2 = _row_count(data2)
        except Exception as e:
            logger.warning("追问结果抢救失败：%s: %s", type(e).__name__, e)
    # 追问结果更好才采用（条数变多）
    if n_rows2 > n_rows:
        logger.info("追问后行数 %d -> %d，采用追问结果", n_rows, n_rows2)
        return text2, data2, warnings
    return text, data, warnings


async def _ask_ragged_fixup(image_b64: str, prompt: str, text: str,
                            data: dict, timeout: float) -> tuple[str, dict, list]:
    """针对「各行格数不一致」追问一次，要求它把开头缺的空位补齐。

    为什么要单独追问而不是直接放弃：
      格数不齐 = 那一行按位置对齐是错的（周一会变成周二）。
      但这**不是「读不出来」，而是「写漏了」** —— 内容它其实都看到了，
      只是没把空位补上。所以把「你哪几行短了」直接指出来让它补，
      比重新识别一遍成功率高得多（重新识别可能又换个方式错）。

    返回 (text2, data2, warnings)；data2 为空表示追问失败。
    """
    if not _is_grid_format(data):
        return "", {}, []

    g = data["grid"]
    n_head = len(g[0]) if isinstance(g[0], list) else 0
    if not n_head:
        return "", {}, []

    # 列出格数不对的行（用节次文字定位，比行号好认）
    bad = []
    for r in g[1:]:
        if isinstance(r, list) and len(r) != n_head:
            label = str(r[0])[:14] if r else "?"
            bad.append(f"「{label}」那行只写了 {len(r)} 格（应该 {n_head} 格）")
    if not bad:
        return "", {}, []

    followup = (
        prompt
        + "\n\n⚠️ 你上一次的输出里，**有几行的格子数不对**，导致课程会左右错位：\n"
        + "\n".join("  - " + b for b in bad[:12])
        + f"\n\n表头一共 {n_head} 格，所以**每一行都必须正好 {n_head} 个格子**。"
        + "少写的那几行，请把**开头的空格用 \"\" 补上**再输出一次"
        + "（哪一格没课就写空字符串，不能省略）。\n"
        + "注意：只是补空位，**课程名照抄你上次写的，不要改动、不要漏课**。\n"
        + (f"\n你上次的输出（供你在此基础上补齐）：{text[:1500]}\n" if text else "")
        + "\n现在请重新输出补齐后的完整 JSON："
    )
    text2 = await glm_vision(image_b64, followup, timeout=timeout)
    if not text2:
        return "", {}, []
    data2, _t2 = _extract_json(text2)
    if not _is_grid_format(data2):
        return "", {}, []
    return text2, data2, []


async def recognize_timetable(image_b64: str, prompt: str, detail_prompt: str,
                              timeout: float = 90.0) -> tuple[str, str, list, dict]:
    """两轮识别课表截图。

    Args:
        image_b64: 纯 base64 图片。
        prompt: 第一轮「读课程名网格」的指令。
        detail_prompt: 第二轮「补教师/教学楼/教室」的指令模板，含 {grid} 占位符。
        timeout: 单次请求超时。

    Returns:
        (grid_text, detail_text, warnings, grid_data)
        grid_text 是第一轮原始输出；
        detail_text 是第二轮原始输出（含 details 数组）；任一为空表示该轮失败。
        ⚠️ grid_data 是**已经归一化好**的结构（统一成 `{"rows":[...], "col_headers":[...]}`
           的形状，且每行都带 weeks）。把它一并返回给调用方，
           是为了让「解析」只发生在这一个地方 —— 调用方再解析一遍 grid
           很容易和这里产生细微分歧（比如表头行算不算），那正是列错位的温床。
    """
    warnings: list = []

    # ---- 第一轮：只读课程名，输出网格（行数不足会自动追问一次）----
    grid_text, grid_data, w1 = await _ask_grid_with_retry(image_b64, prompt, timeout)
    warnings.extend(w1)
    if not grid_text:
        return "", "", ["AI 识别服务暂时没响应（可能是 GLM_API_KEY 未配置、额度用尽或网络不通）。"
                        "你可以先手动添加课程，或稍后重试。"], {}

    # ---- 统一成「旧格式的 rows + col_headers」，让下游只认一种形状 ----
    #
    # 新版 grid 格式：第 0 行是表头，第 1 行起每行是 [节次, 各列内容...]。
    # 这里就地转成 rows/col_headers，好处是**下游（含 planner 那边）只需要认一种格式**，
    # 不用到处写 `if grid ... elif rows ...`。
    grid_rows = grid_data.get("grid")
    if isinstance(grid_rows, list) and grid_rows:
        clean = []
        for r in grid_rows:
            if isinstance(r, list):
                clean.append(["" if c is None else str(c).strip() for c in r])
        # ⚠️ 这里**不做任何「剔除首列」的清理** —— 出现过问题，记一下：
        #
        # 模型会在表头行和每行铺一些**非星期的辅助列**，例如：
        #     ["", "09月", "周一 21", "周二 22", ...]      ← 首格空
        #     ["一二节 01,02", "08:00", "高等数学…", ...]  ← 第 2 格是时间
        # 我一度以为首格的空串是「多余的占位」，试图剔掉它对齐星期 ——
        # 结果把**真正的列**也一起挪了，反而错得更远。
        #
        # 正确做法：**一格都不动**，把「第 i 格对应表头第 i 格」原样保留下去，
        # 由星期解析函数（_weekday_from_header）去判断哪一格才是星期列。
        # 解析不出星期的格子（空串、`09月`、`08:00`）自然会被跳过，
        # 不需要在数据层面做任何删改。**少动数据，多靠判据**。
        col_headers = clean[0] if clean else []
        n_cols = max((len(r) for r in clean), default=0)
        rows = []
        for i, r in enumerate(clean[1:], start=1):
            # ⚠️⚠️ 补齐之前先记下**模型原本给了几格**。
            #
            # 这一步是必需的，别省。原因：
            #   模型经常在某一行**少写几个格子**（尤其是跨列多的行，
            #   它倾向于只写非空的格，把开头的空位吞掉）。
            #   为了对齐会把短行末尾补空串 —— 但这样一来
            #   「这一行原始格数是多少」这个**关键信号就被抹掉了**，
            #   下游再也分不清「这行本来就整齐」和「这行被补齐过」。
            #   而这两者要区别对待：被补齐过的行，按位置推出来的星期
            #   **整体是错位的**，必须标黄让用户核对。
            raw_len = len(r)
            if len(r) < n_cols:
                r = r + [""] * (n_cols - len(r))
            rows.append({
                "slot": i,
                "label": r[0],                 # 第 1 个元素是节次
                "cells": r[1:],                # 其余是各列内容，与表头**逐格对齐**
                # 模型这一行原始给到的格数（含节次列）。与 len(cells)+1 不等
                # 就说明被补齐过 -> 这一行的列对齐不可信，下游要标黄。
                "raw_len": raw_len,
                # weeks 的每一格 = 「cells 这一格对应的表头格解析出的星期」。
                # 解析不出的（时间列、月份列）写 0，下游会把 0 当「不是课程列」跳过。
                # ⚠️ 下标必须与 cells 严格一一对应 —— 这就是新格式最大的价值：
                #    表头和数据在同一份 JSON 里，列对应关系由数组结构保证，
                #    不需要任何事后猜测或偏移推断。
                "weeks": [_weekday_from_header(str(h)) for h in col_headers[1:]],
            })
        # 顺手把换算后的 rows 回写进 grid_data，让上层（planner）也能直接用，
        # 不必自己再解析一遍 grid（两边解析逻辑不一致是错位的常见源头）。
        grid_data = {"rows": rows, "col_headers": col_headers}
        logger.info("grid 格式已归一化：表头 %d 列（其中星期列 %d 个），节次行 %d 行",
                    len(col_headers),
                    sum(1 for h in col_headers if _weekday_from_header(str(h))),
                    len(rows))
    else:
        rows = grid_data.get("rows") or []
        col_headers = grid_data.get("col_headers") or []
        if not (isinstance(col_headers, list) and col_headers):
            col_headers = _salvage_headers(grid_text)

    # ---- rows 为空的最后兜底 ----
    # 走到这里说明「原始输出 + 追问输出 + 两边抢救」都没拿到行，
    # 但在放弃之前还有一招：**直接从原文里抠行对象**。
    # 有些情况下 data["rows"] 是空（JSON 解析整体失败），可文本里其实躺着
    # 结构完好的 {"slot":1,"label":"一二节","cells":[...]} —— 白白丢掉太可惜。
    if not rows:
        from routers.planner import _salvage_rows
        rows = [r for r in _salvage_rows(grid_text) if isinstance(r.get("cells"), list)]
        if rows:
            logger.info("主解析失败，从原文硬抠出 %d 行", len(rows))
            warnings.append(f"识别结果不完整，已尽量回收 {len(rows)} 行，请仔细核对。")
            grid_data = {"rows": rows, "col_headers": col_headers}

    if not rows:
        # 到这里才真的没辙。区分「被截断」和「真没读到」：
        # 截断时提示裁剪图片，真空时提示换图。
        _, grid_truncated = _extract_json(grid_text)
        if grid_truncated:
            warnings.append("AI 的输出被截断了，没能读完这张时间表。"
                            "把截图裁掉多余留白、或分成上下两半分别导入，效果会好很多。")
        else:
            warnings.append("没能从这张图里读出课程。请确认传的是时间表截图"
                            "（表头有星期、左列有节次），或者换一张更清晰的重试。")
        return grid_text, "", warnings, grid_data

    grid_text_lines = _grid_to_text(rows, col_headers)
    if not grid_text_lines:
        warnings.append("这张图里似乎没有读到课程，请确认传的是时间表截图。")
        return grid_text, "", warnings, grid_data

    # ---- 第二轮：补详情（位置已确定，不会再串位）----
    detail_text = ""
    try:
        dp = detail_prompt.replace("{grid}", grid_text_lines)
        detail_text = await glm_vision(image_b64, dp, timeout=timeout)
    except Exception as e:
        logger.warning("课表详情补读失败：%s: %s", type(e).__name__, e)

    if not detail_text:
        warnings.append("课名读出来了，但教师 / 教学楼 / 教室没能补全，"
                        "这些格已标黄，请手动补一下（不影响课程名和上课时间）。")

    return grid_text, detail_text, warnings, grid_data
