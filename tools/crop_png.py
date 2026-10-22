# -*- coding: utf-8 -*-
"""纯标准库 PNG 裁边工具（无第三方依赖）。

用途：把 AI 生成图右下角的水印裁掉。四周各去掉 margin 比例后重新编码。

只支持 8bit 的 truecolor(RGB, color_type=2) / truecolor+alpha(RGBA, color_type=6)，
这两种覆盖了本次生成图的情况；其它格式直接报错退出，不做静默降级。

用法：
    python crop_png.py <输入.png> <输出.png> [margin=0.10]
"""
import struct
import sys
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _read_chunks(data):
    """拆出 IHDR 与全部 IDAT 数据。"""
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
    """把 PNG 逐行过滤器还原成原始像素字节。"""
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
        if ftype == 1:                      # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:                    # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:                    # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:                    # Paeth
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


def _write_png(path, width, height, bpp, pixels):
    """以 filter=None(0) 重新编码写出。"""
    stride = width * bpp
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    colortype = 2 if bpp == 3 else 6
    ihdr = struct.pack(">IIBBBBB", width, height, 8, colortype, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(PNG_SIG)
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def crop(src, dst, margin=0.10):
    """四周各裁掉 margin 比例（相对宽/高），返回新尺寸。"""
    data = open(src, "rb").read()
    if data[:8] != PNG_SIG:
        raise ValueError("不是 PNG 文件: " + src)
    width, height, bitdepth, colortype, idat = _read_chunks(data)
    if bitdepth != 8 or colortype not in (2, 6):
        raise ValueError("只支持 8bit RGB/RGBA，实际=%s/%s" % (bitdepth, colortype))
    bpp = 3 if colortype == 2 else 4

    pixels = _unfilter(zlib.decompress(idat), width, height, bpp)

    dx = int(width * margin)
    dy = int(height * margin)
    nw, nh = width - 2 * dx, height - 2 * dy
    if nw <= 0 or nh <= 0:
        raise ValueError("裁剪比例过大")

    out = bytearray(nw * nh * bpp)
    for y in range(nh):
        s = ((y + dy) * width + dx) * bpp
        out[y * nw * bpp:(y + 1) * nw * bpp] = pixels[s:s + nw * bpp]

    _write_png(dst, nw, nh, bpp, out)
    return nw, nh


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    m = float(sys.argv[3]) if len(sys.argv) > 3 else 0.10
    w, h = crop(sys.argv[1], sys.argv[2], m)
    print("ok -> %s (%dx%d)" % (sys.argv[2], w, h))
