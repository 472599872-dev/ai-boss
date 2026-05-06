const candidates = [
  {
    name: "林嘉",
    role: "AI 产品经理",
    score: 86,
    status: "contact_ready",
    source: "投递人选",
    index: 1,
    read: "未读",
    resume: "已查看",
  },
  {
    name: "李然",
    role: "产品负责人",
    score: 92,
    status: "contact_ready",
    source: "主动触达",
    index: 3,
    read: "已读",
    resume: "已查看",
  },
  {
    name: "陈一舟",
    role: "增长产品",
    score: 78,
    status: "review",
    source: "投递人选",
    index: 5,
    read: "未读",
    resume: "不可用",
  },
  {
    name: "周宁",
    role: "AI 应用产品",
    score: 88,
    status: "resume_requested",
    source: "主动触达",
    index: 8,
    read: "已读",
    resume: "已查看",
  },
];

const navItems = document.querySelectorAll(".nav-item");
const views = document.querySelectorAll(".view");
const addressBar = document.querySelector("#addressBar");
const browserMode = document.querySelector("#browserMode");
const runLog = document.querySelector("#runLog");
const candidateList = document.querySelector("#candidateList");
const scanPosition = document.querySelector("#scanPosition");
const scanTotal = document.querySelector("#scanTotal");
const stopState = document.querySelector("#stopState");
const resumeSheet = document.querySelector("#resumeSheet");
let scanTimer = null;
let stopRequested = false;

function setView(viewName) {
  navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === viewName));
  views.forEach((view) => view.classList.toggle("active", view.dataset.viewPanel === viewName));

  if (viewName === "inbound") {
    addressBar.textContent = "https://www.zhipin.com/web/chat/index";
    browserMode.textContent = "投递人选";
  }

  if (viewName === "outbound") {
    addressBar.textContent = "https://www.zhipin.com/web/chat/recommend";
    browserMode.textContent = "推荐牛人";
  }
}

function renderCandidates() {
  candidateList.innerHTML = candidates
    .map(
      (candidate) => `
        <article class="candidate-card">
          <div>
            <h3>${candidate.name} · ${candidate.role}</h3>
            <div class="candidate-meta">
              ${candidate.source} · 第 ${candidate.index} 位 · ${candidate.read} · 在线简历：${candidate.resume}
            </div>
            <div class="candidate-meta">状态：${candidate.status}</div>
          </div>
          <div class="candidate-actions">
            <span class="tag">${candidate.score} 分</span>
            <button class="subtle-action">查看</button>
            <button class="subtle-action">右侧打开</button>
            <button class="subtle-action">索要简历</button>
            <button class="danger-action">淘汰</button>
          </div>
        </article>
      `,
    )
    .join("");
}

function startScan(type) {
  clearInterval(scanTimer);
  stopRequested = false;
  stopState.textContent = "否";

  const total =
    type === "inbound"
      ? Math.max(
          0,
          Number(document.querySelector("#inboundEnd").value) -
            Number(document.querySelector("#inboundStart").value) +
            1,
        )
      : Number(document.querySelector("#outboundLimit").value);

  let current = 0;
  scanTotal.textContent = String(total);
  scanPosition.textContent = total ? `第 1 位` : "无任务";
  runLog.textContent =
    type === "inbound"
      ? `投递人选扫描开始：已生成 ${total} 位执行队列。`
      : `推荐牛人逐个查看开始：计划查看 ${total} 张卡片。`;

  scanTimer = setInterval(() => {
    if (current >= total) {
      clearInterval(scanTimer);
      scanTimer = null;
      scanPosition.textContent = "已完成";
      runLog.textContent = `扫描完成：实际执行 ${total} 位，队列计数一致。`;
      return;
    }

    current += 1;
    scanPosition.textContent = `第 ${current} 位`;
    runLog.textContent = `正在处理第 ${current} / ${total} 位：打开详情，读取在线简历，生成评分建议。`;

    if (stopRequested) {
      clearInterval(scanTimer);
      scanTimer = null;
      scanPosition.textContent = `第 ${current} 位后停止`;
      runLog.textContent = `收到停止请求：已在当前候选人处理完成后停止，实际执行 ${current} 位。`;
    }
  }, 900);
}

navItems.forEach((item) => {
  item.addEventListener("click", () => setView(item.dataset.view));
});

document.querySelectorAll("[data-jump]").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.jump));
});

document.querySelectorAll("[data-scan]").forEach((button) => {
  button.addEventListener("click", () => startScan(button.dataset.scan));
});

document.querySelectorAll("[data-stop]").forEach((button) => {
  button.addEventListener("click", () => {
    stopRequested = true;
    stopState.textContent = "是";
    runLog.textContent = "已请求停止：当前候选人完成后停止。";
  });
});

document.querySelectorAll(".chat-item").forEach((item) => {
  item.addEventListener("click", () => {
    document.querySelectorAll(".chat-item").forEach((chat) => chat.classList.remove("active"));
    item.classList.add("active");
    const name = item.querySelector("strong").textContent;
    document.querySelector("#detailName").textContent = name;
    runLog.textContent = `右侧已打开候选人：${name}。`;
  });
});

document.querySelector("#resumeButton").addEventListener("click", () => {
  resumeSheet.classList.add("open");
  runLog.textContent = "已打开在线简历弹层，左侧评分面板同步读取简历摘要。";
});

document.querySelector("#closeResume").addEventListener("click", () => {
  resumeSheet.classList.remove("open");
});

document.querySelector("#jobSelect").addEventListener("change", (event) => {
  runLog.textContent = `岗位已切换：${event.target.selectedOptions[0].textContent}。候选人池和评分规则将按岗位隔离。`;
});

renderCandidates();
