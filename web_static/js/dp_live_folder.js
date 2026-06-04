function dpLiveNormalizeFile(file) {
  if (typeof file === 'string') {
    return {name: file.split('/').pop() || file, path: file, size: null, mtime: null};
  }
  const rec = Object.assign({}, file || {});
  rec.path = String(rec.path || rec.value || '');
  rec.name = String(rec.name || rec.path.split('/').pop() || rec.path);
  rec.size = Number.isFinite(Number(rec.size)) ? Number(rec.size) : null;
  rec.mtime = Number.isFinite(Number(rec.mtime)) ? Number(rec.mtime) : null;
  return rec;
}

function dpLiveNormalizeFiles(files) {
  return (files || []).map(dpLiveNormalizeFile).filter(f => f.path);
}

function dpLiveNewestFile(files) {
  const normalized = dpLiveNormalizeFiles(files);
  if (!normalized.length) return null;
  const withMtime = normalized.filter(f => Number.isFinite(f.mtime));
  if (withMtime.length) {
    const sorted = withMtime.slice().sort((a, b) => {
      if (a.mtime !== b.mtime) return a.mtime - b.mtime;
      return a.name.localeCompare(b.name);
    });
    return sorted[sorted.length - 1];
  }
  return normalized[normalized.length - 1];
}

function dpLiveDiffFiles(previousFiles, nextFiles) {
  const previous = new Set(dpLiveNormalizeFiles(previousFiles).map(f => f.path));
  const next = dpLiveNormalizeFiles(nextFiles);
  const added = next.filter(f => !previous.has(f.path));
  return {
    added,
    addedPaths: new Set(added.map(f => f.path)),
  };
}

function dpLiveFindFileItem(listId, path) {
  const list = document.getElementById(listId);
  if (!list || !path) return null;
  return Array.from(list.querySelectorAll('.file-item[data-path]'))
    .find(el => el.getAttribute('data-path') === path) || null;
}

function dpLiveMarkNewItems(listId, addedPaths) {
  const paths = addedPaths instanceof Set ? addedPaths : new Set(addedPaths || []);
  document.querySelectorAll(`#${listId} .file-item[data-path]`).forEach(el => {
    el.classList.toggle('is-new', paths.has(el.getAttribute('data-path')));
  });
}

function dpLiveInfoText(files, added, latest) {
  const n = (files || []).length;
  const latestName = latest ? latest.name : 'None';
  const addedText = added && added.length ? ` · ${added.length} new` : '';
  return `${n} file(s) · Latest: ${latestName}${addedText}`;
}

window.DP = window.DP || {};
window.DP.liveFolder = Object.assign(window.DP.liveFolder || {}, {
  diffFiles: dpLiveDiffFiles,
  findFileItem: dpLiveFindFileItem,
  infoText: dpLiveInfoText,
  markNewItems: dpLiveMarkNewItems,
  newestFile: dpLiveNewestFile,
  normalizeFile: dpLiveNormalizeFile,
  normalizeFiles: dpLiveNormalizeFiles,
});
