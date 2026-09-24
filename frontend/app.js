// =========================================================
// Config
// =========================================================
const API_BASE = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? "http://localhost:8000"
  : "";

// =========================================================
// DOM Elements
// =========================================================
const queryForm = document.getElementById("queryForm");
const questionInput = document.getElementById("questionInput");
const runBtn = document.getElementById("runBtn");
const resultsArea = document.getElementById("resultsArea");
const exampleChips = document.getElementById("exampleChips");
const dbStatusBadge = document.getElementById("dbStatusBadge");

// Header actions
const schemaToggle = document.getElementById("schemaToggle");
const uploadToggle = document.getElementById("uploadToggle");
const tableCountBadge = document.getElementById("tableCountBadge");

// Backdrop
const backdropOverlay = document.getElementById("backdropOverlay");

// Schema Drawer
const schemaPanel = document.getElementById("schemaPanel");
const schemaCloseBtn = document.getElementById("schemaCloseBtn");
const tabTablesBtn = document.getElementById("tabTablesBtn");
const tabRawSchemaBtn = document.getElementById("tabRawSchemaBtn");
const paneTables = document.getElementById("paneTables");
const paneRawSchema = document.getElementById("paneRawSchema");
const tablesList = document.getElementById("tablesList");
const tablesCountText = document.getElementById("tablesCountText");
const refreshTablesBtn = document.getElementById("refreshTablesBtn");
const schemaContent = document.getElementById("schemaContent");
const copySchemaBtn = document.getElementById("copySchemaBtn");

// Upload Modal
const uploadModal = document.getElementById("uploadModal");
const uploadCloseBtn = document.getElementById("uploadCloseBtn");
const cancelUploadBtn = document.getElementById("cancelUploadBtn");
const uploadForm = document.getElementById("uploadForm");
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const selectedFileInfo = document.getElementById("selectedFileInfo");
const selectedFileName = document.getElementById("selectedFileName");
const selectedFileSize = document.getElementById("selectedFileSize");
const customTableName = document.getElementById("customTableName");
const uploadProgressBox = document.getElementById("uploadProgressBox");
const progressStatusLabel = document.getElementById("progressStatusLabel");
const progressPercent = document.getElementById("progressPercent");
const progressBarFill = document.getElementById("progressBarFill");
const progressDetail = document.getElementById("progressDetail");
const submitUploadBtn = document.getElementById("submitUploadBtn");

// State
let schemaLoaded = false;
let currentTables = [];

// =========================================================
// Modal & Drawer Open / Close Controls (Fixes Close Bug)
// =========================================================

function openSchemaPanel() {
  closeUploadModal();
  schemaPanel.removeAttribute("hidden");
  backdropOverlay.removeAttribute("hidden");
  schemaToggle.setAttribute("aria-expanded", "true");
  if (!schemaLoaded) {
    loadSchema();
  }
}

function closeSchemaPanel() {
  schemaPanel.setAttribute("hidden", "");
  if (uploadModal.hasAttribute("hidden")) {
    backdropOverlay.setAttribute("hidden", "");
  }
  schemaToggle.setAttribute("aria-expanded", "false");
}

function openUploadModal() {
  closeSchemaPanel();
  uploadModal.removeAttribute("hidden");
  backdropOverlay.removeAttribute("hidden");
  resetUploadForm();
}

function closeUploadModal() {
  uploadModal.setAttribute("hidden", "");
  if (schemaPanel.hasAttribute("hidden")) {
    backdropOverlay.setAttribute("hidden", "");
  }
}

function closeAllOverlays() {
  closeSchemaPanel();
  closeUploadModal();
}

// =========================================================
// Micro-interaction: Ripple effect on .ripple-origin elements
// =========================================================
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".ripple-origin");
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height) * 2;
  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.cssText = `
    width: ${size}px; height: ${size}px;
    left: ${e.clientX - rect.left - size / 2}px;
    top: ${e.clientY - rect.top - size / 2}px;
  `;
  btn.appendChild(ripple);
  ripple.addEventListener("animationend", () => ripple.remove());
});

// Event Listeners for Opening & Closing
schemaToggle.addEventListener("click", () => {
  if (schemaPanel.hasAttribute("hidden")) {
    openSchemaPanel();
  } else {
    closeSchemaPanel();
  }
});

schemaCloseBtn.addEventListener("click", closeSchemaPanel);
uploadToggle.addEventListener("click", openUploadModal);
uploadCloseBtn.addEventListener("click", closeUploadModal);
cancelUploadBtn.addEventListener("click", closeUploadModal);
backdropOverlay.addEventListener("click", closeAllOverlays);

// Escape key to close any active overlay
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeAllOverlays();
  }
});

// =========================================================
// Schema Drawer Tabs & Content
// =========================================================

tabTablesBtn.addEventListener("click", () => {
  tabTablesBtn.classList.add("active");
  tabRawSchemaBtn.classList.remove("active");
  paneTables.classList.add("active");
  paneRawSchema.classList.remove("active");
});

tabRawSchemaBtn.addEventListener("click", () => {
  tabRawSchemaBtn.classList.add("active");
  tabTablesBtn.classList.remove("active");
  paneRawSchema.classList.add("active");
  paneTables.classList.remove("active");
});

refreshTablesBtn.addEventListener("click", () => {
  loadSchema();
});

copySchemaBtn.addEventListener("click", async () => {
  const text = schemaContent.textContent;
  try {
    await navigator.clipboard.writeText(text);
    copySchemaBtn.textContent = "Copied!";
    setTimeout(() => {
      copySchemaBtn.textContent = "Copy";
    }, 2000);
  } catch (err) {
    console.error("Clipboard copy failed:", err);
  }
});

async function loadSchema() {
  tablesCountText.textContent = "Loading tables from database…";
  schemaContent.textContent = "Loading introspected schema…";

  try {
    const res = await fetch(`${API_BASE}/api/schema`);
    if (!res.ok) throw new Error("Failed to load schema from API");

    const data = await res.json();
    schemaContent.textContent = data.schema || "No schema available.";
    currentTables = data.tables || [];
    tableCountBadge.textContent = String(data.table_count || currentTables.length);
    tablesCountText.textContent = `${currentTables.length} Active Table${currentTables.length === 1 ? "" : "s"}`;

    renderTablesList(currentTables);
    schemaLoaded = true;
  } catch (err) {
    schemaContent.textContent = "Could not load schema. Is the backend running and connected to PostgreSQL?";
    tablesCountText.textContent = "Connection error";
    tablesList.innerHTML = `
      <div class="state-panel error" style="padding: 14px; font-size: 12px;">
        Could not connect to database. Please check your backend/.env DATABASE_URL configuration.
      </div>
    `;
  }
}

function renderTablesList(tables) {
  if (!tables || tables.length === 0) {
    tablesList.innerHTML = `
      <div class="state-panel" style="padding: 16px; font-size: 13px;">
        No tables found in this database.<br>
        <button class="action-btn upload-btn" style="margin: 12px auto 0;" onclick="openUploadModal()">
          ↑ Upload a Dataset
        </button>
      </div>
    `;
    return;
  }

  tablesList.innerHTML = tables.map(table => {
    const colPills = (table.columns || []).map(col => `
      <span class="col-pill" title="${col.type}">
        ${escapeHtml(col.name)}<span class="col-type">:${escapeHtml(col.type)}</span>
      </span>
    `).join("");

    return `
      <div class="table-card">
        <div class="table-card-header">
          <span class="table-card-name">▸ ${escapeHtml(table.name)}</span>
          <span class="table-card-rows">${table.row_count} row${table.row_count === 1 ? "" : "s"}</span>
        </div>
        <div class="table-columns-pills">
          ${colPills}
        </div>
        <div class="table-card-actions">
          <button class="table-action-link" onclick="previewSampleData('${escapeHtml(table.name)}')">Sample Preview</button>
          <button class="table-action-link" onclick="queryTable('${escapeHtml(table.name)}')">Ask AI</button>
          <button class="table-action-link delete" onclick="confirmDeleteTable('${escapeHtml(table.name)}')">Drop</button>
        </div>
      </div>
    `;
  }).join("");
}

window.queryTable = function(tableName) {
  closeSchemaPanel();
  questionInput.value = `Show me 10 records from ${tableName}`;
  questionInput.focus();
  queryForm.requestSubmit();
};

window.previewSampleData = async function(tableName) {
  closeSchemaPanel();
  setLoading(true);
  renderLoadingState(`Fetching sample rows from ${tableName}…`);

  try {
    const res = await fetch(`${API_BASE}/api/tables/${encodeURIComponent(tableName)}/sample`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to fetch sample");

    renderResults({
      question: `Sample preview of table '${tableName}'`,
      generated_sql: `SELECT * FROM "${tableName}" LIMIT 5;`,
      columns: data.columns,
      rows: data.rows,
      row_count: data.count,
      latency_ms: 10
    });
  } catch (err) {
    renderErrorState(`Failed to preview table '${tableName}': ${err.message}`);
  } finally {
    setLoading(false);
  }
};

window.confirmDeleteTable = async function(tableName) {
  if (!confirm(`Are you sure you want to drop table '${tableName}'? This cannot be undone.`)) {
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/tables/${encodeURIComponent(tableName)}`, {
      method: "DELETE"
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to drop table");
    
    await loadSchema();
  } catch (err) {
    alert(`Could not drop table: ${err.message}`);
  }
};

// =========================================================
// Data Ingestion & Large File Upload (Up to 50 GB)
// =========================================================

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function handleFileSelection(file) {
  if (!file) return;

  selectedFileName.textContent = file.name;
  selectedFileSize.textContent = `(${formatBytes(file.size)})`;
  selectedFileInfo.removeAttribute("hidden");

  if (!customTableName.value.trim()) {
    const baseName = file.name.replace(/\.[^/.]+$/, "");
    const cleanName = baseName.replace(/[^a-zA-Z0-9_]+/g, "_").toLowerCase().slice(0, 50);
    customTableName.placeholder = cleanName;
  }
}

fileInput.addEventListener("change", () => {
  if (fileInput.files.length > 0) {
    handleFileSelection(fileInput.files[0]);
  }
});

// Drag & Drop handlers
["dragenter", "dragover"].forEach(eventName => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach(eventName => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.remove("dragover");
  });
});

dropzone.addEventListener("drop", (e) => {
  const dt = e.dataTransfer;
  const files = dt.files;
  if (files.length > 0) {
    fileInput.files = files;
    handleFileSelection(files[0]);
  }
});

function resetUploadForm() {
  uploadForm.reset();
  selectedFileInfo.setAttribute("hidden", "");
  uploadProgressBox.setAttribute("hidden", "");
  submitUploadBtn.disabled = false;
  submitUploadBtn.innerHTML = "<span>Start Ingestion</span> →";
  progressBarFill.style.width = "0%";
  progressPercent.textContent = "0%";
}

uploadForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) {
    alert("Please select a file to upload.");
    return;
  }

  // 50 GB client-side sanity check
  const MAX_BYTES = 50 * 1024 * 1024 * 1024;
  if (file.size > MAX_BYTES) {
    alert("File exceeds the 50 GB maximum limit.");
    return;
  }

  submitUploadBtn.disabled = true;
  submitUploadBtn.textContent = "Ingesting…";
  uploadProgressBox.removeAttribute("hidden");
  progressStatusLabel.textContent = "Uploading file to server…";
  progressPercent.textContent = "0%";
  progressBarFill.style.width = "0%";
  progressDetail.textContent = `Streaming ${formatBytes(file.size)}…`;

  const formData = new FormData();
  formData.append("file", file);
  if (customTableName.value.trim()) {
    formData.append("table_name", customTableName.value.trim());
  }

  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${API_BASE}/api/upload`, true);

  // Upload progress tracking
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const percent = Math.round((event.loaded / event.total) * 100);
      progressBarFill.style.width = `${percent}%`;
      progressPercent.textContent = `${percent}%`;
      progressDetail.textContent = `Uploaded ${formatBytes(event.loaded)} of ${formatBytes(event.total)}`;

      if (percent === 100) {
        progressStatusLabel.textContent = "Parsing & Ingesting into PostgreSQL…";
        progressDetail.textContent = "Creating table schema and streaming records…";
      }
    }
  };

  xhr.onload = async () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      let resp;
      try {
        resp = JSON.parse(xhr.responseText);
      } catch (err) {
        resp = { message: "Upload complete" };
      }

      progressStatusLabel.textContent = "Ingestion Complete!";
      progressPercent.textContent = "100%";
      progressBarFill.style.width = "100%";
      progressDetail.textContent = `Successfully imported into database!`;

      // Refresh schema
      await loadSchema();

      setTimeout(() => {
        closeUploadModal();
        const details = resp.details || {};
        const tableName = details.table_name || "your table";
        const rows = details.rows_inserted || 0;
        
        resultsArea.innerHTML = `
          <div class="state-panel" style="border-color: var(--accent); background: rgba(63, 185, 80, 0.08);">
            <p style="font-weight: 600; color: var(--accent); margin: 0 0 6px;">✓ Successfully Ingested Dataset</p>
            <p style="font-size: 13px; margin: 0 0 12px;">Created table <strong>${escapeHtml(tableName)}</strong> with ${rows} records.</p>
            <button class="chip" onclick="queryTable('${escapeHtml(tableName)}')">
              Ask AI about ${escapeHtml(tableName)} →
            </button>
          </div>
        `;
      }, 900);
    } else {
      let errorMsg = "Upload failed.";
      try {
        const errObj = JSON.parse(xhr.responseText);
        errorMsg = errObj.detail || errorMsg;
      } catch (e) {}

      progressStatusLabel.textContent = "Ingestion Error";
      progressDetail.textContent = errorMsg;
      progressBarFill.style.backgroundColor = "var(--error)";
      submitUploadBtn.disabled = false;
      submitUploadBtn.textContent = "Retry Ingestion";
    }
  };

  xhr.onerror = () => {
    progressStatusLabel.textContent = "Network Error";
    progressDetail.textContent = "Could not reach the server during upload.";
    progressBarFill.style.backgroundColor = "var(--error)";
    submitUploadBtn.disabled = false;
    submitUploadBtn.textContent = "Retry Ingestion";
  };

  xhr.send(formData);
});

// =========================================================
// Example Chips
// =========================================================
exampleChips.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  questionInput.value = chip.dataset.question;
  questionInput.focus();
  queryForm.requestSubmit();
});

// =========================================================
// Query Submission & Execution
// =========================================================
queryForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  setLoading(true);
  renderLoadingState("AI is reading database schema & generating SQL…");

  try {
    const res = await fetch(`${API_BASE}/api/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    const data = await res.json();

    if (!res.ok) {
      renderErrorState(data.detail || "Something went wrong.");
      return;
    }

    renderResults(data);
  } catch (err) {
    renderErrorState("Could not reach the API. Is the backend server running?");
  } finally {
    setLoading(false);
  }
});

function setLoading(isLoading) {
  runBtn.disabled = isLoading;
  questionInput.disabled = isLoading;
}

// =========================================================
// Rendering Output
// =========================================================
function renderLoadingState(message = "Generating SQL and running against database…") {
  resultsArea.innerHTML = `
    <div class="state-panel">
      <span class="loading-dots"><span></span><span></span><span></span></span>
      ${escapeHtml(message)}
    </div>
  `;
}

function renderErrorState(message) {
  resultsArea.innerHTML = `
    <div class="state-panel error">
      ${escapeHtml(message)}
    </div>
  `;
}

function renderResults(data) {
  const { question, generated_sql, columns, rows, row_count, latency_ms } = data;

  const sqlReceiptHtml = `
    <div class="sql-receipt">
      <p class="sql-receipt-label">Generated SQL</p>
      <pre class="sql-code">${escapeHtml(generated_sql)}</pre>
      <div class="run-meta">→ ${row_count} row${row_count === 1 ? "" : "s"} returned · ${latency_ms}ms execution</div>
    </div>
  `;

  let tableHtml;
  if (!rows || rows.length === 0) {
    tableHtml = `
      <div class="results-table-wrap">
        <p class="no-rows">Query ran successfully but returned no rows.</p>
      </div>
    `;
  } else {
    const headerHtml = columns.map((col) => `<th>${escapeHtml(col)}</th>`).join("");
    const bodyHtml = rows
      .map((row) => {
        const cells = columns
          .map((col) => `<td>${formatCell(row[col])}</td>`)
          .join("");
        return `<tr>${cells}</tr>`;
      })
      .join("");

    tableHtml = `
      <div class="results-table-wrap">
        <div class="results-table-scroll">
          <table class="results-table">
            <thead><tr>${headerHtml}</tr></thead>
            <tbody>${bodyHtml}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  resultsArea.innerHTML = sqlReceiptHtml + tableHtml;
}

function formatCell(value) {
  if (value === null || value === undefined) {
    return `<span style="color: var(--text-muted)">null</span>`;
  }
  return escapeHtml(String(value));
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Initial health check & schema pre-load
async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/api/health`);
    if (res.ok) {
      const data = await res.json();
      if (data.database_connected) {
        dbStatusBadge.textContent = "Live DB";
        dbStatusBadge.style.color = "var(--accent)";
      } else {
        dbStatusBadge.textContent = "DB Setup Needed";
        dbStatusBadge.style.color = "var(--warning)";
      }
      loadSchema();
    }
  } catch (err) {
    dbStatusBadge.textContent = "Offline";
    dbStatusBadge.style.color = "var(--error)";
  }
}

checkHealth();
