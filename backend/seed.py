"""
空白时间表种子数据：6:00 - 23:00。

粒度 = 早晚整点 + 白天按阳光学院（福州校本部）真实节次。
- 06:00-08:00：每 1 小时（早间整点）
- 08:10-17:30：按阳光学院第一~第八节（南区/北区时间一致的部分）
- 18:30-20:55：按第九~第十一节
- 21:00-23:00：每 1 小时（晚间整点）

南北区作息：同一节次在南区、北区的上下课时间可能不同。下面用 start_south/
end_south/start_north/end_north 描述；两区一致的节次留空，前端回落到基准时间。

>>> 重要：下面标注 TODO 的南北区时间差是【示例数据】，不是真实校历！ <<<
>>> 请按学校实际的南北区作息表核对修改（改这里 + frontend/js/campus.js   <<<
>>> + vue-app/src/utils/campus.ts 三处，保持完全一致）。                      <<<

前端与数据库共用这份定义，保证两侧一致。
"""

# (order_no, label, start, end, period_type, region, start_south, end_south, start_north, end_north)
DEFAULT_TIME_SLOTS = [
    # ---- 早间（整点，南北区一致）----
    (1, "06:00", "06:00", "07:00", "hour", "both", "", "", "", ""),
    (2, "07:00", "07:00", "08:00", "hour", "both", "", "", "", ""),
    # ---- 上午（阳光学院节次；TODO 示例：北区比南区早 10 分钟）----
    (3, "第一节", "08:10", "08:55", "class", "split", "08:10", "08:55", "08:00", "08:45"),
    (4, "第二节", "09:00", "09:45", "class", "split", "09:00", "09:45", "08:50", "09:35"),
    (5, "第三节", "10:05", "10:50", "class", "split", "10:05", "10:50", "09:55", "10:40"),
    (6, "第四节", "10:55", "11:40", "class", "split", "10:55", "11:40", "10:45", "11:30"),
    # ---- 午间（整点，南北区一致）----
    (7, "12:00", "12:00", "13:00", "hour", "both", "", "", "", ""),
    (8, "13:00", "13:00", "14:00", "hour", "both", "", "", "", ""),
    # ---- 下午（TODO 示例：北区比南区早 5 分钟）----
    (9, "第五节", "14:00", "14:45", "class", "split", "14:00", "14:45", "13:55", "14:40"),
    (10, "第六节", "14:50", "15:35", "class", "split", "14:50", "15:35", "14:45", "15:30"),
    (11, "第七节", "15:55", "16:40", "class", "split", "15:55", "16:40", "15:50", "16:35"),
    (12, "第八节", "16:45", "17:30", "class", "split", "16:45", "17:30", "16:40", "17:25"),
    # ---- 傍晚（整点，南北区一致）----
    (13, "18:00", "18:00", "19:00", "hour", "both", "", "", "", ""),
    # ---- 晚上（TODO 示例：北区比南区早 5 分钟）----
    (14, "第九节", "18:30", "19:15", "class", "split", "18:30", "19:15", "18:25", "19:10"),
    (15, "第十节", "19:20", "20:05", "class", "split", "19:20", "20:05", "19:15", "20:00"),
    (16, "第十一节", "20:10", "20:55", "class", "split", "20:10", "20:55", "20:05", "20:50"),
    # ---- 深夜（整点，南北区一致）----
    (17, "21:00", "21:00", "22:00", "hour", "both", "", "", "", ""),
    (18, "22:00", "22:00", "23:00", "hour", "both", "", "", "", ""),
]


def seed_time_slots(db):
    """幂等地写入时间槽（已存在则跳过）。"""
    from datetime import time as dtime
    from models.orm import TimeSlot
    if db.query(TimeSlot).count() > 0:
        return 0
    for (order_no, label, start, end, ptype, region,
         s_south, e_south, s_north, e_north) in DEFAULT_TIME_SLOTS:
        db.add(TimeSlot(
            order_no=order_no, label=label,
            start_time=dtime.fromisoformat(start),   # "06:00" -> datetime.time
            end_time=dtime.fromisoformat(end),
            period_type=ptype, region=region,
            start_south=s_south, end_south=e_south,
            start_north=s_north, end_north=e_north))
    db.commit()
    return len(DEFAULT_TIME_SLOTS)


def polish_slot_labels(db):
    """把老库里不带「第」字的节次名补成「第一节」这种写法（幂等）。

    显示名统一为「第一节」「第二节」…，这里负责把已经存在的旧库改过来，
    新库由 DEFAULT_TIME_SLOTS 直接写入正确值。
    只处理以「节」结尾且不以「第」开头的标签，避免重复加前缀。
    """
    from models.orm import TimeSlot
    n = 0
    for s in db.query(TimeSlot).filter(
        TimeSlot.label.like("%节"), ~TimeSlot.label.like("第%")
    ).all():
        s.label = "第" + s.label
        n += 1
    if n:
        db.commit()
    return n


def migrate_course_dates(db):
    """把旧的「day 星期几」课程数据补上 date 字段（本周对应的那一天）。

    早期版本用 day(1..7) 存星期，后来改成按真实日期存（date），以支持三周视图。
    这里把 date 为空的旧行按「本周对应星期几」回填，迁移完 day 置空即可。
    幂等：没有需要迁移的行时直接返回 0。
    """
    from datetime import date as ddate, timedelta
    from models.orm import Course

    stale = db.query(Course).filter((Course.date == None) | (Course.date == "")).all()  # noqa: E711
    if not stale:
        return 0
    today = ddate.today()
    monday = today - timedelta(days=today.weekday())   # 本周一
    for c in stale:
        d = c.day if c.day else 1                       # 1=周一
        c.date = (monday + timedelta(days=max(0, min(6, d - 1)))).isoformat()
        if not c.type:
            c.type = "course"
    db.commit()
    return len(stale)


def seed_admin(db):
    """幂等地创建管理员账号（用户名 / 密码取 config.ADMIN_USERNAME / ADMIN_PASSWORD）。

    ★ 只在**账号不存在**时创建；已存在就只把 `is_admin` 补成 True，**不动密码**。
      为什么不动密码：管理员很可能已经把默认密码改掉了，如果每次启动都按
      config 重写一遍，等于「重启即重置密码」—— 那是漏洞，不是便利。

    ★ 为什么管理员只能这样产生、页面上没有「注册成管理员」：
      只要有一个提权入口，统计页里的全站数据就等于对外开放。
      想改密码走「我的 → 修改密码」；想换账号改 .env 里的 ADMIN_USERNAME，
      然后删掉旧管理员账号（或用新用户名重启一次）。

    @returns {int} 1 = 新建或升级成功；0 = 已就绪 / 配置为空；-1 = 同名冲突未提权
    """
    from config import settings
    from models.orm import User
    from routers.auth import hash_pwd      # 懒导入：避免 seed 与 routers 循环引用

    name = (getattr(settings, "ADMIN_USERNAME", "") or "").strip()
    pwd = (getattr(settings, "ADMIN_PASSWORD", "") or "").strip()
    if not name or not pwd:
        return 0                            # 配空 = 不建管理员（关掉后台入口）

    u = db.query(User).filter(User.username == name).first()
    if u:
        if bool(u.is_admin):
            return 0
        # ★ 绝不「见到同名账号就提权」：这个项目曾经部署在公网，
        #   别人完全可能先注册了一个叫 admin 的普通账号。升级包之后
        #   如果无条件把它变成管理员，等于把后台送给注册了这个名字的人。
        #   所以**只有密码和配置一致时**才升级（也就是「这本来就是当初
        #   用默认密码建的那个管理员，只是老版本没有 is_admin 字段」）。
        if u.hashed_password == hash_pwd(pwd):
            u.is_admin = True               # 老库里的同名管理员升级
            db.commit()
            return 1
        print("[警告] 已存在名为 %s 的普通账号，且密码与 ADMIN_PASSWORD 不一致，"
              "**未**自动提权。要用它当管理员，请先在「我的」里改成配置里的密码，"
              "或删掉该账号后重启。" % name, flush=True)
        return -1
    db.add(User(username=name, hashed_password=hash_pwd(pwd),
                nickname="管理员", is_admin=True))
    db.commit()
    return 1
