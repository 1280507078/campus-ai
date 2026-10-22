/* 情绪测评问卷页：作答 8 题，提交后跳转到 emotion-result.html 展示结果。
   题目与评分逻辑来自 quiz-data.js。 */
requireAuth();

let answers = new Array(QUIZ_QUESTIONS.length).fill(null);

function renderQuiz() {
  document.getElementById("quiz").innerHTML = QUIZ_QUESTIONS.map((item, i) => `
    <div class="field quiz-item" style="margin-bottom:16px;">
      <label class="quiz-label">
        <span class="q-no">${i + 1}</span>${item.q}
      </label>
      <div class="opts">
        ${QUIZ_OPTIONS.map(o => `
          <span class="poi" data-i="${i}" data-v="${o.v}"
            onclick="pick(${i},${o.v},this)">${o.t}</span>`).join("")}
      </div>
    </div>`).join("");
}

function pick(i, v, el) {
  answers[i] = v;
  // 同一题内的选项互斥
  el.parentElement.querySelectorAll(".poi").forEach(x => x.classList.remove("selected"));
  el.classList.add("selected");
  updateProgress();
}

function updateProgress() {
  const done = answers.filter(a => a !== null).length;
  const total = QUIZ_QUESTIONS.length;
  document.getElementById("quiz-count").textContent = done;
  document.getElementById("quiz-bar").style.width = (done / total * 100) + "%";
  document.getElementById("btn-submit").disabled = done < total;
}

async function submitQuiz() {
  if (answers.includes(null)) {
    return toast("请先完成全部题目再提交", "error");
  }

  const result = buildQuizResult(answers);
  saveQuizResult(result);

  // 后端记录一笔（空 API），失败不影响用户看结果
  try {
    await api("/emotion/assess", { method: "POST", body: { answers } });
  } catch {}

  // 不带省略号：同一套「有温度」的写法，见 emotion-chat.js 顶部的说明
  toast("测评完成，正在为你打开结果", "ok");
  setTimeout(() => { location.href = "emotion-result.html"; }, 400);
}

/* ---------- 启动 ---------- */
renderTopbar("emotion");
hydrateIcons();
renderQuiz();
updateProgress();
