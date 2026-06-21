function fileBasename(path) {
  const s = String(path || '').trim();
  if (!s) return '';
  return s.split(/[\\/]/).filter(Boolean).pop() || s;
}

function compactPath(path) {
  const s = String(path || '').trim();
  if (!s) return '';
  const parts = s.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 3) return s;
  const prefix = s.startsWith('/') ? '/' : '';
  return `${prefix}${parts[0]}/.../${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

/* Use folder picker to get path then trim to file (user types or we use the
   folder approach — open folder then user picks manually). */
async function pickTiffFile(id) {
  try {
    const e = _tiffEntries.find(e => e.id === id);
    const d = await api('/api/system/select_file', {start: e ? e.path : ''});
    if (d && d.path && !d.cancelled) {
      if (e) { e.path = d.path; e.frames = null; e.scale = null; }
      renderTiffList();
      await scanFrameCounts(false);
      scheduleGifPreview();
    }
  } catch(ex) { toast('File picker error: ' + ex.message, true); }
}

function toggleScaleMode() {
  const auto = gifChecked('gifAutoScale', true);
  const row = document.getElementById('manualScaleRow');
  const input = document.getElementById('gifPxPerUm');
  if (row) row.style.opacity = auto ? '0.55' : '1';
  if (input) input.disabled = auto;
}

function shortScaleInfo(info) {
  if (!info) return '';
  const px = Number(info.pixel_size_um);
  if (Number.isFinite(px) && px > 0) return `${px.toFixed(4)} µm/px`;
  return info.scale_source || '';
}

function formatScaleInfo(d) {
  if (!d) return '';
  const px = Number(d.pixel_size_um);
  const ppum = Number(d.pixels_per_um);
  const source = d.scale_source || 'manual px/um';
  let bits = [];
  if (Number.isFinite(px) && px > 0) bits.push(`${px.toFixed(6)} µm/px`);
  if (Number.isFinite(ppum) && ppum > 0) bits.push(`${ppum.toFixed(4)} px/µm`);
  bits.push(source);
  return bits.join(' · ');
}

function scheduleGifPreview() {
  clearTimeout(_gifPreviewTimer);
  _gifPreviewTimer = setTimeout(refreshGifPreview, 250);
}

function getPreviewEntry() {
  if (_availableActiveIndex >= 0 && _availableActiveIndex < _availableTiffs.length) {
    const f = _availableTiffs[_availableActiveIndex];
    const queued = _tiffEntries.find(e => entryKey(e.path) === entryKey(f.path));
    return {
      path: f.path,
      name: f.name || fileBasename(f.path),
      slices: queued ? queued.slices : '',
    };
  }
  const first = _tiffEntries.find(e => e.path && e.path.trim());
  if (!first) return null;
  return {
    path: first.path,
    name: fileBasename(first.path),
    slices: first.slices || '',
  };
}

function gifProjectRoot() {
  return gifTrimmedValue('folderPath');
}

function gifPrimaryFile() {
  const entry = getPreviewEntry();
  return entry ? entry.path : '';
}

function collectGifFilePayload() {
  return {
    tiffEntries: _tiffEntries.map(e => ({...e})),
    roiPolygons: _roiPolygons.map(p => ({...p, points: Array.isArray(p.points) ? p.points.map(pt => ({...pt})) : []})),
    cropRects: _cropRects.map(r => ({...r})),
  };
}

function restoreGifFilePayload(payload, includeQueue) {
  if (!payload || typeof payload !== 'object') return;
  if (includeQueue && Array.isArray(payload.tiffEntries)) {
    _tiffEntries = payload.tiffEntries.map(e => ({
      id: e.id || ('te_' + (_entryCounter++)),
      path: e.path || '',
      frames: e.frames ?? null,
      slices: e.slices || '',
      scale: e.scale || null,
    }));
    _entryCounter = Math.max(_entryCounter, _tiffEntries.length + 1);
    renderTiffList();
    scanFrameCounts(false);
  }
  if (Array.isArray(payload.roiPolygons)) {
    _roiPolygons = payload.roiPolygons.map(p => ({...p, points: Array.isArray(p.points) ? p.points.map(pt => ({...pt})) : []}));
    const maxPolygonIdx = _roiPolygons.reduce((m, p) => Math.max(m, parseInt(String(p.label || '').replace(/\D/g, ''), 10) || 0), 0);
    _nextPolygonIdx = maxPolygonIdx + 1;
    renderPolygonList();
  }
  if (Array.isArray(payload.cropRects)) {
    _cropRects = payload.cropRects.map(r => ({...r}));
    const maxCropIdx = _cropRects.reduce((m, r) => Math.max(m, parseInt(String(r.label || '').replace(/\D/g, ''), 10) || 0), 0);
    _nextCropRectIdx = maxCropIdx + 1;
    renderCropRectList();
  }
  updateGifCropControls();
  drawPolygons();
}

window.dpApplyRunManifest = manifest => {
  dpApplyRunManifestFallback(manifest);
  const params = manifest.parameters || {};
  if (params.settings) applyGifPrefs(params.settings);
  if (Array.isArray(params.roi_polygons)) {
    _roiPolygons = params.roi_polygons.map(p => ({...p, points: Array.isArray(p.points) ? p.points.map(pt => ({...pt})) : []}));
    renderPolygonList();
  }
  if (manifest.input_files && manifest.input_files.length) {
    _tiffEntries = manifest.input_files
      .filter(rec => rec.path)
      .map((rec, i) => ({id: 'manifest_' + i, path: rec.path, frames: null, slices: rec.slices || '', scale: null}));
    _entryCounter = _tiffEntries.length + 1;
    const folderEl = gifElement('folderPath');
    if (_tiffEntries.length && folderEl) folderEl.value = manifest.project_root || dpPathDir(_tiffEntries[0].path);
    renderTiffList();
    scanFrameCounts(false);
  }
  scheduleGifPreview();
  setStatus('status', 'GIF parameters loaded from run manifest', 'ok');
};

function renderGifFileProfileOptions(data) {
  _gifFileProfileState = data || null;
  const select = document.getElementById('gifFileProfileSelect');
  if (!select) return;
  if (!gifPrimaryFile()) {
    select.innerHTML = '<option value="">No file selected</option>';
    return;
  }
  const selected = data?.selected_profile || data?.last_profile || '';
  select.innerHTML = fileProfileOptionsHtml(data?.profiles || {}, selected);
}

async function loadGifProfileForCurrent(auto, includeQueue = false) {
  const file = gifPrimaryFile();
  if (!file) return null;
  try {
    const data = await loadFileProfile('fluorescence_gif', file, gifProjectRoot(), '');
    renderGifFileProfileOptions(data);
    const shouldApply = !!data.profile && (!auto || document.getElementById('gifAutoLoadProfile')?.checked);
    if (shouldApply) {
      applyGifPrefs(data.profile.settings || {});
      restoreGifFilePayload(data.profile.payload || {}, includeQueue);
      scheduleGifPreview();
      setStatus('gifFileProfileStatus', `Loaded file profile: ${data.selected_profile || data.last_profile || 'default'}`, data.stale ? 'warning' : 'ok');
    } else if (auto && document.getElementById('gifAutoLoadProfile')?.checked && !data.profile) {
      const created = await saveFileProfile('fluorescence_gif', file, gifProjectRoot(), 'default', collectGifPrefs(), collectGifFilePayload());
      renderGifFileProfileOptions(created);
      setStatus('gifFileProfileStatus', 'Created default file profile for this TIFF.', 'ok');
    } else {
      setStatus('gifFileProfileStatus', 'No saved profile for this TIFF yet.', '');
    }
    return data;
  } catch (e) {
    setStatus('gifFileProfileStatus', 'File profile not loaded: ' + e.message, 'warning');
    return null;
  }
}

async function loadSelectedGifFileProfile(auto) {
  const file = gifPrimaryFile();
  if (!file) { setStatus('gifFileProfileStatus', 'Select or queue a TIFF first.', 'error'); return; }
  const name = gifValue('gifFileProfileSelect');
  if (!name) { setStatus('gifFileProfileStatus', 'No profile selected.', 'error'); return; }
  try {
    const data = await loadFileProfile('fluorescence_gif', file, gifProjectRoot(), name);
    renderGifFileProfileOptions(data);
    if (data.profile) {
      applyGifPrefs(data.profile.settings || {});
      restoreGifFilePayload(data.profile.payload || {}, true);
      scheduleGifPreview();
      setStatus('gifFileProfileStatus', `Loaded file profile: ${name}`, data.stale ? 'warning' : 'ok');
    }
  } catch (e) {
    setStatus('gifFileProfileStatus', 'Load failed: ' + e.message, 'error');
  }
}

async function saveGifFileProfile(saveAs) {
  const file = gifPrimaryFile();
  if (!file) { setStatus('gifFileProfileStatus', 'Select or queue a TIFF first.', 'error'); return; }
  let name = gifValue('gifFileProfileSelect') || 'default';
  if (saveAs) {
    name = await promptProfileName(name);
    if (!name) return;
  }
  try {
    const data = await saveFileProfile('fluorescence_gif', file, gifProjectRoot(), name, collectGifPrefs(), collectGifFilePayload());
    renderGifFileProfileOptions(data);
    setStatus('gifFileProfileStatus', `Saved file profile: ${name}`, 'ok');
    toast('GIF file profile saved');
  } catch (e) {
    setStatus('gifFileProfileStatus', 'Save failed: ' + e.message, 'error');
  }
}

async function deleteSelectedGifFileProfile() {
  const file = gifPrimaryFile();
  const name = gifValue('gifFileProfileSelect');
  if (!file || !name) return;
  const confirmed = await DP.dom.confirm({
    title: 'Delete file profile?',
    message: `Delete file profile "${name}"?`,
    confirmText: 'Delete',
    danger: true,
  });
  if (!confirmed) return;
  try {
    const data = await deleteFileProfile('fluorescence_gif', file, gifProjectRoot(), name);
    renderGifFileProfileOptions(data);
    setStatus('gifFileProfileStatus', `Deleted file profile: ${name}`, 'ok');
  } catch (e) {
    setStatus('gifFileProfileStatus', 'Delete failed: ' + e.message, 'error');
  }
}

function setPreviewPlaceholder(message) {
  const wrap = document.getElementById('gifPreviewWrap');
  const ph = document.getElementById('gifPreviewPlaceholder');
  const canvas = document.getElementById('gifRoiCanvas');
  if (wrap) wrap.style.display = 'none';
  if (canvas) canvas.style.display = 'none';
  if (ph) {
    ph.style.display = '';
    ph.textContent = message;
  }
  _imgW = 0;
  _imgH = 0;
}

function setPreviewImage(b64, isGif) {
  const wrap = document.getElementById('gifPreviewWrap');
  const ph = document.getElementById('gifPreviewPlaceholder');
  const img = document.getElementById('gifPreviewImg');
  const canvas = document.getElementById('gifRoiCanvas');
  if (!wrap || !img) return;
  if (ph) ph.style.display = 'none';
  wrap.style.display = '';
  if (canvas) canvas.style.display = isGif ? 'none' : '';
  img.onload = () => {
    initRoiCanvas(!isGif);
  };
  img.src = `data:image/${isGif ? 'gif' : 'png'};base64,${b64}`;
}

function setGeneratedGifPreview(b64, metaText, outputPath) {
  const box = document.getElementById('generatedGifPreview');
  const meta = document.getElementById('generatedGifMeta');
  const name = document.getElementById('generatedGifName');
  if (!box) return;
  if (!b64) {
    box.innerHTML = '<div class="plot-placeholder">Generated GIF preview is unavailable for this output.</div>';
  } else {
    box.innerHTML = `<img src="data:image/gif;base64,${b64}" alt="generated GIF preview"/>`;
  }
  if (meta) meta.textContent = metaText || '';
  if (name) name.textContent = outputPath ? fileBasename(outputPath) : '';
}

function clearGeneratedGifPreview() {
  setGeneratedGifPreview('', 'Generate a GIF to preview the animation here.', '');
}

async function refreshGifPreview() {
  const meta = document.getElementById('gifPreviewMeta');
  const name = document.getElementById('gifPreviewName');
  const entry = getPreviewEntry();
  if (!meta) return;
  if (!entry || !entry.path) {
    setPreviewPlaceholder('Scan a folder or choose a TIFF stack to preview');
    meta.textContent = '';
    if (name) name.textContent = '';
    return;
  }
  try {
    const d = await api('/api/fluorescence/gif_preview', {
      input_path: entry.path.trim(),
      slice_spec: (entry.slices || '').trim(),
      fps: gifNumber('gifFps', 5) || 5,
      lut: gifValue('gifLut', 'Gray'),
      scale_bar_um: gifNumber('gifBarUm', 0),
      px_per_um: gifNumber('gifPxPerUm', 3.45) || 3.45,
      auto_scale: gifChecked('gifAutoScale', true),
      add_timestamp: gifLabelMode() !== 'none',
      label_mode: gifLabelMode(),
      show_roi_overlay: gifShowRoiOverlay(),
      roi_polygons: getClosedRoiPolygons(),
      ...gifCropPayload(),
    });
    if (d.error) {
      setPreviewPlaceholder(d.error);
      meta.textContent = '';
      if (name) name.textContent = '';
      return;
    }
    _imgW = d.width || 0;
    _imgH = d.height || 0;
    setPreviewImage(d.img, false);
    meta.textContent = `Slice ${d.frame} / ${d.n_frames} · ${formatScaleInfo(d)}`;
    if (name) name.textContent = entry.name || fileBasename(entry.path);
  } catch(ex) {
    setPreviewPlaceholder(ex.message || String(ex));
    meta.textContent = '';
  }
}

/* ---------- Polygon ROI overlay ---------- */

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'clearGeneratedGifPreview',
  'collectGifFilePayload',
  'compactPath',
  'deleteSelectedGifFileProfile',
  'fileBasename',
  'formatScaleInfo',
  'getPreviewEntry',
  'gifPrimaryFile',
  'gifProjectRoot',
  'loadGifProfileForCurrent',
  'loadSelectedGifFileProfile',
  'pickTiffFile',
  'refreshGifPreview',
  'renderGifFileProfileOptions',
  'restoreGifFilePayload',
  'saveGifFileProfile',
  'scheduleGifPreview',
  'setGeneratedGifPreview',
  'setPreviewImage',
  'setPreviewPlaceholder',
  'shortScaleInfo',
  'toggleScaleMode',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
