-- =============================================================
-- 校园 AI 助手 · 数据库建表脚本（MySQL 8.0）
-- 可在 Navicat Premium 中直接执行；也可由 SQLAlchemy 自动建表
-- 空白时间表按 6:00 - 23:00，粒度 = 早晚整点 + 白天按阳光学院节次
-- =============================================================

CREATE DATABASE IF NOT EXISTS campus_ai
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE campus_ai;

-- -------------------------------------------------------------
-- 1. 用户表
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  username        VARCHAR(64)  NOT NULL,
  hashed_password VARCHAR(128) NOT NULL,
  nickname        VARCHAR(64)  DEFAULT '',
  created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户账号';

-- -------------------------------------------------------------
-- 2. 空白时间表 · 时间槽（全局共用一套，6:00-23:00）
--    period_type: hour=整点段  class=节次
--    region:      both=南北区一致  split=南北区作息不同
--    start_south/end_south/start_north/end_north: 分区作息时间（空=同基准）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS time_slots (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  order_no    INT         NOT NULL COMMENT '排序序号，决定课表排列',
  label       VARCHAR(32) NOT NULL COMMENT '显示名，如 一节 / 07:00',
  start_time  TIME        NOT NULL COMMENT '基准开始（南区）',
  end_time    TIME        NOT NULL COMMENT '基准结束（南区）',
  period_type VARCHAR(16) DEFAULT 'hour' COMMENT 'hour / class',
  region      VARCHAR(8)  DEFAULT 'both' COMMENT 'both / split',
  start_south VARCHAR(8)  DEFAULT '' COMMENT '南区开始，空=同基准',
  end_south   VARCHAR(8)  DEFAULT '',
  start_north VARCHAR(8)  DEFAULT '' COMMENT '北区开始，空=同基准',
  end_north   VARCHAR(8)  DEFAULT '',
  UNIQUE KEY uq_order_no (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='空白时间表槽位 6:00-23:00';

-- 初始化 6:00-23:00 时间槽（幂等：先清空再插入）
-- >>> 下面 split 行的南北区时间是【示例数据】，请按学校真实作息核对修改 <<<
DELETE FROM time_slots;
INSERT INTO time_slots (order_no, label, start_time, end_time, period_type, region, start_south, end_south, start_north, end_north) VALUES
  (1,  '06:00',   '06:00', '07:00', 'hour',  'both',  '',      '',      '',      ''),
  (2,  '07:00',   '07:00', '08:00', 'hour',  'both',  '',      '',      '',      ''),
  (3,  '一节',  '08:10', '08:55', 'class', 'split', '08:10', '08:55', '08:00', '08:45'),
  (4,  '二节',  '09:00', '09:45', 'class', 'split', '09:00', '09:45', '08:50', '09:35'),
  (5,  '三节',  '10:05', '10:50', 'class', 'split', '10:05', '10:50', '09:55', '10:40'),
  (6,  '四节',  '10:55', '11:40', 'class', 'split', '10:55', '11:40', '10:45', '11:30'),
  (7,  '12:00',   '12:00', '13:00', 'hour',  'both',  '',      '',      '',      ''),
  (8,  '13:00',   '13:00', '14:00', 'hour',  'both',  '',      '',      '',      ''),
  (9,  '五节',  '14:00', '14:45', 'class', 'split', '14:00', '14:45', '13:55', '14:40'),
  (10, '六节',  '14:50', '15:35', 'class', 'split', '14:50', '15:35', '14:45', '15:30'),
  (11, '七节',  '15:55', '16:40', 'class', 'split', '15:55', '16:40', '15:50', '16:35'),
  (12, '八节',  '16:45', '17:30', 'class', 'split', '16:45', '17:30', '16:40', '17:25'),
  (13, '18:00',   '18:00', '19:00', 'hour',  'both',  '',      '',      '',      ''),
  (14, '九节',  '18:30', '19:15', 'class', 'split', '18:30', '19:15', '18:25', '19:10'),
  (15, '十节',  '19:20', '20:05', 'class', 'split', '19:20', '20:05', '19:15', '20:00'),
  (16, '十一节', '20:10', '20:55', 'class', 'split', '20:10', '20:55', '20:05', '20:50'),
  (17, '21:00',   '21:00', '22:00', 'hour',  'both',  '',      '',      '',      ''),
  (18, '22:00',   '22:00', '23:00', 'hour',  'both',  '',      '',      '',      '');

-- -------------------------------------------------------------
-- 3. 课程表（按 user_id 关联账号，逐条存储）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  user_id     INT         NOT NULL,
  date        CHAR(10)    NOT NULL COMMENT '真实日期 YYYY-MM-DD，三周视图按它区分',
  type        VARCHAR(8)  DEFAULT 'course' COMMENT 'course=课程 / activity=活动',
  slot_order  INT         NULL DEFAULT 0 COMMENT '对应 time_slots.order_no；活动可为 0',
  start_time  VARCHAR(8)  DEFAULT '',
  end_time    VARCHAR(8)  DEFAULT '',
  name        VARCHAR(64) NOT NULL,
  teacher     VARCHAR(32) DEFAULT '',
  location    VARCHAR(64) DEFAULT '' COMMENT '教室，如 博文楼-301',
  building    VARCHAR(32) DEFAULT '' COMMENT '教学楼，如 博文楼',
  lng         VARCHAR(24) DEFAULT '' COMMENT '由教学楼自动带出，用户不手填',
  lat         VARCHAR(24) DEFAULT '',
  day         TINYINT     NULL COMMENT '旧字段，仅兼容历史数据',
  created_at  DATETIME    DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user_date (user_id, date),
  KEY idx_slot (slot_order),
  UNIQUE KEY uq_user_date_slot (user_id, date, slot_order),
  CONSTRAINT fk_course_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户课程 / 活动（关联账号）';

-- -------------------------------------------------------------
-- 4. 情绪对话 / 5. 情绪测评
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT  NOT NULL,
  messages   JSON NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_chat_user (user_id),
  CONSTRAINT fk_chat_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='情绪陪伴对话记录';

CREATE TABLE IF NOT EXISTS assessments (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT  NOT NULL,
  result     JSON NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_assess_user (user_id),
  CONSTRAINT fk_assess_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='情绪测评结果';

-- -------------------------------------------------------------
-- 常用查询示例
-- -------------------------------------------------------------
-- 查某用户某一天的课 / 活动（按节次排序）：
-- SELECT c.*, s.label FROM courses c
--   LEFT JOIN time_slots s ON s.order_no = c.slot_order
--   WHERE c.user_id = 1 AND c.date = '2026-09-24' ORDER BY s.order_no;
--
-- 查某用户本周（周一~周日）的全部安排：
-- SELECT * FROM courses
--   WHERE user_id = 1 AND date BETWEEN '2026-09-22' AND '2026-09-28'
--   ORDER BY date, slot_order;
--
-- 查空白时间表全部槽位：
-- SELECT * FROM time_slots ORDER BY order_no;
--
-- 老库升级（把旧的 day 数据补成本周日期；MySQL 写法）：
-- ALTER TABLE courses ADD COLUMN date CHAR(10) NULL, ADD COLUMN type VARCHAR(8) DEFAULT 'course';
-- UPDATE courses SET date = DATE_FORMAT(DATE_ADD(CURDATE(),
--   INTERVAL (day - WEEKDAY(CURDATE()) - 1) DAY), '%Y-%m-%d') WHERE date IS NULL;
--
-- 老库升级（改为「一个时间点只放一条」的唯一键；如原有同名同槽数据需先清理）：
-- ALTER TABLE courses DROP INDEX uq_user_date_slot_name;
-- ALTER TABLE courses ADD UNIQUE KEY uq_user_date_slot (user_id, date, slot_order);
--
-- 老库升级（节次显示名去掉「第」字）：
-- UPDATE time_slots SET label = REPLACE(label, '第', '') WHERE period_type = 'class';
