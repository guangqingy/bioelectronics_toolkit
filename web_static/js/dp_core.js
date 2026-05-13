window.DP = window.DP || {};
window.DP.api = window.DP.api || {};
window.DP.dom = window.DP.dom || {};
window.DP.palette = window.DP.palette || {};
window.DP.keyboard = window.DP.keyboard || {};
window.DP.params = window.DP.params || {};
window.DP.telemetry = window.DP.telemetry || {};

Object.assign(window.DP.api, {request: api});
Object.assign(window.DP.dom, {
  escHtml,
  setStatus,
  toast,
  showErrorBanner,
  dismissErrorBanner,
  btnBusy,
});
Object.assign(window.DP.palette, {
  open: openCommandPalette,
  close: closeCommandPalette,
  render: renderCommandPalette,
});
Object.assign(window.DP.keyboard, {installGlobalShortcuts: dpInstallGlobalKeyboardShortcuts});
Object.assign(window.DP.params, {
  applyGroups: dpApplyParamGroups,
  bindGroups: dpBindParamGroups,
  applyToggleGroups: dpApplyToggleGroups,
  bindToggleGroups: dpBindToggleGroups,
});
Object.assign(window.DP.telemetry, {record: dpRecordTelemetry});

document.addEventListener('DOMContentLoaded', () => {
  installFileListFilters();
  dpEnhanceResettableSections();
  dpInstallGlobalKeyboardShortcuts();
  const commandSearch = document.getElementById('commandSearch');
  if (commandSearch) commandSearch.addEventListener('input', () => {
    _commandPaletteIndex = 0;
    renderCommandPalette();
  });
});
