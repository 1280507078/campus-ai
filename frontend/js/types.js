/**
 * @file 全局类型定义（JSDoc）。
 * 仅用于 VS Code 智能提示与类型检查，运行时无副作用。
 * 需要类型检查时可在文件顶部加 `// @ts-check`，或配置 jsconfig.json。
 */

/**
 * 用户信息。
 * @typedef {Object} User
 * @property {number} id          用户 ID
 * @property {string} username    用户名
 * @property {string} [nickname]  昵称
 */

/**
 * 时间槽（空白时间表的一格，6:00-23:00）。
 * @typedef {Object} TimeSlot
 * @property {number} order_no      排序序号
 * @property {string} label         显示名（如 "第一节" / "07:00"）
 * @property {string} start         基准开始时间 "HH:mm"（南区）
 * @property {string} end           基准结束时间 "HH:mm"
 * @property {"hour"|"class"} period_type  整点段 / 节次
 * @property {"both"|"split"} [region]  both=南北区一致 / split=分区作息
 * @property {string} [start_south] 南区开始（空=同 start）
 * @property {string} [end_south]   南区结束
 * @property {string} [start_north] 北区开始
 * @property {string} [end_north]   北区结束
 */

/**
 * 一条课程或活动。
 * @typedef {Object} Course
 * @property {string} _id            前端本地 ID
 * @property {number} [id]           后端主键
 * @property {string} date           真实日期 "YYYY-MM-DD"（三周视图按它区分）
 * @property {"course"|"activity"} type  课程 / 活动
 * @property {number} slot_order     对应 TimeSlot.order_no；活动可为 0
 * @property {string} start          开始 "HH:mm"
 * @property {string} end            结束 "HH:mm"
 * @property {string} name           课程 / 活动名
 * @property {string} [teacher]      教师（活动为空）
 * @property {string} [location]     教室，如 "博文楼-301"
 * @property {string} [building]     教学楼，如 "博文楼"（活动为空）
 * @property {number|null} [lng]     经度（由教学楼自动带出）
 * @property {number|null} [lat]     纬度
 */

/**
 * 教学楼。
 * @typedef {Object} Building
 * @property {string} name                    楼名
 * @property {"south"|"north"} region         所在校区
 * @property {number} lng                     经度
 * @property {number} lat                     纬度
 */

/**
 * 校园点位类型。
 * - teaching 教学楼（严格 = BUILDINGS 那 10 栋）
 * - facility 院系/场馆           - dorm 宿舍/生活区
 * - food 食堂餐饮               - other 其他（图书馆 / 游泳馆）
 * - parking 停车场              - campus 校区范围
 * - school 附属学校
 * @typedef {"teaching"|"facility"|"dorm"|"food"|"other"|"parking"|"campus"|"school"} PlaceType
 */

/**
 * 校园点位（比 Building 宽）：教学楼之外还包括宿舍、食堂、图书馆等，
 * 它们都能作为导航目的地，但不一定出现在课表里。
 * @typedef {Object} CampusPlace
 * @property {string} name                    点位名
 * @property {PlaceType} type                 点位类型（决定标记配色与筛选）
 * @property {"south"|"north"} [region]       所在校区（仅教学楼有）
 * @property {number} lng                     经度
 * @property {number} lat                     纬度
 */

/**
 * 楼栋与时间槽共享的经纬度坐标。
 * @typedef {[number, number]} LngLat  [经度, 纬度]
 */

/**
 * AI 对话消息。
 * @typedef {Object} ChatMessage
 * @property {"user"|"assistant"|"system"} role
 * @property {string} content
 */
