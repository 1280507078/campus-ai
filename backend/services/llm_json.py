"""从大模型输出里解析 JSON 的小工具（planner 路由与 vision 服务共用）。

单独放在这里是为了**打断循环依赖**：
routers/planner.py 要 import services/vision.py 去调识别，
而 vision.py 又需要这里的 JSON 解析；两边都往对方 import 就会启动即 ImportError。
把共用的小函数抽到第三个模块，两个方向都只依赖它，依赖图就成了树。

这里的东西都只做纯字符串处理，不碰数据库、不碰配置，放哪都能用。
"""
import json
import re


def _strip_trailing_commas(t: str) -> str:
    """去掉 JSON 里的**尾随逗号**：`[1, 2, ]` / `{"a": 1, }`。

    ⚠️ 这是实测中最常见、也最阴的一个坑：
       GLM 视觉模型输出二维数组时，很爱写成
           [["a","b"], ["c","d"], ]
                              ↑ 多一个逗号
       这在 JSON 里是**语法错误**，`json.loads` 直接抛 JSONDecodeError。
       而外层看到「解析失败」只会当成「模型没读出来」，
       于是把一份**内容完全正确**的结果白白丢掉 —— 用户看到的就是
       「识别不到课表」，但模型其实读对了每一个字。

    处理办法：把「逗号后面直接跟 ] 或 }」的逗号删掉。
    要小心别碰字符串**内部**的逗号（比如课名里带逗号），
    所以这里先按字符扫一遍，只在「不在字符串里」时才动手。
    """
    if not t:
        return t
    out = []
    in_str = False
    esc = False
    n = len(t)
    i = 0
    while i < n:
        ch = t[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            # 往后看第一个非空白字符
            j = i + 1
            while j < n and t[j] in " \t\r\n":
                j += 1
            if j < n and t[j] in "]}":
                # 尾随逗号：丢掉这个逗号（后面的空白照留）
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_json(text: str) -> tuple[dict, bool]:
    """从模型输出里抠出 JSON 对象。

    模型很爱在 JSON 外面包一层 ```json ... ```，或者前面加一句「好的，这是结果：」，
    这里做几轮兜底，尽力把花括号那段抠出来。

    **长输出容易被 max_tokens 截断**（JSON 写到一半就没了），所以额外返回
    truncated 标志：截断时不能当作「模型没读到东西」，上层据此决定是报错
    还是把已经完整的部分救回来。

    Returns:
        (obj, truncated)
    """
    if not text:
        return {}, False
    t = text.strip()

    # 去掉 Markdown 代码块围栏（截断时可能只有开围栏没有闭围栏，也一并处理）
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    else:
        t = re.sub(r"^```(?:json)?\s*", "", t).strip()
        t = re.sub(r"\s*```$", "", t).strip()

    # ★ 先修尾随逗号再 parse ★
    # 实测模型给二维数组时经常写成 [..., ]，严格 JSON 解析会直接失败，
    # 而内容其实是**完全正确**的 —— 不修的话就会把好结果当成「没识别出来」。
    cand = [_strip_trailing_commas(t)]
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        cand.append(_strip_trailing_commas(t[i:j + 1]))
    # 也试一下取数组本体（模型有时只给一个裸数组）
    ai, aj = t.find("["), t.rfind("]")
    if ai >= 0 and aj > ai:
        cand.append(_strip_trailing_commas(t[ai:aj + 1]))

    for c in cand:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj, False
        if isinstance(obj, list):          # 模型有时直接给数组，包一层
            return {"grid": obj}, False

    # 到这里说明 JSON 不完整 —— 大概率被输出上限截断了
    truncated = t.count("{") > t.count("}") or t.count("[") > t.count("]")
    return {}, truncated


def salvage_headers(text: str) -> list:
    """从原文里把 col_headers 数组抠出来（JSON 被截断时的兜底）。"""
    m = re.search(r'"col_headers"\s*:\s*\[([^\]]*)\]', text, re.S)
    if not m:
        return []
    return [str(x).strip().strip('"')
            for x in re.findall(r'"([^"]*)"', m.group(1)) if x.strip()]


def salvage_objects(text: str, require_key: str = "name") -> list:
    """从被截断的 JSON 文本里，把已经写完整的对象逐个抠出来。

    截断只发生在最后一两条上，前面那些**是完好的**，直接丢掉太可惜。
    这里用「按最外层大括号配对」扫描，能救回多少算多少。
    """
    i = text.find("[")
    if i < 0:
        return []

    out, depth, start, in_str, esc = [], 0, -1, False, False
    for k in range(i, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = k
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:k + 1])
                    if isinstance(obj, dict) and require_key in obj:
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return out


def weekday_from_header(text: str) -> int:
    """把表头文字（"周一"/"星期一"/"周二 (3/4)"）解析成 1..7；解析不出返回 0。"""
    if not text:
        return 0
    WD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
    m = re.search(r"周\s*([一二三四五六日天])|星期\s*([一二三四五六日天])", str(text))
    if m:
        return WD.get(m.group(1) or m.group(2), 0)
    return 0
