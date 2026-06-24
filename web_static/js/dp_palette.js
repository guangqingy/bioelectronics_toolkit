const DP_STATIC_COMMANDS = [
  {label: 'ABF demo data', url: '/abf/viewer?demo=abf', section: 'Demo', keywords: 'sample patch clamp abf example'},
  {label: 'EChem demo data', url: '/echem/photocurrent?demo=echem', section: 'Demo', keywords: 'sample electrochemistry photocurrent csv example'},
  {label: 'Fluorescence ROI demo data', url: '/fluorescence/roi?demo=fluorescence', section: 'Demo', keywords: 'sample tiff stack fluorescence roi example'},
  {label: 'Histology naming', url: '/histology/naming', section: 'Tool', keywords: 'histology dataprocess project exported tiff raw olympus naming'},
  {label: 'Histology ROI analysis', url: '/histology/analysis', section: 'Tool', keywords: 'histology dataprocess project tiff roi sma macrophage dapi fitc cy5 threshold analysis'},
  {label: 'Run history', url: '/runs', section: 'Workflow', keywords: 'manifest previous outputs history'},
  {label: 'Version API', url: '/api/version', section: 'Developer', keywords: 'version commit api metadata'},
];

let _commandPaletteIndex = 0;

function dpCommandPaletteItems() {
  const viewCommands = [];
  if (typeof VIEW_URLS === 'object' && typeof SETTINGS_VIEW_LABELS === 'object') {
    Object.entries(VIEW_URLS).forEach(([id, url]) => {
      viewCommands.push({
        label: SETTINGS_VIEW_LABELS[id] || id.replaceAll('_', ' '),
        url,
        section: 'Tool',
        keywords: `${id.replaceAll('_', ' ')} ${url}`,
      });
    });
  }
  return viewCommands.concat(DP_STATIC_COMMANDS);
}

function dpCommandScore(command, query) {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const hay = `${command.label} ${command.section} ${command.keywords || ''} ${command.url}`.toLowerCase();
  if (hay.includes(q)) return 100 + q.length;
  let pos = 0;
  let score = 0;
  for (const ch of q) {
    const found = hay.indexOf(ch, pos);
    if (found === -1) return 0;
    score += found === pos ? 3 : 1;
    pos = found + 1;
  }
  return score;
}

function renderCommandPalette() {
  const input = document.getElementById('commandSearch');
  const list = document.getElementById('commandList');
  if (!input || !list) return;
  const query = input.value || '';
  const rows = dpCommandPaletteItems()
    .map((command, index) => ({command, index, score: dpCommandScore(command, query)}))
    .filter(row => row.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, 12)
    .map(row => row.command);
  _commandPaletteIndex = Math.min(_commandPaletteIndex, Math.max(0, rows.length - 1));
  if (!rows.length) {
    list.innerHTML = '<div class="command-empty">No matching command</div>';
    return;
  }
  list.innerHTML = rows.map((command, i) => `
    <button class="command-item ${i === _commandPaletteIndex ? 'active' : ''}" type="button" data-url="${escHtml(command.url)}">
      <span class="command-main">${escHtml(command.label)}</span>
      <span class="command-meta">${escHtml(command.section)} · ${escHtml(command.url)}</span>
    </button>
  `).join('');
  list.querySelectorAll('.command-item').forEach((btn, i) => {
    btn.addEventListener('mouseenter', () => {
      _commandPaletteIndex = i;
      list.querySelectorAll('.command-item').forEach((item, j) => {
        item.classList.toggle('active', j === _commandPaletteIndex);
      });
    });
    btn.addEventListener('click', () => runCommandPaletteSelection());
  });
}

function openCommandPalette() {
  const modal = document.getElementById('commandPalette');
  const input = document.getElementById('commandSearch');
  if (!modal || !input) return;
  _commandPaletteIndex = 0;
  input.value = '';
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');
  renderCommandPalette();
  setTimeout(() => input.focus(), 0);
}

function closeCommandPalette() {
  const modal = document.getElementById('commandPalette');
  if (!modal) return;
  modal.classList.remove('show');
  modal.setAttribute('aria-hidden', 'true');
}

function runCommandPaletteSelection() {
  const items = Array.from(document.querySelectorAll('#commandList .command-item'));
  const item = items[_commandPaletteIndex] || items[0];
  if (!item) return;
  const url = item.getAttribute('data-url');
  if (url) window.location.href = url;
}
