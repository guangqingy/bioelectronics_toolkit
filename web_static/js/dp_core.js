window.DP = window.DP || {};
window.DP.api = window.DP.api || {};
window.DP.dom = window.DP.dom || {};
window.DP.palette = window.DP.palette || {};
window.DP.keyboard = window.DP.keyboard || {};
window.DP.params = window.DP.params || {};
window.DP.telemetry = window.DP.telemetry || {};
window.DP.folder = window.DP.folder || {};
window.DP.settings = window.DP.settings || {};
window.DP.jobs = window.DP.jobs || {};
window.DP.liveFolder = window.DP.liveFolder || {};
window.DP.page = window.DP.page || {};

Object.assign(window.DP.api, {request: api});
Object.assign(window.DP.dom, {
  escHtml,
  setStatus,
  toast,
  showErrorBanner,
  dismissErrorBanner,
  openShortcutModal,
  closeShortcutModal,
  confirm: dpConfirmAction,
  prompt: dpPromptAction,
  btnBusy,
  enhanceEmptyStates: dpEnhanceEmptyStates,
  enhancePlotImages: dpEnhancePlotImages,
  useExamplesDir: dpUseExamplesDir,
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
  setControlValue: dpSetParamControlValue,
});
Object.assign(window.DP.telemetry, {record: dpRecordTelemetry});
Object.assign(window.DP.folder, {
  pick: pickFolder,
  pickFile,
  pickForSettings: pickFolderForSettings,
});
Object.assign(window.DP.settings, {
  open: openPrefsModal,
  close: closePrefsModal,
  openTab: openPrefsTab,
  renderView: renderPrefsView,
});
Object.assign(window.DP.jobs, {
  load: loadBackgroundJobs,
  cancel: cancelBackgroundJob,
  cancelActive: () => dpJobCancel(_dpActiveJobId),
});
[
  'applyPrefsToCurrentPage',
  'deleteSelectedGenericFileProfile',
  'loadCurrentPageIntoSettings',
  'loadPrefsModal',
  'loadRunHistoryForCurrentProject',
  'loadSelectedGenericFileProfile',
  'logoutServer',
  'pickFolderForSettings',
  'renderPrefsView',
  'resetGenericPageDefaults',
  'restoreGenericPageDefaults',
  'saveGenericFileProfile',
  'saveGenericPageDefaults',
  'savePrefsJson',
  'savePrefsVisual',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});

document.addEventListener('DOMContentLoaded', () => {
  installFileListFilters();
  dpEnhanceEmptyStates();
  dpEnhanceExampleEntrypoints();
  dpInstallPlotImageObserver();
  dpEnhanceResettableSections();
  dpInstallGlobalKeyboardShortcuts();
  const commandSearch = document.getElementById('commandSearch');
  if (commandSearch) commandSearch.addEventListener('input', () => {
    _commandPaletteIndex = 0;
    renderCommandPalette();
  });
});
