/* 校园共享数据：阳光学院（福州校本部）作息时间 + 教学楼。
   供 planner.js / nav.js 共用，避免两侧各写一份。

   关于坐标与时间：
   - 教学楼经纬度已按高德 POI 实际查证替换（原先是一组占位值，
     误落在福州市区三坊七巷，离学校约 15 公里）。
   - 同一栋楼在「南区/北区」的作息差异属【示例数据】，非真实校历，
     请按学校实际安排核对（同时改 backend/seed.py，两处保持一致）。 */

/** 空白时间表槽位：6:00-23:00，早晚整点 + 白天按阳光学院真实节次。
 *  导出给前端做离线兜底；在线时以 /api/planner/slots 返回为准。
 *
 *  start_south/end_south/start_north/end_north 描述南北区作息差异：
 *  - region="both"：两区一致，四个字段留空，用 start/end 即可
 *  - region="split"：两区不同，分别给出时间
 *
 *  ※※ 下面 split 行的南北区时间为【示例数据】，非真实校历。※※
 *  ※※ 请按学校实际作息核对修改（同时改 backend/seed.py 与    ※※
 *  ※※ vue-app/src/utils/campus.ts，三处保持一致）。            ※※
 *  @type {TimeSlot[]} */
const TIME_SLOTS = [
  { order_no: 1,  label: "06:00",  start: "06:00", end: "07:00", period_type: "hour",  region: "both" },
  { order_no: 2,  label: "07:00",  start: "07:00", end: "08:00", period_type: "hour",  region: "both" },
  // 上午：TODO 示例 —— 北区比南区早 10 分钟
  { order_no: 3,  label: "第一节",  start: "08:10", end: "08:55", period_type: "class", region: "split",
    start_south: "08:10", end_south: "08:55", start_north: "08:00", end_north: "08:45" },
  { order_no: 4,  label: "第二节",  start: "09:00", end: "09:45", period_type: "class", region: "split",
    start_south: "09:00", end_south: "09:45", start_north: "08:50", end_north: "09:35" },
  { order_no: 5,  label: "第三节",  start: "10:05", end: "10:50", period_type: "class", region: "split",
    start_south: "10:05", end_south: "10:50", start_north: "09:55", end_north: "10:40" },
  { order_no: 6,  label: "第四节",  start: "10:55", end: "11:40", period_type: "class", region: "split",
    start_south: "10:55", end_south: "11:40", start_north: "10:45", end_north: "11:30" },
  { order_no: 7,  label: "12:00",  start: "12:00", end: "13:00", period_type: "hour",  region: "both" },
  { order_no: 8,  label: "13:00",  start: "13:00", end: "14:00", period_type: "hour",  region: "both" },
  // 下午：TODO 示例 —— 北区比南区早 5 分钟
  { order_no: 9,  label: "第五节",  start: "14:00", end: "14:45", period_type: "class", region: "split",
    start_south: "14:00", end_south: "14:45", start_north: "13:55", end_north: "14:40" },
  { order_no: 10, label: "第六节",  start: "14:50", end: "15:35", period_type: "class", region: "split",
    start_south: "14:50", end_south: "15:35", start_north: "14:45", end_north: "15:30" },
  { order_no: 11, label: "第七节",  start: "15:55", end: "16:40", period_type: "class", region: "split",
    start_south: "15:55", end_south: "16:40", start_north: "15:50", end_north: "16:35" },
  { order_no: 12, label: "第八节",  start: "16:45", end: "17:30", period_type: "class", region: "split",
    start_south: "16:45", end_south: "17:30", start_north: "16:40", end_north: "17:25" },
  { order_no: 13, label: "18:00",  start: "18:00", end: "19:00", period_type: "hour",  region: "both" },
  // 晚上：TODO 示例 —— 北区比南区早 5 分钟
  { order_no: 14, label: "第九节",  start: "18:30", end: "19:15", period_type: "class", region: "split",
    start_south: "18:30", end_south: "19:15", start_north: "18:25", end_north: "19:10" },
  { order_no: 15, label: "第十节",  start: "19:20", end: "20:05", period_type: "class", region: "split",
    start_south: "19:20", end_south: "20:05", start_north: "19:15", end_north: "20:00" },
  { order_no: 16, label: "第十一节", start: "20:10", end: "20:55", period_type: "class", region: "split",
    start_south: "20:10", end_south: "20:55", start_north: "20:05", end_north: "20:50" },
  { order_no: 17, label: "21:00",  start: "21:00", end: "22:00", period_type: "hour",  region: "both" },
  { order_no: 18, label: "22:00",  start: "22:00", end: "23:00", period_type: "hour",  region: "both" },
];

/** 教学楼（南区 / 北区）。
 *
 *  坐标是**逐栋查证**过的，不是估的。查证结论（2026-09）：
 *
 *   ① 高德官方 POI 命中（最可信，坐标原样照抄）：
 *      商德楼 / 博文楼 / 正德楼 / 知行楼 / 载德楼 —— 这 5 栋高德有独立 POI，
 *      本表坐标与 POI 返回值**误差 0 米**。
 *
 *   ② 高德有「同类 POI」可锚定：
 *      润知楼 -> 校内的「阳光学院信息实验楼」POI（119.448182,26.005864），
 *                本表落点离它 2.6 米。用户也确认「润知楼 = 信息实验楼」。
 *      雅颂楼 -> 校内的「阳光学院现代音乐学院」POI（119.446928,26.002116），
 *                本表落点在它旁边约 20 米。
 *      致远楼 -> 高德的「福建省福州市马尾区阳光学院人文社科楼」POI
 *                （119.451285,26.005003）—— 致远楼就是人文社科楼，
 *                坐标原样照抄，误差 0 米。
 *                （2026-09 修正：原来写的是 119.451356,26.004844，
 *                  在人文社科楼 POI 正南 18.8 米，地图上标签清晰可见
 *                  落在了楼外面，用户一眼就看出来了。）
 *
 *   ③ 高德查不到，但与已知地标相对位置固定（按实景图量取）：
 *      启智楼 -> 紧贴「菜鸟驿站西区」POI（119.447625,26.005429）。
 *                用户两次确认「启智楼 = 菜鸟驿站西区旁边那栋楼」，
 *                所以坐标直接取菜鸟驿站正西侧约 20 米处
 *                （不能取「阳光学院」POI：那里离菜鸟驿站 40 多米，地图上会显示成
 *                 一个独立的点，与「旁边那栋楼」对不上）。
 *      平远楼 -> 参照「北门停车场」POI 定位（119.450969,26.006022），
 *                本表落点在它东南约 20 米。
 *
 *  ⚠️ 正德楼与雅颂楼的坐标不要互换：
 *     119.447909,26.002130 是高德的「阳光学院正德楼」；
 *     雅颂楼实际在音艺楼（现代音乐学院）旁边。
 *  @type {Building[]} */
const BUILDINGS = [
  // 南区
  { name: "商德楼", region: "south", lng: 119.446323, lat: 26.002632 },  // ① AMap POI「阳光学院福州校本部商德楼」
  { name: "博文楼", region: "south", lng: 119.447942, lat: 26.001751 },  // ① AMap POI「阳光学院博文楼」
  { name: "雅颂楼", region: "south", lng: 119.446960, lat: 26.002300 },  // ② 紧邻现代音乐学院(音艺楼)
  { name: "正德楼", region: "south", lng: 119.447909, lat: 26.002130 },  // ① AMap POI「阳光学院正德楼」
  // 北区
  { name: "知行楼", region: "north", lng: 119.449140, lat: 26.005572 },  // ① AMap POI「阳光学院福州校本部知行楼」
  { name: "载德楼", region: "north", lng: 119.448763, lat: 26.006025 },  // ① AMap POI「阳光学院载德楼」
  { name: "润知楼", region: "north", lng: 119.448192, lat: 26.005841 },  // ② = 信息实验楼
  { name: "启智楼", region: "north", lng: 119.447491, lat: 26.005432 },  // ③ 紧邻菜鸟驿站西区（正西约 20 米）
  { name: "致远楼", region: "north", lng: 119.451285, lat: 26.005003 },  // ③ AMap POI「福建省福州市马尾区阳光学院人文社科楼」（= 致远楼）
  { name: "平远楼", region: "north", lng: 119.451086, lat: 26.005841 },  // ③ 北门停车场(P)旁
];

/* ==================================================================
   校园点位全集（教学楼 + 宿舍 + 餐饮 + 其他 + 停车 + 校区 …）
   ------------------------------------------------------------------
   这些点位**都能当导航目的地**（比如「导航到图书馆」「导航到操场」），
   但不都是「教学楼」—— 课表里只可能写教学楼，所以：

     · 课表识别 / 上课地点判断  -> 用 BUILDINGS（只有 10 栋楼）
     · 地图展示 / 导航目的地    -> 用 CAMPUS_PLACES（全量）

   数据来源：高德 POI「阳光学院」关键词全量拉取后逐条筛出来的
   （tools/lookup_buildings.py 有批量查证脚本）。

   ⚠️ 滨海校区的 POI 已全部排除 —— 阳光学院在长乐另有一个滨海校区，
      高德搜「阳光学院」会带出 9 条那边的点位（滨海A区/B区、刚健苑、
      日新苑、辉光苑、滨海校区自习室、两个公交站…），
      它们离马尾校本部有 15~16 公里。留着的话地图会突然多出一堆
      几十公里外的点，把视野撑爆，所以一律不要。

   type 取值与含义（前端按它给标记上色）：
     teaching  教学楼（严格 = BUILDINGS 那 10 栋，课表里可能出现的）
     facility  院系 / 场馆（也在校内，但不是「教学楼」）
     dorm      宿舍/生活区
     food      食堂/餐厅
     other     其他（图书馆、游泳馆、操场、便利店这类不好归到上面几类的）
     parking   停车场
     campus    校区范围（校本部 / 西区）
     school    附属学校（阳光实验学校，是中学不是本校教学楼）

   ⚠️ 分类口径（改动前先看这几条，都是硬约束）：

     · 图书馆 / 游泳馆 / 操场 归 `other`（导航页显示为「其他」）；
       操场高德无 POI，坐标是人工点选后核过的
     · 不设 `service`（快递）分类：这类点位已不在表中，留一个空分类只会在
       筛选条上多一个按不动的按钮
     · 宿舍类凑齐「东 / 西 / 五 / 六」四个园区，命名统一为「XX宿舍」，
       落点取楼群中心而不是某一栋
     · 食堂类共 5 条：东区餐厅 / 西区餐厅 / 六期餐厅 / 五期餐厅 / 创四小镇；
       7-ELEVEn 归 `other`；园区一层的档口与奶茶店（陈家生煎 / 1点点 /
       益禾堂 / 0090汉堡工厂）不进点位表 —— 与「食堂」并列会让人误以为
       学校食堂只有这几家
     · 同类命名必须一致：都叫「XX餐厅」，不要混用「食堂」与「餐厅」

   @type {CampusPlace[]} */
const CAMPUS_PLACES = [
  /* ---------- ① 教学楼：就是 BUILDINGS 那 10 栋，一栋不多一栋不少 ---------- */
  ...BUILDINGS.map(b => ({ ...b, type: "teaching" })),

  /* ---------- ② 院系 / 场馆（校内，但不是「教学楼」） ---------- */
  { name: "人工智能学院",   type: "facility", lng: 119.449388, lat: 26.005377 },  // AMap POI
  { name: "商学系",        type: "facility", lng: 119.446274, lat: 26.002600 },  // AMap POI
  { name: "会计系",        type: "facility", lng: 119.446216, lat: 26.002577 },  // AMap POI
  { name: "经济系",        type: "facility", lng: 119.446249, lat: 26.002624 },  // AMap POI
  { name: "现代音乐学院",   type: "facility", lng: 119.446928, lat: 26.002116 },  // AMap POI（音艺楼）
  { name: "外语系",        type: "facility", lng: 119.447471, lat: 26.001608 },  // AMap POI
  { name: "外国语与海外教育学院", type: "facility", lng: 119.447563, lat: 26.001615 },  // AMap POI
  { name: "儿童发展与教育学院", type: "facility", lng: 119.451276, lat: 26.005237 },  // AMap POI
  { name: "工程结构试验中心", type: "facility", lng: 119.449182, lat: 26.005892 },  // AMap POI
  { name: "自习中心",      type: "facility", lng: 119.448488, lat: 26.005463 },  // AMap POI「福州校本部自习中心」
  { name: "教工活动中心",   type: "facility", lng: 119.449050, lat: 26.003650 },  // AMap POI

  /* ---------- 宿舍 / 生活区 ---------- */
  { name: "学生公寓",      type: "dorm", lng: 119.445657, lat: 26.004298 },  // AMap POI 阳光学院学生公寓
  /* ⚠️ 以下坐标按用户指定标定（修改前须知）：
       · 原来的「西区」**改名为「西区宿舍」**并换了坐标 —— 旧坐标 119.445785,26.005926
         落在西区楼群的西北角外面，实测后给了西区 9/10/11/13 号楼楼群中心的点。
       · **新增「东区宿舍」** —— 东区 7/8/10 号楼楼群中心。原来地图上根本没有
         东区宿舍这个点，用户要去东区宿舍只能自己找。
       这两个点都是「一片宿舍楼」的总称，不是某一栋，所以落点在楼群中心。 */
  { name: "西区宿舍",      type: "dorm", lng: 119.446697, lat: 26.005108 },  // 西区 9/10/11/13 号楼楼群中心
  { name: "东区宿舍",      type: "dorm", lng: 119.450065, lat: 26.004041 },  // 东区 7/8/10 号楼楼群中心
  /* 五期宿舍：坐标取现在这个点，**不要用 119.453462,26.006816** ——
     那里落在「福建省举重中心」院内，与主校区隔一条路约 560 米（校区外）。
     ★ 独立佐证：高德的「阳光学院东区20号楼」在 119.451947,26.005616 ——
       本点离它只有 **19 米**，且用户截图里那个坐标框正好在「20号楼」标签下方，
       两处互相印证。这条用 `assistant/inputtips` 才查得到（`place/text` 返回 0 条）。 */
  { name: "五期宿舍",      type: "dorm", lng: 119.45204, lat: 26.005447 },
  /* 「六期宿舍」与东/西/五期口径一致，落点取楼群中心。
     ⚠️ 简称「六期」不命中：`findPlace` 走名称完全匹配。不要为此加前缀模糊匹配 ——
       「六期」会同时命中「六期宿舍」和「六期餐厅」，给出不确定的答案。 */
  { name: "六期宿舍",      type: "dorm", lng: 119.446859, lat: 26.008603 },  // AMap POI 阳光学院6期
  /* 创四小镇（创四方园）归 `food`：本体是创业园区，但一层连着一排吃的，
     查这个点通常就是为了找吃的。 */

  /* ---------- 食堂 / 餐饮 ---------- */
  /* ⚠️ 食堂类只放**正经食堂**：7-ELEVEn 是便利店，归 `other`；
     创四方园一层的档口与奶茶店不在表内 —— 它们与「食堂」并列，会让人误以为
     学校食堂只有这几家。当前共 5 条：4 个餐厅 + 创四小镇。 */
  { name: "东区餐厅",      type: "food", lng: 119.448613, lat: 26.004279 },  // AMap POI 阳光学院东区餐厅
  { name: "西区餐厅",      type: "food", lng: 119.446313, lat: 26.004715 },  // AMap POI 阳光学院西区餐厅
  { name: "六期餐厅",      type: "food", lng: 119.446867, lat: 26.008296 },  // AMap POI 阳光学院阳光里餐厅(六期店)
  { name: "创四小镇",      type: "food", lng: 119.448496, lat: 26.005497 },  // AMap POI 阳光学院创四小镇（一层是餐饮档口）
  /* 五期餐厅：高德查不到「阳光学院五期餐厅」，坐标是与「柏斯健身台球(阳光学院店)」
     （119.452117,26.004708）相距约 5 米的那个点，与东区宿舍、五期宿舍同属校园东侧。
     命名与其余餐厅一致（「XX餐厅」），类别 `food`。 */
  { name: "五期餐厅",      type: "food", lng: 119.452159, lat: 26.004726 },

  /* ---------- 其他：图书馆 / 游泳馆 / 操场 / 便利店 ---------- */
  { name: "图书馆",        type: "other", lng: 119.447093, lat: 26.003678 },  // AMap POI 阳光学院图书馆
  { name: "游泳馆",        type: "other", lng: 119.449043, lat: 26.003699 },  // AMap POI 阳光学院-游泳馆
  /* 操场（田径场 / 足球场）：高德**没有**这个 POI（`place/text` 与 `inputtips`
     都搜不到），坐标由坐标拾取器点选后用卫星图核对，落点在跑道西侧直道上。 */
  { name: "操场",          type: "other", lng: 119.448641, lat: 26.00256 },
  { name: "7-ELEVEn",     type: "other", lng: 119.447614, lat: 26.004209 },  // AMap POI（西区宿舍篮球场东侧）

  /* ---------- 停车场 ---------- */
  { name: "图书馆停车场",   type: "parking", lng: 119.446675, lat: 26.003525 },  // AMap POI
  { name: "北门停车场",     type: "parking", lng: 119.450969, lat: 26.006022 },  // AMap POI（校园北门旁）
  { name: "马尾校区停车场", type: "parking", lng: 119.446633, lat: 26.002537 },  // AMap POI（弘毅路与集贤西路交叉口）
  { name: "东区停车场",     type: "parking", lng: 119.449464, lat: 26.005879 },  // AMap POI
  { name: "西区停车场",     type: "parking", lng: 119.448122, lat: 26.003208 },  // AMap POI
  { name: "南区停车场",     type: "parking", lng: 119.452093, lat: 26.002834 },  // AMap POI

  /* ---------- 校区范围 ---------- */
  { name: "阳光学院(福州校本部)", type: "campus", lng: 119.447887, lat: 26.005046 },  // AMap POI 校本部

  /* ---------- 附属学校（是阳光实验学校，不是本校教学楼） ---------- */
  { name: "阳光实验学校初中部", type: "school", lng: 119.446236, lat: 26.007435 },  // AMap POI
  { name: "阳光实验学校高中部", type: "school", lng: 119.445152, lat: 26.008046 },  // AMap POI
];

/** 点位类型的中文名 + 地图标记配色。
 *  前端按这个表渲染筛选按钮和标记颜色，改配色只动这里。
 *  ⚠️ 顺序 = 筛选按钮的显示顺序，把最常用的教学楼放第一个。
 *
 *  `off: true` = **本类型不在导航页展示**（筛选按钮 / 地图标点 / 右侧点位列表
 *  三处一律不出现）。这不是「数据不要了」—— 坐标仍留在 CAMPUS_PLACES 里，
 *  只是导航页把它们藏起来。当前关闭四类：
 *
 *      facility 院系场馆 · parking 停车场 · campus 校区 · school 附属学校
 *
 *  ⚠️ 想恢复某一类，把它的 off 去掉即可，不用动 nav.js。
 *  ⚠️ nav.js 实际用的是 `POIS`（= 这里过滤掉 off 之后的点位全集），
 *     所以「输入起点/终点能不能解析到」也跟着一起关掉了。 */
const PLACE_TYPES = {
  teaching: { label: "教学楼",   color: "#2b6cb0" },  // 蓝
  facility: { label: "院系场馆", color: "#2c7a7b", off: true },  // 青
  dorm:     { label: "宿舍",     color: "#2f855a" },  // 绿
  food:     { label: "食堂餐饮", color: "#c05621" },  // 橙
  other:    { label: "其他",     color: "#0d9488" },  // 青绿（图书馆 / 游泳馆 / 操场 / 7-ELEVEn）
  parking:  { label: "停车场",   color: "#718096", off: true },  // 灰
  campus:   { label: "校区",     color: "#1a202c", off: true },  // 近黑
  school:   { label: "附属学校", color: "#97266d", off: true },  // 玫红
};

/**
 * 按点位名查点位（含非教学楼：宿舍/食堂/快递点等）。
 *
 * 与 findBuilding 一样容忍尾部的分区/教室号：
 * 「知行楼A」「知行楼B203」「图书馆 2 楼」都能落到对应点位上。
 * 教学楼在 CAMPUS_PLACES 里也在，所以这里查得到的东西比 findBuilding 多。
 *
 * @param {string} name
 * @returns {CampusPlace|undefined}
 */
function findPlace(name) {
  const raw = String(name || "").trim();
  if (!raw) return undefined;

  const exact = CAMPUS_PLACES.find(p => p.name === raw);
  if (exact) return exact;

  const strip = t => t
    .replace(/[（(【\[].*?[)）】\]]/g, "")
    .replace(/[A-Za-z]\d*$/g, "")
    .replace(/[0-9]+$/g, "")
    .replace(/[-\s·]+$/g, "")
    .trim();
  const s = strip(raw);
  if (s && s !== raw) {
    const hit = CAMPUS_PLACES.find(p => p.name === s);
    if (hit) return hit;
  }

  // 输入里包含点位名（长的优先，避免短的先命中）
  const contains = [...CAMPUS_PLACES]
    .sort((a, b) => b.name.length - a.name.length)
    .find(p => raw.includes(p.name) && p.name.length >= 2);
  if (contains) return contains;

  // 反过来：输入是点位名的简写（「图书馆停车场」->「图书馆」这种不许，
  // 但「图书馆」省成「图书馆」本来就不是简写）—— 这里按「楼/栋」结尾做简写还原
  if (raw.length >= 2) {
    const short = CAMPUS_PLACES.find(p => p.name.replace(/[楼栋]$/, "") === raw);
    if (short) return short;
  }
  return undefined;
}

/**
 * 学校基础信息。
 * @type {{name: string, center: LngLat, city: string, adcode: string}}
 */
const CAMPUS = {
  name: "阳光学院（福州校本部）",
  center: [119.447887, 26.005046],   // 高德 POI「阳光学院(福州校本部)」实际坐标
  city: "福州",
  adcode: "350100",
};

/* ---------- 教学楼 / 作息查询小工具（planner.js 与 nav.js 共用）---------- */

/**
 * 按楼名查教学楼。
 *
 * 支持「楼名 + 分区/编号」的容错 —— 学校课表里同一栋楼常写成好几个名字：
 *     知行楼、知行楼A、知行楼B、知行楼A203
 * 它们指的是**同一栋楼**：A/B 只是这栋楼里的分区（东翼/西翼、A 座/B 座），
 * 坐标是同一个点。用户手输或识别残留的分区字母，都该落到知行楼上 ——
 * 「知行楼A 和知行楼B 都统一先导航到地图知行楼」就是这个意思。
 *
 * 匹配顺序（严 -> 松）：
 *   1. 完全相等                  -> 知行楼
 *   2. 去掉尾部分区/编号后相等    -> 知行楼B203 -> 知行楼
 *   3. 楼名包含在输入里          -> 知行楼B栋 -> 知行楼
 *
 * ⚠️ 只做「输入包含楼名」这种单向匹配，不做反向（别让「知行」把「知行楼」匹掉），
 *    否则传进来一个「楼」字就会把第一栋楼配上。
 *
 * @param {string} name
 * @returns {Building|undefined}
 */
function findBuilding(name) {
  const raw = String(name || "").trim();
  if (!raw) return undefined;

  // 1) 完全相等
  const exact = BUILDINGS.find(b => b.name === raw);
  if (exact) return exact;

  // 2) 去掉尾部的分区字母 / 教室号 / 括号备注，再比一次
  //    「知行楼B203」「知行楼A」「知行楼（北）」「知行楼-2」-> 「知行楼」
  const stripped = raw
    .replace(/[（(【\[].*?[)）】\]]/g, "")     // 去掉括号备注
    .replace(/[A-Za-z]\d*$/g, "")            // 去掉尾部的 A / B203 / A12
    .replace(/[0-9]+$/g, "")                 // 去掉纯数字尾巴 301
    .replace(/[-\s·]+$/g, "")                // 收尾的连字符 / 空格
    .trim();
  if (stripped && stripped !== raw) {
    const hit = BUILDINGS.find(b => b.name === stripped);
    if (hit) return hit;
  }

  // 3) 楼名出现在输入里（「知行楼B栋」「去知行楼上课」）
  //    按名字长的优先，避免「博文」被更短的规则先命中
  const contains = [...BUILDINGS]
    .sort((a, b) => b.name.length - a.name.length)
    .find(b => raw.includes(b.name));
  if (contains) return contains;

  // 4) 反过来：输入是楼名的简写（「博文」-> 「博文楼」「知行」-> 「知行楼」）
  //    课表截图里模型常把「楼」字漏掉，用户手输也爱省这个字。
  //    只认 ≥2 字的简写，「楼」这种单字太泛了不认。
  if (raw.length >= 2) {
    const short = BUILDINGS.find(b => b.name.replace(/[楼栋]$/, "") === raw);
    if (short) return short;
  }
  return undefined;
}

/**
 * 判断一个「地点文字」是不是线上/虚拟教室（不是实体楼）。
 *
 * 课表里 @ 后面除了楼名，还常见「线上 / 网课 / 慕课 / 网络工 / 腾讯会议」这类 ——
 * 它们没有坐标、不能导航，作息也谈不上南北区。前端用它：
 *   · 核对表里给这类行标一个「南」字，说明时间按南区（基准）作息算；
 *   · 课程卡片上不显示「导航」按钮。
 *
 * 口径与后端 `planner.py::_is_virtual_place()` **保持一致**，改一处要同步另一处，
 * 否则会出现「后端当线上课、前端当教学楼」这种自相矛盾的状态。
 *
 * @param {string} text
 * @returns {boolean}
 */
function isVirtualPlaceName(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  const VIRTUAL = ["线上", "网课", "慕课", "网络", "云课堂", "腾讯会议", "钉钉",
                   "直播", "录播", "雨课堂", "学习通", "超星", "智慧树", "虚拟"];
  return VIRTUAL.some(k => t.includes(k));
}

/**
 * 按楼名查所属校区。
 * @param {string} name
 * @returns {"south"|"north"|undefined}
 */
function regionOfBuilding(name) {
  const b = findBuilding(name);
  return b ? b.region : undefined;
}

/**
 * 某节次在指定校区的时间段；该校区没单独设时间就回落到基准（南区）时间。
 * 「根据教学楼把第一列的节次时间改成那个区上课时间」就靠它。
 * @param {TimeSlot} slot
 * @param {"south"|"north"} region
 * @returns {{start: string, end: string}}
 */
function slotTimeFor(slot, region) {
  if (slot.region === "split") {
    const s = region === "north" ? slot.start_north : slot.start_south;
    const e = region === "north" ? slot.end_north : slot.end_south;
    if (s && e) return { start: s, end: e };
  }
  return { start: slot.start, end: slot.end };
}

/**
 * 日期格式化："2026-09-22" -> "9/22"
 * @param {string} iso
 * @returns {string}
 */
function fmtMD(iso) {
  const parts = String(iso).split("-");
  return Number(parts[1]) + "/" + Number(parts[2]);
}
