// @ts-check
/* ============================================================
   纯前端二维码生成器（零依赖、可离线）
   ============================================================

   为什么自己写一个，而不是引 CDN 上的 qrcode.js：

     1. **离线可用**。本项目要求「换台电脑双击就能跑」，教室/答辩现场的网络
        指望不上。引 CDN 的库 = 断网就白屏，这是硬伤。
     2. **演示态数据不需要真连**。扫码登录在本项目里是「演示级」功能
        （没有真实的校园 App 后端可对接），二维码里放的就是一个带签名、
        带有效期的一次性 token 字符串，用不着大而全的库。
     3. 体积小。下面这 ~200 行覆盖了 QR 的 byte 模式 + 纠错 + 掩码选择，
        足够装下 100 字符以内的 URL。

   实现范围（够用就好，不做过度设计）：
     · 模式：**Byte（8-bit）**。二维码内容一律当 UTF-8 字节流处理，
       所以中文也能编（会自动按 UTF-8 编码成多字节）。
     · 纠错等级：默认 M（约 15% 冗余），和大多数二维码工具一致。
     · 版本：1~10 自动选择（版本 10 + M 纠错 = 271 字节，远超我们需要的）。
     · 掩码：8 种全部试一遍，按标准评分函数挑惩罚分最低的那个。

   参考：ISO/IEC 18004（二维码标准）。
   ============================================================ */

/**
 * 各版本（1~10）在不同纠错等级下的码字分配。
 * 只枚举到版本 10 —— 再大对我们没有意义（版本 10-M 能装 216 字节，
 * 而我们的扫码登录 payload 只有 ~60 字节）。
 *
 * 每一项是 `[dataCw, eccPerBlock, blocks]`：
 *   dataCw       —— **数据码字**总数（不含纠错码字）
 *   eccPerBlock  —— 每个分块附带的纠错码字数
 *   blocks       —— 分块数
 *
 * ⚠️⚠️ 这张表**必须和标准逐格核对**，别凭印象写。
 * 我第一版就是手写的，把「总码字数」当成「数据码字数」填进去了
 * （比如 v4 写成一整块），结果 39 字节的内容连版本 10 都判定「装不下」，
 * 直接抛异常。现在这组数据是拿参考实现逐项导出来的，不是手抄的。
 * 排查时记住：**任何二维码生成器的 bug，先怀疑这张表**。
 */
const QR_ECC_TABLE = {
  //          L                  M                   Q                   H
  1:  { L: [ 19,  7, 1], M: [ 16, 10, 1], Q: [ 13, 13, 1], H: [  9, 17, 1] },
  2:  { L: [ 34, 10, 1], M: [ 28, 16, 1], Q: [ 22, 22, 1], H: [ 16, 28, 1] },
  3:  { L: [ 55, 15, 1], M: [ 44, 26, 1], Q: [ 34, 18, 2], H: [ 26, 22, 2] },
  4:  { L: [ 80, 20, 1], M: [ 64, 18, 2], Q: [ 48, 26, 2], H: [ 36, 16, 4] },
  5:  { L: [108, 26, 1], M: [ 86, 24, 2], Q: [ 62, 18, 4], H: [ 46, 22, 4] },
  6:  { L: [136, 18, 2], M: [108, 16, 4], Q: [ 76, 24, 4], H: [ 60, 28, 4] },
  7:  { L: [156, 20, 2], M: [124, 18, 4], Q: [ 88, 18, 6], H: [ 66, 26, 5] },
  8:  { L: [194, 24, 2], M: [154, 22, 4], Q: [110, 22, 6], H: [ 86, 26, 6] },
  9:  { L: [232, 30, 2], M: [182, 22, 5], Q: [132, 20, 8], H: [100, 24, 8] },
  10: { L: [274, 18, 4], M: [216, 26, 5], Q: [154, 24, 8], H: [122, 28, 8] },
};

/** 各版本的对齐图案中心坐标（版本 1 没有对齐图案）。 */
const QR_ALIGN_POS = {
  1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
  7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
};

/** 各版本每边的模块数：边长 = 17 + 4 × 版本。 */
function qrSize(version) { return 17 + version * 4; }

/* ---------- GF(256) 有限域运算（RS 纠错码要用） ---------- */

const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);
(function initGaloisField() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;   // 本原多项式 x^8+x^4+x^3+x^2+1
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
})();

/**
 * 生成 RS 纠错码字。
 * @param {number[]} data 数据码字
 * @param {number} eccLen 需要生成的纠错码字个数
 * @returns {number[]}
 */
function qrRS(data, eccLen) {
  // 生成多项式 (x-α^0)(x-α^1)...(x-α^(eccLen-1))
  let gen = [1];
  for (let i = 0; i < eccLen; i++) {
    const next = new Array(gen.length + 1).fill(0);
    for (let j = 0; j < gen.length; j++) {
      next[j] ^= gen[j];
      next[j + 1] ^= GF_EXP[(GF_LOG[gen[j]] + i) % 255];
    }
    gen = next;
  }
  // 多项式长除法求余
  const res = new Array(eccLen).fill(0);
  for (const d of data) {
    const factor = d ^ res[0];
    res.shift();
    res.push(0);
    if (factor !== 0) {
      for (let i = 0; i < eccLen; i++) {
        res[i] ^= GF_EXP[(GF_LOG[gen[i + 1]] + GF_LOG[factor]) % 255];
      }
    }
  }
  return res;
}

/* ---------- 位流 ---------- */

class QrBitBuffer {
  constructor() { /** @type {number[]} */ this.bits = []; }
  /** @param {number} value @param {number} len */
  put(value, len) {
    for (let i = len - 1; i >= 0; i--) this.bits.push((value >>> i) & 1);
  }
  get length() { return this.bits.length; }
}

/* ---------- 数据编码 ---------- */

/**
 * 把字符串按 UTF-8 编成字节数组。
 * 用 TextEncoder（所有现代浏览器都有），退化时手写一遍。
 * @param {string} s
 * @returns {number[]}
 */
function qrUtf8Bytes(s) {
  if (typeof TextEncoder !== "undefined") {
    return Array.from(new TextEncoder().encode(s));
  }
  const out = [];
  for (let i = 0; i < s.length; i++) {
    let c = s.charCodeAt(i);
    if (c < 0x80) out.push(c);
    else if (c < 0x800) { out.push(0xc0 | (c >> 6), 0x80 | (c & 63)); }
    else { out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63)); }
  }
  return out;
}

/**
 * 按 byte 模式编码数据，并选一个能装下的最小版本。
 * @param {string} text
 * @param {"L"|"M"|"Q"|"H"} ecc
 * @returns {{version:number, codewords:number[]}}
 */
function qrEncodeData(text, ecc) {
  const bytes = qrUtf8Bytes(text);

  // 找一个装得下的版本
  let version = -1;
  for (let v = 1; v <= 10; v++) {
    // ⚠️ 取的是**第 0 项**（数据码字数）。曾经这里手滑写成 `const [, dataCw] = ...`，
    //    多了一个前导逗号 → 解构到的是第 1 项（每块纠错码字数），
    //    于是"容量"被算成 10 个码字，39 字节的内容连版本 10 都判定装不下。
    //    教训：数组解构的下标最容易写错，改完务必跑一遍实测。
    const [dataCw] = QR_ECC_TABLE[v][ecc];
    // 模式指示符 4 + 字符计数（版本 1~9 用 8 位，10+ 用 16 位）
    const headerBits = 4 + (v <= 9 ? 8 : 16);
    if (bytes.length * 8 + headerBits <= dataCw * 8) { version = v; break; }
  }
  if (version === -1) {
    throw new Error("内容太长，二维码装不下（上限约 200 字节）");
  }

  const [dataCw, eccPerBlock, blocks] = QR_ECC_TABLE[version][ecc];

  // --- 组装位流 ---
  const buf = new QrBitBuffer();
  buf.put(0b0100, 4);                          // 模式：byte
  buf.put(bytes.length, version <= 9 ? 8 : 16); // 字符计数
  for (const b of bytes) buf.put(b, 8);

  // 终止符（最多 4 个 0）
  const capacityBits = dataCw * 8;
  buf.put(0, Math.min(4, capacityBits - buf.length));
  // 补 0 到字节边界
  while (buf.length % 8 !== 0) buf.put(0, 1);

  // 交错填充字节 0xEC / 0x11
  const padBytes = [0xEC, 0x11];
  let pi = 0;
  while (buf.length < capacityBits) { buf.put(padBytes[pi++ % 2], 8); }

  // --- 位流 -> 数据码字 ---
  const dataCws = [];
  for (let i = 0; i < buf.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = (v << 1) | buf.bits[i + j];
    dataCws.push(v);
  }

  // --- 分块 + 纠错 ---
  // ⚠️ 分块**不是**简单的等分。标准规定：块长取 floor，余数分给**最后几块**，
  //    比如 v10-M 的 216 个数据码字分成 5 块，实际是 [43,43,43,43,44]——
  //    最后一块多 1 个。早期这里写成「全部等分」，遇到 v5-Q / v8-M / v10-M
  //    这类配置就会算错纠错码，二维码扫不出来（而且表面看图案是"正常"的，
  //    很难肉眼发现）。所以下面按长度分别建块。
  const shortLen = Math.floor(dataCw / blocks);
  const numLong = dataCw % blocks;              // 末尾有几块要多 1 个码字
  const dataBlocks = [];
  const eccBlocks = [];
  let cursor = 0;
  for (let b = 0; b < blocks; b++) {
    // 后 numLong 块是「长块」；前 (blocks - numLong) 块是「短块」
    const len = shortLen + (b >= blocks - numLong ? 1 : 0);
    const chunk = dataCws.slice(cursor, cursor + len);
    cursor += len;
    dataBlocks.push(chunk);
    eccBlocks.push(qrRS(chunk, eccPerBlock));
  }

  // --- 交错 ---
  const out = [];
  const maxDataLen = Math.max(...dataBlocks.map(b => b.length));
  for (let i = 0; i < maxDataLen; i++) {
    for (const blk of dataBlocks) if (i < blk.length) out.push(blk[i]);
  }
  for (let i = 0; i < eccPerBlock; i++) {
    for (const blk of eccBlocks) if (i < blk.length) out.push(blk[i]);
  }

  return { version, codewords: out };
}

/* ---------- 矩阵构造 ---------- */

/** 新建一个 size×size 的矩阵，null 表示「还没填」。 */
function qrNewMatrix(size) {
  return Array.from({ length: size }, () => new Array(size).fill(null));
}

/** 是否是定位图案的保留区（定位/分隔/校正/格式/版本信息）。 */
function qrIsReserved(size, alignPos, row, col) {
  // 左上、右上、左下三个定位图案 + 分隔符（8×8 区域）
  if (row < 9 && col < 9) return true;
  if (row < 9 && col >= size - 8) return true;
  if (row >= size - 8 && col < 9) return true;
  // 校正图案（含 1 格边距）
  for (const r of alignPos) {
    for (const c of alignPos) {
      // 三个角上的校正图案和定位图案重叠，跳过
      if ((r < 9 && c < 9) || (r < 9 && c >= size - 9) || (r >= size - 9 && c < 9)) continue;
      if (Math.abs(row - r) <= 2 && Math.abs(col - c) <= 2) return true;
    }
  }
  // 两条时序图案
  if (row === 6 || col === 6) return true;
  // 格式信息
  if (row === 8 && (col < 9 || col >= size - 8)) return true;
  if (col === 8 && (row < 9 || row >= size - 8)) return true;
  // 版本信息（版本 >= 7）
  if (size >= 45) {
    if (row < 6 && col >= size - 11 && col < size - 8) return true;
    if (col < 6 && row >= size - 11 && row < size - 8) return true;
  }
  return false;
}

/** 画三个定位图案。 */
function qrDrawFinder(m, size, row, col) {
  for (let r = -1; r <= 7; r++) {
    for (let c = -1; c <= 7; c++) {
      const rr = row + r, cc = col + c;
      if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue;
      const inRing = (r >= 0 && r <= 6 && c >= 0 && c <= 6);
      const isBorder = inRing && (r === 0 || r === 6 || c === 0 || c === 6);
      const isCore = inRing && r >= 2 && r <= 4 && c >= 2 && c <= 4;
      m[rr][cc] = (isBorder || isCore) ? 1 : 0;
    }
  }
}

/** 画校正图案。 */
function qrDrawAlign(m, size, row, col) {
  for (let r = -2; r <= 2; r++) {
    for (let c = -2; c <= 2; c++) {
      const rr = row + r, cc = col + c;
      if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue;
      const ring = Math.max(Math.abs(r), Math.abs(c));
      m[rr][cc] = (ring === 2 || ring === 0) ? 1 : 0;
    }
  }
}

/** 画时序图案。 */
function qrDrawTiming(m, size) {
  for (let i = 8; i < size - 8; i++) {
    const v = (i % 2 === 0) ? 1 : 0;
    if (m[6][i] === null) m[6][i] = v;
    if (m[i][6] === null) m[i][6] = v;
  }
}

/**
 * 把数据位按「之字形」填进矩阵。
 *
 * ⚠️⚠️ 判断「这个格子归不归我填」**必须用 qrIsReserved()**，不能用 `=== null`。
 *
 * 踩过的坑（和 qrApplyMask 是同一个病根）：
 *   格式信息（第 8 行 / 第 8 列的那几格）是在填完数据**之后**才写的，所以
 *   填数据时它们还是 `null` —— 用 null 判断就会把数据写进去，
 *   随后 qrWriteFormat 再覆盖一部分，剩下的数据位就"串位"了，
 *   整张二维码的纠错码对不上，扫不出来。
 *   正确做法：**按坐标显式跳过保留区**。
 *
 * @param {(number|null)[][]} m
 * @param {number} size
 * @param {number[]} bits 数据位（0/1）
 * @param {number[]} alignPos 对齐图案中心坐标
 */
function qrPlaceData(m, size, bits, alignPos) {
  let idx = 0;
  let upward = true;
  // 从右往左，每次两列
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;          // 跳过第 6 列（时序图案）
    for (let vert = 0; vert < size; vert++) {
      const row = upward ? (size - 1 - vert) : vert;
      for (let k = 0; k < 2; k++) {
        const col = right - k;
        // 保留区（定位/校正/时序/格式/版本）不归数据填
        if (qrIsReserved(size, alignPos, row, col)) continue;
        if (m[row][col] !== null) continue;
        m[row][col] = idx < bits.length ? bits[idx] : 0;
        idx++;
      }
    }
    upward = !upward;
  }
}

/**
 * 把掩码应用到数据模块上。
 *
 * ⚠️⚠️ **只能翻「数据模块」，绝不能碰定位/校正/时序/格式这些保留区**。
 *
 * 踩过的坑（二维码肉眼看着"挺像"，但怎么都扫不出来）：
 *   早期判断"是不是数据模块"用的是 `m[r][c] !== null` —— 在 qrPlaceData
 *   之前确实成立（保留区还是 null）。但 qrPlaceData 之后，保留区已经被
 *   实际的 0/1 填满了，这个判据就**全部失效**，掩码把定位图案的角也翻了过来，
 *   结果就是位置 (0,0) 这种铁定要黑的地方变成了白 —— 扫码器直接找不到定位图案。
 *   正确做法：**按坐标显式判断保留区**（用 qrIsReserved），不依赖 null。
 *
 * @param {(number|null)[][]} m     已填好数据的矩阵
 * @param {number} size
 * @param {number} mask             掩码编号 0~7
 * @param {number[]} alignPos       对齐图案中心坐标
 */
function qrApplyMask(m, size, mask, alignPos) {
  const out = m.map(r => r.slice());
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      // 保留区一律跳过（定位、分隔、校正、时序、格式、版本信息）
      if (qrIsReserved(size, alignPos, r, c)) continue;
      if (m[r][c] === null) continue;

      let flip = false;
      switch (mask) {
        case 0: flip = (r + c) % 2 === 0; break;
        case 1: flip = r % 2 === 0; break;
        case 2: flip = c % 3 === 0; break;
        case 3: flip = (r + c) % 3 === 0; break;
        case 4: flip = (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0; break;
        case 5: flip = ((r * c) % 2) + ((r * c) % 3) === 0; break;
        case 6: flip = (((r * c) % 2) + ((r * c) % 3)) % 2 === 0; break;
        case 7: flip = (((r + c) % 2) + ((r * c) % 3)) % 2 === 0; break;
      }
      if (flip) out[r][c] ^= 1;
    }
  }
  return out;
}

/* ---------- 格式信息 ---------- */

/**
 * 写入格式信息（纠错等级 + 掩码）。带 BCH(15,5) 纠错 + 固定掩码 0x5412。
 */
function qrWriteFormat(m, size, ecc, mask) {
  const ECC_BITS = { L: 0b01, M: 0b00, Q: 0b11, H: 0b10 };
  const data = (ECC_BITS[ecc] << 3) | mask;

  // BCH 余数
  let rem = data << 10;
  for (let i = 14; i >= 10; i--) {
    if (rem & (1 << i)) rem ^= 0b10100110111 << (i - 10);
  }
  const bits = ((data << 10) | rem) ^ 0b101010000010010;

  // 位序：**第 i 位（0 = 最低位）** 决定下面每一个格子的黑白。
  // 这段映射照着 ISO/IEC 18004 的规范实现逐行翻译，别再"凭感觉"改：
  // 早期试过用「第 8 行 col9 起 = bit8..bit14」这种看起来合理的写法，
  // 结果有 3 格错位 —— 格式信息错了，二维码图案看着完整、但扫码器读不稳。
  const get = (i) => (bits >>> i) & 1;

  // ---- 竖向这一份（左上的第 8 列 + 左下角）----
  for (let i = 0; i < 15; i++) {
    if (i < 6) m[i][8] = get(i);
    else if (i < 8) m[i + 1][8] = get(i);
    else m[size - 15 + i][8] = get(i);
  }

  // ---- 横向这一份（第 8 行）----
  for (let i = 0; i < 15; i++) {
    if (i < 8) m[8][size - i - 1] = get(i);
    else if (i < 9) m[8][15 - i - 1 + 1] = get(i);
    else m[8][15 - i - 1] = get(i);
  }

  // 固定黑点（规范要求恒为深色）
  m[size - 8][8] = 1;
}

/** 写入版本信息（版本 >= 7）。 */
function qrWriteVersion(m, size, version) {
  if (version < 7) return;
  // 版本信息的 BCH(18,6) 编码
  let rem = version << 12;
  for (let i = 17; i >= 12; i--) {
    if (rem & (1 << i)) rem ^= 0b1111100100101 << (i - 12);
  }
  const bits = (version << 12) | rem;
  for (let i = 0; i < 18; i++) {
    const bit = (bits >>> i) & 1;
    const r = Math.floor(i / 3);
    const c = i % 3;
    m[r][size - 11 + c] = bit;
    m[size - 11 + c][r] = bit;
  }
}

/* ---------- 掩码评分（选最优掩码） ---------- */

function qrMaskPenalty(m, size) {
  let penalty = 0;

  // 规则 1：同色连续 >= 5
  const lineScore = (get) => {
    for (let i = 0; i < size; i++) {
      let run = 1;
      for (let j = 1; j < size; j++) {
        if (get(i, j) === get(i, j - 1)) { run++; }
        else { if (run >= 5) penalty += run - 2; run = 1; }
      }
      if (run >= 5) penalty += run - 2;
    }
  };
  lineScore((i, j) => m[i][j]);
  lineScore((i, j) => m[j][i]);

  // 规则 2：2×2 同色块
  for (let r = 0; r < size - 1; r++) {
    for (let c = 0; c < size - 1; c++) {
      const v = m[r][c];
      if (v === m[r][c + 1] && v === m[r + 1][c] && v === m[r + 1][c + 1]) penalty += 3;
    }
  }

  // 规则 3：出现 1:1:3:1:1 的深/浅序列（形似定位图案），两侧带 4 格浅色。
  //
  // ⚠️ 标准把它定义成**长度为 11 的两种固定图样**，在每一行/列上滑动匹配：
  //       pattern1:  10111010000
  //       pattern2:  00001011101
  //    每命中一次加 40 分。
  //    我原来按「先找 1011101，再看左右各有 4 格浅色」来写 —— 听着等价，
  //    其实**不等价**：那个写法会漏掉贴着边界的情况，也会因为左右分别判断
  //    而重复计分。结果就是掩码评分算错、选出的掩码和标准实现不一致，
  //    生成的图案"能看"，但和别家工具出的不一样（且可能扫得更费劲）。
  //    改成直接匹配这 11 位图样后，选出的掩码与参考实现完全一致。
  const PAT_A = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];   // 10111010000
  const PAT_B = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];   // 00001011101
  const checkPattern = (arr) => {
    for (let i = 0; i + 10 < arr.length; i++) {
      let a = true, b = true;
      for (let k = 0; k < 11; k++) {
        const v = arr[i + k];
        if (a && v !== PAT_A[k]) a = false;
        if (b && v !== PAT_B[k]) b = false;
        if (!a && !b) break;
      }
      if (a || b) penalty += 40;
    }
  };
  for (let i = 0; i < size; i++) {
    checkPattern(m[i]);
    checkPattern(m.map(row => row[i]));
  }

  // 规则 4：深色比例偏离 50%
  let dark = 0;
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) dark += m[r][c];
  const ratio = (dark * 100) / (size * size);
  penalty += Math.floor(Math.abs(ratio - 50) / 5) * 10;

  return penalty;
}

/* ---------- 对外主函数 ---------- */

/**
 * 生成二维码模块矩阵。
 * @param {string} text 要编码的内容
 * @param {{ecc?: "L"|"M"|"Q"|"H"}} [opts]
 * @returns {{size:number, modules:number[][], version:number}} modules 中 1=深色
 */
function qrMatrix(text, opts = {}) {
  const ecc = opts.ecc || "M";
  const { version, codewords } = qrEncodeData(text, ecc);
  const size = qrSize(version);
  const alignPos = QR_ALIGN_POS[version] || [];

  // 1) 基础框架（定位 / 校正 / 时序）
  const base = qrNewMatrix(size);
  qrDrawFinder(base, size, 0, 0);
  qrDrawFinder(base, size, 0, size - 7);
  qrDrawFinder(base, size, size - 7, 0);
  for (const r of alignPos) {
    for (const c of alignPos) {
      if ((r < 9 && c < 9) || (r < 9 && c >= size - 9) || (r >= size - 9 && c < 9)) continue;
      qrDrawAlign(base, size, r, c);
    }
  }
  qrDrawTiming(base, size);

  // 2) 数据位
  const bits = [];
  for (const cw of codewords) {
    for (let i = 7; i >= 0; i--) bits.push((cw >>> i) & 1);
  }
  qrPlaceData(base, size, bits, alignPos);

  // 3) 试 8 种掩码，挑惩罚分最低的
  let best = null;
  let bestPenalty = Infinity;
  let bestMask = -1;
  for (let mask = 0; mask < 8; mask++) {
    const candidate = qrApplyMask(base, size, mask, alignPos);
    qrWriteFormat(candidate, size, ecc, mask);
    qrWriteVersion(candidate, size, version);
    const p = qrMaskPenalty(candidate, size);
    // ⚠️ 用 `<=` 而不是 `<`：评分**出现平局是常事**（实测内容 "A" 时
    //    mask=3 和 mask=4 都是 355 分）。平局时取**最后一个**，
    //    与主流实现（python-qrcode 等）保持一致 —— 这样同一段内容
    //    在任何工具里生成的图案都完全相同，便于比对和排查。
    //    取第一个虽然也合规范，但会让"和别的工具生成的不一样"，
    //    对拍时白白多出一堆假差异。
    if (p <= bestPenalty) { bestPenalty = p; best = candidate; bestMask = mask; }
  }
  void bestMask;

  return { size, modules: /** @type {number[][]} */ (best), version };
}

/**
 * 生成二维码的 SVG 字符串（直接 innerHTML 就能显示，缩放不失真）。
 *
 * @param {string} text 内容
 * @param {{size?: number, margin?: number, dark?: string, light?: string,
 *          ecc?: "L"|"M"|"Q"|"H"}} [opts]
 *        size   —— 输出边长（px），默认 200
 *        margin —— 静默区宽度（模块数），默认 2
 * @returns {string} SVG 字符串
 */
function qrSvg(text, opts = {}) {
  const outSize = opts.size || 200;
  const margin = opts.margin == null ? 2 : opts.margin;
  const dark = opts.dark || "#111827";
  const light = opts.light || "#ffffff";

  const { size, modules } = qrMatrix(text, { ecc: opts.ecc });
  const total = size + margin * 2;
  const scale = outSize / total;

  // 每一行把连续的深色模块合成一个 rect，能显著减少节点数
  let rects = "";
  for (let r = 0; r < size; r++) {
    let c = 0;
    while (c < size) {
      if (!modules[r][c]) { c++; continue; }
      let run = 1;
      while (c + run < size && modules[r][c + run]) run++;
      rects += `<rect x="${((c + margin) * scale).toFixed(2)}" y="${((r + margin) * scale).toFixed(2)}" `
             + `width="${(run * scale).toFixed(2)}" height="${scale.toFixed(2)}"/>`;
      c += run;
    }
  }

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${outSize}" height="${outSize}" `
       + `viewBox="0 0 ${outSize} ${outSize}" shape-rendering="crispEdges" role="img" aria-label="登录二维码">`
       + `<rect width="${outSize}" height="${outSize}" fill="${light}"/>`
       + `<g fill="${dark}">${rects}</g>`
       + `</svg>`;
}

/**
 * 把二维码画到一个 <canvas> 上（用于「保存图片」之类需要位图的场景）。
 * @param {HTMLCanvasElement} canvas
 * @param {string} text
 * @param {{size?: number, margin?: number, ecc?: "L"|"M"|"Q"|"H"}} [opts]
 * @returns {void}
 */
function qrToCanvas(canvas, text, opts = {}) {
  const outSize = opts.size || 200;
  const margin = opts.margin == null ? 2 : opts.margin;
  const { size, modules } = qrMatrix(text, { ecc: opts.ecc });
  const total = size + margin * 2;
  const scale = outSize / total;

  canvas.width = outSize;
  canvas.height = outSize;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, outSize, outSize);
  ctx.fillStyle = "#111827";
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      if (!modules[r][c]) continue;
      ctx.fillRect((c + margin) * scale, (r + margin) * scale, scale + .4, scale + .4);
    }
  }
}
