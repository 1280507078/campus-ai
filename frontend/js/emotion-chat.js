/* AI 倾听对话页：只有对话，不含测评。测评已拆到 emotion-quiz.html */
requireAuth();

let history = JSON.parse(localStorage.getItem("emotion_history") || "[]");

function renderChat() {
  const box = document.getElementById("chat");
  if (!history.length) {
    box.innerHTML =
      `<div class="msg ai"><div class="bubble">你好，我是你的情绪陪伴伙伴。<br>
       今天过得怎么样？有什么想说的，我都在听。</div></div>`;
    return;
  }
  box.innerHTML = history.map(m =>
    `<div class="msg ${m.role === "user" ? "me" : "ai"}">
       <div class="bubble">${escapeHtml(m.content)}</div></div>`).join("");
  box.scrollTop = box.scrollHeight;
}

function addMsg(role, content) {
  history.push({ role, content });
  localStorage.setItem("emotion_history", JSON.stringify(history));
  renderChat();
}

/* ============================================================
   等待体验（2026-10-14 需求）
   ------------------------------------------------------------
   原来等回复时先插一个「……」的占空气泡。用户否掉了这个做法，三条要求：
     ① **不要省略号** —— 一串点点看着像卡死，也像敷衍；
     ② 要给出**大概还要多久**，让人心里有数；
     ③ 措辞要有**温度** —— 这是情绪陪伴页，不是下载进度条。

   所以现在：先说一句「人话」（我在认真想怎么回你），再跟一个不断缩短的秒数；
   超过预估时间就换成「还在写，别急」这类安抚语，**绝不干等**。

   ⚠️ 预估不写死：把上一轮的真实耗时记进 localStorage 做平滑，机器快慢自适应。
      首次用 12 秒兜底（实测短回复在 10~15 秒量级）。
   ⚠️ 所有文案一律**不带省略号**，包括按钮上的字。
   ============================================================ */

/** 上一轮真实耗时的平滑值（毫秒），存在本地 */
const ETA_KEY = "emotion_reply_ms";
const ETA_DEFAULT = 12000;
/** 低于这个耗时就不记 —— 否则一次极快的回复会把预估拉得很低，
 *  下次真的慢下来时用户会觉得「说好的几秒呢」。 */
const ETA_MIN_RECORD = 3000;

/** 等待开场白：随机挑一条，免得每轮都是同一句显得机械。 */
const WAIT_LINES = [
  "我在认真读你的话，想好好回你一句。",
  "你的话我收到了。给我一点点时间，我不想随便敷衍你。",
  "我在慢慢想怎么回应你，别急。",
  "先深呼吸一下，我在这里，正在组织想对你说的话。",
];

/** 超出预估之后的话：不报负数秒数，换成安抚。 */
const OVERRUN_LINES = [
  "还在写，不想草草收尾，再等我一会儿。",
  "我想说得再仔细一点，马上就好。",
  "就快好了，谢谢你愿意等我把话说完。",
];

/** @param {string[]} arr */
function pickOne(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** 预估本轮回复耗时（毫秒）。 */
function estimatedMs() {
  const v = Number(localStorage.getItem(ETA_KEY) || "");
  return (v >= ETA_MIN_RECORD && v <= 60000) ? v : ETA_DEFAULT;
}

/** 记下本轮真实耗时，做指数平滑（新值占 4 成）。 */
function rememberMs(ms) {
  if (!(ms >= ETA_MIN_RECORD && ms <= 60000)) return;
  const prev = Number(localStorage.getItem(ETA_KEY) || "");
  const next = prev >= ETA_MIN_RECORD ? Math.round(prev * 0.6 + ms * 0.4) : Math.round(ms);
  localStorage.setItem(ETA_KEY, String(next));
}

/**
 * 在当前对话流末尾挂一个「正在写」的气泡，并每秒更新剩余时间。
 *
 * 这个气泡**不进 history**（那会污染对话记录、还会被写进 localStorage）——
 * 它只是 DOM 上的临时节点，回复到了就摘掉。
 *
 * @param {number} estMs 预估耗时
 * @returns {{stop: () => void}}
 */
function showWaiting(estMs) {
  const box = document.getElementById("chat");
  const wrap = document.createElement("div");
  wrap.className = "msg ai";
  const bubble = document.createElement("div");
  bubble.className = "bubble wait-bubble";
  wrap.appendChild(bubble);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;

  const head = pickOne(WAIT_LINES);
  const t0 = Date.now();

  const paint = () => {
    const left = Math.ceil((estMs - (Date.now() - t0)) / 1000);
    // 还有 1 秒以上就报数；否则换成安抚语 —— 报「还要 -3 秒」比不报更伤信任
    const tail = left > 1 ? `大概还要 ${left} 秒。` : pickOne(OVERRUN_LINES);
    bubble.innerHTML = `${escapeHtml(head)}`
      + `<span class="wait-eta">${escapeHtml(tail)}</span>`;
    box.scrollTop = box.scrollHeight;
  };
  paint();
  const timer = setInterval(paint, 1000);

  return {
    stop() { clearInterval(timer); if (wrap.parentNode) wrap.remove(); },
  };
}

async function send() {
  const input = document.getElementById("msg");
  const btn = document.getElementById("btn-send");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  addMsg("user", text);

  // 发送期间禁止重复点击，避免连点产生多轮请求
  btn.disabled = true;
  btn.textContent = "正在写";              // ★ 不带省略号

  const wait = showWaiting(estimatedMs());
  const t0 = Date.now();
  let reply;
  try {
    const data = await api("/emotion/chat", {
      method: "POST",
      // ⚠️ `slice(0, -1)` 去掉刚 push 进去的**这一条用户消息**：
      //    后端 `messages = [system, *history[-10:], {"role":"user","message": text}]`
      //    会自己再补一条，history 里带着就会把同一句话连说两遍。
      //    （原来这里切掉的是"占位气泡"，顺带把用户消息留在了 history 里 —— 一直有这个重复。）
      body: { message: text, history: history.slice(0, -1) },
    });
    reply = data.reply || localComfort(text);
  } catch {
    // 后端 / GLM 未接入时的本地温柔兜底
    reply = localComfort(text);
  } finally {
    rememberMs(Date.now() - t0);
    wait.stop();
    btn.disabled = false;
    btn.textContent = "发送";
  }

  addMsg("assistant", reply);
}

/** GLM 未接入时的本地安抚兜底（保证演示可用） */
function localComfort(text) {
  if (/累|疲|烦|压力|焦虑/.test(text)) return "听起来你最近挺累的。先深呼吸一下，你的感受是真实且合理的，我们可以一起慢慢梳理。";
  if (/难过|伤心|哭|委屈/.test(text)) return "抱抱你。难过的时候不用急着好起来，我陪着你，你可以多说说发生了什么。";
  if (/考试|成绩|挂科|学不|作业/.test(text)) return "学业上的担心很常见，你已经很努力了。要不要我们一起把任务拆小一点，一步一步来？";
  return "我在认真听。谢谢你愿意和我分享，可以再多和我说说你的感受吗？";
}

function clearChat() {
  history = [];
  localStorage.removeItem("emotion_history");
  renderChat();
  toast("对话已清空", "ok");
}

/* ---------- 启动 ---------- */
renderTopbar("emotion");
hydrateIcons();
renderChat();
