# -*- coding: utf-8 -*-
"""把校园实景照裁成首页 Hero 轮播用的宽幅图。

为什么要裁：
  1. 原图是手机竖拍/4:3，Hero 是宽横幅，直接用 background-size:cover
     会把画面裁得只剩中间一条，还可能把「小红书」水印露出来。
  2. 所以在服务端先裁成 3:1 的宽幅，裁掉底部水印，再压到 1200 宽。

用法：
    python make_hero.py [原图目录]

    原图目录默认取脚本旁的 hero-src/，里面按下面 JOBS 的文件名放原片。
    ⚠️ 原片（手机拍摄的照片）**没有打进压缩包**，只在 pack 时把裁好的
       hero-*.jpg 放进 frontend/img/。想重新裁，把原片放回 hero-src/ 即可。
"""
from PIL import Image
import os
import sys

# 原片目录：命令行第一个参数优先，默认脚本旁的 hero-src/
_HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "hero-src")
DST = os.path.abspath(os.path.join(_HERE, "..", "frontend", "img"))

# (源文件, 输出名, 裁剪框 left,top,right,bottom)
#
# 裁成 3:1 —— 首屏在桌面端大约是 3.9:1（1080 容器里 1024 宽 × 260 高），
# 3:1 的原片用 background-size:cover 只会上下各裁掉一点点，
# 关键地标（「阳光学院」石牌 / 图书馆门头）都能完整保住。
JOBS = [
    # 校门：1440x1080。石牌在 y≈675~836，水印在 y>1000。取 480~960：
    # 上沿留到云层下缘，下沿避开地面水印。
    ("img4.jpg", "hero-gate.jpg", (0, 480, 1440, 960)),
    # 图书馆：1280x2343 竖图。「图书馆」牌匾在 y≈790，取 600~1027 的横条：
    # 楼体、牌匾、入口台阶都在里面。
    ("img5.jpg", "hero-library.jpg", (0, 600, 1280, 1027)),
]

OUT_W = 1200
QUALITY = 84


def run():
    os.makedirs(DST, exist_ok=True)
    for name, out, box in JOBS:
        src = os.path.join(SRC, name)
        im = Image.open(src).convert("RGB")
        im = im.crop(box)
        w, h = im.size
        im = im.resize((OUT_W, max(1, round(h * OUT_W / w))), Image.LANCZOS)
        dst = os.path.join(DST, out)
        im.save(dst, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        print("%-18s %sx%s -> %s  %d KB"
              % (out, im.size[0], im.size[1], dst, os.path.getsize(dst) // 1024))


if __name__ == "__main__":
    run()
