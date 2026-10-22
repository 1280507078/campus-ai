"""ORM 模型：用户、时间槽、课程、情绪对话、测评结果。"""
from sqlalchemy import (Column, Integer, String, Text, ForeignKey, DateTime,
                        JSON, Time, Boolean, UniqueConstraint)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    hashed_password = Column(String(128), nullable=False)
    nickname = Column(String(64), default="")
    # 管理员标记。**不是**「权限等级」那一套：只有 0/1，用来决定能不能进
    # /api/admin/* 与 admin.html。默认 False，普通注册永远是普通用户 ——
    # 页面上也没有任何「把自己变成管理员」的入口，只能由 seed_admin 在
    # 启动时按配置创建（否则谁都能提权）。
    is_admin = Column(Boolean, default=False, nullable=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow)

    courses = relationship("Course", back_populates="owner", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", cascade="all, delete-orphan",
                                 passive_deletes=True)
    assessments = relationship("Assessment", cascade="all, delete-orphan",
                               passive_deletes=True)


class TimeSlot(Base):
    """
    空白时间表的时间槽（全局共用一套，6:00-23:00）。
    粒度 = 早晚整点 + 白天按阳光学院真实节次。
    order_no 决定在课表格子里的排列顺序。

    南北区作息差异（start_south/end_south/start_north/end_north）：同一节次在南区、
    北区的上下课时间可能不同。region="both" 表示两区一致，此时四列留空，前端用
    start_time/end_time 即可。
    """
    __tablename__ = "time_slots"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(Integer, unique=True, nullable=False)   # 排序序号 1..N
    label = Column(String(32), nullable=False)                # 显示名，如 "第一节" / "07:00"
    start_time = Column(Time, nullable=False)                 # 基准开始时间（南区）
    end_time = Column(Time, nullable=False)                   # 基准结束时间（南区）
    period_type = Column(String(16), default="hour")          # hour / class
    region = Column(String(8), default="both")                # both / south / north（南北区作息不同）
    start_south = Column(String(8), default="")               # 南区开始 "08:10"（空=同基准）
    end_south = Column(String(8), default="")                 # 南区结束
    start_north = Column(String(8), default="")               # 北区开始（空=同基准）
    end_north = Column(String(8), default="")                 # 北区结束


class Course(Base):
    """用户课程 / 活动：按 user_id 关联账号，逐条存储。

    时间维度用真实日期 date（"YYYY-MM-DD"）而不是「星期几」，因为前端要同时浏览
    本周及后两周共三周，同一个「周三第三节」在三周里是三组不同的数据。
    type 区分「课程」（course，有节次/教学楼、可导航）与「活动」（activity，
    只记名称与时间点，不参与课程作息与导航）。
    """
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    date = Column(String(10), index=True, nullable=False)   # "2026-09-22"
    type = Column(String(8), default="course")              # course / activity
    slot_order = Column(Integer, index=True, default=0)     # 对应 time_slots.order_no；活动可为 0
    start_time = Column(String(8), default="")              # "08:10"
    end_time = Column(String(8), default="")                # "08:55"
    name = Column(String(64), nullable=False)
    teacher = Column(String(32), default="")
    location = Column(String(64), default="")               # 教室，如 "博文楼-301"
    building = Column(String(32), default="")               # 教学楼，如 "博文楼"
    # 上课周次（**原始中文串**，如 "2-3,6-8周(全部) 19周(全部)"）。
    #
    # 为什么存原始串而不是解析后的数组：
    #   1. 空串天然等于「每周都上」—— 手动添加的课、历史数据都不用回填；
    #   2. 解析规则（各校写法五花八门）以后大概率还要改，存原始串才能改；
    #      存成数组就把「教务原文」永久丢了，解析错了无从追溯。
    #   3. 解析只在前端渲染时做一次（纯正则，几十分之一毫秒），不构成性能问题。
    # 长度 128 足够：实测最长的一条约 60 字符。
    weeks = Column(String(128), default="")
    # 经纬度保留（导航用），但表单不再让用户手填，由教学楼自动查表赋值
    lng = Column(String(24), default="")
    lat = Column(String(24), default="")
    day = Column(Integer, nullable=True)                    # 旧字段，仅兼容历史数据，新代码不再写
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="courses")

    # 同一用户、同一天、同一节次下**只能有一条**（课程或活动都算），
    # 即「一个时间点只放一个安排」；"沿用上周"也靠它判重。
    __table_args__ = (
        UniqueConstraint("user_id", "date", "slot_order", name="uq_user_date_slot"),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    messages = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class Assessment(Base):
    __tablename__ = "assessments"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    result = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    """意见反馈（用户提交，管理员在后台看）。

    设计上的三个刻意选择：

    1. **只收「类型 + 正文 + 联系方式」**，不做工单状态流转、不做回复线程。
       这是作品里的一条反馈通道，不是客服系统；加上「已处理/处理中」
       就得有人天天维护，没人维护时那个状态比没有更误导。
    2. `content` 用 Text 且限制 1000 字（在 routers/feedback.py 里校验）——
       限制放接口层，库里不设长度，将来放宽不用改表。
    3. `page` 记「从哪个页面提交」（account / emotion / plan …）。
       为什么要它：用户说「这个页面有问题」时，光看正文常常不知道是哪个页面。
       前端传的是**页面标识**而不是完整 URL，避免把域名、查询串（可能带 token）
       一起存进库。
    """
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind = Column(String(16), default="suggest")     # suggest / bug / ux / other
    content = Column(Text, nullable=False)
    contact = Column(String(64), default="")         # 可选，用户自己留的邮箱/微信
    page = Column(String(32), default="")            # 页面标识，不是完整 URL
    # 管理员回复。空串 = 还没回复（不用额外的 status 字段：有没有回复
    # 就是唯一的状态，多一个字段就多一个能对不上的地方）。
    reply = Column(Text, default="")
    replied_at = Column(DateTime, nullable=True)
    replied_by = Column(Integer, nullable=True)      # 回复的管理员 user_id，便于追责
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
