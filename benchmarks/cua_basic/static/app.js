const app = document.querySelector("#app");
const runMeta = document.querySelector("#run-meta");
const caseLabel = document.querySelector("#case-label");
const seedLabel = document.querySelector("#seed-label");
const statusPill = document.querySelector("#status-pill");
const COLUMNS = ["Todo", "In progress", "Done"];

const params = new URLSearchParams(window.location.search);
const runId = params.get("run_id");

let currentRun = null;
let resultMessage = null;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function showError(error) {
  app.innerHTML = "";
  const card = element("section", "error-card");
  const inner = element("div");
  inner.append(element("h1", "", "Benchmark unavailable"));
  inner.append(element("p", "", error instanceof Error ? error.message : String(error)));
  card.append(inner);
  app.append(card);
}

function reflectResult(result, idleText = "State recorded") {
  if (!resultMessage) return;
  if (result.passed) {
    resultMessage.textContent = "Case passed";
    resultMessage.classList.add("passed");
    statusPill.textContent = "Passed";
    statusPill.classList.add("passed");
  } else {
    resultMessage.textContent = idleText;
  }
}

async function record(type, payload = {}, idleText = "State recorded") {
  const result = await requestJson(`/api/runs/${currentRun.run_id}/events`, {
    method: "POST",
    body: JSON.stringify({ type, payload }),
  });
  reflectResult(result, idleText);
  return result;
}

async function renderDashboard() {
  const cases = await requestJson("/api/cases");
  app.innerHTML = "";

  const header = element("section", "dashboard-header");
  const titleGroup = element("div");
  titleGroup.append(element("p", "eyebrow", "Internal evaluation fixture"));
  titleGroup.append(element("h1", "", "Ten browser tasks. One controlled surface."));
  const intro = element(
    "p",
    "",
    "Start a seeded case here for manual inspection, or create runs through the JSON API for repeatable visual, AX, and full-profile ablations.",
  );
  header.append(titleGroup, intro);

  const controls = element("div", "field");
  const seedLabelNode = element("label", "", "Seed for new runs");
  seedLabelNode.htmlFor = "dashboard-seed";
  const seedInput = element("input");
  seedInput.id = "dashboard-seed";
  seedInput.type = "number";
  seedInput.value = params.get("seed") || "0";
  seedInput.min = "0";
  controls.append(seedLabelNode, seedInput);

  const grid = element("section", "case-grid");
  for (const item of cases) {
    const card = element("article", "case-card");
    card.append(element("span", "case-number", `CASE ${item.id} · ${item.category}`));
    card.append(element("h2", "", item.title));
    card.append(element("p", "", item.description));
    const start = element("button", "primary-button", "Start case");
    start.type = "button";
    start.addEventListener("click", async () => {
      start.disabled = true;
      start.textContent = "Starting…";
      try {
        const run = await requestJson("/api/runs", {
          method: "POST",
          body: JSON.stringify({ case_id: item.id, seed: Number(seedInput.value || 0) }),
        });
        window.location.assign(run.url);
      } catch (error) {
        start.disabled = false;
        start.textContent = "Start case";
        showError(error);
      }
    });
    card.append(start);
    grid.append(card);
  }

  app.append(header, controls, grid);
}

function mountCaseShell(run) {
  document.title = `CUA Basic · Case ${run.case_id} · seed ${run.seed}`;
  const template = document.querySelector("#case-shell-template");
  const fragment = template.content.cloneNode(true);
  fragment.querySelector("#case-title").textContent = run.title;
  fragment.querySelector("#task-copy").textContent = run.task;
  fragment.querySelector("#workspace-title").textContent = `Case ${run.case_id}`;
  app.innerHTML = "";
  app.append(fragment);

  runMeta.hidden = false;
  caseLabel.textContent = `Case ${run.case_id} · ${run.title}`;
  seedLabel.textContent = `seed ${run.seed}`;
  resultMessage = document.querySelector("#result-message");
  reflectResult(run.result, "Ready");
  return document.querySelector("#workspace");
}

function drawShape(context, shape, x, y, size, color) {
  context.save();
  context.fillStyle = color;
  context.strokeStyle = color;
  context.lineWidth = 5;
  context.beginPath();
  if (shape === "circle") {
    context.arc(x, y, size, 0, Math.PI * 2);
  } else if (shape === "triangle") {
    context.moveTo(x, y - size);
    context.lineTo(x + size, y + size);
    context.lineTo(x - size, y + size);
    context.closePath();
  } else if (shape === "square") {
    context.rect(x - size, y - size, size * 2, size * 2);
  } else {
    context.moveTo(x, y - size);
    context.lineTo(x + size, y);
    context.lineTo(x, y + size);
    context.lineTo(x - size, y);
    context.closePath();
  }
  context.fill();
  context.restore();
}

function drawMark(context, mark, x, y) {
  context.save();
  context.strokeStyle = "#fffdf8";
  context.fillStyle = "#fffdf8";
  context.lineWidth = 5;
  context.lineCap = "round";
  if (mark === "dot") {
    context.beginPath();
    context.arc(x, y, 6, 0, Math.PI * 2);
    context.fill();
  } else if (mark === "stripe") {
    context.beginPath();
    context.moveTo(x - 11, y);
    context.lineTo(x + 11, y);
    context.stroke();
  } else {
    context.beginPath();
    context.moveTo(x - 9, y - 9);
    context.lineTo(x + 9, y + 9);
    context.moveTo(x + 9, y - 9);
    context.lineTo(x - 9, y + 9);
    context.stroke();
  }
  context.restore();
}

function renderVisualTarget(workspace, config) {
  const canvas = element("canvas", "visual-canvas");
  canvas.width = 840;
  canvas.height = 480;
  canvas.setAttribute("aria-label", "Custom-drawn target grid");
  workspace.append(canvas);

  const context = canvas.getContext("2d");
  const palette = { blue: "#2a5f9e", orange: "#d66b2c", green: "#237a57", purple: "#7653a8" };
  const columns = 6;
  const rows = 4;
  const cellWidth = canvas.width / columns;
  const cellHeight = canvas.height / rows;

  context.fillStyle = "#fbfaf6";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#e3e0d6";
  context.lineWidth = 1;
  for (let column = 1; column < columns; column += 1) {
    context.beginPath();
    context.moveTo(column * cellWidth, 0);
    context.lineTo(column * cellWidth, canvas.height);
    context.stroke();
  }
  for (let row = 1; row < rows; row += 1) {
    context.beginPath();
    context.moveTo(0, row * cellHeight);
    context.lineTo(canvas.width, row * cellHeight);
    context.stroke();
  }
  config.cells.forEach((cell, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = column * cellWidth + cellWidth / 2;
    const y = row * cellHeight + cellHeight / 2;
    drawShape(context, cell.shape, x, y, 30, palette[cell.color]);
    drawMark(context, cell.mark, x, y);
  });

  canvas.addEventListener("click", async (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    const column = Math.min(columns - 1, Math.max(0, Math.floor(x / cellWidth)));
    const row = Math.min(rows - 1, Math.max(0, Math.floor(y / cellHeight)));
    await record("select_cell", { cell: row * columns + column }, "Selection recorded");
  });
}

function renderSemanticPanels(workspace) {
  const grid = element("div", "panel-grid");
  const enabled = new Set();
  for (const panelName of ["Billing", "Notifications"]) {
    const panel = element("section", "settings-panel");
    panel.setAttribute("aria-labelledby", `panel-${panelName.toLowerCase()}`);
    const heading = element("h3", "", panelName);
    heading.id = `panel-${panelName.toLowerCase()}`;
    panel.append(
      heading,
      element(
        "p",
        "",
        panelName === "Billing"
          ? "Manage invoices and payment reminders."
          : "Manage product updates and account alerts.",
      ),
    );
    const button = element("button", "primary-button", "Enable");
    button.type = "button";
    button.addEventListener("click", async () => {
      enabled.add(panelName);
      button.textContent = "Enabled";
      button.disabled = true;
      await record("enable_panel", { panel: panelName }, `${panelName} enabled`);
    });
    panel.append(button);
    grid.append(panel);
  }
  workspace.append(grid);
}

function renderOrderSearch(workspace, config) {
  const searchRow = element("div", "search-row");
  const field = element("div", "field");
  const label = element("label", "", "Order number");
  label.htmlFor = "order-search";
  const input = element("input", "search-input");
  input.id = "order-search";
  input.type = "search";
  input.placeholder = "Search orders";
  field.append(label, input);
  searchRow.append(field);

  const table = element("table", "data-table");
  table.innerHTML = "<thead><tr><th>Order</th><th>Customer</th><th>Status</th><th></th></tr></thead>";
  const body = element("tbody");
  table.append(body);

  const renderRows = () => {
    body.innerHTML = "";
    const query = input.value.trim().toUpperCase();
    const visible = config.orders.filter((order) => !query || order.includes(query));
    for (const [index, order] of visible.entries()) {
      const row = element("tr");
      row.append(element("td", "", order));
      row.append(element("td", "", `Account ${String(index + 17).padStart(2, "0")}`));
      row.append(element("td", "", index % 2 ? "Pending" : "Ready"));
      const actionCell = element("td");
      const open = element("button", "secondary-button", "Open");
      open.type = "button";
      open.setAttribute("aria-label", `Open ${order}`);
      open.addEventListener("click", () => record("open_order", { order }, `${order} opened`));
      actionCell.append(open);
      row.append(actionCell);
      body.append(row);
    }
  };
  input.addEventListener("input", renderRows);
  renderRows();
  workspace.append(searchRow, table);
}

function renderUserForm(workspace) {
  const form = element("form", "form-grid");
  form.innerHTML = `
    <div class="field">
      <label for="user-name">Full name</label>
      <input id="user-name" name="name" autocomplete="off" required />
    </div>
    <div class="field">
      <label for="user-email">Email</label>
      <input id="user-email" name="email" type="email" autocomplete="off" required />
    </div>
    <div class="field">
      <label for="user-role">Role</label>
      <select id="user-role" name="role">
        <option>Viewer</option>
        <option>Editor</option>
        <option>Reviewer</option>
        <option>Analyst</option>
      </select>
    </div>
    <label class="checkbox-row">
      <input name="send_invite" type="checkbox" />
      <span>Send invite</span>
    </label>
    <div class="wide"><button class="primary-button" type="submit">Create user</button></div>
  `;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    await record(
      "submit_user",
      {
        name: String(data.get("name") || ""),
        email: String(data.get("email") || ""),
        role: String(data.get("role") || ""),
        send_invite: data.has("send_invite"),
      },
      "User submitted",
    );
  });
  workspace.append(form);
}

function renderPreferences(workspace, config) {
  const target = config.preferences;
  const initialTimezone = ["UTC", "Asia/Shanghai", "Europe/London", "America/Los_Angeles"].find(
    (zone) => zone !== target.timezone,
  );
  const form = element("form", "form-grid");
  form.innerHTML = `
    <div class="field wide">
      <label for="timezone">Timezone</label>
      <select id="timezone" name="timezone">
        <option>UTC</option>
        <option>Asia/Shanghai</option>
        <option>Europe/London</option>
        <option>America/Los_Angeles</option>
      </select>
    </div>
    <label class="checkbox-row">
      <input name="email_notifications" type="checkbox" />
      <span>Email notifications</span>
    </label>
    <label class="checkbox-row">
      <input name="compact_mode" type="checkbox" />
      <span>Compact mode</span>
    </label>
    <div class="wide"><button class="primary-button" type="submit">Save preferences</button></div>
  `;
  form.elements.timezone.value = initialTimezone;
  form.elements.email_notifications.checked = !target.email_notifications;
  form.elements.compact_mode.checked = !target.compact_mode;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await record(
      "save_preferences",
      {
        timezone: form.elements.timezone.value,
        email_notifications: form.elements.email_notifications.checked,
        compact_mode: form.elements.compact_mode.checked,
      },
      "Preferences saved",
    );
  });
  workspace.append(form);
}

function renderScrollList(workspace) {
  const list = element("div", "scroll-list");
  list.setAttribute("aria-label", "Invoices");
  for (let index = 1; index <= 100; index += 1) {
    const item = `INV-${String(index).padStart(3, "0")}`;
    const button = element("button", "invoice-row");
    button.type = "button";
    button.append(element("strong", "", item), element("small", "", `$${(index * 37.25).toFixed(2)}`));
    button.addEventListener("click", () => record("select_item", { item }, `${item} selected`));
    list.append(button);
  }
  workspace.append(list);
}

function renderBoard(workspace, config) {
  const board = element("div", "board");
  const cards = new Map();
  let dragged = null;
  let dragOrigin = null;

  const finishMove = async (card, column) => {
    if (!card || !column) return;
    card.style.transform = "";
    card.classList.remove("dragging");
    column.append(card);
    await record(
      "move_card",
      { card: card.dataset.card, column: column.dataset.column },
      `${card.dataset.card} moved to ${column.dataset.column}`,
    );
  };

  for (const columnName of COLUMNS) {
    const column = element("section", "board-column");
    column.dataset.column = columnName;
    column.append(element("h3", "", columnName));
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      column.classList.add("drop-active");
    });
    column.addEventListener("dragleave", () => column.classList.remove("drop-active"));
    column.addEventListener("drop", async (event) => {
      event.preventDefault();
      column.classList.remove("drop-active");
      const title = event.dataTransfer.getData("text/plain");
      await finishMove(cards.get(title), column);
    });
    board.append(column);
  }

  const todo = board.querySelector('[data-column="Todo"]');
  for (const title of config.cards) {
    const card = element("article", "kanban-card", title);
    card.dataset.card = title;
    card.draggable = true;
    card.tabIndex = 0;
    card.setAttribute("aria-label", `${title}, draggable card`);
    card.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", title);
      event.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      dragged = card;
      dragOrigin = { x: event.clientX, y: event.clientY };
      card.setPointerCapture(event.pointerId);
      card.classList.add("dragging");
    });
    card.addEventListener("pointermove", (event) => {
      if (dragged !== card || !dragOrigin) return;
      card.style.transform = `translate(${event.clientX - dragOrigin.x}px, ${event.clientY - dragOrigin.y}px)`;
    });
    card.addEventListener("pointerup", async (event) => {
      if (dragged !== card) return;
      card.releasePointerCapture(event.pointerId);
      card.style.visibility = "hidden";
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest(".board-column");
      card.style.visibility = "";
      dragged = null;
      dragOrigin = null;
      if (target) await finishMove(card, target);
      else {
        card.style.transform = "";
        card.classList.remove("dragging");
      }
    });
    cards.set(title, card);
    todo.append(card);
  }
  workspace.append(board);
}

function renderWaitDialog(workspace, config) {
  const panel = element("section", "operation-panel");
  panel.append(element("h3", "", "Prepare workspace index"));
  const copy = element("p", "", "The operation finishes asynchronously and then asks for confirmation.");
  const progress = element("div", "progress-track");
  const bar = element("div", "progress-bar");
  progress.append(bar);
  const start = element("button", "primary-button", "Start operation");
  start.type = "button";
  panel.append(copy, progress, start);

  const dialog = element("dialog", "modal");
  const content = element("div", "modal-content");
  content.append(element("h3", "", "Apply generated index?"));
  content.append(element("p", "", "The index is ready. Choose how this benchmark run should continue."));
  const actions = element("div", "modal-actions");
  const cancel = element("button", "secondary-button", "Cancel");
  const confirm = element("button", "primary-button", "Confirm");
  for (const [button, choice] of [
    [cancel, "Cancel"],
    [confirm, "Confirm"],
  ]) {
    button.type = "button";
    button.addEventListener("click", async () => {
      dialog.close();
      await record("dialog_choice", { choice }, `${choice} selected`);
    });
  }
  actions.append(cancel, confirm);
  content.append(actions);
  dialog.append(content);

  start.addEventListener("click", async () => {
    await record("start", {}, "Operation running…");
    start.disabled = true;
    start.textContent = "Running…";
    bar.classList.add("running");
    window.setTimeout(() => {
      bar.style.width = "100%";
      dialog.showModal();
    }, config.delay_ms);
  });
  workspace.append(panel, dialog);
}

function renderCrossTab(workspace) {
  const panel = element("section", "tab-panel");
  panel.append(element("h3", "", "Secure access check"));
  panel.append(element("p", "", "The one-time code is shown on a separate reference page."));
  const link = element("a", "download-link", "Open Access code");
  link.href = `/case/09/code?run_id=${currentRun.run_id}`;
  link.target = "_blank";
  link.rel = "noopener";
  const row = element("form", "code-row");
  const field = element("div", "field");
  const label = element("label", "", "Six-digit access code");
  label.htmlFor = "access-code";
  const input = element("input", "code-input");
  input.id = "access-code";
  input.inputMode = "numeric";
  input.maxLength = 6;
  input.autocomplete = "off";
  field.append(label, input);
  const submit = element("button", "primary-button", "Submit code");
  submit.type = "submit";
  row.append(field, submit);
  row.addEventListener("submit", async (event) => {
    event.preventDefault();
    await record("submit_code", { code: input.value.trim() }, "Code submitted");
  });
  panel.append(link, row);
  workspace.append(panel);
}

function renderCodeReference(config) {
  document.title = `CUA Basic · Case 09 reference · seed ${currentRun.seed}`;
  app.innerHTML = "";
  runMeta.hidden = false;
  caseLabel.textContent = "Case 09 · Reference tab";
  seedLabel.textContent = `seed ${currentRun.seed}`;
  const panel = element("section", "tab-panel");
  panel.append(element("p", "eyebrow", "One-time reference"));
  panel.append(element("h1", "", "Access code"));
  panel.append(element("div", "access-code", config.code));
  panel.append(element("p", "", "Return to the original benchmark tab and submit this code."));
  app.append(panel);
}

async function sha256(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function renderFileRoundtrip(workspace, config) {
  const panel = element("section", "upload-panel");
  panel.append(element("h3", "", "Document normalizer"));
  panel.append(element("p", "", "Upload the benchmark fixture, process it, and download the normalized result."));

  const field = element("div", "field");
  const label = element("label", "", "Source file");
  label.htmlFor = "source-file";
  const input = element("input");
  input.id = "source-file";
  input.type = "file";
  input.accept = ".txt,text/plain";
  field.append(label, input);
  const summary = element("div", "file-summary", `Expected fixture: ${config.upload.name}`);
  const process = element("button", "primary-button", "Start processing");
  process.type = "button";
  process.disabled = true;
  const progress = element("div", "progress-track");
  const bar = element("div", "progress-bar");
  progress.append(bar);
  const downloadHolder = element("div");

  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    summary.textContent = `Selected: ${file.name} · ${file.size} bytes`;
    const hash = await sha256(file);
    await record("upload", { name: file.name, size: file.size, sha256: hash }, "Upload recorded");
    process.disabled = false;
  });

  process.addEventListener("click", async () => {
    process.disabled = true;
    process.textContent = "Processing…";
    bar.classList.add("running");
    await record("process", {}, "Processing…");
    window.setTimeout(() => {
      bar.style.width = "100%";
      process.textContent = "Processed";
      const link = element("a", "download-link", "Download result");
      link.href = `/api/runs/${currentRun.run_id}/download`;
      link.download = config.download_name;
      link.addEventListener("click", () => {
        window.setTimeout(async () => {
          const result = await requestJson(`/api/runs/${currentRun.run_id}/result`);
          reflectResult(result, "Download requested");
        }, 250);
      });
      downloadHolder.append(link);
    }, config.delay_ms);
  });

  panel.append(field, summary, process, progress, downloadHolder);
  workspace.append(panel);
}

function renderCase(workspace, run) {
  const renderers = {
    "01": () => renderVisualTarget(workspace, run.config),
    "02": () => renderSemanticPanels(workspace),
    "03": () => renderOrderSearch(workspace, run.config),
    "04": () => renderUserForm(workspace),
    "05": () => renderPreferences(workspace, run.config),
    "06": () => renderScrollList(workspace),
    "07": () => renderBoard(workspace, run.config),
    "08": () => renderWaitDialog(workspace, run.config),
    "09": () => renderCrossTab(workspace),
    "10": () => renderFileRoundtrip(workspace, run.config),
  };
  renderers[run.case_id]();
}

async function initialize() {
  if (!runId) {
    await renderDashboard();
    return;
  }
  currentRun = await requestJson(`/api/runs/${runId}/view`);
  const referenceTab = window.location.pathname.endsWith("/code");
  if (referenceTab && currentRun.case_id === "09") {
    renderCodeReference(currentRun.config);
    return;
  }
  const workspace = mountCaseShell(currentRun);
  renderCase(workspace, currentRun);
}

initialize().catch(showError);
