"""全局异常处理：把 FastAPI / Pydantic 的英文报错统一转成中文。

覆盖三类：
  1. 业务异常 HTTPException —— 已经是中文，原样返回
  2. 参数校验失败 422      —— Pydantic 默认给 "String should have at least 6 characters" 这类英文
  3. 未捕获的服务端异常    —— 默认返回 "Internal Server Error"，对用户毫无帮助
"""
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("campus-ai")

# ---------- 字段中文名 ----------
FIELD_NAMES = {
    "username": "用户名",
    "password": "密码",
    "nickname": "昵称",
    "day": "星期",
    "slot_order": "节次",
    "start": "开始时间",
    "end": "结束时间",
    "name": "课程名称",
    "teacher": "任课教师",
    "location": "上课地点",
    "building": "教学楼",
    "lng": "经度",
    "lat": "纬度",
    "courses": "课程列表",
    "message": "消息内容",
    "history": "对话历史",
    "answers": "作答内容",
    "goal": "学习目标",
    "days": "计划天数",
    "origin": "起点",
    "destination": "终点",
}

# ---------- 校验错误类型 → 中文模板 ----------
TYPE_MESSAGES = {
    "string_too_short": lambda f, ctx: f"{f}长度不足，至少需要 {ctx.get('min_length', '?')} 个字符",
    "string_too_long": lambda f, ctx: f"{f}过长，最多 {ctx.get('max_length', '?')} 个字符",
    "missing": lambda f, ctx: f"缺少必填项：{f}",
    "int_parsing": lambda f, ctx: f"{f}必须是整数",
    "float_parsing": lambda f, ctx: f"{f}必须是数字",
    "bool_parsing": lambda f, ctx: f"{f}必须是布尔值（true/false）",
    "greater_than_equal": lambda f, ctx: f"{f}不能小于 {ctx.get('ge', '?')}",
    "less_than_equal": lambda f, ctx: f"{f}不能大于 {ctx.get('le', '?')}",
    "greater_than": lambda f, ctx: f"{f}必须大于 {ctx.get('gt', '?')}",
    "less_than": lambda f, ctx: f"{f}必须小于 {ctx.get('lt', '?')}",
    "list_type": lambda f, ctx: f"{f}格式不正确，应为列表",
    "dict_type": lambda f, ctx: f"{f}格式不正确，应为对象",
    "string_type": lambda f, ctx: f"{f}必须是文本",
    "int_type": lambda f, ctx: f"{f}必须是整数",
    "float_type": lambda f, ctx: f"{f}必须是数字",
    "json_invalid": lambda f, ctx: "请求数据格式错误，不是合法的 JSON",
}

# ---------- 状态码 → 兜底中文 ----------
STATUS_TEXT = {
    400: "请求有误，请检查填写的内容",
    401: "登录状态已失效，请重新登录",
    403: "没有权限执行该操作",
    404: "请求的接口不存在，请检查地址是否正确",
    405: "该接口不支持这种请求方式",
    409: "数据冲突，请刷新后重试",
    422: "提交的内容不符合要求，请检查后重试",
    429: "操作过于频繁，请稍后再试",
    500: "服务器开小差了，请稍后重试",
    502: "服务暂时不可用，请稍后重试",
    503: "服务正在维护，请稍后重试",
}

# 这些英文提示是框架内部抛出的，统一映射成中文
DETAIL_TEXT = {
    "Not Found": "请求的接口不存在，请检查地址是否正确",
    "Method Not Allowed": "该接口不支持这种请求方式",
    "Internal Server Error": "服务器开小差了，请稍后重试",
    "Forbidden": "没有权限执行该操作",
    "Unauthorized": "请先登录后再操作",
}


def _field_label(loc: tuple) -> str:
    """把 ('body','password') 这种位置信息转成 '密码'。"""
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    if not parts:
        return "请求内容"
    key = parts[-1]
    # 列表下标如 courses.0.name → 取最后有意义的字段名
    label = FIELD_NAMES.get(key, key)
    if len(parts) > 1 and parts[-2].isdigit():
        return f"第 {int(parts[-2]) + 1} 项的{label}"
    return label


def _translate_validation(errors: list) -> str:
    """把一个或多个校验错误合并成一句中文提示。"""
    msgs = []
    for err in errors:
        field = _field_label(tuple(err.get("loc", ())))
        etype = err.get("type", "")
        ctx = err.get("ctx", {}) or {}
        maker = TYPE_MESSAGES.get(etype)
        if maker:
            msgs.append(maker(field, ctx))
        else:
            # 未知类型：给出字段名 + 原始提示，避免暴露纯英文
            msgs.append(f"{field}填写有误")
    # 去重后拼接，最多展示 3 条
    unique = list(dict.fromkeys(msgs))[:3]
    return "；".join(unique) if unique else "提交的内容不符合要求"


def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常处理统一注册到应用上。"""

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        """参数校验失败：Pydantic 的英文 → 中文。"""
        detail = _translate_validation(exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": detail, "code": "VALIDATION_ERROR"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException):
        """业务异常：detail 已是中文则原样用，否则按状态码给中文兜底。"""
        detail = exc.detail
        if not isinstance(detail, str) or not detail.strip():
            detail = STATUS_TEXT.get(exc.status_code, "请求处理失败")
        else:
            # 框架内部的英文提示（Not Found / Method Not Allowed 等）统一换掉
            detail = DETAIL_TEXT.get(detail.strip(), detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, exc: Exception):
        """未捕获异常：给用户中文提示，同时把真实原因打到服务端日志便于排查。"""
        logger.exception("接口 %s %s 发生未处理异常", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": STATUS_TEXT[500], "code": "INTERNAL_ERROR"},
        )
