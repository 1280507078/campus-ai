"""天气地图导航：天气提示 + 路径规划。

天气提示的生成策略（三级降级）：
  1. 有高德数据 + 有 GLM   -> 由 AI 生成自然、口语化的出行建议
  2. 有高德数据 + 无 GLM   -> 用规则拼出「温度/天气/带伞建议」，也够用
  3. 没有高德数据          -> 返回固定占位文案，页面不空白

路径规划同理：配置了高德 Key 就返回真实步行路线，否则返回空结构。
"""
import logging

from fastapi import APIRouter

from config import settings
from models.schemas import NavIn
from services.amap import (
    amap_weather,
    amap_walking_route,
    is_configured as amap_ready,
)
from services.glm import glm_chat, is_configured as glm_ready

logger = logging.getLogger(__name__)
router = APIRouter()

PLACEHOLDER_TIP = "暂时拿不到实时天气，出门前记得看一眼窗外，按需增减衣物。"

# 需要提醒带伞的天气现象关键字
RAIN_WORDS = ("雨", "雪", "雷", "阵雨", "暴雨", "大雨", "中雨", "小雨", "冻雨", "冰雹")


def _rule_based_tip(live: dict) -> str:
    """无 GLM 时的规则版提示：把实况数据拼成一句人话。"""
    city = live.get("city") or "本地"
    weather = live.get("weather") or "未知"
    temp = live.get("temperature") or "?"
    humidity = live.get("humidity") or ""
    wind = live.get("winddirection") or ""
    power = live.get("windpower") or ""

    parts = [f"{city}现在{weather}，气温 {temp}℃"]
    if wind or power:
        parts.append(f"刮{wind}{power}级风")
    parts.append(f"湿度 {humidity}%" if humidity else "")
    tip = "，".join(p for p in parts if p) + "。"

    if any(w in weather for w in RAIN_WORDS):
        tip += "正在下雨/雪，出门记得带伞，注意路面湿滑。"
    else:
        try:
            t = int(temp)
            if t >= 32:
                tip += "天气偏热，注意防晒补水。"
            elif t <= 5:
                tip += "气温很低，注意保暖，多穿一件。"
            else:
                tip += "气温适中，出门可酌情带把伞以防万一。"
        except (TypeError, ValueError):
            tip += "出门可酌情带把伞以防万一。"

    # 预报里有雨也提醒
    for c in (live.get("forecast") or [])[:1]:
        dw = c.get("dayweather") or ""
        nw = c.get("nightweather") or ""
        if any(w in dw + nw for w in RAIN_WORDS):
            tip += f" 今明两天有雨（白天{dw}、夜间{nw}），建议随身带伞。"
            break

    return tip


async def _ai_tip(live: dict) -> str:
    """有 GLM 时让 AI 说一句自然的出行建议；失败则回退到规则版。"""
    if not glm_ready():
        return ""
    summary = (
        f"城市：{live.get('city')}\n"
        f"实况：{live.get('weather')}，气温 {live.get('temperature')}℃，"
        f"湿度 {live.get('humidity')}%，{live.get('winddirection')}{live.get('windpower')}级风"
    )
    fc = live.get("forecast") or []
    if fc:
        c = fc[0]
        summary += (
            f"\n今日预报：白天{c.get('dayweather')} {c.get('daytemp')}℃，"
            f"夜间{c.get('nightweather')} {c.get('nighttemp')}℃"
        )
    reply = await glm_chat(
        [
            {"role": "system", "content": (
                "你是校园生活助手。根据天气数据，用一句不超过 40 字的中文口语化建议，"
                "告诉同学今天穿什么、要不要带伞、要不要防晒。直接给建议，不要寒暄，"
                "不要重复罗列数据，不要用 emoji。"
            )},
            {"role": "user", "content": summary},
        ],
        temperature=0.7,
    )
    return reply


@router.get("/weather")
async def weather(city_adcode: str = "350100"):
    """查询天气并给出一句出行提示。

    Args:
        city_adcode: 城市 adcode，默认福州（阳光学院所在地）。
    """
    live = await amap_weather(city_adcode)

    if not live:
        return {
            "raw": {},
            "tip": PLACEHOLDER_TIP,
            "source": "placeholder",
            "configured": amap_ready(),
        }

    tip = await _ai_tip(live)
    source = "ai" if tip else ""
    if not tip:
        tip = _rule_based_tip(live)
        source = "rule"

    return {
        "raw": live,
        "tip": tip,
        "source": source,
        "configured": True,
    }


@router.post("/route")
async def route(data: NavIn):
    """步行路径规划。未配置高德 Key 时返回空结构。"""
    raw = await amap_walking_route(data.origin, data.destination)
    return {"raw": raw, "configured": amap_ready()}
