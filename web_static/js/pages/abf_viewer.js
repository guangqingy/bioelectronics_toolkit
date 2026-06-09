let currentFilePath = null;
let exportQueue = [];
let queueActiveIndex = -1;
let _folderFiles = [];
let _latestFile = null;
let _folderRefreshTimer = null;

window.dpCurrentFilePath = () => currentFilePath || '';
window.dpCurrentProjectRoot = () => document.getElementById("folderPath").value.trim();
window.dpCollectFileProfilePayload = () => ({
  exportQueue: exportQueue.slice(),
  queueActiveIndex,
});
window.dpApplyFileProfilePayload = payload => {
  if (payload && Array.isArray(payload.exportQueue)) {
    exportQueue = payload.exportQueue.slice();
    queueActiveIndex = Number.isInteger(payload.queueActiveIndex) ? payload.queueActiveIndex : (exportQueue.length ? 0 : -1);
    if (queueActiveIndex >= exportQueue.length) queueActiveIndex = exportQueue.length - 1;
    renderQueue();
  }
};
window.dpAfterFileProfileApplied = () => {
  updateAbfParameterGroups();
  renderQueue();
  plot();
};
window.dpApplyRunManifest = async manifest => {
  dpApplyRunManifestFallback(manifest);
  const inputPath = dpFirstManifestInput(manifest);
  if (inputPath) {
    currentFilePath = inputPath;
    document.getElementById("folderPath").value = manifest.project_root || dpPathDir(inputPath);
    document.getElementById("selectedFile").textContent = baseName(inputPath);
  }
  if (manifest.parameters && Array.isArray(manifest.parameters.paths)) {
    exportQueue = manifest.parameters.paths.slice();
    queueActiveIndex = exportQueue.length ? 0 : -1;
  } else if ((manifest.input_files || []).length > 1) {
    exportQueue = manifest.input_files.map(rec => rec.path).filter(Boolean);
    queueActiveIndex = exportQueue.length ? 0 : -1;
  }
  renderQueue();
  updateAbfParameterGroups();
  if (currentFilePath) plot();
};

function baseName(path) {
  return (path || '').split('/').pop() || path;
}

function setStatusBar(message, kind) {
  setStatus("status", message, kind || "");
}

function updateAbfParameterGroups() {
  dpApplyToggleGroups("rNorm", "data-rnorm-state");
}

function updateBreadcrumb(path) {
  const crumb = document.getElementById("breadcrumb");
  crumb.textContent = path || "";
}

function goUpFolder() {
  const input = document.getElementById("folderPath");
  const current = (input.value || "").trim();
  if (!current) return;
  const parts = current.split("/").filter(Boolean);
  if (!parts.length) return;
  parts.pop();
  input.value = "/" + parts.join("/");
  scanFolder();
}

function renderSubdirs(subdirs) {
  const section = document.getElementById("subdirSection");
  const list = document.getElementById("subdirList");
  if (!subdirs || !subdirs.length) {
    section.style.display = "none";
    list.innerHTML = "";
    return;
  }
  section.style.display = "block";
  list.innerHTML = subdirs.map(d =>
    `<div class="file-item" data-path="${d.path}" onclick="openSubdir(this)">${d.name}</div>`
  ).join("");
}

function openSubdir(el) {
  const p = el.getAttribute("data-path");
  document.getElementById("folderPath").value = p;
  scanFolder();
}

function renderFiles(files) {
  const list = document.getElementById("fileList");
  if (!files || !files.length) {
    list.innerHTML = '<div class="file-list-empty">No .abf files found in this folder</div>';
    return;
  }
  list.innerHTML = files.map((f, idx) =>
    `<div class="file-item" data-index="${idx}" data-path="${escHtml(f.path)}" data-mtime="${escHtml(f.mtime || '')}" title="${escHtml(f.path)}" onclick="selectFile(this)">${escHtml(f.name)}</div>`
  ).join("");
}

function updateFolderRefreshInfo(added) {
  const el = document.getElementById("folderRefreshInfo");
  if (!el) return;
  el.textContent = DP.liveFolder.infoText(_folderFiles, added || [], _latestFile);
}

function activateFilePath(path) {
  document.querySelectorAll("#fileList .file-item").forEach(e => {
    e.classList.toggle("active", e.getAttribute("data-path") === path);
  });
}

function selectFileByPath(path) {
  const el = DP.liveFolder.findFileItem("fileList", path);
  if (el) selectFile(el);
}

function openLatestFile() {
  if (!_latestFile) {
    setStatusBar("No ABF file available.", "warning");
    return;
  }
  selectFileByPath(_latestFile.path);
}

function configureFolderAutoRefresh() {
  if (_folderRefreshTimer) {
    clearInterval(_folderRefreshTimer);
    _folderRefreshTimer = null;
  }
  const enabled = document.getElementById("folderAutoRefresh")?.checked;
  if (!enabled) {
    setStatusBar("Auto refresh off.", "ok");
    return;
  }
  const seconds = Number(document.getElementById("folderRefreshSeconds")?.value || 5);
  const intervalMs = Math.max(2, seconds) * 1000;
  _folderRefreshTimer = setInterval(() => {
    scanFolder({
      preserveSelection: true,
      selectNewestAdded: document.getElementById("openNewOnRefresh")?.checked,
      selectFirstIfEmpty: false,
      reloadCurrent: false,
      silent: true,
    });
  }, intervalMs);
  setStatusBar("Auto refresh on.", "ok");
}

function renderQueue() {
  const list = document.getElementById("queueList");
  if (!list) return;
  if (!exportQueue.length) {
    list.innerHTML = '<div class="file-list-empty">Queue is empty</div>';
    return;
  }
  list.innerHTML = exportQueue.map((p, i) => {
    const active = i === queueActiveIndex ? " active" : "";
    return `<div class="file-item${active}" onclick="queueSelect(${i})" title="${p}">${baseName(p)}</div>`;
  }).join("");
}

function queueSelect(i) {
  if (i < 0 || i >= exportQueue.length) return;
  queueActiveIndex = i;
  renderQueue();
}

function queueAddCurrent() {
  if (!currentFilePath) {
    setStatusBar("Select a file first.", "warning");
    return;
  }
  if (!exportQueue.includes(currentFilePath)) {
    exportQueue.push(currentFilePath);
  }
  queueActiveIndex = exportQueue.indexOf(currentFilePath);
  renderQueue();
  setStatusBar("Added to export queue.", "ok");
}

function queueAddAll() {
  const items = document.querySelectorAll("#fileList .file-item[data-path]");
  if (!items.length) {
    setStatusBar("No ABF files to add.", "warning");
    return;
  }
  let added = 0;
  items.forEach(el => {
    const p = el.getAttribute("data-path");
    if (p && !exportQueue.includes(p)) {
      exportQueue.push(p);
      added += 1;
    }
  });
  if (queueActiveIndex < 0 && exportQueue.length) queueActiveIndex = 0;
  renderQueue();
  setStatusBar("Queue updated: +" + added + " file(s).", "ok");
}

function queueRemoveActive() {
  if (queueActiveIndex < 0 || queueActiveIndex >= exportQueue.length) {
    setStatusBar("Select one queue item to remove.", "warning");
    return;
  }
  exportQueue.splice(queueActiveIndex, 1);
  if (queueActiveIndex >= exportQueue.length) queueActiveIndex = exportQueue.length - 1;
  renderQueue();
}

function queueClear() {
  exportQueue = [];
  queueActiveIndex = -1;
  renderQueue();
  setStatusBar("Queue cleared.", "ok");
}

function queueMove(delta) {
  if (queueActiveIndex < 0 || queueActiveIndex >= exportQueue.length) return;
  const j = queueActiveIndex + delta;
  if (j < 0 || j >= exportQueue.length) return;
  const tmp = exportQueue[queueActiveIndex];
  exportQueue[queueActiveIndex] = exportQueue[j];
  exportQueue[j] = tmp;
  queueActiveIndex = j;
  renderQueue();
}

function buildExportPayload(path, fmt) {
  return {
    path,
    fmt,
    mode: "save",
    sweep: Number(document.getElementById("sweepNum").value || 0),
    channel: Number(document.getElementById("channel").value || 0),
    i_ch: document.getElementById("iCh").value === "" ? null : Number(document.getElementById("iCh").value),
    v_ch: document.getElementById("vCh").value === "" ? null : Number(document.getElementById("vCh").value),
    r_norm: document.getElementById("rNorm").checked,
    bl_pre0: document.getElementById("blPre0").value === "" ? null : Number(document.getElementById("blPre0").value),
    bl_pre1: document.getElementById("blPre1").value === "" ? null : Number(document.getElementById("blPre1").value),
    x_min: document.getElementById("xMin").value === "" ? null : Number(document.getElementById("xMin").value),
    x_max: document.getElementById("xMax").value === "" ? null : Number(document.getElementById("xMax").value),
    dsf: Number(document.getElementById("dsf").value || 1)
  };
}

function abfExportParams(fmt) {
  const payload = buildExportPayload(currentFilePath || "", fmt || "csv");
  delete payload.path;
  return payload;
}

function recordAbfExport(title, inputPaths, outputPaths, parameters, metadata, status) {
  recordRunHistory({
    view: "abf_viewer",
    title,
    status: status || "ok",
    project_root: document.getElementById("folderPath").value.trim(),
    input_files: (inputPaths || []).map(p => ({path: p, role: "source_abf"})),
    outputs: dpAsPathRecords(outputPaths || [], metadata?.output_type || "abf_output"),
    parameters: parameters || {},
    metadata: metadata || {},
  });
}

async function queueExportAllCsv() {
  if (!exportQueue.length) {
    setStatusBar("Queue is empty.", "warning");
    return;
  }
  let ok = 0;
  const total = exportQueue.length;
  const savedPaths = [];
  const warnings = [];
  for (let i = 0; i < total; i++) {
    const p = exportQueue[i];
    setStatusBar("Queue export " + (i + 1) + "/" + total + ": " + baseName(p), "loading");
    try {
      const data = await dpRunJobEndpoint("/api/abf/export_job", buildExportPayload(p, "csv"), {
        interval_ms: 1000,
        on_update: job => {
          const pct = typeof job.progress === "number" ? " " + Math.round(job.progress * 100) + "%" : "";
          const msg = job.message ? " · " + job.message : "";
          setStatusBar("Queue export " + (i + 1) + "/" + total + ": " + baseName(p) + pct + msg, "loading");
        },
      });
      if (data.error) throw new Error(data.error);
      ok += 1;
      if (data.saved_path) savedPaths.push(data.saved_path);
    } catch (e) {
      warnings.push(baseName(p) + ": " + e.message);
      // Keep exporting remaining items and report aggregate status.
    }
  }
  setStatusBar("Queue export done: " + ok + "/" + total + " saved", ok === total ? "ok" : "warning");
  recordAbfExport(
    "ABF Queue CSV Export",
    exportQueue,
    savedPaths,
    abfExportParams("csv"),
    {output_type: "csv", total, saved_count: ok},
    ok === total ? "ok" : "warning"
  );
}

async function scanFolder(options) {
  const opts = Object.assign({
    preserveSelection: false,
    selectLatest: false,
    selectNewestAdded: false,
    selectFirstIfEmpty: true,
    reloadCurrent: true,
    silent: false,
  }, options || {});
  const folder = document.getElementById("folderPath").value.trim();
  if (!folder) {
    setStatusBar("Enter a folder path.", "error");
    return;
  }
  updateBreadcrumb(folder);
  if (!opts.silent) setStatusBar("Scanning folder...", "loading");

  try {
    const data = await api("/api/abf/browse", { folder });
    if (data.error) {
      setStatusBar(data.error, "error");
      renderFiles([]);
      renderSubdirs([]);
      return;
    }
    const previousFiles = _folderFiles.slice();
    const files = DP.liveFolder.normalizeFiles(data.files || []);
    const diff = DP.liveFolder.diffFiles(previousFiles, files);
    _folderFiles = files;
    _latestFile = DP.liveFolder.newestFile(files);
    renderFiles(files);
    DP.liveFolder.markNewItems("fileList", diff.addedPaths);
    updateFolderRefreshInfo(diff.added);

    const tree = await api("/api/abf/browse/tree", { folder });
    renderSubdirs(tree.subdirs || []);

    if (files.length) {
      const currentStillAvailable = currentFilePath && files.some(f => f.path === currentFilePath);
      const newestAdded = DP.liveFolder.newestFile(diff.added);
      let targetPath = null;
      if (opts.selectLatest && _latestFile) targetPath = _latestFile.path;
      else if (opts.selectNewestAdded && newestAdded) targetPath = newestAdded.path;
      else if (opts.preserveSelection && currentStillAvailable) targetPath = currentFilePath;
      else if (!opts.preserveSelection && _latestFile) targetPath = _latestFile.path;
      else if ((!currentFilePath || !currentStillAvailable) && opts.selectFirstIfEmpty !== false && _latestFile) targetPath = _latestFile.path;

      if (targetPath) {
        if (targetPath === currentFilePath && opts.reloadCurrent === false) {
          activateFilePath(targetPath);
          if (!opts.silent) setStatusBar("Scan complete: " + files.length + " ABF file(s).", "ok");
        } else {
          selectFileByPath(targetPath);
        }
      } else if (!opts.silent) {
        setStatusBar("Scan complete: " + files.length + " ABF file(s).", "ok");
      }
    } else {
      currentFilePath = null;
      document.getElementById("selectedFile").textContent = "No file selected";
      setPlot("plotArea", null);
      setStatusBar("Scan complete. No ABF files in this folder.", "warning");
    }
  } catch (e) {
    setStatusBar("Scan failed: " + e.message, "error");
  }
}

async function selectFile(el) {
  document.querySelectorAll("#fileList .file-item").forEach(node => node.classList.remove("active"));
  el.classList.add("active");
  currentFilePath = el.getAttribute("data-path");
  document.getElementById("selectedFile").textContent = currentFilePath.split("/").pop();

  setStatusBar("Loading ABF metadata...", "loading");
  try {
    const data = await api("/api/abf/info", { path: currentFilePath });
    if (data.error) {
      setStatusBar(data.error, "error");
      return;
    }

    const channel = document.getElementById("channel");
    const iCh = document.getElementById("iCh");
    const vCh = document.getElementById("vCh");

    channel.innerHTML = "";
    iCh.innerHTML = '<option value="">None</option>';
    vCh.innerHTML = '<option value="">None</option>';

    (data.channels || []).forEach(ch => {
      const o1 = document.createElement("option");
      o1.value = String(ch.index);
      o1.textContent = ch.label;
      channel.appendChild(o1);

      const o2 = document.createElement("option");
      o2.value = String(ch.index);
      o2.textContent = ch.label;
      iCh.appendChild(o2);

      const o3 = document.createElement("option");
      o3.value = String(ch.index);
      o3.textContent = ch.label;
      vCh.appendChild(o3);
    });

    if (data.channels && data.channels.length) {
      channel.value = String(data.channels[0].index);
      iCh.value = String(data.channels[0].index);
      if (data.channels.length > 1) {
        vCh.value = String(data.channels[1].index);
      }
    }

    document.getElementById("sweepNum").max = Math.max(0, (data.num_sweeps || 1) - 1);
    document.getElementById("sweepNum").value = 0;
    document.getElementById("infoSweeps").textContent = String(data.num_sweeps || "-");
    document.getElementById("infoChannels").textContent = String(data.channel_count || "-");
    document.getElementById("infoDuration").textContent = String(data.duration_s || "-") + " s";
    document.getElementById("infoSampleRate").textContent = String(Math.round((data.sample_rate || 0) / 1000)) + " kHz";
    document.getElementById("infoSection").style.display = "block";

    await loadGenericFileProfileForCurrent(true);
    await plot();
  } catch (e) {
    setStatusBar("Metadata load failed: " + e.message, "error");
  }
}

async function plot() {
  if (!currentFilePath) {
    return;
  }

  const channel = document.getElementById("channel").value;
  if (channel === "") {
    setStatusBar("Select a channel to plot.", "warning");
    return;
  }

  const payload = {
    path: currentFilePath,
    sweep: Number(document.getElementById("sweepNum").value || 0),
    channel: Number(channel),
    i_ch: document.getElementById("iCh").value === "" ? null : Number(document.getElementById("iCh").value),
    v_ch: document.getElementById("vCh").value === "" ? null : Number(document.getElementById("vCh").value),
    r_norm: document.getElementById("rNorm").checked,
    bl_pre0: document.getElementById("blPre0").value === "" ? null : Number(document.getElementById("blPre0").value),
    bl_pre1: document.getElementById("blPre1").value === "" ? null : Number(document.getElementById("blPre1").value),
    x_min: document.getElementById("xMin").value === "" ? null : Number(document.getElementById("xMin").value),
    x_max: document.getElementById("xMax").value === "" ? null : Number(document.getElementById("xMax").value),
    y_min: document.getElementById("yMin").value === "" ? null : Number(document.getElementById("yMin").value),
    y_max: document.getElementById("yMax").value === "" ? null : Number(document.getElementById("yMax").value),
    dsf: Number(document.getElementById("dsf").value || 1)
  };

  setStatusBar("Rendering plot...", "loading");

  if (window.dpUplotAvailable && window.dpUplotAvailable()) {
    try {
      const data = await api("/api/abf/trace_data", payload);
      if (data.error) throw new Error(data.error);
      if (!window.dpRenderTrace("plotArea", data)) throw new Error("uplot-render-failed");
      setStatusBar("Ready", "ok");
      return;
    } catch (_e) {
      // Fall back to the legacy matplotlib PNG path below.
    }
  }

  await plotPng(payload);
}

async function plotPng(payload) {
  if (window.dpDestroyTrace) window.dpDestroyTrace("plotArea");
  try {
    const data = await api("/api/abf/plot", payload);
    if (data.error) {
      setStatusBar(data.error, "error");
      return;
    }
    setPlot("plotArea", data.img, "png");
    setStatusBar("Ready", "ok");
  } catch (e) {
    setStatusBar("Plot failed: " + e.message, "error");
  }
}

async function exportFig(fmt) {
  if (!currentFilePath) {
    setStatusBar("Select a file first.", "warning");
    return;
  }
  setStatusBar("Saving export...", "loading");
  try {
    const payload = buildExportPayload(currentFilePath, fmt);
    const data = await dpRunJobEndpoint("/api/abf/export_job", payload, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === "number" ? " " + Math.round(job.progress * 100) + "%" : "";
        const msg = job.message ? " · " + job.message : "";
        setStatusBar("Saving export" + pct + msg, "loading");
      },
    });
    if (data.error) throw new Error(data.error);
    const saved = data.saved_path || "saved";
    setStatusBar("Saved: " + saved, "ok");
    recordAbfExport(
      `ABF ${fmt.toUpperCase()} Export`,
      [currentFilePath],
      data.saved_path ? [data.saved_path] : [],
      payload,
      {output_type: fmt}
    );
  } catch (e) {
    setStatusBar("Export failed: " + e.message, "error");
  }
}

function exportCSV() {
  exportFig("csv");
}

window.addEventListener("load", function() {
  const params = new URLSearchParams(window.location.search);
  const rNormParam = (params.get("rnorm") || params.get("r_norm") || "").toLowerCase();
  if (["1", "true", "yes", "on"].includes(rNormParam)) {
    const rNorm = document.getElementById("rNorm");
    if (rNorm) rNorm.checked = true;
  }
  dpBindToggleGroups("rNorm", "data-rnorm-state");
  const ctrl = document.getElementById("ctrlPanel");
  const main = document.getElementById("mainContent");
  const folderInput = document.getElementById("folderPath");
  if (ctrl) ctrl.classList.add("abf-panel");
  if (main) main.classList.add("abf-main");
  if (params.get("demo") === "abf") {
    folderInput.value = DEFAULT_EXAMPLES_DIR || "examples";
  }
  renderQueue();
  if (folderInput.value.trim()) {
    scanFolder();
  } else {
    setStatusBar("Choose an ABF folder to begin.", "");
  }
});

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'abfExportParams',
  'baseName',
  'buildExportPayload',
  'configureFolderAutoRefresh',
  'exportCSV',
  'exportFig',
  'goUpFolder',
  'openLatestFile',
  'openSubdir',
  'plot',
  'plotPng',
  'queueAddAll',
  'queueAddCurrent',
  'queueClear',
  'queueExportAllCsv',
  'queueMove',
  'queueRemoveActive',
  'queueSelect',
  'recordAbfExport',
  'renderFiles',
  'renderQueue',
  'renderSubdirs',
  'scanFolder',
  'selectFile',
  'setStatusBar',
  'updateAbfParameterGroups',
  'updateBreadcrumb',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
