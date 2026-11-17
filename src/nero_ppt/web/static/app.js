/* Nero PPT 前端脚本：原生 JS，无框架。
   安全规则：所有动态文本用 textContent（不使用 innerHTML 拼接用户数据）。 */
"use strict";

const PHASE_STATUS_CN = {
  "intake/ready": "准备中（待启动）", "intake/running": "解析资料中…",
  "intake/needs_input": "资料待处理", "outline/running": "生成大纲中…",
  "outline/awaiting_review": "大纲待审核", "sample/running": "生成样张中…",
  "sample/awaiting_review": "样张待审核", "batch/running": "生成正文中…",
  "batch/awaiting_review": "正文批次待审核", "final/running": "整稿组装中…",
  "final/awaiting_review": "整稿待审核", "final/completed": "已完成",
  "intake/failed": "失败", "outline/failed": "失败", "sample/failed": "失败",
  "batch/failed": "失败", "final/failed": "失败",
  "intake/cancelled": "已取消", "outline/cancelled": "已取消",
  "sample/cancelled": "已取消", "batch/cancelled": "已取消", "final/cancelled": "已取消",
  "outline/interrupted": "已中断", "sample/interrupted": "已中断",
  "batch/interrupted": "已中断", "final/interrupted": "已中断",
};
const AWAITING = { outline: "outline", sample: "sample", batch: "batch", final: "final" };

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function bind(id, handler) {
  const node = document.getElementById(id);
  if (node) node.addEventListener("click", handler);
  else console.warn("nero: 页面缺少元素 #" + id + "（JS 绑定跳过）");
  return node;
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({credentials: "same-origin"}, options));
  let body = null;
  try { body = await res.json(); } catch { /* 非 JSON */ }
  if (!res.ok) {
    const err = body && body.detail && body.detail.error ? body.detail.error : {code: "http_" + res.status, message: "请求失败"};
    const e = new Error(err.message || "请求失败");
    e.code = err.code;
    e.details = err.details || {};
    e.status = res.status;
    throw e;
  }
  return body;
}

function jsonRequest(method, path, payload) {
  return api(path, {method, headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)});
}

function showError(id, message) {
  const node = document.getElementById(id);
  node.hidden = false;
  node.textContent = message;
}

function stageText(phase, status) {
  return PHASE_STATUS_CN[phase + "/" + status] || (phase + " · " + status);
}

/* ---------------------------------------------------------------- 登出 */

async function logout() {
  await fetch("/auth/logout", {method: "POST", credentials: "same-origin"});
  location.href = "/";
}

/* ---------------------------------------------------------------- 新建任务页 */

async function initNewJobPage(isDemo) {
  bind("logout", logout);
  if (!isDemo) { /* password 模式无需额外处理 */ }
  try {
    const scenes = await api("/api/scenarios");
    const select = document.getElementById("scene");
    for (const s of scenes.items) {
      const opt = el("option", "", `${s.name || s.id}（v${s.version}）`);
      opt.value = s.id;
      select.appendChild(opt);
    }
  } catch (e) { showError("create-error", "场景加载失败：" + e.message); }

  let hasModelCatalog = false;
  let selectedModelOk = false;
  let selectedModelId = "";
  const modelSel = document.getElementById("model-select");
  const testBtn = document.getElementById("test-model");
  const testStatus = document.getElementById("model-test-status");
  const createBtn = document.getElementById("create");
  try {
    const models = await api("/api/models");
    const hint = document.getElementById("model-hint");
    if (!models.items.length) {
      modelSel.appendChild(el("option", "", "（尚未配置模型）"));
      hint.textContent = "还没有可用模型：填写下方表单添加本地（如 Ollama）或远程模型，测试通过后即可创建任务。";
      renderModelManager(models);
      // 首次使用引导：无模型时直接展开配置表单
      document.getElementById("model-mgr").hidden = false;
    } else {
      hasModelCatalog = true;
      for (const m of models.items) {
        const label = m.available ? `${m.name}（${m.model_name}）` : `${m.name}（不可用：${m.unavailable_reason}）`;
        const opt = el("option", "", label);
        opt.value = m.id;
        if (!m.available) opt.disabled = true;
        if (m.id === models.default) opt.selected = true;
        modelSel.appendChild(opt);
      }
      hint.textContent = "模型密钥保存在服务端，不会下发到浏览器。创建任务前请先测试所选模型的可用性；也可以点「管理模型…」添加自己的模型。";
      renderModelManager(models);
      updateCreateGate();
    }
  } catch (e) { showError("create-error", "模型列表加载失败：" + e.message); }

  function updateCreateGate() {
    testBtn.disabled = !hasModelCatalog || !modelSel.value ||
      modelSel.selectedOptions[0]?.disabled;
    // 目录有模型时，必须测试通过才能创建任务
    createBtn.disabled = hasModelCatalog && !selectedModelOk;
  }

  modelSel && modelSel.addEventListener("change", () => {
    selectedModelOk = false;
    selectedModelId = modelSel.value;
    testStatus.textContent = "";
    testStatus.className = "muted";
    updateCreateGate();
  });

  testBtn && testBtn.addEventListener("click", async () => {
    const id = modelSel.value;
    if (!id) return;
    testBtn.disabled = true;
    testStatus.className = "muted";
    testStatus.textContent = "正在测试连通性（最小真实调用，约 2~10 秒）…";
    try {
      const r = await jsonRequest("POST", `/api/models/${encodeURIComponent(id)}/test`, {});
      if (r.ok) {
        selectedModelOk = true;
        testStatus.className = "ok-text";
        testStatus.textContent = `✓ 模型可用：${r.model}（延迟 ${r.latency_seconds}s）`;
      } else {
        selectedModelOk = false;
        testStatus.className = "error";
        testStatus.textContent = `✗ ${r.error}`;
      }
    } catch (e) {
      selectedModelOk = false;
      testStatus.className = "error";
      testStatus.textContent = `✗ 测试失败：${e.message}`;
    }
    updateCreateGate();
  });

  const mgrPanel = document.getElementById("model-mgr");
  bind("toggle-model-mgr", () => {
    mgrPanel.hidden = !mgrPanel.hidden;
  });
  bind("cancel-model", () => {
    mgrPanel.hidden = true;
    clearModelForm();
  });
  bind("save-model", async () => {
    const errNode = document.getElementById("model-mgr-error");
    errNode.hidden = true;
    const name = document.getElementById("m-name").value.trim();
    const url = document.getElementById("m-url").value.trim();
    const model = document.getElementById("m-model").value.trim();
    const key = document.getElementById("m-key").value;
    try {
      const r = await jsonRequest("POST", "/api/models",
                                  {name, base_url: url, model_name: model, api_key: key});
      clearModelForm();
      await reloadModels(r.model.id);
      testStatus.className = "muted";
      testStatus.textContent = "模型已添加，请点击「测试可用性」验证后创建任务。";
      selectedModelOk = false;
      updateCreateGate();
    } catch (e) { showError("model-mgr-error", e.message); }
  });

  function clearModelForm() {
    for (const id of ["m-name", "m-url", "m-model", "m-key"]) document.getElementById(id).value = "";
  }

  async function reloadModels(selectId) {
    const models = await api("/api/models");
    modelSel.textContent = "";
    hasModelCatalog = models.items.length > 0;
    if (!models.items.length) {
      modelSel.appendChild(el("option", "", "（尚未添加模型）"));
      modelSel.disabled = true;
    } else {
      modelSel.disabled = false;
      for (const m of models.items) {
        const label = m.available ? `${m.name}（${m.model_name}）` : `${m.name}（不可用：${m.unavailable_reason}）`;
        const opt = el("option", "", label);
        opt.value = m.id;
        if (!m.available) opt.disabled = true;
        if (m.id === models.default || m.id === selectId) opt.selected = true;
        modelSel.appendChild(opt);
      }
    }
    renderModelManager(models);
    updateCreateGate();
  }

  function renderModelManager(models) {
    const list = document.getElementById("my-models");
    list.textContent = "";
    document.getElementById("my-models-count").textContent =
      models.items.length ? `（${models.items.length} 个）` : "";
    if (!models.items.length) {
      list.appendChild(el("li", "muted", "暂无，点「＋ 配置新模型」添加"));
      return;
    }
    for (const m of models.items) {
      const li = el("li");
      li.appendChild(el("span", "", `${m.name} · ${m.model_name}${m.available ? "" : "（密钥未配置）"}`));
      const del = el("button", "ghost", "删除");
      // 两步确认：第一次点击进入确认态（不再依赖可能被拦截的原生 confirm）
      let armed = false;
      let disarmTimer = null;
      del.addEventListener("click", async () => {
        if (!armed) {
          armed = true;
          del.textContent = "确认删除？";
          del.classList.add("danger");
          disarmTimer = setTimeout(() => {
            armed = false;
            del.textContent = "删除";
            del.classList.remove("danger");
          }, 3000);
          return;
        }
        clearTimeout(disarmTimer);
        del.disabled = true;
        del.textContent = "删除中…";
        try {
          await api(`/api/models/${encodeURIComponent(m.id)}`, {method: "DELETE"});
          if (selectedModelId === m.id) selectedModelOk = false;  // 删掉的是已测试通过的
          const errNode = document.getElementById("my-models-error");
          errNode.hidden = false;
          errNode.className = "ok-text";
          errNode.textContent = `已删除「${m.name}」`;
          setTimeout(() => { errNode.hidden = true; }, 2500);
          await reloadModels();
          updateCreateGate();
        } catch (e) {
          const errNode = document.getElementById("my-models-error");
          errNode.hidden = false;
          errNode.className = "error";
          errNode.textContent = `删除失败：${e.message}`;
          del.disabled = false;
          del.textContent = "删除";
          del.classList.remove("danger");
        }
      });
      li.appendChild(del);
      list.appendChild(li);
    }
  }

  const materialInput = document.getElementById("material");
  const materialList = document.getElementById("material-list");
  materialInput.addEventListener("change", () => {
    materialList.textContent = "";
    for (const f of materialInput.files) materialList.appendChild(el("li", "", f.name));
  });

  document.querySelectorAll('input[name="template"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document.getElementById("template-file").hidden = radio.value !== "upload" || !radio.checked;
    });
  });

  bind("create", async () => {
    const errNode = document.getElementById("create-error");
    errNode.hidden = true;
    const topic = document.getElementById("topic").value.trim();
    if (!topic) { showError("create-error", "请填写主题"); return; }
    try {
      if (hasModelCatalog && !selectedModelOk) {
        showError("create-error", "请先点击「测试可用性」并确保模型可用，再创建任务");
        return;
      }
      const created = await jsonRequest("POST", "/api/jobs", {
        title: topic, scene_id: document.getElementById("scene").value,
        model_id: modelSel && modelSel.value ? modelSel.value : null,
        brief: {topic,
                audience: document.getElementById("audience").value.trim(),
                objective: document.getElementById("objective").value.trim(),
                page_range_min: Number(document.getElementById("page-min").value) || 5,
                page_range_max: Number(document.getElementById("page-max").value) || 12,
                duration_minutes: Number(document.getElementById("duration").value) || 10,
                extra_requirements: document.getElementById("extra").value.trim()},
      });
      const jobId = created.job_id;
      let revision = created.revision;  // 每步从响应取最新（模板上传会推进修订）
      for (const f of materialInput.files) {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api(`/api/jobs/${jobId}/documents`, {method: "POST", body: fd});
        if (r.revision) revision = r.revision;
      }
      const useUpload = document.querySelector('input[name="template"]:checked').value === "upload";
      if (useUpload) {
        const tf = document.getElementById("template-file").files[0];
        if (tf) {
          const fd = new FormData();
          fd.append("file", tf);
          const r = await api(`/api/jobs/${jobId}/template`, {method: "POST", body: fd});
          if (r.revision) revision = r.revision;
        }
      }
      await jsonRequest("POST", `/api/jobs/${jobId}/start`, {expected_revision: revision});
      location.href = `/jobs/${jobId}`;
    } catch (e) { showError("create-error", e.message); }
  });

  try {
    const jobs = await api("/api/jobs");
    const list = document.getElementById("recent");
    for (const j of jobs.items.slice(0, 8)) {
      const li = el("li");
      const link = el("a", "", j.title);
      link.href = `/jobs/${j.id}`;
      li.appendChild(link);
      li.appendChild(el("span", "muted", stageText(j.phase, j.status)));
      list.appendChild(li);
    }
  } catch { /* 忽略列表错误 */ }
}

/* ---------------------------------------------------------------- 工作台 */

let pollTimer = null;
let pollBackoffMs = 2000;
let pollFailures = 0;
let pollSuspendCount = 0;  // 用户正在输入/编辑时暂停轮询，避免重渲染打断

function suspendPolling() {
  pollSuspendCount += 1;
  stopPolling();
}

function resumePolling() {
  pollSuspendCount = Math.max(0, pollSuspendCount - 1);
  if (pollSuspendCount === 0 && currentJobId) schedulePoll();
}
let currentJobId = "";
let currentView = null;
let selectedSlideId = null;
let pendingPlan = null;
let detailTab = "outline"; // 右栏 Tab：outline（默认，总览大纲）/ detail（单页内容）

function switchDetailTab(tab) {
  detailTab = tab;
  document.getElementById("tab-detail").classList.toggle("active", tab === "detail");
  document.getElementById("tab-outline").classList.toggle("active", tab === "outline");
  document.getElementById("outline-editor").hidden = tab !== "outline";
  const hasDetail = tab === "detail" && selectedSlideId && currentView
    && (currentView.specs_detail || []).some(s => s.slide_id === selectedSlideId);
  document.getElementById("slide-detail").hidden = !hasDetail;
  document.getElementById("detail-empty").hidden = tab !== "detail" || hasDetail;
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

function schedulePoll() {
  stopPolling();
  pollTimer = setTimeout(pollOnce, pollBackoffMs);
}

async function pollOnce() {
  if (document.hidden) { schedulePoll(); return; }  // 页面不可见时暂停
  try {
    const view = await api(`/api/jobs/${currentJobId}`);
    pollBackoffMs = 2000;  // 成功重置退避
    if (pollFailures > 0) { pollFailures = 0; document.getElementById("conn-alert").hidden = true; }
    renderView(view);
    const terminal = view.status === "completed" || view.status === "cancelled";
    if (terminal) { stopPolling(); return; }
    schedulePoll();
  } catch (e) {
    if (e.status === 409) {
      showError("action-error", "版本已变化，请刷新页面获取最新状态（你的输入不会被覆盖）");
      stopPolling();
      return;
    }
    pollFailures += 1;
    if (pollFailures >= 3) {
      // 连接中断要显式可见，不让页面停留在"加载中"假象
      document.getElementById("conn-alert").hidden = false;
    }
    pollBackoffMs = Math.min(pollBackoffMs * 2, 30000);  // 失败退避
    schedulePoll();
  }
}

// 可导航/可操作的任务状态：非运行中均可（待审核、失败、中断、已发布，及 intake 的待启动/待补充）
function canNavigate(view) {
  return ["awaiting_review", "completed", "failed", "ready", "needs_input", "interrupted"].includes(view.status);
}

const PHASE_ORDER = [
  {id: "intake", name: "1 解析资料"},
  {id: "outline", name: "2 生成大纲"},
  {id: "sample", name: "3 样张审核"},
  {id: "final", name: "4 整稿交付"},
];
// batch（正文批次）不在 UI 显示：当前架构规格一次全量生成、样张即全稿预览，
// batch 阶段无独立产物与操作；状态机保留该阶段以兼容历史数据与未来分批生成。

let viewStep = null; // 当前查看的步骤（null = 跟随任务阶段）

function renderProgress(view) {
  const ol = document.getElementById("phase-progress");
  ol.textContent = "";
  const curIdx = PHASE_ORDER.findIndex(p => p.id === (view.phase === "batch" ? "sample" : view.phase));
  const reached = new Set(view.reached_phases || []);
  reached.add(view.phase);
  reached.add("intake");
  const viewing = viewStep || view.phase;
  PHASE_ORDER.forEach((p, i) => {
    const li = el("li", "phase-step");
    li.appendChild(el("span", "phase-name", p.name));
    let cls = "pending";
    if (view.status === "completed") cls = "done";
    else if (i < curIdx) cls = "done";
    else if (i === curIdx) {
      cls = {running: "active", awaiting_review: "review", needs_input: "warn",
             failed: "failed", interrupted: "failed", cancelled: "failed",
             ready: "active"}[view.status] || "active";
    }
    if (p.id === viewing) li.classList.add("viewing");
    // 点击步骤 = 纯查看（只切换预览内容，不改变任务状态）；
    // 改变状态需在步骤视图里显式点「切换到此步骤工作」或「重新执行」
    if (reached.has(p.id)) {
      li.classList.add("clickable");
      li.title = p.id === view.phase
        ? "当前工作阶段"
        : "查看该步骤产物（不会改变任务状态；编辑/重跑需在视图内显式操作）";
      li.addEventListener("click", () => setViewStep(p.id));
    } else {
      li.title = "尚未到达该步骤";
    }
    li.classList.add(cls);
    ol.appendChild(li);
  });
  renderStepNav(view, reached, viewing);
}

function setViewStep(phase) {
  if (viewStep === phase) return;
  viewStep = phase;
  selectedSlideId = null;
  if (phase === "outline") detailTab = "outline";
  renderView(currentView);  // 纯 UI 切换，不发请求、不改任务状态
}

function renderStepNav(view, reached, viewing) {
  const nav = document.getElementById("step-nav");
  if (!nav) return;
  nav.textContent = "";
  const viewIdx = PHASE_ORDER.findIndex(p => p.id === (viewing === "batch" ? "sample" : viewing));
  const prev = el("button", "ghost step-nav-btn", "‹ 上一步");
  const next = el("button", "ghost step-nav-btn", "下一步 ›");
  // 纯查看导航：运行中也可查看历史步骤（无副作用）
  prev.disabled = viewIdx <= 0;
  next.disabled = viewIdx >= PHASE_ORDER.length - 1
    || !reached.has(PHASE_ORDER[viewIdx + 1].id);
  prev.addEventListener("click", () => setViewStep(PHASE_ORDER[viewIdx - 1].id));
  next.addEventListener("click", () => setViewStep(PHASE_ORDER[viewIdx + 1].id));
  nav.appendChild(prev);
  nav.appendChild(el("span", "muted",
    viewing === view.phase
      ? `正在查看：${PHASE_ORDER[viewIdx].name}（当前工作阶段）`
      : `正在查看：${PHASE_ORDER[viewIdx].name}（仅浏览，任务仍在「${phaseName(view.phase)}」）`));
  nav.appendChild(next);
}

function phaseName(phase) {
  return (PHASE_ORDER.find(p => p.id === phase) || {name: phase}).name;
}

async function reopenTo(phase, rerun, renderOnly = false) {
  const errNode = document.getElementById("action-error");
  errNode.hidden = true;
  if (rerun && !window.confirm(renderOnly
    ? "将用当前排版引擎重渲染样张（内容/规格不变），覆盖现有样张。确定？"
    : "重新执行将覆盖该步骤的结果，后续步骤仍为旧版产物（需逐步重新执行）。确定？")) return;
  try {
    await jsonRequest("POST", `/api/jobs/${currentJobId}/reopen`,
                      {phase, expected_revision: currentView.revision, rerun,
                       render_only: renderOnly});
    viewStep = phase;
    selectedSlideId = null;
    if (phase === "outline") detailTab = "outline";
    await pollOnce();
  } catch (e) {
    // 服务层会返回具体原因（如"正在执行，完成后才能切换"），直接透传
    showError("action-error", e.message || "操作失败，请刷新页面");
  }
}

const STEP_KIND_CN = {parse: "解析资料", outline: "生成大纲", render_batch: "渲染页面",
                      assemble: "整稿组装", regenerate_pages: "按修改重生成"};
const STEP_STATUS_CN = {pending: "排队中", running: "执行中", done: "完成",
                        failed: "失败", interrupted: "已中断", cancelled: "已作废"};

function renderTimeline(view) {
  const box = document.getElementById("step-timeline");
  box.textContent = "";
  if (!view.steps || !view.steps.length) {
    box.appendChild(el("p", "muted", "尚无执行记录"));
    return;
  }
  for (const s of [...view.steps].reverse()) {
    const item = el("div", `step-line step-${s.status}`);
    const head = el("div", "step-head");
    head.appendChild(el("span", "step-kind", STEP_KIND_CN[s.kind] || s.kind));
    head.appendChild(el("span", `step-badge st-${s.status}`, STEP_STATUS_CN[s.status] || s.status));
    head.appendChild(el("span", "muted step-time", (s.updated_at || "").replace("T", " ").slice(0, 19)));
    item.appendChild(head);
    if (s.error) item.appendChild(el("div", "step-error", s.error));
    if ((s.status === "failed" || s.status === "interrupted") && view.status !== "completed") {
      const btn = el("button", "ghost step-retry", "重试此步骤");
      btn.addEventListener("click", () => retryStep(s.id));
      item.appendChild(btn);
    }
    box.appendChild(item);
  }
}

function editOutlinePage(idx) {
  const area = document.getElementById("preview-area");
  const cards = area.querySelectorAll(".outline-card");
  const card = cards[idx];
  if (!card || !currentView || !currentView.outline) return;
  const p = currentView.outline.pages[idx];
  suspendPolling();  // 编辑期间暂停轮询，防止 2 秒刷新重建卡片打断输入
  card.textContent = "";
  card.classList.add("editing");
  card.appendChild(el("div", "muted", `编辑第 ${idx + 1} 页（保存后立即生效）`));
  const titleIn = el("input");
  titleIn.value = p.title || "";
  titleIn.maxLength = 200;
  const purposeIn = el("input");
  purposeIn.value = p.purpose || "";
  purposeIn.placeholder = "这一页要达成什么（可选）";
  const msgIn = document.createElement("textarea");
  msgIn.rows = 3;
  msgIn.value = p.key_message || "";
  msgIn.placeholder = "这一页的关键结论/要传达的信息（可选）";
  card.appendChild(el("label", "", "标题")); card.appendChild(titleIn);
  card.appendChild(el("label", "", "目的")); card.appendChild(purposeIn);
  card.appendChild(el("label", "", "关键结论")); card.appendChild(msgIn);
  const acts = el("div", "actions");
  const saveBtn = el("button", "primary", "保存本页");
  const cancelBtn = el("button", "ghost", "取消");
  acts.appendChild(saveBtn); acts.appendChild(cancelBtn);
  card.appendChild(acts);
  cancelBtn.addEventListener("click", () => {
    resumePolling();
    renderPreview(currentView);
  });
  saveBtn.addEventListener("click", async () => {
    const newTitle = titleIn.value.trim();
    if (!newTitle) { showError("action-error", "标题不能为空"); return; }
    currentView.outline.pages[idx].title = newTitle;
    currentView.outline.pages[idx].purpose = purposeIn.value.trim();
    currentView.outline.pages[idx].key_message = msgIn.value.trim();
    try {
      await jsonRequest("PUT", `/api/jobs/${currentJobId}/outline`,
                        {pages: currentView.outline.pages.map(x => ({
                          slide_id: x.slide_id, title: x.title,
                          purpose: x.purpose || "", key_message: x.key_message || "",
                          is_appendix: !!x.is_appendix, target_seconds: x.target_seconds || 45,
                        })),
                         expected_revision: currentView.revision});
      document.getElementById("save-outline").hidden = true;
      resumePolling();
      await pollOnce();  // 保存成功后刷新卡片与左侧列表
    } catch (e) {
      resumePolling();
      showError("action-error", e.status === 409 ? "版本已变化，请刷新页面后重试" : e.message);
    }
  });
}

async function retryStep(stepId) {
  const errNode = document.getElementById("action-error");
  errNode.hidden = true;
  try {
    await jsonRequest("POST", `/api/jobs/${currentJobId}/steps/${stepId}/retry`,
                      {expected_revision: currentView.revision});
    await pollOnce();
  } catch (e) {
    if (e.status === 409) showError("action-error", "版本已变化，请刷新页面后重试");
    else showError("action-error", e.message);
  }
}

function renderView(view) {
  currentView = view;
  document.getElementById("stage").textContent = stageText(view.phase, view.status);
  document.getElementById("revision-badge").textContent = `修订 ${view.revision}`;
  renderProgress(view);
  renderTimeline(view);
  const errBox = document.getElementById("job-error");
  if (view.error && view.status !== "completed") {
    errBox.hidden = false;
    errBox.textContent = `任务问题：${view.error}`;
  } else {
    errBox.hidden = true;
  }
  const modelBadge = document.getElementById("model-badge");
  if (modelBadge) {
    modelBadge.textContent = view.model ? `模型：${view.model.name}` : "模型：未绑定";
    modelBadge.classList.toggle("warn-badge", !view.model);
  }
  syncModelPanel(view);
  document.getElementById("queue-hint").textContent =
    view.pending_step ? `排队/执行中：${view.pending_step.kind}` : "";

  renderOutline(view);
  renderPageList(view);
  renderPreview(view);
  renderReviewActions(view);
  if (selectedSlideId) renderSlideDetail(view, selectedSlideId);
  switchDetailTab(detailTab);  // 刷新右栏显隐（保持用户当前 Tab，不强制切换）
}

function renderOutline(view) {
  const list = document.getElementById("outline-list");
  list.textContent = "";
  const outline = view.outline;
  if (!outline || !outline.pages) {
    list.appendChild(el("li", "muted", "大纲尚未生成"));
    return;
  }
  const editable = view.phase === "outline" && view.status === "awaiting_review";
  outline.pages.forEach((p, idx) => {
    const li = el("li", "outline-item");
    const row = el("div", "outline-row");
    row.appendChild(el("span", "muted outline-no", `${idx + 1}.`));
    const input = el("input");
    input.value = p.title;
    input.dataset.slideId = p.slide_id;
    input.disabled = !editable;
    if (editable) {
      // 输入期间暂停轮询（否则 2 秒刷新会重建侧栏丢字）；失焦时把值同步进数据
      input.addEventListener("focus", () => suspendPolling());
      input.addEventListener("blur", () => {
        p.title = input.value.trim() || p.title;
        resumePolling();
      });
    }
    input.addEventListener("change", () => { document.getElementById("save-outline").hidden = false; });
    row.appendChild(input);
    if (editable) {
      const up = el("button", "ghost", "↑");
      up.addEventListener("click", () => moveOutline(idx, -1));
      const down = el("button", "ghost", "↓");
      down.addEventListener("click", () => moveOutline(idx, 1));
      const del = el("button", "ghost", "删");
      del.addEventListener("click", () => removeOutline(idx));
      row.appendChild(up); row.appendChild(down); row.appendChild(del);
    }
    li.appendChild(row);
    // 摘要行：完整展示目的/结论（可换行，不截断）
    const meta = [];
    if (p.purpose) meta.push(`目的：${p.purpose}`);
    if (p.key_message) meta.push(`结论：${p.key_message}`);
    const flags = [];
    if (p.target_seconds) flags.push(`${p.target_seconds}s`);
    if (p.is_appendix) flags.push("附录");
    if (flags.length) meta.push(flags.join(" · "));
    if (meta.length) li.appendChild(el("div", "outline-meta", meta.join("　")));
    if (!editable) {
      // 非编辑态：点击条目直接查看该页内容（与左侧页面列表联动）
      li.classList.add("linklike");
      li.title = "点击查看该页内容与讲稿";
      li.addEventListener("click", () => {
        selectedSlideId = p.slide_id;
        switchDetailTab("detail");
        renderPageList(currentView);
        renderSlideDetail(currentView, p.slide_id);
      });
    }
    list.appendChild(li);
  });
  document.getElementById("save-outline").hidden = !editable;
}

function collectOutline() {
  // 大纲数据由行内 blur 与卡片编辑实时同步，直接取数据源
  return currentView.outline.pages.map(p => ({
    slide_id: p.slide_id, title: p.title,
    purpose: p.purpose || "", key_message: p.key_message || "",
    is_appendix: !!p.is_appendix, target_seconds: p.target_seconds || 45,
  }));
}

function moveOutline(idx, delta) {
  const pages = currentView.outline.pages;
  const j = idx + delta;
  if (j < 0 || j >= pages.length) return;
  [pages[idx], pages[j]] = [pages[j], pages[idx]];
  renderOutline(currentView);
}

function removeOutline(idx) {
  currentView.outline.pages.splice(idx, 1);
  renderOutline(currentView);
}

async function saveOutline() {
  try {
    const pages = collectOutline();
    suspendPolling();
    await jsonRequest("PUT", `/api/jobs/${currentJobId}/outline`,
                      {pages, expected_revision: currentView.revision});
    document.getElementById("save-outline").hidden = true;
    resumePolling();
    await pollOnce();
  } catch (e) {
    resumePolling();
    if (e.status === 409) showError("action-error", "版本已变化，请刷新后重试");
    else showError("action-error", e.message);
  }
}

function renderPageList(view) {
  const list = document.getElementById("page-list");
  list.textContent = "";
  if (!view.slides.length) {
    list.appendChild(el("li", "muted", "页面尚未生成"));
    return;
  }
  view.slides.forEach((s, idx) => {
    const li = el("li", s.slide_id === selectedSlideId ? "active" : "", `${idx + 1}. ${s.headline}`);
    li.addEventListener("click", () => {
      selectedSlideId = s.slide_id;
      switchDetailTab("detail");
      renderPageList(view);
      renderSlideDetail(view, s.slide_id);
    });
    list.appendChild(li);
  });
}

const DELIVERABLE_META = [
  {kind: "pptx", label: "演示文稿 PPTX", ext: ".pptx", note: "含逐页讲稿备注，可编辑"},
  {kind: "pdf", label: "幻灯片 PDF", ext: ".pdf", note: "与 PPTX 同版本渲染"},
  {kind: "notes", label: "讲稿 Markdown", ext: ".md", note: "逐页口述正文与转场"},
];

function renderDeliverables(view, step) {
  const area = document.getElementById("preview-area");
  if (step && step !== "final") return;  // 交付物只在整稿交付步骤展示
  const finalRev = (view.step_revisions || {}).final_render;
  if (!finalRev) return;  // 尚无整稿产物（未组装或重跑中）
  const box = el("div", "deliverables");
  const title = view.status === "completed"
    ? "🎉 任务完成 · 交付物下载（最终发布版本）"
    : view.status === "running"
      ? "整稿重新组装中…（下方为上一次生成版本）"
      : "整稿已生成 · 交付物下载（待发布版本，批准后即为最终版）";
  box.appendChild(el("div", "dl-title", title));
  const row = el("div", "dl-row");
  for (const meta of DELIVERABLE_META) {
    const art = view.artifacts.find(a => a.kind === meta.kind
      && (finalRev == null || Number(a.revision) === finalRev));
    if (!art) continue;
    const item = el("div", "dl-item");
    const link = el("a", "dl-link", meta.label);
    link.href = `/api/jobs/${view.id}/artifacts/${art.id}`;
    link.download = `${view.title || "演示文稿"}${meta.ext}`;
    item.appendChild(link);
    item.appendChild(el("div", "muted", meta.note));
    row.appendChild(item);
  }
  box.appendChild(row);
  area.appendChild(box);
}

function renderPreview(view) {
  const area = document.getElementById("preview-area");
  area.textContent = "";
  document.getElementById("preview-version").textContent = `（修订 ${view.revision}）`;
  const step = viewStep || view.phase;
  renderStageBanner(view, step);
  renderDeliverables(view, step);
  // 样张/整稿：按版本浏览（render_versions 列出该步全部历史版本，点版本 chip 切换预览/下载）
  if (step === "sample" || step === "final") {
    renderRenderedStep(view, step);
    return;
  }
  // 大纲/资料/批次视图不展示 PPT 渲染产物
  if (step === "outline" && view.outline && Array.isArray(view.outline.pages) && view.outline.pages.length) {
    const awaiting = view.status === "awaiting_review";
    area.appendChild(el("p", "muted", awaiting
      ? "大纲已生成 ✓ 在右侧「大纲」标签核对/编辑每页标题与结论，确认后点「批准并继续」；或点上方「重新执行」重新生成。"
      : "当前大纲内容："));
    view.outline.pages.forEach((p, idx) => {
      const card = el("div", "outline-card");
      const head = el("div", "card-head");
      head.appendChild(el("span", "card-no", `${idx + 1}`));
      head.appendChild(el("span", "card-title", p.title));
      head.appendChild(el("span", "muted card-sec", p.target_seconds ? `${p.target_seconds}s` : ""));
      if (awaiting) {
        const editBtn = el("button", "ghost card-edit", "✎ 编辑");
        editBtn.addEventListener("click", () => editOutlinePage(idx));
        head.appendChild(editBtn);
      }
      card.appendChild(head);
      if (p.purpose) card.appendChild(el("div", "card-line", `目的：${p.purpose}`));
      if (p.key_message) card.appendChild(el("div", "card-line", `结论：${p.key_message}`));
      const meta = [];
      if (p.evidence && p.evidence.length) meta.push(`证据 ${p.evidence.length} 条`);
      if (p.visual_intents && p.visual_intents.length) meta.push(p.visual_intents.join("/"));
      if (p.is_appendix) meta.push("附录");
      if (meta.length) card.appendChild(el("div", "muted card-meta", meta.join(" · ")));
      area.appendChild(card);
    });
    return;
  }
  if (step === "intake") {
    area.appendChild(el("p", "muted", (view.materials || []).length
      ? "资料清单见上方；点「重新执行」重新解析。"
      : "尚未上传资料；可在上方「＋ 添加资料/模板」上传后重新执行解析。"));
    return;
  }
  if (step === "batch") {
    area.appendChild(el("p", "muted", "正文与样张一次性生成（样张即全稿预览），切到「样张审核」查看。"));
    return;
  }
  area.appendChild(el("p", "muted", view.pending_step ? "生成中，请稍候…" : "该步骤暂无产物；可点上方「重新执行」生成。"));
}

const viewVersion = {sample: null, final: null}; // 每步当前预览的版本（null = 最新）

function fmtTime(iso) {
  return (iso || "").replace("T", " ").slice(5, 16);
}

function renderRenderedStep(view, step) {
  const area = document.getElementById("preview-area");
  const versions = (view.render_versions || {})[step] || [];
  if (!versions.length) {
    area.appendChild(el("p", "muted", view.pending_step ? "生成中，请稍候…" : "该步骤暂无产物；可点上方「重新执行」生成。"));
    return;
  }
  const selectedRev = viewVersion[step]
    && versions.some(v => v.revision === viewVersion[step]) ? viewVersion[step] : versions[versions.length - 1].revision;
  viewVersion[step] = selectedRev;
  const current = versions.find(v => v.revision === selectedRev);
  const isLatest = current.revision === versions[versions.length - 1].revision;

  // 版本历史条（最新在前）
  const history = el("div", "version-history");
  history.appendChild(el("span", "muted v-label", `版本（${versions.length}）：`));
  for (const v of [...versions].reverse()) {
    const chip = el("button", "ghost v-chip" + (v.revision === selectedRev ? " active" : ""),
      `V${v.version} · ${fmtTime(v.created_at)}`);
    chip.title = "点击切换预览此版本";
    chip.addEventListener("click", () => { viewVersion[step] = v.revision; renderView(currentView); });
    history.appendChild(chip);
  }
  area.appendChild(history);
  if (!isLatest) {
    area.appendChild(el("div", "stale-hint",
      `⚠ 正在预览历史版本 V${current.version}；最新版本为 V${versions.length}，点上方版本条切回。`));
  }

  // 当前版本下载链接
  const dl = el("div", "actions");
  dl.appendChild(el("span", "muted",
    `当前预览：V${current.version}（${fmtTime(current.created_at)}生成）${isLatest ? " · 最新" : ""}`));
  const dlMeta = [{kind: "pptx", label: "⬇ PPTX"}, {kind: "pdf", label: "⬇ PDF"}, {kind: "notes", label: "⬇ 讲稿"}];
  for (const m of dlMeta) {
    const id = current.files[m.kind];
    if (!id) continue;
    const a = el("a", "dl-link", m.label);
    a.href = `/api/jobs/${view.id}/artifacts/${id}`;
    a.download = "";
    dl.appendChild(a);
  }
  area.appendChild(dl);

  // 缩略图（该版本全部页面）
  for (const id of current.thumb_ids) {
    const img = document.createElement("img");
    img.src = `/api/jobs/${view.id}/artifacts/${id}`;
    img.alt = "页面预览";
    area.appendChild(img);
  }
  if (current.files.pdf) {
    const link = el("a", "", "查看完整 PDF");
    link.href = `/api/jobs/${view.id}/artifacts/${current.files.pdf}`;
    link.target = "_blank";
    area.appendChild(link);
  }
}

function staleHint(view, step) {
  const sr = view.step_revisions || {};
  if (step === "sample" && sr.outline && sr.specs && sr.outline > sr.specs)
    return `大纲已更新（修订 ${sr.outline}），当前样张/规格基于旧大纲（修订 ${sr.specs}），建议重新执行本步`;
  if (step === "final" && sr.final_render
      && ((sr.specs && sr.specs > sr.final_render) || (sr.outline && sr.outline > sr.final_render)))
    return "大纲/规格在整稿生成后有过更新，当前整稿基于旧输入，建议重新执行本步";
  return null;
}

function renderStageBanner(view, step) {
  const area = document.getElementById("preview-area");
  const canAct = canNavigate(view);
  const banner = el("div", "stage-banner");
  const stale = staleHint(view, step);
  if (stale) banner.appendChild(el("div", "stale-hint", `⚠ ${stale}`));
  const tips = {
    intake: "本步负责解析上传的资料与模板；补充/替换资料后可重新执行解析（大纲也会随之重新生成）。",
    outline: "在大纲核对每页标题与结论；后续步骤（样张/整稿）均基于此大纲。",
    sample: "样张即全稿预览：确认页面内容与排版后批准进入整稿；发现问题时可回大纲修改或直接重新执行本步。",
    batch: "当前架构下正文与样张一次性生成（样张即全稿预览），本步无需单独执行。",
    final: "整稿组装完成后可预览/下载并发布；内容有变可重新执行本步。",
  };
  if (tips[step]) banner.appendChild(el("div", "stage-tip muted", tips[step]));
  // 查看的步骤非当前工作阶段：提供显式的状态切换（编辑/审核在该步生效）
  if (step !== view.phase && canAct
      && ((view.reached_phases || []).includes(step) || step === view.phase)) {
    const row = el("div", "actions");
    const switchBtn = el("button", "primary switch-step-btn", "⤵ 切换到此步骤工作");
    switchBtn.title = "任务状态将回到该步骤的审核态（已有产物保留），编辑/审核/批准在此步生效";
    switchBtn.addEventListener("click", () => reopenTo(step, false));
    row.appendChild(switchBtn);
    row.appendChild(el("span", "muted",
      `当前任务处于「${phaseName(view.phase)}」，切换后才能在此步编辑或审核`));
    banner.appendChild(row);
  }
  if (view.error && /模型未配置|模型.*不可用/.test(view.error) && canAct) {
    banner.appendChild(el("div", "stale-hint",
      "⚠ 本任务未配置可用模型：请点顶栏「⚙ 配置」选择模型并保存（可勾选保存后重试失败步骤）。"));
  }
  if (canAct && view.pending_step && view.pending_step.kind) {
    banner.appendChild(el("div", "stage-tip muted",
      `正在执行：${STEP_KIND_CN[view.pending_step.kind] || view.pending_step.kind}…`));
  } else if (canAct && RERUN_LABEL[step]) {
    const rerunRow = el("div", "actions");
    if (step === "sample") {
      // 样张双档：仅重渲染（内容不变，应用最新排版）与重新生成规格（内容按最新大纲重做）
      const reRender = el("button", "primary rerun-btn", "↻ 重新渲染样张（内容不变，修正排版）");
      reRender.addEventListener("click", () => reopenTo(step, true, true));
      const regen = el("button", "ghost rerun-btn", "↻ 重新生成规格与样张（内容重做）");
      regen.addEventListener("click", () => reopenTo(step, true, false));
      rerunRow.appendChild(reRender);
      rerunRow.appendChild(regen);
    } else {
      const btn = el("button", "ghost rerun-btn", `↻ 重新执行：${RERUN_LABEL[step]}`);
      btn.addEventListener("click", () => reopenTo(step, true));
      rerunRow.appendChild(btn);
    }
    banner.appendChild(rerunRow);
  }
  if (step === "intake" && view.materials && view.materials.length) {
    const ul = el("ul", "material-list");
    for (const m of view.materials) {
      const status = {ok: "✓ 已解析", needs_input: "⚠ 需人工处理", pending: "待解析"}[m.parse_status] || m.parse_status;
      ul.appendChild(el("li", "", `${m.kind === "template" ? "[模板] " : ""}${m.display_name} · ${status}`));
    }
    banner.appendChild(ul);
  }
  if (step === "intake") {
    // 第 1 步可补充/替换资料后重跑解析；无资料时重跑按钮禁用
    const uploadRow = el("div", "actions");
    const fileInput = el("input");
    fileInput.type = "file"; fileInput.multiple = true; fileInput.hidden = true;
    fileInput.accept = ".pdf,.docx,.txt,.md,.pptx,.potx";
    const uploadBtn = el("button", "ghost", "＋ 选择文件（可按住 Ctrl 多选）");
    uploadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => uploadIntakeFiles(fileInput.files));
    const dropzone = el("div", "dropzone", "或将文件拖拽到此处 · 支持一次多选");
    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault(); dropzone.classList.remove("dragover");
      if (e.dataTransfer && e.dataTransfer.files.length) uploadIntakeFiles(e.dataTransfer.files);
    });
    uploadRow.appendChild(uploadBtn);
    uploadRow.appendChild(fileInput);
    const status = el("div", "muted upload-status", "支持 .pdf / .docx / .txt / .md / .pptx 资料，模板为 .pptx / .potx");
    banner.appendChild(dropzone);
    banner.appendChild(uploadRow);
    banner.appendChild(status);
    const hasMaterial = (view.materials || []).some(m => m.kind === "material");
    const rerunBtn = banner.querySelector(".rerun-btn");
    if (rerunBtn && !hasMaterial && view.status === "ready") {
      rerunBtn.disabled = true;
      rerunBtn.title = "请先添加资料";
    }
  }
  area.appendChild(banner);
}

const RERUN_LABEL = {intake: "重新解析资料", outline: "重新生成大纲",
                     sample: "重新生成规格与样张", final: "重新组装整稿"};

async function uploadIntakeFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const status = document.querySelector("#preview-area .upload-status");
  const errNode = document.getElementById("action-error");
  errNode.hidden = true;
  suspendPolling();  // 上传期间暂停轮询，防止重建视图闪烁
  let ok = 0;
  const failed = [];
  try {
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      if (status) status.textContent = `正在上传 ${i + 1}/${files.length}：${f.name}…`;
      const isTemplate = /\.(pptx|potx)$/i.test(f.name);
      const fd = new FormData();
      fd.append("file", f);
      // 模板与资料是两个端点；模板上传会重建档案并推进修订
      const path = isTemplate ? `/api/jobs/${currentJobId}/template`
                              : `/api/jobs/${currentJobId}/documents`;
      try {
        const r = await api(path, {method: "POST", body: fd});
        currentView.revision = r.revision || currentView.revision;
        ok++;
      } catch (e) {
        failed.push(`${f.name}（${e.message}）`);
      }
    }
    if (status) {
      status.textContent = failed.length
        ? `已上传 ${ok}/${files.length}；失败：${failed.join("、")}`
        : `✓ 已上传 ${ok} 个文件`;
    }
    await pollOnce();
  } finally {
    resumePolling();
  }
  if (failed.length) showError("action-error", `部分文件上传失败，请检查格式/大小后重试`);
}

function renderReviewActions(view) {
  const box = document.getElementById("review-actions");
  const kind = AWAITING[view.phase];
  // 审核动作只在「查看的步骤 = 任务当前阶段」时显示，避免在历史步骤视图误操作
  const viewingCurrent = (viewStep || view.phase) === view.phase;
  const canReview = viewingCurrent && view.status === "awaiting_review" && kind;
  box.hidden = !canReview;
  if (!canReview) return;
  document.getElementById("approve").onclick = () => review(kind, "approve", "");
  document.getElementById("reject").onclick = () => {
    const comment = document.getElementById("reject-comment").value.trim();
    review(kind, "reject", comment);
  };
}

async function review(kind, decision, comment) {
  const errNode = document.getElementById("action-error");
  errNode.hidden = true;
  try {
    await jsonRequest("POST", `/api/jobs/${currentJobId}/reviews`,
                      {target_kind: kind, decision, comment, expected_revision: currentView.revision});
    await pollOnce();
  } catch (e) {
    if (e.status === 409) showError("action-error", "版本已变化或状态不允许该操作，请刷新页面");
    else showError("action-error", e.message);
  }
}

function renderSlideDetail(view, slideId) {
  const detail = document.getElementById("slide-detail");
  const spec = (currentView.specs_detail || []).find(s => s.slide_id === slideId);
  // 显隐由 switchDetailTab 统一管理，这里只填充内容
  document.getElementById("detail-title").textContent = spec ? spec.headline : slideId;
  document.getElementById("detail-purpose").textContent = spec ? spec.purpose || "" : "";
  const body = document.getElementById("detail-body");
  body.textContent = "";
  if (spec) {
    for (const block of spec.body_blocks || []) {
      const div = el("div", "block");
      if (block.kind === "bullets") {
        const ul = el("ul");
        for (const item of block.items) ul.appendChild(el("li", "", item));
        div.appendChild(ul);
      } else if (block.kind === "table") {
        const table = el("table");
        const head = el("tr");
        for (const h of block.headers) head.appendChild(el("th", "", h));
        table.appendChild(head);
        for (const row of block.rows) {
          const tr = el("tr");
          for (const cell of row) tr.appendChild(el("td", "", cell));
          table.appendChild(tr);
        }
        div.appendChild(table);
      } else if (block.kind === "chart") {
        div.appendChild(el("p", "", `图表：${(block.series || []).map(s => s.name).join("、")}（${block.categories.length} 类别）`));
      } else {
        div.appendChild(el("p", "", JSON.stringify(block)));
      }
      body.appendChild(div);
    }
    const notes = document.getElementById("detail-notes");
    notes.textContent = spec.speaker_notes ? spec.speaker_notes.script : "";
  }
  const sources = document.getElementById("detail-sources");
  sources.textContent = "";
  for (const ref of (spec ? spec.source_refs : []) || []) {
    sources.appendChild(el("li", "", `${ref.source_id} ${ref.block_id || ""} ${ref.quote || ""}`.trim()));
  }
  if (!sources.children.length) sources.appendChild(el("li", "muted", "无来源引用"));
}

async function planChange() {
  const instruction = document.getElementById("change-instruction").value.trim();
  if (!instruction) { showError("action-error", "请填写修改意见"); return; }
  try {
    pendingPlan = await jsonRequest("POST", `/api/jobs/${currentJobId}/changes/plan`, {instruction});
    document.getElementById("change-plan-text").textContent =
      `影响范围：${pendingPlan.scope}；${pendingPlan.explanation}`;
    document.getElementById("apply-change").hidden = false;
  } catch (e) { showError("action-error", e.message); }
}

async function applyChange() {
  if (!pendingPlan) return;
  try {
    await jsonRequest("POST", `/api/jobs/${currentJobId}/changes/${pendingPlan.change_id}/apply`,
                      {expected_revision: currentView.revision});
    pendingPlan = null;
    document.getElementById("apply-change").hidden = true;
    document.getElementById("change-plan-text").textContent = "";
    await pollOnce();
  } catch (e) { showError("action-error", e.message); }
}

async function initWorkbenchPage(jobId, isDemo) {
  currentJobId = jobId;
  bind("logout", logout);
  bind("save-outline", saveOutline);
  bind("plan-change", planChange);
  bind("apply-change", applyChange);
  bind("tab-detail", () => switchDetailTab("detail"));
  bind("tab-outline", () => switchDetailTab("outline"));
  bind("model-toggle", toggleModelPanel);
  bind("model-save", saveJobModel);
  window.addEventListener("beforeunload", stopPolling);  // 页面卸载停止轮询
  await pollOnce();
}

/* ---------------------------------------------------------------- 任务模型配置 */

let modelCatalog = null; // {items, default}

async function toggleModelPanel() {
  const panel = document.getElementById("model-panel");
  panel.hidden = !panel.hidden;
  if (panel.hidden) return;
  if (!modelCatalog) {
    try {
      modelCatalog = await api("/api/models");
    } catch (e) {
      showError("action-error", `模型目录加载失败：${e.message}`);
      panel.hidden = true;
      return;
    }
  }
  const select = document.getElementById("model-select");
  select.textContent = "";
  const current = currentView && currentView.model ? currentView.model.id : "";
  if (!modelCatalog.items.length) {
    select.appendChild(el("option", "", "（模型目录为空，请在创建页配置）"));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const m of modelCatalog.items) {
    const opt = el("option", "",
      `${m.name}${m.available ? "" : `（不可用：${m.unavailable_reason || "未配置密钥"}）`}`);
    opt.value = m.id;
    select.appendChild(opt);
  }
  const target = current || modelCatalog.default || "";
  if ([...select.options].some(o => o.value === target)) select.value = target;
}

async function saveJobModel() {
  const select = document.getElementById("model-select");
  const errNode = document.getElementById("action-error");
  errNode.hidden = true;
  if (!select.value) { showError("action-error", "无可选模型"); return; }
  const rerunFailed = document.getElementById("model-rerun-failed").checked
    && currentView && ["failed", "interrupted"].includes(currentView.status);
  try {
    const r = await jsonRequest("PUT", `/api/jobs/${currentJobId}/model`,
      {model_profile_id: select.value, expected_revision: currentView.revision,
       rerun_failed: rerunFailed});
    document.getElementById("model-panel").hidden = true;
    await pollOnce();
    if (r.retried_step) {
      showError("action-error", "");
      const ok = document.getElementById("action-error");
      ok.hidden = false;
      ok.classList.add("ok-msg");
      ok.textContent = "模型已配置，失败步骤已重新入队执行…";
      setTimeout(() => { ok.classList.remove("ok-msg"); ok.hidden = true; }, 6000);
    }
  } catch (e) {
    showError("action-error", e.status === 409 ? "版本已变化，请刷新后重试" : e.message);
  }
}

function syncModelPanel(view) {
  // 面板打开时保持选项与最新视图同步（避免轮询覆盖选择）
  const panel = document.getElementById("model-panel");
  if (panel.hidden) return;
  const select = document.getElementById("model-select");
  const current = view.model ? view.model.id : "";
  if (select.value && [...select.options].some(o => o.value === select.value)) return;
  if (current && [...select.options].some(o => o.value === current)) select.value = current;
}
