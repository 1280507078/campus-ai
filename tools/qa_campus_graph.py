"""★ 校内路网「贴路」硬指标守卫（本工程自带，随包交付）。

## 这个脚本存在的唯一理由

交付前我们用 `campus-graph.js` 自己的数据算「路线中间点离路网最远 0.0 m」，
报了个满分 —— 结果用户在手机上看到蓝线**根本没在路上**。为什么？

因为那是**循环论证**：拿来比对的那张"路网"，就是路线自己走的那张图，
距离当然恒等于 0。唯一有意义的度量只有一个：

    把路网折线投影到**高德底图的真实道路像素**上，量它离最近的白色路面有多远。

高德干净底图（`showLabel:false`，无注记文字污染）上**只有道路是纯白 (#ffffff)**，
建筑是浅灰 (244/249)、绿地水面另有配色，所以 `r,g,b >= 254` 就是道路。

## 用法

    python tools/qa_campus_graph.py

退出码 0 = 通过；非 0 = 有硬指标不达标（可以接进 CI / 打包脚本）。

## 依赖

numpy / scipy / Pillow。参考底图在 `tools/refdata/`：
    campus_shot.png   单张整屏截的高德干净底图（3000×3800，z18 / dpr 2）
    campus_shot.json  页面回报的 bounds（**必须用页面回报值，不能自己算**）
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GRAPH_JS = ROOT / "frontend" / "js" / "campus-graph.js"
REF = HERE / "refdata"

# ── 阈值（改这里就是改标准）───────────────────────────────
TOL_NODE_P90 = 6.0     # 节点离路 p90，米
TOL_SAMPLE_P90 = 1.0   # 采样点离路 p90，米
TOL_SAMPLE_P95 = 8.0   # 采样点离路 p95，米
TOL_OFFRATIO = 8.0     # 离路 >3 m 的里程占比上限，%
TOL_OFF_M = 3.0        # 「离路」的判定阈值，米
CLIP = (119.4452, 26.0014, 119.4516, 26.0090)   # 只统计校园范围，避免统计窗口边缘


# ── 1. 从 campus-graph.js 里抠出 CAMPUS_GRAPH ──────────────
def load_graph(js_path):
    """把 JS 对象字面量转成 JSON 再解析。

    这个字面量是脚本生成的、结构固定（无注释、无尾逗号、无函数值），
    唯一和 JSON 的差别就是 **key 没加引号**，补上引号即可。
    """
    src = js_path.read_text(encoding="utf-8")
    m = re.search(r"const CAMPUS_GRAPH\s*=\s*(\{.*?\n\};)", src, re.S)
    if not m:
        raise SystemExit(f"× 在 {js_path} 里找不到 `const CAMPUS_GRAPH = {{...}};`")
    lit = m.group(1).rstrip()
    if lit.endswith(";"):
        lit = lit[:-1]                               # 去掉结尾分号
    lit = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', lit)
    # 生成器为了让 JS 好读，会在最后一项后面留尾逗号；JSON 不允许。
    # 这里没有字符串值，所以直接删掉 `,]` / `,}` 前的逗号是安全的。
    lit = re.sub(r",(\s*[\]\}])", r"\1", lit)
    return json.loads(lit)


# ── 2. 参考底图 → 路网距离场 ────────────────────────────────
def build_edt():
    meta = json.loads((REF / "campus_shot.json").read_text(encoding="utf-8"))
    (lngW, latS), (lngE, latN) = meta["info"]["bounds"]
    IW, IH = Image.open(REF / "campus_shot.png").size

    def my(lat):
        s = math.sin(math.radians(lat))
        return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)

    Y0, Y1 = my(latN), my(latS)
    M = 40075016.686 * math.cos(math.radians(26.0055)) / 360.0 * (lngE - lngW) / IW

    img = np.asarray(Image.open(REF / "campus_shot.png").convert("RGB")).astype(np.int16)
    road = (img[..., 0] >= 254) & (img[..., 1] >= 254) & (img[..., 2] >= 254)
    # 水印（右下角"高德地图"字样）也是纯白，挖掉
    road[int(IH - 34 * 2):, : 360 * 2] = False
    # 橙色高架（马尾隧道）压住地面：它下面本来就看不到白路，不算"不贴路"
    orange = (img[..., 0] > 225) & (img[..., 1] > 120) & (img[..., 1] < 225) & (img[..., 2] < 190)
    road &= ~ndi.binary_dilation(orange, np.ones((21, 21), bool))
    edt = ndi.distance_transform_edt(~road) * M

    def to_px(lng, lat):
        return (lng - lngW) / (lngE - lngW) * IW, (my(lat) - Y0) / (Y1 - Y0) * IH

    cx0, cy0 = to_px(CLIP[0], CLIP[3])
    cx1, cy1 = to_px(CLIP[2], CLIP[1])
    ins = np.zeros_like(road)
    ins[int(cy0):int(cy1), int(cx0):int(cx1)] = True
    print(f"参考底图 {IW}×{IH}（z{meta['info']['zoom']} / dpr{meta['dpr']}），"
          f"1 px = {M:.5f} m；统计窗口取校园范围")
    return to_px, edt, ins, IW, IH, M


def main():
    g = load_graph(GRAPH_JS)
    nodes, edges = g["nodes"], g["edges"]
    to_px, edt, ins, IW, IH, M = build_edt()
    print(f"待检图 {GRAPH_JS.name}：{len(nodes)} 节点 / {len(edges)} 边")

    def d_at(lng, lat):
        x, y = to_px(lng, lat)
        xi, yi = int(round(x)), int(round(y))
        if not (0 <= xi < IW and 0 <= yi < IH and ins[yi, xi]):
            return None
        return float(edt[yi, xi])

    def e_pts(e):
        a, b = nodes[e[0]], nodes[e[1]]
        pts = [(a["lng"], a["lat"])]
        if len(e) > 2:
            pts += [(p[0], p[1]) for p in e[2]]
        pts.append((b["lng"], b["lat"]))
        return pts

    # ── 节点 ──
    nd = np.array([d for d in (d_at(v["lng"], v["lat"]) for v in nodes.values()) if d is not None])
    print(f"\n【节点 {len(nd)} 个】离最近白色道路")
    for q in (50, 75, 90, 95):
        print(f"    p{q:<2} = {np.percentile(nd, q):6.2f} m")
    node_p90 = float(np.percentile(nd, 90))

    # ── 边（每段按 1 m 采样）──
    ds, L_all, L_off = [], 0.0, 0.0
    for e in edges:
        pts = e_pts(e)
        for a, b in zip(pts, pts[1:]):
            la = math.radians((a[1] + b[1]) / 2)
            L = math.hypot((b[0] - a[0]) * 111320 * math.cos(la), (b[1] - a[1]) * 110540)
            if L <= 0:
                continue
            n = max(2, int(L) + 1)
            for t in range(n):
                f = t / n
                v = d_at(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                if v is None:
                    continue
                ds.append(v)
                L_all += L / n
                if v > TOL_OFF_M:
                    L_off += L / n
    ds = np.array(ds)
    print(f"\n【采样点 {len(ds):,} 个】离最近白色道路")
    for q in (50, 75, 90, 95, 99):
        print(f"    p{q:<2} = {np.percentile(ds, q):6.2f} m")
    print(f"    max  = {ds.max():6.2f} m")
    for t in (1, 3, 5, 10):
        print(f"    >{t:>2}m：{(ds > t).mean() * 100:5.2f}%")
    s_p90, s_p95 = float(np.percentile(ds, 90)), float(np.percentile(ds, 95))
    off_ratio = L_off / L_all * 100

    # ── 断言 ──
    print("\n" + "=" * 58)
    checks = [
        (f"节点离路 p90 <= {TOL_NODE_P90} m", node_p90 <= TOL_NODE_P90, f"{node_p90:.2f} m"),
        (f"采样点离路 p90 <= {TOL_SAMPLE_P90} m", s_p90 <= TOL_SAMPLE_P90, f"{s_p90:.2f} m"),
        (f"采样点离路 p95 <= {TOL_SAMPLE_P95} m", s_p95 <= TOL_SAMPLE_P95, f"{s_p95:.2f} m"),
        (f"离路 >{TOL_OFF_M:.0f} m 的里程占比 <= {TOL_OFFRATIO}%", off_ratio <= TOL_OFFRATIO,
         f"{off_ratio:.2f}%（{L_off/1000:.2f} / {L_all/1000:.2f} km）"),
    ]
    bad = 0
    for name, cond, val in checks:
        print(f"  {'[PASS]' if cond else '[FAIL]'} {name}   <- {val}")
        bad += 0 if cond else 1
    print("=" * 58)
    print(f"  结论: {'全部通过' if not bad else f'{bad} 项不达标'}")
    print("=" * 58)

    # ── 出图 ──
    out = Image.open(REF / "campus_shot.png").convert("RGB")
    dr = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(out, "RGBA")
    for e in edges:
        pts = e_pts(e)
        vs = [d_at(p[0], p[1]) for p in pts]
        hot = any(v is not None and v > TOL_OFF_M for v in vs)
        dr.line([to_px(p[0], p[1]) for p in pts],
                fill=(255, 0, 0, 235) if hot else (0, 160, 0, 190),
                width=4 if hot else 3)
    dst = HERE / "qa_campus_graph.png"
    out.resize((IW // 3, IH // 3), Image.LANCZOS).save(dst)
    print(f"\n已写入 {dst}（红=离路 >{TOL_OFF_M:.0f} m，绿=贴路）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
