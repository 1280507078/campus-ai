"""课表 Excel（.xls / .xlsx）解析。

为什么单独一个模块、而不是塞进 routers/planner.py：
    解析逻辑本身有一百多行「看表格长相做判断」的代码，和路由的职责混在一起
    会让 planner.py 更难读；而且这部分**纯函数、不碰数据库**，单独放一个文件
    可以直接用命令行喂文件调试，不用起服务。

支持的后端（**按可用性自动降级，缺哪个都不影响这个功能**）：
    .xls   -> xlrd       （老版 BIFF，教务系统导出的基本都是这种）
    .xlsx  -> openpyxl   （新版 OOXML）
    pandas -> 可选。装了就用它选引擎、拿 DataFrame；没装就直接调上面两个库。

> **为什么刻意做成「不依赖 pandas」**（2026-09-26 的真实事故）：
>   原来这里第一行就是 `import pandas as pd`，而 requirements.txt 又漏写了
>   pandas 本身（只写了它的两个引擎 xlrd / openpyxl），启动脚本的依赖探测
>   同样没含 pandas —— 三处互相掩护，导致用户机器上永远装不上 pandas，
>   一导入表格就 ModuleNotFoundError，被兜底文案说成「文件损坏」。
>   最要命的是**用户重装多少次都没用**：探测语句没通过就不会触发安装。
>
>   治本的办法不是「把 pandas 加进依赖清单」（那只是修了这一处），
>   而是**让这个功能不依赖它**：pandas 在这里的唯一用途就是「打开工作簿、
>   遍历单元格」，而 xlrd / openpyxl 本来就能干这件事，且它们才是真正的
>   解析引擎。pandas 只是包装。
>
>   所以现在：pandas 在就用（少写点代码），不在就自己读 —— 两条路产出
>   完全一样的二维表，下游解析逻辑一行都不用改。
>
> ⚠️ 只解析**第一个 sheet**。原因见 _pick_sheet 的注释。
"""
from __future__ import annotations

import re
from typing import Optional

# 课程名后面跟的周次备注，形如：
#   "14-15周(全部)"  "2-3,6-8周(全部)"  "1,3,10周(全部)"  "5周(全部)"
# 课名与周次之间用换行分隔（单元格内换行）。
_WEEK_NOTE_RE = re.compile(r"^\d[\d,\-，－]*周")

# 「[实践学时]」「[理论学时]」这类后缀，同一门课的不同学时类型
# 在课表里算两条独立记录，**要保留**（上课时间/地点确实不同），
# 只是在识别「是不是同一门课」时忽略。
_TAG_RE = re.compile(r"[\[【(（](实践|理论|实验)学时[\]】)）]")

# 表头里「上午/下午/晚上」出现的位置
_PERIOD_WORDS = ("上午", "下午", "晚上")
# 星期表头：一 二 三 四 五 六 日
_WEEKDAY_HEADERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}

# 星期表头的各种写法。各校模板不统一，实测/常见的有：
#     "一"           阳光学院这份就是（最简）
#     "星期一"       最常见的全称
#     "周一"         最口语的简写
#     "周1" "星期1"  数字写法（有些教务系统导出就是数字）
#     "一\n周一"     单元格里换行塞了两行
#     "星期一(9/7)"  还带日期
#
# ⚠️ 为什么要单独做这个：以前只认 `v in _WEEKDAY_HEADERS`（要求整格**正好**
#    是一个「一」字）。只要模板写「星期一」，整张表的表头行就找不到，
#    用户拿到的是「没在这张表里找到「星期一~日」的课表格子」—— 提示里写着
#    「星期一」，而他表格里明明就是「星期一」，看了只会更懵。必须修。
_WEEKDAY_PREFIX_RE = re.compile(r"^(?:星期|周|礼拜)\s*")
_WEEKDAY_DIGIT_RE = re.compile(r"^(?:星期|周|礼拜)?\s*([1-7])\s*(?:$|[\s(（])")


def parse_weekday(value) -> int | None:
    """把星期表头的一格解析成 1..7；不是星期表头就返回 None。

    宽容度对齐 parse_period()：整格**包含**星期信息即可，不要求正好一个字。
    解析不出返回 None —— 绝不抛异常，一格脏值不能毁掉整张表。
    """
    if value is None:
        return None
    t = str(value).strip()
    if not t:
        return None

    # 多行单元格（"一\n周一"）逐行试，取第一个能认出来的
    for line in [p.strip() for p in re.split(r"[\r\n]+", t) if p.strip()] or [t]:
        # 去掉「星期/周/礼拜」前缀 + 可能的括号备注（"星期一(9/7)"）
        core = _WEEKDAY_PREFIX_RE.sub("", line)
        core = re.split(r"[(（\s]", core)[0].strip()
        if core in _WEEKDAY_HEADERS:
            return _WEEKDAY_HEADERS[core]
        m = _WEEKDAY_DIGIT_RE.match(line)
        if m:
            n = int(m.group(1))
            return 7 if n == 0 else n

    # 兜底：整格正好是一个数字 1..7（有些系统表头就是 1 2 3 4 5）
    if t.isdigit() and 1 <= int(t) <= 7:
        return int(t)
    return None

# 「第几节」这一格的各种写法。各学校教务模板并不统一，实测/常见的有：
#     "1"         纯数字（阳光学院这份就是）
#     "第1节"     带「第」「节」二字
#     "1-2"  "1~2"  "1—2"   连排写到一格
#     "1,2"  "1、2"          同上，用顿号/逗号
#     "1-2节"                 连排 + 节字
#     "一"  "二"              极少数模板用汉字
#     "上午1"                 带分段前缀
# 解析不出来时**返回 None**，由调用方决定怎么兜底（绝不能抛异常，
# 否则一个格子写错就让整张表导入失败）。
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def parse_period(value) -> int | None:
    """把「第几节」这一格解析成 1..12 的整数；解析不出返回 None。

    ⚠️ 这是**唯一**一处「节次文字 -> 数字」的解析入口。
       以前路由层直接写 `int(period)`，只要格子不是纯数字就抛 ValueError，
       被最外层的兜底 except 抓住，用户看到的是「这个表格读不了，可能是文件
       损坏或格式太特殊」—— 明明是「第1节」这种一眼能懂的内容，却被告知文件坏了，
       完全没法自查。所以解析必须宽容，失败就交给上层标黄。

    取连排的首节（"3-4" -> 3）：课表一个格子放不下两节，落到首节让用户核对时
    自己调整，比整条丢掉好。
    """
    if value is None:
        return None
    # 数字直接返回（Excel 单元格可能是 float，如 3.0）
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = int(value)
        return n if 1 <= n <= 12 else None

    s = str(value).strip()
    if not s:
        return None

    # 抹掉「第」「节」「上午/下午/晚上」这些修饰，只留数字
    s = re.sub(r"[第节\s]", "", s)
    s = re.sub(r"^(上午|下午|晚上|中午)", "", s)

    # 汉字数字（整格就是「三」这种）
    if s in _CN_NUM:
        return _CN_NUM[s]

    # 取开头第一个 1~2 位数字：'3-4' / '3,4' / '3、4' / '3节' 都能命中
    m = re.search(r"\d{1,2}", s)
    if not m:
        return None
    n = int(m.group(0))
    return n if 1 <= n <= 12 else None


def looks_like_period(value) -> bool:
    """这一格是否「看起来是个节次」（用于判定节次列 / 课名行）。"""
    return parse_period(value) is not None


class _Iat:
    """替身：让 `grid.iat[r, c]` 这种**下标语法**能跑。

    ⚠️ 这里踩过一次坑：pandas 的 `.iat` 是个支持 `[]` 的**属性**
    （`df.iat[r, c]`），不是方法（不是 `df.iat(r, c)`）。
    我一开始写成了方法，结果下游几十处 `df.iat[r, c]` 全部报
    `TypeError: 'method' object is not subscriptable`。
    所以这里必须实现 `__getitem__`，把 (r, c) 元组接住。
    """

    __slots__ = ("_rows",)

    def __init__(self, rows: list[list[str]]):
        self._rows = rows

    def __getitem__(self, key):
        r, c = key
        if r < 0 or r >= len(self._rows):
            return ""
        row = self._rows[r]
        if c < 0 or c >= len(row):
            return ""
        v = row[c]
        return "" if v is None else v


class _Grid:
    """极薄的二维表包装，只提供下游用到的两个能力：`.shape` 与 `.iat[r, c]`。

    为什么要造这个东西：整个解析逻辑（_extract_grid / _read_cells 等上百行）
    都在用 `df.shape` 和 `df.iat[r, c]` 读格子。只要有这两个，pandas 的
    DataFrame 和我们自己读出来的二维 list 就是可以互换的 ——
    于是「没有 pandas」时，下面的代码**一行都不用改**。

    刻意不实现更多方法：接口越窄，两种后端行为不一致的风险越小。
    """

    __slots__ = ("_rows", "iat")

    def __init__(self, rows: list[list[str]]):
        self._rows = rows
        self.iat = _Iat(rows)

    @property
    def shape(self) -> tuple[int, int]:
        n = len(self._rows)
        m = max((len(r) for r in self._rows), default=0)
        return n, m


class _Book:
    """自带的「工作簿」：只有 sheet_names 和 parse(name)，够 _pick_sheet 用。

    用 xlrd（.xls）或 openpyxl（.xlsx）把**所有工作表**读成二维字符串表，
    再按需返回。不做任何智能处理 —— 所有「哪张表是课表」的判断留在
    _pick_sheet 里，两条后端共享同一套逻辑，行为一致。
    """

    def __init__(self, sheets: dict[str, _Grid]):
        self._sheets = sheets
        self.sheet_names = list(sheets.keys())

    def parse(self, name: str, header=None, dtype=None):    # noqa: ARG002
        # header/dtype 是为了跟 pandas 的签名对齐，这里不需要：
        # 自带的 _Grid 永远不给表头、永远给字符串。
        return self._sheets[name]


def _open_workbook(data: bytes, warnings: list[str]):
    """打开 Excel，返回一个「有 sheet_names / parse()」的对象。

    优先 pandas（它最懂文件类型与引擎搭配）；没有 pandas 就自己来，
    按文件魔数判断该用 xlrd 还是 openpyxl。

    两条路都失败时才抛 ValueError（文案是给用户看的，不是给开发者看的）。
    """
    import io

    # ── 路线 1：有 pandas 就用它 ──
    try:
        import pandas as pd
        return pd.ExcelFile(io.BytesIO(data))
    except ImportError:
        pass                       # 没装 pandas，走下面的自带实现
    except Exception as exc:       # noqa: BLE001
        # pandas 在，但打不开这个文件（损坏 / 根本不是 Excel）。
        # pandas 的报错里如果提到引擎，说明是引擎缺失，文案要指到点子上。
        msg = str(exc)
        if "xlrd" in msg or "openpyxl" in msg or "Missing optional dependency" in msg:
            warnings.append("服务器缺少 Excel 解析引擎（xlrd / openpyxl）。")
            raise ValueError(
                "服务器缺少 Excel 解析组件，暂时读不了表格。"
                "可以先把表格另存为 CSV 再导入（文件 → 另存为 → CSV UTF-8）。"
            ) from exc
        raise ValueError("这个文件打不开，可能不是有效的 Excel 文件，或者已经损坏。") from exc

    # ── 路线 2：没有 pandas，自己读 ──
    #
    # 按文件头判断格式，比看扩展名可靠：用户可能把 .xls 改名成 .xlsx。
    #   .xls  (OLE2 复合文档) 头：D0 CF 11 E0 A1 B1 1A E1
    #   .xlsx / .xlsm (ZIP)   头：50 4B 03 04  ("PK\x03\x04")
    is_zip = data[:4] == b"PK\x03\x04"
    is_ole2 = data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    if is_ole2:
        return _read_with_xlrd(data)
    if is_zip:
        return _read_with_openpyxl(data)

    # 头都不认识：交给 pandas 之外的兜底判断，给用户一句人话。
    # （微信有时会把文件包成 HTML，或者用户把 txt 改名成 xls。）
    raise ValueError("这个文件打不开，可能不是有效的 Excel 文件，或者已经损坏。")


def _read_with_xlrd(data: bytes) -> "_Book":
    """用 xlrd 读老版 .xls（OLE2 / BIFF）—— 教务系统导出的基本都是这种。"""
    try:
        import xlrd
    except ImportError as exc:
        raise ValueError(
            "服务器缺少读取 .xls 的组件（xlrd），暂时读不了这张表。"
            "可以先把表格另存为 .xlsx 或 CSV 再导入"
            "（Excel 里：文件 → 另存为 → 选择格式）。"
        ) from exc

    sheets: dict[str, _Grid] = {}
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:                                  # noqa: BLE001
        raise ValueError("这个文件打不开，可能不是有效的 Excel 文件，或者已经损坏。") from exc

    for name in book.sheet_names():
        sh = book.sheet_by_name(name)
        rows: list[list[str]] = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                v = sh.cell_value(r, c)
                # xlrd 会把数字读成 float（如 1.0），日期读成 float。
                # 这里统一转成「像 Excel 里显示的那样」的干净字符串，
                # 否则 1.0 这种值会污染后面 parse_period / parse_weekday 的判断。
                row.append(_cell_to_text(v, sh.cell_type(r, c), book.datemode))
            rows.append(row)
        sheets[str(name)] = _Grid(rows)
    return _Book(sheets)


def _read_with_openpyxl(data: bytes) -> "_Book":
    """用 openpyxl 读新版 .xlsx / .xlsm。"""
    try:
        import openpyxl
    except ImportError as exc:
        raise ValueError(
            "服务器缺少读取 .xlsx 的组件（openpyxl），暂时读不了这张表。"
            "可以先把表格另存为 CSV 再导入（文件 → 另存为 → CSV UTF-8）。"
        ) from exc

    import io

    sheets: dict[str, _Grid] = {}
    try:
        # data_only=True：取**公式的计算结果**而不是公式本身。
        # 教务模板里偶尔有 =SUM() 这类公式格，读公式文本会得到 "=SUM(...)"
        # 这种没意义的字符串，干扰节次/星期的识别。
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:                                  # noqa: BLE001
        raise ValueError("这个文件打不开，可能不是有效的 Excel 文件，或者已经损坏。") from exc

    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append([_cell_to_text(v, None, None) for v in row])
            sheets[str(ws.title)] = _Grid(rows)
    finally:
        try:
            wb.close()          # read_only 模式必须关，否则文件句柄泄漏
        except Exception:       # noqa: BLE001
            pass
    return _Book(sheets)


def _cell_to_text(value, ctype=None, datemode=None) -> str:
    """把单元格值转成干净的字符串。

    ⚠️ 这里最容易出的事是「1.0」这种浮点尾巴。
    xlrd 把数字统一读成 float，于是表里的「第 1 节」会变成 "1.0"，
    下游 parse_period("1.0") 虽然能兜住（它只取第一个数字），
    但课名、教室号里混进 ".0" 就难看了（"306.0" 不是教室号）。
    所以整数型 float 一律去掉小数点；空值统一成空串。

    datemode 是 xlrd 的日期基准（1900/1904），只有真的拿到日期类型时才用得上；
    这里的表格是课表，基本不会有日期格，但传进来不会有害。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # 1.0 -> "1"；1.5 -> "1.5"（保留非整数）
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def parse_timetable_excel(data: bytes, filename: str = "", monday: str = "") -> dict:
    """把课表 Excel 解析成课程列表（不落库，交给前端核对）。

    返回 {"courses": [...], "warnings": [...], "sheet": "模版"}，
    其中 courses 的字段与 /planner/import-image 的 ImportedCourse 完全一致，
    所以前端可以走同一个 toLocalCourse() + openReview() 流程。
    """

    warnings: list[str] = []

    # ── 打开工作簿 ────────────────────────────────────────────────
    #
    # 这里有**两条路**，产出完全一样的 _Book（只是薄的二维表包装）：
    #
    #   1) 有 pandas  -> 让它选引擎（它最懂文件类型和引擎搭配）
    #   2) 没有 pandas -> 直接调 xlrd / openpyxl
    #
    # 为什么值得写两条：见模块头部的说明。简单说就是 —— 用户机器上
    # 少装了一个包，不该让整个「导入课表」功能废掉，更不该让用户
    # 反复重装而毫无效果。xlrd / openpyxl 才是真正的解析引擎，
    # pandas 在这里只是包装层，去掉它功能完全不受影响。
    book = _open_workbook(data, warnings)

    sheet_name, df = _pick_sheet(book, warnings)

    grid = _extract_grid(df, warnings)
    if not grid["columns"]:
        raise ValueError(
            "没在这张表里找到「星期一~日」的时间表格子。"
            "如果这是教务系统导出的原始表格，请确认第一个工作表就是时间表本身。"
        )

    courses = _read_cells(df, grid, warnings)
    if not courses:
        warnings.append("表格读通了，但所有格子里都没有课 —— 可能是空模板。")

    return {
        "courses": courses,
        "warnings": _dedupe(warnings),
        "sheet": sheet_name,
        "monday": monday or "",
    }


def _pick_sheet(xl, warnings: list[str]) -> tuple[str, "object"]:
    """选要解析的工作表。

    ⚠️ 只取第一个 sheet，不「把所有 sheet 合并」。

    实测踩到的坑：用户从教务系统导出的 .xls 里，第一个 sheet 是本学期的课表，
    后面往往还挂着上学期的、或者别的学院的残留表（同一份文件里见过
    「2026-2027-1 网络工程」和「2025-2 经济管理学院」两张完全无关的表）。
    如果无脑合并，两个学院的课会混进同一张课表，而且**不会报错** ——
    用户要到上课时才发现多了几门不属于自己的课。

    优先按「名字里带模版/课表」挑，挑不到就用第一个，并提示用户确认。

    `xl` 只要是「有 sheet_names、有 parse(name)」的对象即可 ——
    pandas 的 ExcelFile 和下面自带的 _Book 都满足这个约定。
    """
    names = list(xl.sheet_names or [])
    if not names:
        raise ValueError("这个 Excel 里没有任何工作表。")

    preferred = None
    for kw in ("模版", "模板", "课表", "课程表"):
        for n in names:
            if kw in str(n):
                preferred = n
                break
        if preferred:
            break
    picked = preferred or names[0]

    if len(names) > 1:
        others = [n for n in names if n != picked]
        warnings.append(
            f"这个表格有 {len(names)} 个工作表，我只读了「{picked}」，"
            f"其它（{('、'.join(str(o) for o in others[:3]))}）已跳过。"
            "如果时间表在别的工作表里，请在 Excel 里把那张表移到第一个再导入。"
        )
    return picked, _parse_sheet(xl, picked)


def _parse_sheet(xl, name: str):
    """把某张工作表读成「有 .shape / .iat 的二维表」。

    为什么要包一层：pandas 的 `ExcelFile.parse()` 返回 DataFrame，
    需要 `.fillna("")` 把空值变空串；而自带的 _Book 返回的 _Grid
    本来就保证是字符串。两条后端的收尾动作不同，统一在这里做，
    免得调用方还要关心「我用的是哪条后端」。
    """
    df = xl.parse(name, header=None, dtype=str)
    if hasattr(df, "fillna"):
        # pandas 路线：NaN -> ""，否则后面 str(nan) 会得到 "nan" 这种脏值
        return df.fillna("")
    return df


def _extract_grid(df, warnings: list[str]) -> dict:
    """定位课表网格：星期表头行 + 节次列 + 上午/下午/晚上 分段。

    真实表格长这样（阳光学院教务导出）：

        行2 | 课程名称 | 任课教师 | 学分 | 开课周次 |      |      | 一 二 三 四 五 六 日
        行3 | 计算机组成原理 | 郑骅 | 3 | 1-19 | 上午 |  1   | 课名... 教室...（每节次两行）
        行4 | 计算机操作系统 | ...  | 3 | 1-19 |      |  2   | （空，教室在下一行的「教室」行）

    ⚠️ 左边 A~D 列（课程名称/任课教师/学分/开课周次）是**与右侧网格并行的课程总表**，
       同一门课在两处都出现。解析时**只用右侧网格**，否则每门课都会重复一遍。
       判定「哪列开始是网格」的依据就是找到星期表头行，从那里取列。
    """
    rows = df.shape[0]
    cols = df.shape[1]

    # 1) 找星期表头行：一行里出现 ≥5 个「星期X」（一/二/…/日、周一、星期1 都认）
    header_row = None
    header_cols: dict[int, int] = {}
    for r in range(rows):
        found = {}
        for c in range(cols):
            w = parse_weekday(df.iat[r, c])
            if w is not None:
                found[c] = w
        # 去重后要求覆盖 ≥5 个不同的星期，避免「星期一/星期二」被数成同一列
        if len(found) >= 5 and len(set(found.values())) >= 5:
            header_row, header_cols = r, found
            break

    if header_row is None:
        return {"header_row": None, "columns": {}, "slot_col": None, "period_sep": {}}

    grid_start = min(header_cols)             # 星期表头最左边那一列（周一）
    slot_col = None
    # 从星期表头往左找节次列，但别一路找到 A~D 列的课程总表里去。
    # ⚠️ 用「整列像节次」判定而不是「某个格子像节次」：
    #    A~D 列里有「学分」这种恰好是 1 位数字的格（如 3、2），
    #    只抽 5 行做「存在任一格像节次」的判断，很容易把学分列误认成节次列。
    #    改成「这一列的绝大多数非空格子都像节次」才认，误判几乎消除。
    #
    # ⚠️ 但「多数票」在**只有一行课**的小表上会挂：整列只有 1 个非空格，
    #    max(2, ...) 要求至少 2 票，永远达不到 -> slot_col=None -> 一门课都读不出。
    #    所以下面补一个「单行也认」的兜底（见 else if hit == len(non_empty) == 1）。
    best = None
    for c in range(grid_start - 1, -1, -1):
        vals = [str(df.iat[r, c]).strip()
                for r in range(header_row + 1, rows)]
        non_empty = [v for v in vals if v and v != "教室"]
        if not non_empty:
            continue
        hit = sum(1 for v in non_empty if looks_like_period(v))
        # 严格多数票：表里有多个节次行时走这条，稳
        if hit >= max(2, int(len(non_empty) * 0.6)):
            slot_col = c
            break
        # 兜底：这一列**全部**非空格都像节次，且只有 1 个（小表/单行课表）。
        # 条件是「100% 命中」而不是「50%」，所以不会把学分列（3、2）误认进来。
        if hit == len(non_empty) == 1:
            best = best if best is not None else c
    if slot_col is None and best is not None:
        slot_col = best

    # 2) 找「上午/下午/晚上」所在的列（用来判断节次属于哪一段）
    period_col = None
    for c in range(grid_start):
        for r in range(header_row + 1, rows):
            if str(df.iat[r, c]).strip() in _PERIOD_WORDS:
                period_col = c
                break
        if period_col is not None:
            break

    period_sep: dict[int, str] = {}
    if period_col is not None:
        for r in range(header_row + 1, rows):
            w = str(df.iat[r, period_col]).strip()
            if w in _PERIOD_WORDS:
                period_sep[r] = w

    # 3) 判断「节次列的数字是大节还是小节」—— 决定一节课要占 1 格还是 2 格。
    #
    # 背景（真实踩过一次）：
    #   阳光学院教务导出的表格里，**一节课的格子是上下合并的**，节次列写
    #   `1,2,3,...,12`，但课程只出现在奇数行：
    #
    #       行3 | 1   | 数据库原理与应用 / 14-15周   <- 这门课实际占第 1-2 小节
    #       行4 | 2   | (空，是上一行合并下来的)
    #       行5 | 教室| 软件工程实验室二(启智楼306)
    #       行6 | 3   | 数据库原理与应用 / 1周       <- 占第 3-4 小节
    #       行7 | 4   | (空)
    #
    #   也就是说：节次列的 1、2 其实是**同一大节的两个小节**，课程单元格把
    #   它们合并了。而库里 TimeSlot 是**逐小节**的（第一节/第二节/第三节…），
    #   所以「表格第 1 行」对应的是库里的 2 个槽，不是 1 个。
    #
    #   如果按「1 行 = 1 小节」导入，课会落到第一、三、五、七节上，
    #   表现就是用户看到的「**每个格子只有一节、隔一格才有课**」——
    #   明明课表上是连堂两节，系统里却显示成一节。
    #
    # 判据（不用猜，看的是表格的**物理结构**）：
    #   有「教室」行 => 这是「一大节两行（课名行 + 教室行）」的模板，
    #   节次列的数字就是**大节序号**（1,2,3... 每个大节含 2 个小节）。
    #
    #   这个判据很稳：没有教室行的模板（那种节次直接是连续小节）本来就不会
    #   出现「教室」这个关键字，而它的节次列数字也不会成对出现。
    has_loc_row = any(
        str(df.iat[r, slot_col]).strip() == "教室"
        for r in range(header_row + 1, rows)
    ) if slot_col is not None else False

    return {
        "header_row": header_row,
        "columns": header_cols,
        "slot_col": slot_col,
        "period_sep": period_sep,
        "big_period": has_loc_row,
    }


def _read_cells(df, grid: dict, warnings: list[str]) -> list[dict]:
    """按「节次两行一组」读网格，产出课程列表。

    ⚠️ 真实表格的节次是**上下两行一组**，但顺序和直觉相反 ——
       是「课名行在前、地点行在后」，而且地点行专门用一格文字标出「教室」：

        行3  col5='1'      col6='数据库原理与应用 / 14-15周…'   ← 第1节 课名行
        行4  col5='2'      col6=''                              ← 第2节 没课
        行5  col5='教室'   col6='软件工程实验室二(启智楼306) …'  ← 地点行（属于第1节）

       也就是说：**地点行跟在整组课名行之后**，靠 col5 那一格的「教室」二字定位，
       它描述的是**上面那一节**（或上面那几节共享）的地点，不是下面那一节。
       所以读到「教室」行时，要把它贴给**紧邻的上一个节次**，不能往后贴。

       换行分隔的多门课与地点一一对应（第 i 门课配第 i 个地点）。
    """
    header_row = grid["header_row"]
    columns = grid["columns"]
    slot_col = grid["slot_col"]
    if slot_col is None:
        # ---------------------------------------------------------------
        # 兜底：整张表都认不出「第几节」，但格子里的课是真的。
        # ---------------------------------------------------------------
        # 触发场景：节次列写的是「上午」「一」这类 parse_period 认不出的文字，
        # 或者那列干脆是空的（模板只画了网格，节次在别处）。
        #
        # 以前这里直接 `return []` + 一句「已跳过」，结果是：**用户明明看到
        # 表里有课，导入后一门都没有**，提示还只说「已跳过」，完全不知道
        # 下一步该干什么 —— 比报错更让人摸不着头脑。
        #
        # 现在的处理：把「课名行」的判据放宽成「这一行在网格里有非空课名」，
        # 节次统一挂 1（也就是当天第一节课），并**明确告诉用户有几门课
        # 需要手动改节次**。宁可导进来让用户改，也不要静默丢掉。
        return _read_cells_no_period(df, grid, header_row, columns, warnings)

    return _read_cells_from(df, grid, slot_col, header_row, columns, warnings)


def _read_cells_no_period(df, grid: dict, header_row: int, columns: dict,
                          warnings: list[str]) -> list[dict]:
    """认不出节次时的兜底读取：按「行里有课名」收集，节次一律记 1。

    ⚠️ 只在**确实没有**更靠谱的节次判据时才走这里。所有课都会挂到当天第一节，
       所以必须配一句显眼的提示让用户去核对表里改，绝不能默默这么干。
    """
    skip_rows = {"教室", "上午", "下午", "晚上"}
    courses: list[dict] = []
    # 「教室」行本身不是课，它给的是**上一行**的教室。先把它认出来并跳过，
    # 同时把地点记下来给上面那条课名行用 —— 不然教室会被当成课名导进去。
    loc_rows: dict[int, dict[int, str]] = {}
    for r in range(header_row + 1, df.shape[0]):
        if "教室" in {str(df.iat[r, c]).strip() for c in range(df.shape[1])}:
            loc_rows[r] = {c: str(df.iat[r, c]).strip() for c in columns}

    for r in range(header_row + 1, df.shape[0]):
        if r in loc_rows:
            continue
        row_vals = [str(df.iat[r, c]).strip() for c in columns]
        if not any(v and v != "nan" for v in row_vals):
            continue
        # 跳过纯标记行（整行只有 上午/下午/晚上 这类分段词）
        non_mark = [v for v in row_vals if v and v != "nan" and v not in skip_rows]
        if not non_mark:
            continue
        # 这一行的教室：往下找最近的「教室」行（与主路径同一套行序约定）
        loc_cells: dict[int, str] = {}
        for rr in sorted(loc_rows):
            if rr > r:
                loc_cells = loc_rows[rr]
                break
        for c, weekday in columns.items():
            cell = str(df.iat[r, c]).strip()
            if not cell or cell == "nan" or cell in skip_rows:
                continue
            for item in _split_cell(cell, loc_cells.get(c, "")):
                item["weekday"] = weekday
                # 节次未知 -> 挂当天第一节（order_no 3），并交给上层标黄
                item["slot_order"] = 1
                item["period"] = ""
                courses.append(item)

    merged = _merge_same_course(courses, warnings)
    if merged:
        warnings.append(
            f"这张表的「第几节」这一列看不出来（写的不是 1/2/3 这种数字），"
            f"已先把 {len(merged)} 门课按「当天第一节」导入。"
            "导入后请在核对表里把节次改成实际的上课时间。"
        )
    return merged


def _read_cells_from(df, grid: dict, slot_col: int, header_row: int,
                     columns: dict, warnings: list[str]) -> list[dict]:
    """拿到确定的 (slot_col, header_row, columns) 后，真正扫网格。

    从 `_read_cells` 拆出来，唯一的目的是让「节次列没认出来」时还能换个
    判据**再试一次**（见 _read_cells 里的重试逻辑）—— 那条路径需要
    用另一套 slot_col/columns 重进一次本函数，而不是重写一遍扫描。
    """
    rows = df.shape[0]
    courses: list[dict] = []
    period_of_row: dict[int, str] = {}
    cur_period = ""
    for r in range(header_row + 1, rows):
        cur_period = grid["period_sep"].get(r, cur_period)
        period_of_row[r] = cur_period

    # 先把「课名行」和「教室行」的行号都摸出来。
    name_rows: list[tuple[int, int]] = []      # (行号, 节次)
    loc_rows: dict[int, dict[int, str]] = {}   # 行号 -> {列号: 地点文字}

    for r in range(header_row + 1, rows):
        v = str(df.iat[r, slot_col]).strip()
        if v == "教室":
            loc_rows[r] = {c: str(df.iat[r, c]).strip() for c in columns}
        else:
            n = parse_period(v)
            if n is not None:
                name_rows.append((r, n))

    # 逐个课名行绑定地点：往**下**找最近的一条「教室」行即可。
    # 因为「教室」行必定属于它上面那组课名（见上面的行序示例），
    # 而下一个节次的课名行若没课就是个空格子，不会成为 name_rows 的一员。
    for r, period in name_rows:
        loc_cells: dict[int, str] = {}
        for rr in sorted(loc_rows):
            if rr > r:
                loc_cells = loc_rows[rr]
                break
        for c, weekday in columns.items():
            cell = str(df.iat[r, c]).strip()
            if not cell:
                continue
            loc_text = loc_cells.get(c, "")
            for item in _split_cell(cell, loc_text):
                item["weekday"] = weekday
                item["slot_order"] = period
                item["period"] = period_of_row.get(r, "")
                # 见 _extract_grid 里 big_period 的说明：这份表是「一大节两行」，
                # 节次列的数字是大节序号，一门课实际横跨 2 个小节。
                item["slot_span"] = 2 if grid.get("big_period") else 1
                courses.append(item)

    return _merge_same_course(courses, warnings)


def _split_cell(cell: str, loc_text: str) -> list[dict]:
    """拆开一个格子：可能含多门课，地点与课名按出现顺序一一对应。

    格子内容示例（多门课、课名后跟周次备注）：
        数据库原理与应用
        14-15周(全部)
        计算机网络
        2-3,6-8周(全部)
        计算机组成原理[实践学时]
        6,8,10周(全部)

    地点格：
        软件工程实验室二(启智楼306) 知行楼B（原工B）-302 基础实验室1(启智楼101) 线上

    ⚠️ 课名与周次备注是**两行**（课名行 + 周次行），不是同一行。
       所以按行遍历，遇到「以数字开头且含周」的行就把它当成上一条的备注。
    """
    lines = [ln.strip() for ln in str(cell).split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return []

    # 地点：地点格是**空格分隔**的一串，但地点名内部也可能有空格
    # （如「知行楼B（原工B）-302」没空格，而「软件工程实验室二(启智楼306)」也没有）。
    # 实测这份表格地点之间用单个空格分隔、地点内部无空格，直接 split 即可。
    locations = [x for x in re.split(r"\s+", loc_text) if x] if loc_text else []

    items: list[dict] = []
    pending: Optional[dict] = None
    for ln in lines:
        if _WEEK_NOTE_RE.match(ln):
            # 周次备注行 -> 补到上一条
            if pending is not None:
                pending["weeks"] = (pending.get("weeks", "") + " " + ln).strip()
            continue
        if pending is not None:
            items.append(pending)
        pending = {"name": ln, "weeks": "", "uncertain": []}
    if pending is not None:
        items.append(pending)

    # 地点按顺序贴；数量不一致时（常见于多门课共享地点格）多出来的忽略，
    # 缺的留空 —— 不猜，让用户在核对弹窗里补。
    for i, it in enumerate(items):
        it["location"] = locations[i] if i < len(locations) else ""
        it["teacher"] = ""
        it["building"] = ""
    return items


def _merge_same_course(courses: list[dict], warnings: list[str]) -> list[dict]:
    """同一格里的同一门课合并成一条（周次备注并起来）。

    为什么需要：一门课在一学期里可能「前 9 周周一上、后 9 周换教室」，
    教务导出的格子里就会把课名写两遍。对用户来说这是**一门课**，
    在核对弹窗里出现两条一模一样的行只会让人困惑。

    ⚠️ 但 **不同星期 / 不同节次** 的绝不合并 —— 那是两次真实的上课。
       合并键 =（星期, 节次, 去标签后的课名）。
    """
    out: list[dict] = []
    index: dict[tuple, dict] = {}
    for it in courses:
        base = _TAG_RE.sub("", it["name"]).strip() or it["name"].strip()
        key = (it["weekday"], it["slot_order"], base)
        if key in index:
            prev = index[key]
            if it.get("weeks") and it["weeks"] not in prev.get("weeks", ""):
                prev["weeks"] = (prev.get("weeks", "") + " " + it["weeks"]).strip()
            if it.get("location") and not prev.get("location"):
                prev["location"] = it["location"]
            continue
        index[key] = it
        out.append(it)
    return out


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for x in items:
        k = str(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out
