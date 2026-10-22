"""全局配置：所有第三方 Key 先留空，部署前在 .env 或环境变量中填入。"""
from pathlib import Path

from pydantic_settings import BaseSettings

# 项目 backend 目录的绝对路径。用它来定位 SQLite 文件与 .env，
# 这样无论从哪个工作目录启动（双击 bat / 别处 cmd / IDE），数据库
# 都落在同一个地方，不会出现「换个目录就找不到老数据」的问题。
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # ===== 智谱 GLM（学业计划 / 情绪陪伴对话）=====
    GLM_API_KEY: str = ""                       # TODO: 填入智谱 API Key
    GLM_MODEL: str = "glm-4-flash"
    GLM_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    # 课表截图识别用的视觉模型。glm-4v-flash 免费，识别课表足够用；
    # 想要更准可在 .env 覆盖成 glm-4v / glm-4v-plus（按量计费）。
    GLM_VISION_MODEL: str = "glm-4v-flash"

    # ===== Qdrant Cloud（情绪陪伴长期记忆检索）=====
    QDRANT_URL: str = ""                        # TODO: 填入 Qdrant Cloud 地址
    QDRANT_API_KEY: str = ""                    # TODO: 填入 Qdrant Cloud Key

    # ===== 高德（天气用 Web 服务 Key，地图用 JS API Key）=====
    AMAP_WEB_KEY: str = ""                      # TODO: 高德 Web 服务 Key（后端天气/地理编码）
    AMAP_JS_KEY: str = ""                       # TODO: 高德 JS API Key（前端地图，写在前端 config.js）
    AMAP_SECURITY_CODE: str = ""                # TODO: 高德 JS API 安全密钥（前端用）

    # ===== 数据库 =====
    # 默认 SQLite：零配置，解压即可运行。数据库文件固定放在 backend/ 目录下
    # （绝对路径），不随启动时的工作目录变化。
    # 想改用 MySQL（Navicat 管理）时，在 backend/.env 里覆盖成下面这行即可：
    #   DATABASE_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/campus_ai?charset=utf8mb4
    DATABASE_URL: str = ""

    # ===== 服务端口（供启动脚本读取，改端口时两边保持一致）=====
    API_PORT: int = 8000
    WEB_PORT: int = 5500

    # ===== 鉴权 =====
    # 注意：密钥长度建议 >= 32 字节，否则 PyJWT 会给出 InsecureKeyLengthWarning。
    # 部署前请在 .env 里改成自己的随机串，例如：
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    SECRET_KEY: str = "campus-ai-assistant-dev-secret-key-change-me-in-production-2026"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 天

    # ---- 管理员账号（seed.py 的 seed_admin 在启动时按这两个值建号）----
    # 为什么要写进配置而不是硬编码在 seed.py 里：改密码应该改配置，不该改代码；
    # 而且演示机、自己机器、线上可以各用各的。
    # 配空任意一个 = **不建管理员**（后台入口自动关掉），不会留下空口令账号。
    # 在 .env 里写：
    #       ADMIN_USERNAME=admin
    #       ADMIN_PASSWORD=换成你自己的
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "zrc666"

    class Config:
        env_file = str(BASE_DIR / ".env")
        extra = "ignore"

    def resolved_database_url(self) -> str:
        """把空的 / 相对的 DATABASE_URL 解析成绝对路径的 SQLite 地址。"""
        url = (self.DATABASE_URL or "").strip()
        if not url:
            return "sqlite:///" + (BASE_DIR / "campus_ai.db").as_posix()
        # 相对 sqlite 路径 -> 基于 backend 目录展开，避免受工作目录影响
        prefix = "sqlite:///"
        if url.startswith(prefix):
            raw = url[len(prefix):]
            if raw and not raw.startswith("/") and ":" not in raw[:3]:
                return prefix + (BASE_DIR / raw).as_posix()
        return url


settings = Settings()
