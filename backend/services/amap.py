"""高德 Web 服务封装：天气、地理编码、路径规划。

设计要点：
  1. **未配置 key 时自动降级**：返回空结构，由上层走占位文案。
  2. **所有异常都被吞掉并降级**：网络不通、key 无效、额度用尽……
     任何情况都不让页面白屏。出错时打印日志便于排查。
  3. 高德返回值有个注意点：`status` 是**字符串** "1"/"0"，不是数字。
     且失败时 `info` 里才有原因（如 DAILY_QUERY_OVER_LIMIT）。

未配置 AMAP_WEB_KEY 时，前端地图仍可用（那是 JS API，走前端 config.js），
只是后端的「天气提示」和「路径规划」会走占位。
"""
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

BASE = "https://restapi.amap.com/v3"
TIMEOUT = 10.0


def is_configured() -> bool:
    """是否填了高德 Web 服务 Key。"""
    return bool((settings.AMAP_WEB_KEY or "").strip())


def _check(data: dict, api_name: str) -> bool:
    """高德接口的通用成功判断。status 是字符串 "1" 表示成功。"""
    if str(data.get("status")) == "1":
        return True
    logger.warning(
        "高德 %s 返回失败：info=%s infocode=%s",
        api_name, data.get("info"), data.get("infocode"),
    )
    return False


async def amap_weather(city_adcode: str) -> dict:
    """查询实况天气 + 未来预报。

    Args:
        city_adcode: 城市 adcode，如福州 "350100"、北京 "110101"。

    Returns:
        实况天气 dict（含 weather/temperature/humidity/winddirection 等），
        外加一个 "forecast" 键放未来 3 天预报列表。
        未配置或失败时返回 {}。
    """
    if not is_configured():
        logger.info("未配置 AMAP_WEB_KEY，天气降级为占位。")
        return {}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        live: dict = {}
        casts: list = []

        # 实况天气
        try:
            r = await client.get(f"{BASE}/weather/weatherInfo", params={
                "key": settings.AMAP_WEB_KEY,
                "city": city_adcode,
                "extensions": "base",
                "output": "JSON",
            })
            data = r.json()
            if _check(data, "weather(base)") and data.get("lives"):
                live = data["lives"][0]
        except Exception as e:
            logger.warning("高德实况天气调用失败：%s: %s", type(e).__name__, e)

        # 未来预报（失败不影响实况）
        try:
            r = await client.get(f"{BASE}/weather/weatherInfo", params={
                "key": settings.AMAP_WEB_KEY,
                "city": city_adcode,
                "extensions": "all",
                "output": "JSON",
            })
            data = r.json()
            if _check(data, "weather(all)") and data.get("forecasts"):
                casts = data["forecasts"][0].get("casts", []) or []
        except Exception as e:
            logger.warning("高德预报天气调用失败：%s: %s", type(e).__name__, e)

        if not live:
            return {}
        result = dict(live)
        result["forecast"] = casts
        return result


async def amap_geocode(address: str) -> Optional[dict]:
    """地理编码：地址/地标名称 -> 经纬度。

    Returns:
        {"lng": "119.29", "lat": "26.08", "formatted": "..."}；失败返回 None。
    """
    if not is_configured() or not (address or "").strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{BASE}/geocode/geo", params={
                "key": settings.AMAP_WEB_KEY,
                "address": address.strip(),
                "output": "JSON",
            })
            data = r.json()
            if not _check(data, "geocode"):
                return None
            geocodes = data.get("geocodes") or []
            if not geocodes:
                logger.info("高德地理编码无结果：%s", address)
                return None
            g = geocodes[0]
            lng, lat = (g.get("location") or ",").split(",")[:2]
            return {"lng": lng, "lat": lat, "formatted": g.get("formatted_address", "")}
    except Exception as e:
        logger.warning("高德地理编码失败：%s: %s", type(e).__name__, e)
        return None


async def amap_walking_route(origin: str, destination: str,
                             city_adcode: str = "350100") -> dict:
    """步行路径规划。

    Args:
        origin: 起点，格式 "lng,lat"
        destination: 终点，格式 "lng,lat"
        city_adcode: 城市编码，高德要求传。

    Returns:
        {"distance": "320", "duration": "300", "steps": [...]}；
        未配置或失败时返回 {}。
    """
    if not is_configured():
        logger.info("未配置 AMAP_WEB_KEY，路径规划降级为占位。")
        return {}
    if not (origin or "").strip() or not (destination or "").strip():
        return {}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{BASE}/direction/walking", params={
                "key": settings.AMAP_WEB_KEY,
                "origin": origin.strip(),
                "destination": destination.strip(),
                "city": city_adcode,
                "output": "JSON",
            })
            data = r.json()
            if not _check(data, "walking"):
                return {}
            paths = data.get("route", {}).get("paths") or []
            if not paths:
                logger.info("高德步行规划无结果：%s -> %s", origin, destination)
                return {}
            p = paths[0]
            steps = [
                {
                    "instruction": s.get("instruction", ""),
                    "road": s.get("road", ""),
                    "distance": s.get("distance", "0"),
                    "duration": s.get("duration", "0"),
                }
                for s in (p.get("steps") or [])
            ]
            return {
                "distance": p.get("distance", "0"),   # 米
                "duration": p.get("duration", "0"),   # 秒
                "steps": steps,
            }
    except Exception as e:
        logger.warning("高德步行规划失败：%s: %s", type(e).__name__, e)
        return {}
