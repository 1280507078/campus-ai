/* 情绪测评共用数据与评分逻辑：题目、选项、分级、建议文案。
   被 emotion-quiz.js（问卷页）与 emotion-result.js（结果页）共同引用，
   避免两处各写一份导致文案不一致。 */

const QUIZ_OPTIONS = [
  { v: 0, t: "完全没有", short: "完全没有" },
  { v: 1, t: "有几天", short: "有几天" },
  { v: 2, t: "一半以上时间", short: "一半以上" },
  { v: 3, t: "几乎每天", short: "几乎每天" },
];

const QUIZ_QUESTIONS = [
  { q: "做事时提不起劲或没有兴趣", d: "depression" },
  { q: "感到心情低落、沮丧或绝望", d: "depression" },
  { q: "入睡困难、睡不安稳或睡得过多", d: "depression" },
  { q: "感到疲倦或没有活力", d: "depression" },
  { q: "感到紧张、焦虑或急躁", d: "anxiety" },
  { q: "无法停止或控制担忧", d: "anxiety" },
  { q: "对各种各样的事情担忧过多", d: "anxiety" },
  { q: "很难放松下来、坐立不安", d: "anxiety" },
];

/** 每个维度满分（4 题 × 3 分） */
const QUIZ_DIM_MAX = 12;
/** 存储键：答卷与结果都放这里，供结果页读取 */
const QUIZ_STORE_KEY = "emotion_assess_result";

/**
 * 分级：按得分占满分比例划三档。
 * 返回 { level: 中文级别, tone: 语义色名 }
 */
function gradeLevel(score, max) {
  const ratio = max > 0 ? score / max : 0;
  if (ratio >= 0.66) return { level: "偏高", tone: "high" };
  if (ratio >= 0.33) return { level: "中等", tone: "mid" };
  return { level: "较低", tone: "low" };
}

/** 计算总分与分级，生成完整结果对象 */
function buildQuizResult(answers) {
  const sum = (dim) =>
    QUIZ_QUESTIONS.reduce((s, q, i) => (q.d === dim ? s + (answers[i] || 0) : s), 0);

  const depression = sum("depression");
  const anxiety = sum("anxiety");
  const depGrade = gradeLevel(depression, QUIZ_DIM_MAX);
  const anxGrade = gradeLevel(anxiety, QUIZ_DIM_MAX);
  // 取两个维度里较高的那个作为整体判断
  const worst = depression >= anxiety ? depGrade : anxGrade;
  const overallScore = Math.max(depression, anxiety);

  return {
    answers: answers.slice(),
    depression,
    anxiety,
    depressionLevel: depGrade.level,
    anxietyLevel: anxGrade.level,
    depressionTone: depGrade.tone,
    anxietyTone: anxGrade.tone,
    overallLevel: worst.level,
    overallTone: worst.tone,
    overallScore,
    max: QUIZ_DIM_MAX,
    at: Date.now(),
  };
}

/** 依据结果生成建议文案（总分越高的越具体） */
function quizAdvice(r) {
  const high = r.overallTone === "high";
  const mid = r.overallTone === "mid";

  if (high) {
    return {
      headline: "你最近的情绪负荷有些重，这很需要被认真对待",
      body:
        "这不是你的错，也不是意志力的问题。情绪持续低落或紧张，本身就是身体在提醒你需要休息和支持。" +
        "请多关照自己，和信任的人聊聊你的感受，也建议联系学校心理健康中心，让专业的人陪你一起看看。",
      tone: "high",
    };
  }
  if (mid) {
    return {
      headline: "整体还算平稳，但有些信号值得留意",
      body:
        "你最近可能承受了一些压力，偶尔会感到低落或紧张——这属于常见的波动范围。" +
        "试着把节奏放慢一点，保证睡眠，留一点时间做让自己放松的事。如果这种状态持续超过两周，建议找专业老师聊一聊。",
      tone: "mid",
    };
  }
  return {
    headline: "整体状态不错，保持住就很好了",
    body:
      "你的情绪状态目前比较稳定。继续保持规律作息、适度运动和与人交流的习惯，" +
      "这些看起来平常的小事，正是让人稳住的底气。",
    tone: "low",
  };
}

/** 三个具体可执行的小建议（按维度高低挑） */
function quizActions(r) {
  const list = [];
  const depHigh = r.depressionTone !== "low";
  const anxHigh = r.anxietyTone !== "low";

  if (depHigh) {
    list.push("把今天要做的事只留下最重要的 1 件，完成后给自己一个明确的肯定。");
    list.push("出门走 15 分钟，晒到太阳。光照对情绪的改善比想象中明显。");
  } else {
    list.push("给这周定一个小目标，比如读完一章书、跑两次步，完成后记下来。");
  }

  if (anxHigh) {
    list.push("睡前 30 分钟放下手机，试试 4-7-8 呼吸法：吸气 4 秒、屏息 7 秒、呼气 8 秒。");
    list.push("把担心的事写在纸上，分成「能控制」和「控制不了」两栏，只处理前一栏。");
  } else {
    list.push("给朋友或家人发条消息，聊聊近况。连接感是很好的情绪缓冲。");
  }

  list.push("今晚尽量在 23:30 前睡，睡够 7 小时——睡眠是情绪的地基。");
  return list.slice(0, 3);
}

/** 是否达到「建议寻求专业帮助」的程度 */
function quizNeedHelp(r) {
  return r.overallScore >= 8 || r.depression >= 8 || r.anxiety >= 8;
}

/** 读取上次结果（结果页用），没有则返回 null */
function loadQuizResult() {
  try {
    const raw = localStorage.getItem(QUIZ_STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** 保存结果（问卷页用） */
function saveQuizResult(r) {
  try {
    localStorage.setItem(QUIZ_STORE_KEY, JSON.stringify(r));
  } catch {
    /* 隐私模式下可能写不进去，不影响当前页展示 */
  }
}
