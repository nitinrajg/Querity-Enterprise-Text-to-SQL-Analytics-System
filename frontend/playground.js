// =========================================================
// DuckDB SQL Playground — Frontend Logic with Smart Autocomplete
// =========================================================

const API_BASE = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? "http://localhost:8000"
  : "";

// DOM Elements
const editorCard = document.getElementById("editorCard");
const duckdbSqlInput = document.getElementById("duckdbSqlInput");
const runDuckDbBtn = document.getElementById("runDuckDbBtn");
const clearEditorBtn = document.getElementById("clearEditorBtn");
const formatSqlBtn = document.getElementById("formatSqlBtn");
const copySqlBtn = document.getElementById("copySqlBtn");
const exportCsvBtn = document.getElementById("exportCsvBtn");
const exportJsonBtn = document.getElementById("exportJsonBtn");
const refreshDuckTablesBtn = document.getElementById("refreshDuckTablesBtn");

const sidebarTablesList = document.getElementById("sidebarTablesList");
const tableCountPill = document.getElementById("tableCountPill");
const tablesFilterInput = document.getElementById("tablesFilterInput");
const templatesBar = document.getElementById("templatesBar");
const playgroundHistory = document.getElementById("playgroundHistory");

const resultsBodyWrap = document.getElementById("resultsBodyWrap");
const resRowCount = document.getElementById("resRowCount");
const resLatency = document.getElementById("resLatency");
const resEngine = document.getElementById("resEngine");

// Autocomplete DOM Elements
const sqlAutocompleteDropdown = document.getElementById("sqlAutocompleteDropdown");
const autocompleteItemsList = document.getElementById("autocompleteItemsList");

// Upload Modal DOM Elements
const uploadToggle = document.getElementById("uploadToggle");
const uploadModal = document.getElementById("uploadModal");
const uploadCloseBtn = document.getElementById("uploadCloseBtn");
const cancelUploadBtn = document.getElementById("cancelUploadBtn");
const backdropOverlay = document.getElementById("backdropOverlay");
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
let currentResultData = null;
let currentTablesData = [];
let queryHistory = JSON.parse(localStorage.getItem("duckdb_history") || "[]");

// Autocomplete State
let activeSuggestions = [];
let selectedSuggestionIndex = 0;
let isAutocompleteVisible = false;

// =========================================================
// SQL Knowledge Catalog (Clauses, Keywords, Functions)
// =========================================================
const SQL_CLAUSES = [
  { text: "SELECT", insertText: "SELECT ", type: "clause", meta: "Clause" },
  { text: "FROM", insertText: "FROM ", type: "clause", meta: "Clause" },
  { text: "WHERE", insertText: "WHERE ", type: "clause", meta: "Clause" },
  { text: "GROUP BY", insertText: "GROUP BY ", type: "clause", meta: "Clause" },
  { text: "ORDER BY", insertText: "ORDER BY ", type: "clause", meta: "Clause" },
  { text: "HAVING", insertText: "HAVING ", type: "clause", meta: "Clause" },
  { text: "LIMIT", insertText: "LIMIT ", type: "clause", meta: "Clause" },
  { text: "OFFSET", insertText: "OFFSET ", type: "clause", meta: "Clause" },
  { text: "JOIN", insertText: "JOIN ", type: "clause", meta: "Join" },
  { text: "LEFT JOIN", insertText: "LEFT JOIN ", type: "clause", meta: "Join" },
  { text: "RIGHT JOIN", insertText: "RIGHT JOIN ", type: "clause", meta: "Join" },
  { text: "INNER JOIN", insertText: "INNER JOIN ", type: "clause", meta: "Join" },
  { text: "FULL OUTER JOIN", insertText: "FULL OUTER JOIN ", type: "clause", meta: "Join" },
  { text: "CROSS JOIN", insertText: "CROSS JOIN ", type: "clause", meta: "Join" },
  { text: "ON", insertText: "ON ", type: "clause", meta: "Keyword" },
  { text: "USING", insertText: "USING ()", type: "clause", meta: "Keyword" },
  { text: "WITH", insertText: "WITH ", type: "clause", meta: "CTE Clause" },
  { text: "AS", insertText: "AS ", type: "clause", meta: "Keyword" },
  { text: "DISTINCT", insertText: "DISTINCT ", type: "clause", meta: "Keyword" },
  { text: "UNION ALL", insertText: "UNION ALL ", type: "clause", meta: "Set Operator" },
  { text: "UNION", insertText: "UNION ", type: "clause", meta: "Set Operator" },
  { text: "INTERSECT", insertText: "INTERSECT ", type: "clause", meta: "Set Operator" },
  { text: "EXCEPT", insertText: "EXCEPT ", type: "clause", meta: "Set Operator" },
  { text: "SUMMARIZE", insertText: "SUMMARIZE ", type: "clause", meta: "DuckDB Stats" },
  { text: "DESCRIBE", insertText: "DESCRIBE ", type: "clause", meta: "DuckDB Schema" },
  { text: "EXPLAIN", insertText: "EXPLAIN ", type: "clause", meta: "Query Plan" },
  { text: "PIVOT", insertText: "PIVOT ", type: "clause", meta: "DuckDB OLAP" },
  { text: "UNPIVOT", insertText: "UNPIVOT ", type: "clause", meta: "DuckDB OLAP" },
  { text: "QUALIFY", insertText: "QUALIFY ", type: "clause", meta: "DuckDB Filter" },
  { text: "OVER", insertText: "OVER ()", type: "clause", meta: "Window" },
  { text: "PARTITION BY", insertText: "PARTITION BY ", type: "clause", meta: "Window" },
  { text: "CREATE TABLE", insertText: "CREATE TABLE ", type: "clause", meta: "DDL" },
  { text: "CREATE OR REPLACE TABLE", insertText: "CREATE OR REPLACE TABLE ", type: "clause", meta: "DDL" },
  { text: "DROP TABLE", insertText: "DROP TABLE ", type: "clause", meta: "DDL" },
  { text: "INSERT INTO", insertText: "INSERT INTO ", type: "clause", meta: "DML" },
  { text: "VALUES", insertText: "VALUES ", type: "clause", meta: "DML" },
  { text: "CASE WHEN", insertText: "CASE WHEN  THEN  ELSE  END", type: "clause", meta: "Conditional" },
  { text: "THEN", insertText: "THEN ", type: "clause", meta: "Keyword" },
  { text: "ELSE", insertText: "ELSE ", type: "clause", meta: "Keyword" },
  { text: "END", insertText: "END", type: "clause", meta: "Keyword" },
  { text: "BETWEEN", insertText: "BETWEEN  AND ", type: "clause", meta: "Operator" },
  { text: "LIKE", insertText: "LIKE '%%'", type: "clause", meta: "Operator" },
  { text: "ILIKE", insertText: "ILIKE '%%'", type: "clause", meta: "Operator" },
  { text: "IN", insertText: "IN ()", type: "clause", meta: "Operator" },
  { text: "IS NULL", insertText: "IS NULL", type: "clause", meta: "Operator" },
  { text: "IS NOT NULL", insertText: "IS NOT NULL", type: "clause", meta: "Operator" },
  { text: "AND", insertText: "AND ", type: "clause", meta: "Logic" },
  { text: "OR", insertText: "OR ", type: "clause", meta: "Logic" },
  { text: "NOT", insertText: "NOT ", type: "clause", meta: "Logic" }
];

const SQL_FUNCTIONS = [
  { text: "COUNT(*)", insertText: "COUNT(*)", type: "function", meta: "Aggregate" },
  { text: "COUNT()", insertText: "COUNT()", type: "function", meta: "Aggregate" },
  { text: "SUM()", insertText: "SUM()", type: "function", meta: "Aggregate" },
  { text: "AVG()", insertText: "AVG()", type: "function", meta: "Aggregate" },
  { text: "MIN()", insertText: "MIN()", type: "function", meta: "Aggregate" },
  { text: "MAX()", insertText: "MAX()", type: "function", meta: "Aggregate" },
  { text: "RANK()", insertText: "RANK() OVER (ORDER BY )", type: "function", meta: "Window" },
  { text: "ROW_NUMBER()", insertText: "ROW_NUMBER() OVER (ORDER BY )", type: "function", meta: "Window" },
  { text: "DENSE_RANK()", insertText: "DENSE_RANK() OVER (ORDER BY )", type: "function", meta: "Window" },
  { text: "LAG()", insertText: "LAG() OVER (ORDER BY )", type: "function", meta: "Window" },
  { text: "LEAD()", insertText: "LEAD() OVER (ORDER BY )", type: "function", meta: "Window" },
  { text: "COALESCE()", insertText: "COALESCE()", type: "function", meta: "Scalar" },
  { text: "ROUND()", insertText: "ROUND()", type: "function", meta: "Math" },
  { text: "DATE_TRUNC()", insertText: "DATE_TRUNC('month', )", type: "function", meta: "Date/Time" },
  { text: "EXTRACT()", insertText: "EXTRACT(year FROM )", type: "function", meta: "Date/Time" },
  { text: "CONCAT()", insertText: "CONCAT()", type: "function", meta: "String" },
  { text: "CAST()", insertText: "CAST( AS VARCHAR)", type: "function", meta: "Conversion" },
  { text: "SUBSTRING()", insertText: "SUBSTRING()", type: "function", meta: "String" }
];

// =========================================================
// Initialization
// =========================================================
document.addEventListener("DOMContentLoaded", () => {
  loadDuckDbTables();
  renderHistoryChips();
  setupUploadModal();
  setupAutocomplete();

  // Set default initial query if editor is empty
  if (!duckdbSqlInput.value.trim()) {
    duckdbSqlInput.value = "SELECT * FROM customers ORDER BY lifetime_spend DESC LIMIT 10;";
  }

  // Auto-run initial query
  executeDuckDbQuery(duckdbSqlInput.value.trim());
});

// =========================================================
// Table & Schema Explorer
// =========================================================
async function loadDuckDbTables() {
  try {
    const res = await fetch(`${API_BASE}/api/playground/tables`);
    if (!res.ok) throw new Error("Could not fetch tables");
    const data = await res.json();
    currentTablesData = data.tables || [];
    renderTablesSidebar(currentTablesData);
  } catch (err) {
    sidebarTablesList.innerHTML = `
      <div style="padding:16px;color:var(--error);font-family:var(--font-mono);font-size:11px;">
        Failed to load tables: ${err.message}
      </div>
    `;
  }
}

function renderTablesSidebar(tables) {
  tableCountPill.textContent = `${tables.length} table${tables.length === 1 ? '' : 's'}`;

  const filterText = (tablesFilterInput.value || "").toLowerCase().trim();

  const filtered = tables.filter(t => {
    if (!filterText) return true;
    if (t.name.toLowerCase().includes(filterText)) return true;
    return t.columns && t.columns.some(c => c.name.toLowerCase().includes(filterText));
  });

  if (filtered.length === 0) {
    sidebarTablesList.innerHTML = `
      <div style="padding:20px;text-align:center;color:var(--text-dim);font-family:var(--font-mono);font-size:11px;">
        No tables matching "${filterText}"
      </div>
    `;
    return;
  }

  sidebarTablesList.innerHTML = filtered.map((t, idx) => `
    <div class="duck-table-card" id="tableCard_${idx}">
      <div class="duck-table-header" onclick="toggleTableColumns(${idx})">
        <span class="duck-table-name">
          <span style="font-size:10px;opacity:0.7;">▸</span> ${escapeHtml(t.name)}
        </span>
        <span class="duck-table-count">${t.row_count.toLocaleString()} rows</span>
      </div>
      <div class="duck-table-actions">
        <button class="mini-action-btn" onclick="insertTableQuery('${escapeHtml(t.name)}', 'select')" title="SELECT * FROM table">Select</button>
        <button class="mini-action-btn" onclick="insertTableQuery('${escapeHtml(t.name)}', 'summarize')" title="SUMMARIZE table">Stats</button>
        <button class="mini-action-btn" onclick="insertTableQuery('${escapeHtml(t.name)}', 'describe')" title="DESCRIBE table">Schema</button>
      </div>
      <div class="duck-columns-list" id="colList_${idx}" style="display:none;">
        ${(t.columns || []).map(c => `
          <div class="duck-column-item" onclick="insertColumnName('${escapeHtml(c.name)}')" title="Click to insert column name">
            <span>${escapeHtml(c.name)}</span>
            <span class="duck-col-type">${escapeHtml(c.type)}</span>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

window.toggleTableColumns = function(idx) {
  const colList = document.getElementById(`colList_${idx}`);
  if (!colList) return;
  colList.style.display = colList.style.display === "none" ? "flex" : "none";
};

window.insertTableQuery = function(tableName, action) {
  let q = "";
  if (action === "select") {
    q = `SELECT * FROM "${tableName}" LIMIT 20;`;
  } else if (action === "summarize") {
    q = `SUMMARIZE "${tableName}";`;
  } else if (action === "describe") {
    q = `DESCRIBE "${tableName}";`;
  }
  duckdbSqlInput.value = q;
  duckdbSqlInput.focus();
  executeDuckDbQuery(q);
};

window.insertColumnName = function(colName) {
  insertAtCursor(duckdbSqlInput, colName);
};

tablesFilterInput.addEventListener("input", () => {
  renderTablesSidebar(currentTablesData);
});

refreshDuckTablesBtn.addEventListener("click", () => {
  loadDuckDbTables();
});

// =========================================================
// Autocomplete & Suggestion Engine
// =========================================================
function setupAutocomplete() {
  duckdbSqlInput.addEventListener("input", handleEditorInput);
  duckdbSqlInput.addEventListener("keydown", handleEditorKeyDown);
  duckdbSqlInput.addEventListener("click", () => hideAutocomplete());

  // Close when clicking outside editor
  document.addEventListener("click", (e) => {
    if (!editorCard.contains(e.target)) {
      hideAutocomplete();
    }
  });
}

function handleEditorInput() {
  const cursorPos = duckdbSqlInput.selectionStart;
  const textBefore = duckdbSqlInput.value.substring(0, cursorPos);

  // Extract currently typed token (alphanumeric, underscore, or dot)
  const tokenMatch = textBefore.match(/([a-zA-Z0-9_.]+)$/);

  if (!tokenMatch || tokenMatch[1].length < 1) {
    hideAutocomplete();
    return;
  }

  const token = tokenMatch[1];
  const suggestions = computeSuggestions(token);

  if (suggestions.length === 0) {
    hideAutocomplete();
    return;
  }

  activeSuggestions = suggestions;
  selectedSuggestionIndex = 0;
  renderAutocompleteDropdown(token);
  positionAutocompleteDropdown(cursorPos);
  showAutocomplete();
}

function computeSuggestions(rawToken) {
  const token = rawToken.toLowerCase();
  const results = [];

  // Check if token contains a table/alias dot (e.g. `c.name` or `customers.email`)
  let targetTable = null;
  let colToken = token;

  if (token.includes(".")) {
    const parts = token.split(".");
    const tablePart = parts[0];
    colToken = parts.slice(1).join(".");

    // Find table matching tablePart or table alias
    targetTable = currentTablesData.find(t => t.name.toLowerCase() === tablePart);
  }

  // 1. Table suggestions (if no dot in token)
  if (!token.includes(".")) {
    currentTablesData.forEach(t => {
      const nameLower = t.name.toLowerCase();
      if (nameLower.startsWith(token) || nameLower.includes(token)) {
        results.push({
          text: t.name,
          insertText: t.name,
          type: "table",
          meta: `${t.row_count.toLocaleString()} rows`,
          score: nameLower.startsWith(token) ? 100 : 50
        });
      }
    });
  }

  // 2. Column suggestions
  if (targetTable) {
    // Specific table columns
    (targetTable.columns || []).forEach(c => {
      const nameLower = c.name.toLowerCase();
      if (!colToken || nameLower.startsWith(colToken) || nameLower.includes(colToken)) {
        results.push({
          text: c.name,
          insertText: c.name,
          type: "column",
          meta: `${c.type} · ${targetTable.name}`,
          score: nameLower.startsWith(colToken) ? 90 : 40,
          prefixToReplace: colToken
        });
      }
    });
  } else {
    // All known columns
    currentTablesData.forEach(t => {
      (t.columns || []).forEach(c => {
        const nameLower = c.name.toLowerCase();
        if (nameLower.startsWith(token) || nameLower.includes(token)) {
          // Avoid duplicate columns across tables if identical
          if (!results.some(r => r.text === c.name && r.type === "column")) {
            results.push({
              text: c.name,
              insertText: c.name,
              type: "column",
              meta: `${c.type} · ${t.name}`,
              score: nameLower.startsWith(token) ? 80 : 35
            });
          }
        }
      });
    });
  }

  // 3. SQL Clauses & Keywords (if no dot)
  if (!token.includes(".")) {
    SQL_CLAUSES.forEach(kw => {
      const kwLower = kw.text.toLowerCase();
      if (kwLower.startsWith(token) || kwLower.includes(token)) {
        results.push({
          text: kw.text,
          insertText: kw.insertText,
          type: "clause",
          meta: kw.meta,
          score: kwLower.startsWith(token) ? 70 : 30
        });
      }
    });

    // 4. SQL Analytical Functions
    SQL_FUNCTIONS.forEach(fn => {
      const fnLower = fn.text.toLowerCase();
      if (fnLower.startsWith(token) || fnLower.includes(token)) {
        results.push({
          text: fn.text,
          insertText: fn.insertText,
          type: "function",
          meta: fn.meta,
          score: fnLower.startsWith(token) ? 60 : 25
        });
      }
    });
  }

  // Sort by score descending
  results.sort((a, b) => b.score - a.score);

  // Return top 12 items
  return results.slice(0, 12);
}

function renderAutocompleteDropdown(token) {
  const filterToken = token.includes(".") ? token.split(".").pop() : token;

  autocompleteItemsList.innerHTML = activeSuggestions.map((item, idx) => {
    const isSelected = idx === selectedSuggestionIndex;
    const badgeClass = getBadgeClass(item.type);
    const badgeLabel = getBadgeLabel(item.type);
    const highlightedName = highlightMatch(item.text, filterToken);

    return `
      <div class="autocomplete-item ${isSelected ? 'selected' : ''}" data-index="${idx}" onmousedown="selectSuggestion(${idx})">
        <div class="autocomplete-item-left">
          <span class="item-badge ${badgeClass}">${badgeLabel}</span>
          <span class="autocomplete-item-name">${highlightedName}</span>
        </div>
        <span class="autocomplete-item-meta">${escapeHtml(item.meta || '')}</span>
      </div>
    `;
  }).join('');
}

function getBadgeClass(type) {
  if (type === "clause") return "badge-clause";
  if (type === "table") return "badge-table";
  if (type === "column") return "badge-column";
  if (type === "function") return "badge-function";
  return "badge-clause";
}

function getBadgeLabel(type) {
  if (type === "clause") return "CLAUSE";
  if (type === "table") return "TABLE";
  if (type === "column") return "COLUMN";
  if (type === "function") return "FUNC";
  return "SQL";
}

function highlightMatch(text, token) {
  if (!token) return escapeHtml(text);
  const regex = new RegExp(`(${escapeRegex(token)})`, "gi");
  return text.replace(regex, '<span class="match-highlight">$1</span>');
}

function escapeRegex(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function positionAutocompleteDropdown(cursorPos) {
  try {
    const coords = getCaretCoordinates(duckdbSqlInput, cursorPos);
    const editorRect = editorCard.getBoundingClientRect();
    
    // Position below the line
    let topPos = coords.top + 26;
    let leftPos = coords.left;

    // Clamp within editor bounds
    const maxLeft = duckdbSqlInput.clientWidth - 370;
    if (leftPos > maxLeft) leftPos = Math.max(16, maxLeft);
    if (leftPos < 16) leftPos = 16;

    sqlAutocompleteDropdown.style.top = `${topPos}px`;
    sqlAutocompleteDropdown.style.left = `${leftPos}px`;
  } catch (_) {
    sqlAutocompleteDropdown.style.top = "100px";
    sqlAutocompleteDropdown.style.left = "30px";
  }
}

function showAutocomplete() {
  sqlAutocompleteDropdown.removeAttribute("hidden");
  isAutocompleteVisible = true;
}

function hideAutocomplete() {
  sqlAutocompleteDropdown.setAttribute("hidden", "");
  isAutocompleteVisible = false;
  activeSuggestions = [];
}

function handleEditorKeyDown(e) {
  // If suggestions are visible
  if (isAutocompleteVisible && activeSuggestions.length > 0) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectedSuggestionIndex = (selectedSuggestionIndex + 1) % activeSuggestions.length;
      renderSelectedSuggestion();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      selectedSuggestionIndex = (selectedSuggestionIndex - 1 + activeSuggestions.length) % activeSuggestions.length;
      renderSelectedSuggestion();
      return;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      // If Ctrl/Cmd+Enter, user wants to execute query
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        hideAutocomplete();
        executeDuckDbQuery();
        return;
      }
      e.preventDefault();
      selectSuggestion(selectedSuggestionIndex);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      hideAutocomplete();
      return;
    }
  }

  // Regular shortcuts
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    hideAutocomplete();
    executeDuckDbQuery();
    return;
  }

  // Ctrl+Space to manually trigger suggestions
  if ((e.ctrlKey || e.metaKey) && e.key === " ") {
    e.preventDefault();
    handleEditorInput();
    return;
  }

  // Tab indents 2 spaces when no dropdown
  if (e.key === "Tab" && !isAutocompleteVisible) {
    e.preventDefault();
    insertAtCursor(duckdbSqlInput, "  ");
    return;
  }
}

function renderSelectedSuggestion() {
  const items = autocompleteItemsList.querySelectorAll(".autocomplete-item");
  items.forEach((item, idx) => {
    if (idx === selectedSuggestionIndex) {
      item.classList.add("selected");
      item.scrollIntoView({ block: "nearest" });
    } else {
      item.classList.remove("selected");
    }
  });
}

window.selectSuggestion = function(index) {
  if (index < 0 || index >= activeSuggestions.length) return;
  const suggestion = activeSuggestions[index];

  const cursorPos = duckdbSqlInput.selectionStart;
  const textBefore = duckdbSqlInput.value.substring(0, cursorPos);
  const textAfter = duckdbSqlInput.value.substring(cursorPos);

  // Find the token start
  const tokenMatch = textBefore.match(/([a-zA-Z0-9_.]+)$/);
  if (!tokenMatch) {
    hideAutocomplete();
    return;
  }

  const token = tokenMatch[1];
  let replaceStart = cursorPos - token.length;

  // If token had a dot (e.g. `c.col`), replace only after the dot if column
  if (token.includes(".") && suggestion.type === "column") {
    const dotPos = token.lastIndexOf(".");
    replaceStart = cursorPos - (token.length - dotPos - 1);
  }

  const insertion = suggestion.insertText || suggestion.text;
  duckdbSqlInput.value = duckdbSqlInput.value.substring(0, replaceStart) + insertion + textAfter;

  // Place cursor after inserted text
  let newCursorPos = replaceStart + insertion.length;
  // If function ends with '()', place cursor inside parentheses
  if (insertion.endsWith("()") || insertion.endsWith("')")) {
    newCursorPos -= 1;
  } else if (insertion.endsWith("() OVER (ORDER BY )")) {
    newCursorPos -= 1;
  }

  duckdbSqlInput.selectionStart = duckdbSqlInput.selectionEnd = newCursorPos;
  duckdbSqlInput.focus();

  hideAutocomplete();
};

// Caret coordinates calculator for textarea
function getCaretCoordinates(textarea, position) {
  const div = document.createElement("div");
  const style = window.getComputedStyle(textarea);
  
  const properties = [
    "direction", "boxSizing", "width", "height", "overflowX", "overflowY",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "borderStyle",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "fontStyle", "fontVariant", "fontWeight", "fontStretch", "fontSize",
    "fontSizeAdjust", "lineHeight", "fontFamily", "textAlign", "textTransform",
    "textIndent", "textDecoration", "letterSpacing", "wordSpacing", "tabSize", "MozTabSize"
  ];

  properties.forEach(prop => {
    div.style[prop] = style[prop];
  });

  div.style.position = "absolute";
  div.style.visibility = "hidden";
  div.style.whiteSpace = "pre-wrap";
  div.style.wordWrap = "break-word";
  div.style.top = "0px";
  div.style.left = "0px";

  const text = textarea.value.substring(0, position);
  div.textContent = text;

  const span = document.createElement("span");
  span.textContent = textarea.value.substring(position) || ".";
  div.appendChild(span);

  document.body.appendChild(div);
  
  const coords = {
    top: span.offsetTop - textarea.scrollTop + textarea.offsetTop,
    left: span.offsetLeft - textarea.scrollLeft + textarea.offsetLeft
  };

  document.body.removeChild(div);
  return coords;
}

// =========================================================
// Query Execution
// =========================================================
async function executeDuckDbQuery(sqlQuery) {
  const query = (sqlQuery || duckdbSqlInput.value).trim();
  if (!query) return;

  setLoadingState(true);

  try {
    const res = await fetch(`${API_BASE}/api/playground/duckdb`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql: query, max_rows: 1000 })
    });

    const data = await res.json();

    if (!res.ok) {
      renderErrorState(data.detail || "Query execution failed.");
      return;
    }

    currentResultData = data;
    renderResults(data);
    saveToHistory(query);

    // If query modified tables (CREATE/DROP/ALTER), refresh tables sidebar
    if (data.tables) {
      currentTablesData = data.tables;
      renderTablesSidebar(currentTablesData);
    }
  } catch (err) {
    renderErrorState(`Network or Server error: ${err.message}`);
  } finally {
    setLoadingState(false);
  }
}

function renderResults(data) {
  resRowCount.textContent = `${(data.row_count || 0).toLocaleString()}`;
  resLatency.textContent = `${data.latency_ms || 0}ms`;
  resEngine.textContent = data.engine || "DuckDB";

  exportCsvBtn.disabled = !data.rows || data.rows.length === 0;
  exportJsonBtn.disabled = !data.rows || data.rows.length === 0;

  if (!data.columns || data.columns.length === 0 || !data.rows || data.rows.length === 0) {
    resultsBodyWrap.innerHTML = `
      <div class="duck-empty-state">
        <span class="duck-empty-icon">✓</span>
        <span>Query executed successfully. Result set is empty.</span>
      </div>
    `;
    return;
  }

  const columns = data.columns;
  const types = data.types || [];
  const rows = data.rows;

  let tableHtml = `
    <table class="duck-table">
      <thead>
        <tr>
          <th style="width:40px;color:var(--text-dim);font-weight:400;text-align:right;">#</th>
          ${columns.map((col, cIdx) => `
            <th>
              <span>${escapeHtml(col)}</span>
              ${types[cIdx] ? `<span class="col-type-tag">${escapeHtml(types[cIdx])}</span>` : ''}
            </th>
          `).join('')}
        </tr>
      </thead>
      <tbody>
  `;

  rows.forEach((row, rowIdx) => {
    tableHtml += `<tr>`;
    tableHtml += `<td style="color:var(--text-dim);font-size:10.5px;text-align:right;">${rowIdx + 1}</td>`;
    columns.forEach(col => {
      const val = row[col];
      if (val === null || val === undefined) {
        tableHtml += `<td class="val-null">null</td>`;
      } else if (typeof val === "number") {
        tableHtml += `<td style="color:#79c0ff;font-variant-numeric:tabular-nums;">${val.toLocaleString()}</td>`;
      } else if (typeof val === "boolean") {
        tableHtml += `<td style="color:#ff7b72;">${val}</td>`;
      } else {
        tableHtml += `<td>${escapeHtml(String(val))}</td>`;
      }
    });
    tableHtml += `</tr>`;
  });

  tableHtml += `</tbody></table>`;
  resultsBodyWrap.innerHTML = tableHtml;
}

function renderErrorState(errorMessage) {
  resRowCount.textContent = "0";
  resLatency.textContent = "0ms";
  exportCsvBtn.disabled = true;
  exportJsonBtn.disabled = true;

  resultsBodyWrap.innerHTML = `
    <div class="duck-error-box">
      <strong>⚠️ DuckDB Execution Error:</strong>\n\n${escapeHtml(errorMessage)}
    </div>
  `;
}

function setLoadingState(isLoading) {
  runDuckDbBtn.disabled = isLoading;
  if (isLoading) {
    resultsBodyWrap.innerHTML = `
      <div class="duck-loading-box">
        <div class="duck-loading-spinner"></div>
        <span>Executing in DuckDB vectorized engine…</span>
      </div>
    `;
  }
}

// =========================================================
// History & Templates
// =========================================================
function saveToHistory(query) {
  if (!query) return;
  queryHistory = queryHistory.filter(q => q !== query);
  queryHistory.unshift(query);
  if (queryHistory.length > 8) queryHistory.pop();
  localStorage.setItem("duckdb_history", JSON.stringify(queryHistory));
  renderHistoryChips();
}

function renderHistoryChips() {
  if (!queryHistory || queryHistory.length === 0) {
    playgroundHistory.innerHTML = "";
    return;
  }

  playgroundHistory.innerHTML = `
    <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.06em;">Recent:</span>
    ${queryHistory.map(q => `
      <button class="history-item-chip" type="button" title="${escapeHtml(q)}" onclick="loadHistoryQuery(this)">
        ${escapeHtml(q)}
      </button>
    `).join('')}
  `;
}

window.loadHistoryQuery = function(btn) {
  const q = btn.title || btn.textContent.trim();
  duckdbSqlInput.value = q;
  duckdbSqlInput.focus();
  executeDuckDbQuery(q);
};

templatesBar.addEventListener("click", (e) => {
  const chip = e.target.closest(".template-chip");
  if (!chip) return;
  const q = chip.dataset.query;
  if (q) {
    duckdbSqlInput.value = q;
    duckdbSqlInput.focus();
    executeDuckDbQuery(q);
  }
});

// =========================================================
// Editor Controls & Formatting
// =========================================================
runDuckDbBtn.addEventListener("click", () => {
  executeDuckDbQuery();
});

clearEditorBtn.addEventListener("click", () => {
  duckdbSqlInput.value = "";
  duckdbSqlInput.focus();
  hideAutocomplete();
});

formatSqlBtn.addEventListener("click", () => {
  const raw = duckdbSqlInput.value;
  if (!raw.trim()) return;

  const keywords = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN", "ON", "UNION ALL", "UNION", "WITH", "AS", "INSERT INTO", "VALUES", "CREATE TABLE", "DROP TABLE", "SUMMARIZE", "EXPLAIN", "DESCRIBE"];
  let formatted = raw;
  keywords.forEach(kw => {
    const reg = new RegExp(`\\b${kw}\\b`, "gi");
    formatted = formatted.replace(reg, kw);
  });
  duckdbSqlInput.value = formatted;
});

copySqlBtn.addEventListener("click", async () => {
  const sql = duckdbSqlInput.value;
  if (!sql) return;
  await navigator.clipboard.writeText(sql);
  const origText = copySqlBtn.textContent;
  copySqlBtn.textContent = "Copied!";
  setTimeout(() => { copySqlBtn.textContent = origText; }, 1500);
});

// =========================================================
// Dataset Upload Integration for DuckDB Playground
// =========================================================
function setupUploadModal() {
  if (!uploadToggle || !uploadModal) return;

  uploadToggle.addEventListener("click", () => {
    uploadModal.removeAttribute("hidden");
    backdropOverlay.removeAttribute("hidden");
    resetUploadForm();
  });

  const closeUpload = () => {
    uploadModal.setAttribute("hidden", "");
    backdropOverlay.setAttribute("hidden", "");
  };

  uploadCloseBtn?.addEventListener("click", closeUpload);
  cancelUploadBtn?.addEventListener("click", closeUpload);
  backdropOverlay?.addEventListener("click", closeUpload);

  fileInput?.addEventListener("change", () => {
    if (fileInput.files.length > 0) {
      handleSelectedFile(fileInput.files[0]);
    }
  });

  ["dragenter", "dragover"].forEach(eventName => {
    dropzone?.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag-over");
    });
  });

  ["dragleave", "drop"].forEach(eventName => {
    dropzone?.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-over");
    });
  });

  dropzone?.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length > 0) {
      fileInput.files = e.dataTransfer.files;
      handleSelectedFile(e.dataTransfer.files[0]);
    }
  });

  uploadForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!fileInput.files || fileInput.files.length === 0) {
      alert("Please select a file to ingest.");
      return;
    }

    const file = fileInput.files[0];
    const tableNameVal = customTableName.value.trim();

    uploadProgressBox.removeAttribute("hidden");
    submitUploadBtn.disabled = true;
    cancelUploadBtn.disabled = true;
    progressStatusLabel.textContent = "Uploading & Ingesting Dataset…";
    progressPercent.textContent = "0%";
    progressBarFill.style.width = "0%";
    progressDetail.textContent = `Streaming "${file.name}" to DuckDB & PostgreSQL…`;

    const formData = new FormData();
    formData.append("file", file);
    if (tableNameVal) {
      formData.append("table_name", tableNameVal);
    }

    try {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/api/upload`, true);

      xhr.upload.onprogress = (evt) => {
        if (evt.lengthComputable) {
          const pct = Math.round((evt.loaded / evt.total) * 100);
          progressPercent.textContent = `${pct}%`;
          progressBarFill.style.width = `${pct}%`;
          if (pct === 100) {
            progressDetail.textContent = "Ingesting into DuckDB analytical engine…";
          }
        }
      };

      xhr.onload = async () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const resp = JSON.parse(xhr.responseText);
          const finalTable = resp.details?.table_name || tableNameVal || file.name.split('.')[0];
          
          progressStatusLabel.textContent = "✓ Ingestion Complete!";
          progressDetail.textContent = `Dataset available as table: "${finalTable}"`;
          progressBarFill.style.background = "var(--accent)";

          setTimeout(async () => {
            closeUpload();
            await loadDuckDbTables();
            duckdbSqlInput.value = `SELECT * FROM "${finalTable}" LIMIT 20;`;
            duckdbSqlInput.focus();
            executeDuckDbQuery();
          }, 1200);
        } else {
          let errDetail = "Upload failed.";
          try {
            errDetail = JSON.parse(xhr.responseText).detail || errDetail;
          } catch (_) {}
          progressStatusLabel.textContent = "❌ Ingestion Failed";
          progressDetail.textContent = errDetail;
          progressBarFill.style.background = "var(--error)";
          submitUploadBtn.disabled = false;
          cancelUploadBtn.disabled = false;
        }
      };

      xhr.onerror = () => {
        progressStatusLabel.textContent = "❌ Network Error";
        progressDetail.textContent = "Could not connect to server.";
        progressBarFill.style.background = "var(--error)";
        submitUploadBtn.disabled = false;
        cancelUploadBtn.disabled = false;
      };

      xhr.send(formData);
    } catch (uploadErr) {
      progressStatusLabel.textContent = "❌ Error";
      progressDetail.textContent = uploadErr.message;
      submitUploadBtn.disabled = false;
      cancelUploadBtn.disabled = false;
    }
  });
}

function handleSelectedFile(file) {
  selectedFileName.textContent = file.name;
  selectedFileSize.textContent = formatBytes(file.size);
  selectedFileInfo.removeAttribute("hidden");
  if (!customTableName.value.trim()) {
    const rawName = file.name.split(".")[0];
    customTableName.value = rawName.toLowerCase().replace(/[^a-z0-9_]/g, "_").substring(0, 50);
  }
}

function resetUploadForm() {
  uploadForm.reset();
  selectedFileInfo.setAttribute("hidden", "");
  uploadProgressBox.setAttribute("hidden", "");
  progressBarFill.style.width = "0%";
  progressBarFill.style.background = "var(--accent-blue)";
  submitUploadBtn.disabled = false;
  cancelUploadBtn.disabled = false;
}

function formatBytes(bytes) {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

// =========================================================
// Exports
// =========================================================
exportCsvBtn.addEventListener("click", () => {
  if (!currentResultData || !currentResultData.rows || !currentResultData.columns) return;
  const cols = currentResultData.columns;
  const rows = currentResultData.rows;

  const headerRow = cols.map(c => `"${c.replace(/"/g, '""')}"`).join(",");
  const dataRows = rows.map(r => cols.map(c => {
    const val = r[c];
    if (val === null || val === undefined) return "";
    return `"${String(val).replace(/"/g, '""')}"`;
  }).join(","));

  const csvContent = [headerRow, ...dataRows].join("\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  downloadFile(blob, `duckdb_export_${Date.now()}.csv`);
});

exportJsonBtn.addEventListener("click", () => {
  if (!currentResultData || !currentResultData.rows) return;
  const jsonStr = JSON.stringify(currentResultData.rows, null, 2);
  const blob = new Blob([jsonStr], { type: "application/json" });
  downloadFile(blob, `duckdb_export_${Date.now()}.json`);
});

function downloadFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// =========================================================
// Helpers
// =========================================================
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function insertAtCursor(textarea, text) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const val = textarea.value;
  textarea.value = val.substring(0, start) + text + val.substring(end);
  textarea.selectionStart = textarea.selectionEnd = start + text.length;
  textarea.focus();
}
