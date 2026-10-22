"""Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any


class UserCreate(BaseModel):
    """注册：只需要用户名 + 密码；昵称由后端按用户名生成（页面仍保留昵称展示位）。

    两次密码是否一致由前端校验，后端不做无意义的中转字段。
    """
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    nickname: Optional[str] = ""


class UserLogin(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProfileUpdate(BaseModel):
    """改昵称：用户名不可改，昵称可改，且要与全站显示名（用户名 + 昵称）不重复。"""
    nickname: str = Field(..., min_length=2, max_length=32)


class PasswordChange(BaseModel):
    """改密码：必须验证旧密码。"""
    old_password: str = Field(..., min_length=1, max_length=64)
    new_password: str = Field(..., min_length=6, max_length=64)


class AccountDelete(BaseModel):
    """注销账号：必须输入密码确认，删除后不可恢复。"""
    password: str = Field(..., min_length=1, max_length=64)


# ---- 扫码登录（演示级，实现见 services/scan_login.py）----

class ScanCreateOut(BaseModel):
    """申请二维码票据的返回。ticket 不含任何用户信息，可安全显示在二维码里。"""
    ticket: str
    expires_in: int                   # 有效秒数


class ScanStatusOut(BaseModel):
    """二维码状态：pending 待扫 / scanned 已扫待确认 / confirmed 已确认 / expired 已过期。"""
    status: str
    expires_in: int = 0
    scanned: bool = False             # 手机是否已经扫到（前端据此换文案）


class ScanConfirmIn(BaseModel):
    """手机端确认：带上二维码里的票据。"""
    ticket: str = Field(..., min_length=1, max_length=128)


class CourseIn(BaseModel):
    """一条课程或活动。date 为真实日期（按日期区分），星期几由前端推导。

    ## 为什么经纬度要用「宽松解析」而不是直接 `Optional[float]`

    前端保存课表时，会把**从后端读回来的行**原样提交（用户可能只改了一格）。
    而从 SQLite 读出来的 `lng/lat` 是**字符串**（如 `"119.447887"`），
    教学楼被清空或选了没收录的楼时更会变成**空串 `""`**。

    这种 `""` 用 `Optional[float]` 直接解析会失败：
        float("") -> ValueError -> Pydantic 报 422
    而课表保存是**一批一起提交**的，只要有一条脏数据，
    **整批都存不进去** —— 用户看到的是「保存失败 / 后端连不上」，
    实际只是某一格的教学楼没填。这个坑很隐蔽，所以在入口处就抹平掉。

    解析规则：能转数字就转；空串 / None / "null" / 非法值一律当 None（表示"没有坐标"）。
    坐标本来就是可选的（查不到楼就不给坐标，导航时才按楼名查表），
    所以这里**丢掉一个坏坐标比让整批保存失败要好得多**。
    """
    date: str = Field(..., description="YYYY-MM-DD")
    type: str = "course"              # course=课程 / activity=活动
    slot_order: Optional[int] = 0     # 对应 time_slots.order_no；活动可为 0
    start: str = ""                   # "08:10"
    end: str = ""                     # "08:55"
    name: str
    teacher: str = ""
    location: str = ""                # 教室，如 "博文楼-301"
    building: str = ""                # 教学楼，如 "博文楼"
    # 上课周次原始串（如 "2-3,6-8周(全部)"）。**空 = 每周都上**。
    # 与 ImportedCourse.weeks 同名同义，前端导入/保存时原样来回带。
    weeks: str = ""
    lng: Optional[float] = None       # 教学楼经度（由前端按教学楼自动带出）
    lat: Optional[float] = None       # 教学楼纬度

    @field_validator("lng", "lat", mode="before")
    @classmethod
    def _tolerant_coord(cls, v):
        """把 ""/None/"null"/非法值统一归成 None，能转数字就转。"""
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none", "undefined", "nan"):
                return None
            try:
                return float(s)
            except ValueError:
                return None          # 脏数据 -> 当没有坐标，别拖垮整批保存
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @field_validator("slot_order", mode="before")
    @classmethod
    def _tolerant_slot(cls, v):
        """slot_order 同理：空串 / 非法值归成 0（活动就是 0）。"""
        if v is None:
            return 0
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none", "undefined"):
                return 0
            try:
                return int(float(s))
            except ValueError:
                return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0


class TimetableUpdate(BaseModel):
    courses: List[CourseIn]


class ImportImageIn(BaseModel):
    """课表截图导入：前端把图片转成 dataURL 或纯 base64 传过来。

    image 允许带 `data:image/png;base64,` 前缀，后端会自动剥掉；
    参考周一是给模型做「周一=哪天」的锚点，让返回的 date 直接可用。
    """
    image: str = Field(..., description="base64 或 dataURL")
    monday: str = ""                  # 当前浏览周的周一 YYYY-MM-DD（可空）


class ImportedCourse(BaseModel):
    """识别出来的一条课程。uncertain 列出这行里没看清/对不上的字段名。"""
    date: str = ""
    weekday: Optional[int] = None     # 1=周一 ... 7=周日（模型给出，用于日期换算）
    slot_order: Optional[int] = None  # 第几节（从 1 数）
    start: str = ""
    end: str = ""
    name: str = ""
    teacher: str = ""
    building: str = ""
    location: str = ""
    # 上课周次原始串（教务表格里那一列，如 "1周(全部)" / "2-3,6-8周(全部)"）。
    # 后端**只透传不解析** —— 「第几周」的判断依赖前端当前浏览的是哪一周，
    # 后端解析了也用不上，反而多一层可能出错的地方。
    # 图片识别那条路不会给这个字段（模型读不出周次），默认空 = 每周都上。
    weeks: str = ""
    # 这门课**横跨几个小节**（连堂）。
    #
    # 为什么需要这个字段：教务表格的节次列写法有两种模板 ——
    #   ① 每行一个小节（数字 1..12 连续，每个数字都可能出现课）
    #      -> span = 1
    #   ② 「一大节两行」（课名行 + 教室行，课程单元格上下合并）
    #      -> 节次列的 1、2 其实是同一大节的两个小节，课实际占 2 格，span = 2
    #
    #   模板 ② 如果按 1 导入，课会落到第一、三、五…节上，用户看到的是
    #   「每格只有一节、隔一格才有课」，与课表上「连堂两节」的观感不符。
    #   解析器（services/timetable_excel.py）已经判好并透传到这里。
    slot_span: int = 1
    uncertain: List[str] = []


class ImportImageOut(BaseModel):
    ok: bool = True
    courses: List[ImportedCourse] = []
    warnings: List[str] = []          # 整体性问题（如「有 3 处课程名没认出来」）
    slots: List[int] = []             # 图片里出现的节次数（前端据此对号入座）
    model: str = ""                   # 实际命中的视觉模型，便于排查
    raw: str = ""                     # 模型原始输出，识别错时可对照


class ImportSheetIn(BaseModel):
    """课表表格导入：前端把 .xls / .xlsx 转成 base64 传过来。

    为什么不直接收 multipart 文件：
        前端是零构建环境，多传一个文件字段就要改 api() 封装；
        而课表表格本身很小（实测 32KB），base64 后也才 43KB，
        走和其它接口一致的 JSON 通道最省事。

    name 只用来判断扩展名（.xls 用 xlrd、.xlsx 用 openpyxl），
    monday 是当前浏览周的周一，用于把「星期几」换算成具体日期。
    """
    data: str = Field(..., description="表格文件的 base64（可带 dataURL 前缀）")
    name: str = ""                    # 原始文件名，用于判断 xls / xlsx
    monday: str = ""


class ImportSheetOut(BaseModel):
    """表格解析结果。字段与 ImportImageOut 对齐，前端可复用同一套核对流程。"""
    ok: bool = True
    courses: List[ImportedCourse] = []
    warnings: List[str] = []          # 整体性问题（多 sheet、缺地点等）
    sheet: str = ""                   # 实际解析的工作表名
    slots: List[int] = []             # 表格里出现的节次数


class TimetableImportIn(BaseModel):
    """确认导入：前端把（用户已补全的）识别结果落库。"""
    courses: List[CourseIn]


class InheritRequest(BaseModel):
    """「沿用上周」：把 source 区间（上周一~上周日）的课程+活动复制到 target 区间。"""
    source_start: str                 # 上周一 YYYY-MM-DD
    source_end: str                   # 上周日 YYYY-MM-DD
    target_start: str                 # 目标周一 YYYY-MM-DD
    target_end: str                   # 目标周日 YYYY-MM-DD


class TimeSlotOut(BaseModel):
    order_no: int
    label: str
    start: str
    end: str
    period_type: str
    region: str = "both"
    start_south: str = ""             # 南区开始（空=同 start）
    end_south: str = ""
    start_north: str = ""             # 北区开始（空=同 start）
    end_north: str = ""


class PlanCourse(BaseModel):
    """前端传来的单条课表记录（只带生成计划需要的字段）。"""
    date: str = ""
    type: str = "course"
    start: str = ""
    end: str = ""
    name: str = ""
    building: str = ""
    location: str = ""
    # ★ 2026-10-18：上课周次的**原始串**（"2-3,6-8周(全部)"，空 = 每周都上）。
    #   必须传上来：课表里的课是「每周重复」的 —— 库里一条 date=9/8 的课，
    #   weeks 说明的那几周**每周二都要上**。后端算空档时如果只按 date 相等判断，
    #   就会认为「9/8 之后全天空闲」，于是把上课时间当自习时间排进计划
    #   （实测输出过「14:50-15:35 预习计算机组成原理」，那正是上课时段）。
    weeks: str = ""


class PlanRequest(BaseModel):
    goal: str = ""
    days: int = 7
    # 学生画像：让计划贴合「大学生」而不是泛泛而谈
    major: str = ""          # 专业
    grade: str = ""          # 年级（大一/大二…）
    # ⚠️ 关键字段：前端**主动把课表传上来**。
    #    早期这里是空的，后端只能去数据库读 Course 表 —— 结果是
    #    「用户刚导入/刚编辑、还没点保存」或「只想临时生成一次」时，
    #    数据库里没数据，AI 看到的是空课表，于是回答「你还没有添加任何课程」。
    #    现在改为「前端传什么就用什么」，数据库只作为兜底（前端没传时才读）。
    courses: List[PlanCourse] = []
    # ★ 2026-10-15：AI伴学页改成**对话式**，于是有了"上一轮说了什么"。
    #   形如 `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`，
    #   由前端把**当前这条之前**的消息发上来（当前这条在 goal 里，别重复发）。
    #   ⚠️ 后端只用其中的 **user 原话**（见 routers/planner.py 的说明）：
    #      计划正文动辄两三千字，把上一轮的 assistant 全文回灌既贵又没用。
    history: List[dict] = []


class AgentStep(BaseModel):
    """Agent 的一次工具调用（用于在前端展示「它到底干了什么」）。

    Agent 和「黑盒问答」最直观的区别就在这里：调用过程对用户可见 ——
    「先查了你的课表 → 又算了这 7 天的空档 → 顺手看了下天气 → 才动笔」。
    """
    tool: str                       # 工具名，如 find_free_slots
    args: dict = {}                 # 模型传进来的参数
    ok: bool = True                 # 是否执行成功
    summary: str = ""               # 一句话结果摘要（给人看，不是原始 JSON）


class PlanResponse(BaseModel):
    plan: str
    # ⚠️ 默认空列表：AI 不可用、降级到本地规则计划时这里就是空的，
    #    前端据此决定要不要渲染「Agent 调用记录」那一段（空的不该显示一个空框）。
    trace: List[AgentStep] = []
    # ★ 2026-10-21：本次对话的 token 用量与折算成本（见 services/usage.py）。
    #   默认 None —— 降级路径（没调模型）就是没有，前端不该显示「消耗 0 token」。
    #   前端不读它也不影响任何东西，纯附加信息；评测脚本靠它拿到真实成本。
    usage: Optional[Dict[str, Any]] = None


class ChatIn(BaseModel):
    message: str
    history: List[dict] = []


class ChatOut(BaseModel):
    reply: str


class AssessIn(BaseModel):
    answers: List[int] = []          # 各题分值


class AssessOut(BaseModel):
    result: dict


class NavIn(BaseModel):
    origin: str                      # "lng,lat"
    destination: str                 # "lng,lat"

class FeedbackIn(BaseModel):
    kind: str = "suggest"        # suggest / bug / ux / other（未知值回落成 other）
    content: str = ""
    contact: str = ""            # 可选
    page: str = ""               # 页面标识（account / emotion / plan …），不传完整 URL


class FeedbackOut(BaseModel):
    id: int
    kind: str = "other"
    kindLabel: str = ""
    content: str = ""
    contact: str = ""
    page: str = ""
    createdAt: str = ""          # ISO8601（UTC，带 Z）
    username: str = ""           # 仅管理员列表里有值
    # 管理员回复：空串 = 还没回复。前端据此决定是否显示「已回复」标记 ——
    # 不额外传 status，避免"有回复但状态还是待处理"这种对不上的情况。
    reply: str = ""
    repliedAt: str = ""


class FeedbackReplyIn(BaseModel):
    """管理员回复。允许空串 = **撤回回复**（改回未回复状态）。"""
    reply: str = ""
