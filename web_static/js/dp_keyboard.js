function dpClickFocusedListItem(delta) {
  const active = document.activeElement;
  const list = active && active.classList && active.classList.contains('file-item')
    ? active.closest('.file-list')
    : null;
  if (!list) return false;
  const visible = Array.from(list.querySelectorAll('.file-item:not([hidden])'));
  const idx = visible.indexOf(active);
  const next = visible[idx + delta];
  if (!next) return true;
  next.focus();
  return true;
}

function dpTryGlobalAction(kind) {
  if (kind === 'save') {
    const saveFns = [
      'saveSelectedFileProfile',
      'saveGenericFileProfile',
      'saveRoiFileProfile',
      'saveGifFileProfile',
      'saveTiffFileProfile',
    ];
    for (const name of saveFns) {
      if (typeof window[name] === 'function') {
        window[name](false);
        return true;
      }
    }
  }
  if (kind === 'export') {
    const exportSelectors = [
      '#btnGenerate',
      '#btnAnalyze',
      '#btnMergeExport',
      '#btnStackExport',
      '[onclick="exportCSV()"]',
    ];
    for (const sel of exportSelectors) {
      const btn = document.querySelector(sel);
      if (btn && !btn.disabled && btn.offsetParent !== null) {
        btn.click();
        return true;
      }
    }
  }
  return false;
}

function dpInstallGlobalKeyboardShortcuts() {
  document.addEventListener('keydown', ev => {
    const tag = (ev.target && ev.target.tagName || '').toLowerCase();
    const inTextField = ['input', 'textarea', 'select'].includes(tag);
    if (ev.key === 'Escape') {
      closeShortcutModal();
      closeCommandPalette();
      dismissErrorBanner();
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      openCommandPalette();
      return;
    }
    if (document.getElementById('commandPalette')?.classList.contains('show')) {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        const count = document.querySelectorAll('#commandList .command-item').length;
        if (count) {
          _commandPaletteIndex = (_commandPaletteIndex + (ev.key === 'ArrowDown' ? 1 : -1) + count) % count;
          renderCommandPalette();
        }
        return;
      }
      if (ev.key === 'Enter') {
        ev.preventDefault();
        runCommandPaletteSelection();
        return;
      }
    }
    if (!inTextField && ev.key === '?') {
      ev.preventDefault();
      openShortcutModal();
      return;
    }
    if (!inTextField && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {
      if (dpClickFocusedListItem(ev.key === 'ArrowDown' ? 1 : -1)) ev.preventDefault();
      return;
    }
    if (!inTextField && ev.key === 'Enter' && ev.target?.classList?.contains('file-item')) {
      ev.preventDefault();
      ev.target.click();
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 's') {
      if (dpTryGlobalAction('save')) ev.preventDefault();
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'e') {
      if (dpTryGlobalAction('export')) ev.preventDefault();
    }
  });
}
