"""情绪陪伴：AI 倾听对话（GLM + Qdrant 长期记忆）+ 情绪测评问卷。"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models.orm import User, ChatSession, Assessment
from models.schemas import ChatIn, ChatOut, AssessIn, AssessOut
from routers.auth import get_current_user
from services.glm import glm_chat
from services import qdrant as memory

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# 用哪个 embedding 模型就决定了向量维度。与 services/glm.py 的候选顺序一致。
EMBED_DIM = 2048


@router.post("/chat", response_model=ChatOut)
async def chat(data: ChatIn, user: User = Depends(get_current_user)):
    # 1) 检索这个用户此前说过的相关片段，让 AI「记得」他
    past = await memory.search_memory(user.id, data.message, dim=EMBED_DIM)

    system = (
        "你是温柔、耐心的情绪陪伴助手。请认真倾听用户，共情他们的感受，"
        "给予安慰与鼓励，不做诊断，必要时温和建议寻求专业帮助。"
        "\n\n⚠️ 关于「具体事件」的严格约束（请务必遵守）："
        "\n不要把用户没说过的具体事件、活动、比赛、课程名、考试名当成事实说出来。"
        "包括你「记得」的内容 —— 那是你从历史对话里检索到的一些片段，"
        "**可能不准确、可能已经过时、也可能根本不属于这位同学**。"
        "\n宁可只说感受层面的东西（「最近压力看起来不小」「听起来你很累」），"
        "也不要提任何具体的活动名。"
        "反例：❌「记得你之前提到的数学建模大赛」 ❌「你的高数考试快到了吧」"
        "正例：✅「听起来你手头的事情堆在一起，压力很大」"
    )
    if past:
        # 有记忆时，把片段作为「背景」附在 system 里，并明确要求自然使用、不要罗列。
        #
        # ⚠️ 这里必须**明确禁止复述具体活动名**。
        #    注意点：原来只说「别生硬罗列」，结果AI 张口就是
        #    「记得你之前提到的数学建模大赛」—— 而用户压根没说过竞赛。
        #    根因是历史记忆里有别的话题留下的片段（或语义检索串到了不相干的片段），
        #    模型拿到「背景」后当成了确定事实。对情绪陪伴来说，**编造用户的经历**
        #    比「没记住」伤害大得多 —— 用户会觉得「这 AI 在瞎编」，
        #    整个陪伴的可信度就没了。所以宁可不提。
        system += (
            "\n\n下面是你检索到的历史片段，**只用于帮助你理解用户的情绪状态**：\n"
            + "\n".join(f"- {p}" for p in past)
            + "\n\n使用规则（重要）："
              "\n1. 可以体现「你大概知道他的状态」这种熟悉感，但**不要复述片段里的具体内容**；"
              "\n2. **绝对不要**把片段里的活动名、比赛名、课程名、考试名讲出来当事实；"
              "\n3. 片段可能不准 —— 如果和用户当前说的话对不上，**以用户当前说的为准**，"
              "宁可完全不提记忆；"
              "\n4. 不要说「根据我的记忆」「你之前提到过」这类话。"
        )

    messages = [
        {"role": "system", "content": system},
        *data.history[-10:],
        {"role": "user", "content": data.message},
    ]
    reply = await glm_chat(messages)
    reply = reply or "我在听，慢慢说，我会一直陪着你。"

    # 2) 把此处对话写回长期记忆（失败不影响返回结果）
    try:
        await memory.add_memory(user.id, data.message, role="user", dim=EMBED_DIM)
        if reply:
            await memory.add_memory(user.id, reply, role="assistant", dim=EMBED_DIM)
    except Exception as e:
        logger.warning("写入情绪记忆失败（不影响本次对话）：%s: %s", type(e).__name__, e)

    return ChatOut(reply=reply)


@router.get("/memory/stats")
async def memory_stats(user: User = Depends(get_current_user)):
    """记忆库健康状态（供调试与演示用）。"""
    return memory.stats()


@router.post("/assess", response_model=AssessOut)
async def assess(data: AssessIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """简化版 PHQ-9 / GAD-7：前 4 题抑郁维度，后 4 题焦虑维度，各题 0-3 分。"""
    ans = data.answers
    dep = sum(ans[0:4]); anx = sum(ans[4:8])
    grade = lambda s, mx: "偏高" if s >= mx * 0.66 else ("中等" if s >= mx * 0.33 else "较低")
    result = {
        "depression": dep, "anxiety": anx,
        "depressionLevel": grade(dep, 12), "anxietyLevel": grade(anx, 12),
        "advice": ("你最近的情绪负荷有些重。请多关照自己，必要时和信任的人聊聊，"
                   "或联系学校心理中心获得专业支持。")
                  if (dep >= 8 or anx >= 8) else
                  "整体状态还不错，继续保持规律作息与适度运动，照顾好自己。",
    }
    db.add(Assessment(user_id=user.id, result=result))
    db.commit()
    return AssessOut(result=result)
