# -*- coding: utf-8 -*-
"""纯标准库 PNG 工具：解码 -> 整数倍降采样 -> 逐行自适应过滤重编码。

为什么要自己写：本机装不上 Pillow（沙箱不放行 pip 网络），
但首页三张卡片的悬停图原始生成图有 1.5MB 左右一张（PNG 存平滑渐变很亏），
三张加起来 4.4MB 直接压在首页上，手机上打开会很难受。

做法：
  1. 解码出原图像素；
  2. 按 factor 做**箱式平均**降采样（不是隔行取样，避免锯齿）；
  3. 重编码时**逐行挑最优过滤器**（Sub/Up/Average/Paeth 里选绝对值和最小的），
     平滑渐变经过 Sub/Paeth 之后大量为 0，zlib 能压掉一个数量级。

用法：
    python shrink_png.py <输入.png> <输出.png> [factor=2]
"""
import struct
import sys
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _read_chunks(data):
    pos = 8
    idat = bytearray()
    width = height = bitdepth = colortype = None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", body[:10])
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        pos += 12 + length
    return width, height, bitdepth, colortype, bytes(idat)


def _paeth(a, b, c):
    pa = abs(b - c)
    pb = abs(a - c)
    pc = abs(a + b - 2 * c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw, width, height, bpp):
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    p = 0
    o = 0
    for _ in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ftype != 0:
            raise ValueError("未知的 PNG 行过滤器类型: %d" % ftype)
        out[o:o + stride] = line
        o += stride
        prev = line
    return out


def _downsample(pixels, width, height, bpp, factor):
    """箱式平均降采样（factor 为整数倍）。"""
    if factor <= 1:
        return pixels, width, height
    nw, nh = width // factor, height // factor
    area = factor * factor
    out = bytearray(nw * nh * bpp)
    for y in range(nh):
        sy = y * factor
        for x in range(nw):
            sx = x * factor
            acc = [0] * bpp
            for dy in range(factor):
                row = (sy + dy) * width
                for dx in range(factor):
                    base = (row + sx + dx) * bpp
                    for k in range(bpp):
                        acc[k] += pixels[base + k]
            o = (y * nw + x) * bpp
            for k in range(bpp):
                out[o + k] = acc[k] // area
    return out, nw, nh


def _filter_row(cur, prev, bpp, ftype):
    """按指定过滤器类型生成一行过滤后的字节。"""
    stride = len(cur)
    out = bytearray(stride)
    if ftype == 0:
        return bytearray(cur)
    if ftype == 1:
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            out[i] = (cur[i] - a) & 0xFF
    elif ftype == 2:
        for i in range(stride):
            out[i] = (cur[i] - prev[i]) & 0xFF
    elif ftype == 3:
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            out[i] = (cur[i] - ((a + prev[i]) >> 1)) & 0xFF
    else:
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            c = prev[i - bpp] if i >= bpp else 0
            out[i] = (cur[i] - _paeth(a, prev[i], c)) & 0xFF
    return out


def _cost(line):
    """启发式代价：把字节当有符号数取绝对值求和（PNG 官方推荐的那个指标）。"""
    s = 0
    for b in line:
        s += b if b < 128 else 256 - b
    return s


def _encode(path, width, height, bpp, pixels):
    stride = width * bpp
    raw = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        cur = pixels[y * stride:(y + 1) * stride]
        best_ft, best_line, best_cost = 0, None, None
        for ft in (1, 2, 3, 4):          # 0 基本不会赢，跳过省时间
            line = _filter_row(cur, prev, bpp, ft)
            c = _cost(line)
            if best_cost is None or c < best_cost:
                best_ft, best_line, best_cost = ft, line, c
        raw.append(best_ft)
        raw += best_line
        prev = cur

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    colortype = 2 if bpp == 3 else 6
    with open(path, "wb") as f:
        f.write(PNG_SIG)
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colortype, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def shrink(src, dst, factor=2, posterize=0):
    data = open(src, "rb").read()
    if data[:8] != PNG_SIG:
        raise ValueError("不是 PNG: " + src)
    width, height, bitdepth, colortype, idat = _read_chunks(data)
    if bitdepth != 8 or colortype not in (2, 6):
        raise ValueError("只支持 8bit RGB/RGBA")
    bpp = 3 if colortype == 2 else 4

    pixels = _unfilter(zlib.decompress(idat), width, height, bpp)
    pixels, nw, nh = _downsample(pixels, width, height, bpp, factor)
    if posterize > 0:
        # 抹掉低位：平滑渐变里低位全是抖动噪声，砍掉后 Sub/Paeth 差分大量归零，
        # zlib 体积能再降一截。这几张图只当「蒙着白纱的背景」，肉眼看不出差别。
        step = 1 << posterize
        half = step >> 1
        for i in range(len(pixels)):
            pixels[i] = ((pixels[i] >> posterize) << posterize) | half
    _encode(dst, nw, nh, bpp, pixels)
    return nw, nh


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    f = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    p = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    w, h = shrink(sys.argv[1], sys.argv[2], f, p)
    print("ok -> %s (%dx%d)" % (sys.argv[2], w, h))
