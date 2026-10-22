#!/usr/bin/env python3
"""校园点位坐标批量查证工具。

作用：把「点位名」批量转成「高德坐标」，并输出可直接粘进 campus.js 的代码。
      教学楼的默认列表和 campus.js 的 BUILDINGS 一致，
      生活类点位（宿舍/食堂/快递等）与 CAMPUS_PLACES 对应。

用法（在项目根目录执行）：
    python tools/lookup_buildings.py                  # 用默认列表（教学楼 + 生活类点位）
    python tools/lookup_buildings.py 明德楼 至善楼     # 查你指定的楼名
    python tools/lookup_buildings.py --city 350100 商德楼   # 指定城市
    python tools/lookup_buildings.py --places         # 只查生活类点位（宿舍/食堂/快递…）

要点：
  1. 搜索词会自动加上学校名前缀（默认「阳光学院」），
     否则「知行楼」会搜到全国各地的同名楼。
     生活类点位有些本身含「阳光学院」前缀（如「阳光学院东区餐厅」），
     工具会自动判断，不会拼成「阳光学院阳光学院东区餐厅」。
  2. 每条结果都会打印 POI 全名 + 地址，方便你判断是不是本校那个。
  3. 搜不到的点位会明确标出来，提示你用坐标拾取器手动补
     （https://lbs.amap.com/tools/picker）。
  4. **注意排除滨海校区** —— 阳光学院在长乐还有个滨海校区，
     「阳光学院」关键词会带出十几公里外的点位，工具会按距离自动标注提醒。

注意：本工具用的是高德 **Web 服务 Key**（后端那个），
不是前端的 JS Key —— 两个不一样，别混。
"""
import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"

# 默认查证列表：与 frontend/js/campus.js 对应
# 教学楼（= BUILDINGS，南区 / 北区）
DEFAULT_SOUTH = ["商德楼", "博文楼", "雅颂楼", "正德楼"]
DEFAULT_NORTH = ["知行楼", "载德楼", "润知楼", "启智楼", "致远楼", "平远楼"]
# 生活类点位（= CAMPUS_PLACES 里 type 不是 teaching 的那些）
DEFAULT_PLACES = [
    "图书馆", "人工智能学院", "商学系", "会计系", "经济系", "现代音乐学院",
    "外语系", "外国语与海外教育学院", "儿童发展与教育学院", "工程结构试验中心",
    "自习中心", "教工活动中心",
    # ⚠️ 与 frontend/js/campus.js 的 CAMPUS_PLACES 保持一致（2026-10-14 精简过）：
    #    「西区」已改名「西区宿舍」并换坐标；新增「操场」「东区宿舍」；
    #    陈家生煎 / 1点点 / 益禾堂 / 0090汉堡工厂 已从餐饮里删除，
    #    7-ELEVEn 归入「其他」。查证脚本的清单跟着走，免得对不上。
    "学生公寓", "西区宿舍", "东区宿舍", "五期宿舍", "六期宿舍", "创四小镇", "操场",
    "东区餐厅", "西区餐厅", "六期餐厅", "五期餐厅", "7-ELEVEn", "游泳馆",
]
# 校本部中心，用于判断查到的点位是不是跑到滨海校区去了
CAMPUS_CENTER = (119.447887, 26.005046)


def distance_m(lng: float, lat: float) -> float:
    """粗略算到校本部中心的距离（米）。校园才 1 公里，
    这点距离用平面近似足够了，不需要 haversine。"""
    dx = (lng - CAMPUS_CENTER[0]) * 100000 * math.cos(math.radians(26))
    dy = (lat - CAMPUS_CENTER[1]) * 111000
    return math.hypot(dx, dy)



def load_web_key() -> str:
    """从 backend/.env 读高德 Web 服务 Key。"""
    if not ENV.exists():
        sys.exit(f"找不到 {ENV}，请先在 backend/ 下创建 .env 并填 AMAP_WEB_KEY")
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("AMAP_WEB_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    sys.exit("backend/.env 里的 AMAP_WEB_KEY 是空的，请先填入高德 Web 服务 Key")


def search_poi(key: str, keyword: str, city: str, offset: int = 3) -> list:
    """调高德 place/text 搜 POI。返回结果列表（可能为空）。"""
    url = (
        "https://restapi.amap.com/v3/place/text"
        f"?key={key}&keywords={urllib.parse.quote(keyword)}"
        f"&city={city}&offset={offset}&page=1&extensions=base"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"    请求失败：{type(e).__name__}: {e}")
        return []

    if str(data.get("status")) != "1":
        print(f"    高德返回失败：info={data.get('info')} infocode={data.get('infocode')}")
        return []
    return data.get("pois") or []


def lookup(key: str, names: list, city: str, prefix: str) -> dict:
    """逐个查证，返回 {点位名: (lng, lat) 或 None}。

    搜索词拼接有个注意点：生活类点位本身可能就含学校前缀
    （「阳光学院东区餐厅」「阳光学院信息实验楼」），
    再拼一次会变成「阳光学院阳光学院东区餐厅」，必然搜不到。
    所以先看名字里有没有前缀，有就不拼。
    """
    found = {}
    for name in names:
        print(f"\n  ▸ {name}")
        keyword = name if prefix in name else prefix + name
        results = search_poi(key, keyword, city, offset=3)
        if not results:
            print("    ✗ 没搜到。请用坐标拾取器手动补：https://lbs.amap.com/tools/picker")
            found[name] = None
            continue

        # 打印前几条，让人/程序都能判断
        best = None
        for i, p in enumerate(results[:3]):
            loc = p.get("location", "")
            poi_name = p.get("name", "")
            addr = p.get("address", "")
            if not loc or "," not in loc:
                continue
            lng, lat = loc.split(",")
            dist = distance_m(float(lng), float(lat))
            mark = ""
            # 优先选 POI 名里带点名的（最可能是本校那个）；同时排除远在滨海校区的
            if name in poi_name or name.rstrip("楼栋") in poi_name:
                if dist <= 1500:
                    mark = "  ← 采用这条"
                    if best is None:
                        best = (float(lng), float(lat))
                else:
                    mark = f"  ← 在 {dist/1000:.1f}km 外，疑似滨海校区，已跳过"
            elif dist > 1500:
                mark = f"  ← 在 {dist/1000:.1f}km 外（滨海校区？）"
            print(f"    [{i+1}] {poi_name}  ({lng}, {lat})  {addr}  距校本部 {dist:.0f}m{mark}")
        found[name] = best
        time.sleep(0.2)      # 温柔一点，别触发限流
    return found


def emit_code(south: dict, north: dict, places: dict = None) -> None:
    """输出可直接粘进 campus.js 的 BUILDINGS / CAMPUS_PLACES 代码。"""
    print("\n" + "=" * 66)
    print("  可直接粘进 frontend/js/campus.js 的代码")
    print("=" * 66)

    if south or north:
        print("\nconst BUILDINGS = [")
        print("  // 南区")
        for name, pt in south.items():
            if pt:
                print(f'  {{ name: "{name}", region: "south", lng: {pt[0]:.6f}, lat: {pt[1]:.6f} }},')
            else:
                print(f'  // {{ name: "{name}", region: "south", lng: 0, lat: 0 }},  // ← 待手动补')
        print("  // 北区")
        for name, pt in north.items():
            if pt:
                print(f'  {{ name: "{name}", region: "north", lng: {pt[0]:.6f}, lat: {pt[1]:.6f} }},')
            else:
                print(f'  // {{ name: "{name}", region: "north", lng: 0, lat: 0 }},  // ← 待手动补')
        print("];")

    if places:
        print("\n// 生活类点位：粘进 CAMPUS_PLACES，type 按实际用途改（dorm/food/service/facility…）")
        for name, pt in places.items():
            if pt:
                print(f'  {{ name: "{name}", type: "facility", lng: {pt[0]:.6f}, lat: {pt[1]:.6f} }},')
            else:
                print(f'  // {{ name: "{name}", type: "facility", lng: 0, lat: 0 }},  // ← 待手动补')

    all_found = {**(south or {}), **(north or {}), **(places or {})}
    missing = [n for n, p in all_found.items() if not p]
    if missing:
        print(f"\n⚠️ 还有 {len(missing)} 个没查到，需要手动补：{'、'.join(missing)}")
        print("   方法：打开 https://lbs.amap.com/tools/picker ，")
        print("        搜学校名定位，把地图拖到该位置，点一下读经纬度，填进注释行。")


def main():
    ap = argparse.ArgumentParser(description="校园点位坐标批量查证")
    ap.add_argument("names", nargs="*", help="要查的点位名；不填则用内置校园列表")
    ap.add_argument("--city", default="350100", help="城市 adcode，默认福州 350100")
    ap.add_argument("--prefix", default="阳光学院", help="搜索前缀，默认「阳光学院」")
    ap.add_argument("--places", action="store_true", help="只查生活类点位（宿舍/食堂/快递…）")
    ap.add_argument("--buildings", action="store_true", help="只查教学楼")
    args = ap.parse_args()

    key = load_web_key()
    print("=" * 66)
    print("  校园点位坐标查证")
    print(f"  城市 adcode: {args.city}   搜索前缀: {args.prefix}")
    print("=" * 66)

    if args.names:
        found = lookup(key, args.names, args.city, args.prefix)
        # 用户自定义列表时不分南北区，全按南区输出方便复用
        emit_code(found, {})
    elif args.places:
        print("\n【生活类点位】")
        places = lookup(key, DEFAULT_PLACES, args.city, args.prefix)
        emit_code({}, {}, places)
    elif args.buildings:
        print("\n【南区】")
        south = lookup(key, DEFAULT_SOUTH, args.city, args.prefix)
        print("\n【北区】")
        north = lookup(key, DEFAULT_NORTH, args.city, args.prefix)
        emit_code(south, north)
    else:
        print("\n【南区】")
        south = lookup(key, DEFAULT_SOUTH, args.city, args.prefix)
        print("\n【北区】")
        north = lookup(key, DEFAULT_NORTH, args.city, args.prefix)
        print("\n【生活类点位】")
        places = lookup(key, DEFAULT_PLACES, args.city, args.prefix)
        emit_code(south, north, places)

    print()


if __name__ == "__main__":
    main()
