function dpControlValue(control) {
  if (control.type === 'checkbox' || control.type === 'radio') return control.checked;
  return control.value;
}

function dpSetControlValue(control, value) {
  if (control.type === 'checkbox' || control.type === 'radio') control.checked = !!value;
  else control.value = value ?? '';
  control.dispatchEvent(new Event('input', {bubbles: true}));
  control.dispatchEvent(new Event('change', {bubbles: true}));
}

function dpParamTokens(raw) {
  return String(raw || '')
    .split(/[\s,|]+/)
    .map(s => s.trim().toLowerCase())
    .filter(Boolean);
}

function dpApplyParamGroups(selectId, attr) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const current = String(select.value || '').toLowerCase();
  document.querySelectorAll(`[${attr}]`).forEach(group => {
    const tokens = dpParamTokens(group.getAttribute(attr));
    group.hidden = !(tokens.includes(current) || tokens.includes('*'));
  });
}

function dpBindParamGroups(selectId, attr) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const boundAttr = `data-param-group-bound-${attr.replace(/[^a-z0-9]+/gi, '-')}`;
  if (select.getAttribute(boundAttr) !== '1') {
    select.addEventListener('change', () => dpApplyParamGroups(selectId, attr));
    select.setAttribute(boundAttr, '1');
  }
  dpApplyParamGroups(selectId, attr);
}

function dpApplyToggleGroups(controlId, attr) {
  const control = document.getElementById(controlId);
  if (!control) return;
  const checked = !!control.checked;
  document.querySelectorAll(`[${attr}]`).forEach(group => {
    const tokens = dpParamTokens(group.getAttribute(attr));
    const wantsOn = tokens.includes('checked') || tokens.includes('on') || tokens.includes('true');
    const wantsOff = tokens.includes('unchecked') || tokens.includes('off') || tokens.includes('false');
    group.hidden = !((checked && wantsOn) || (!checked && wantsOff) || tokens.includes('*'));
  });
}

function dpBindToggleGroups(controlId, attr) {
  const control = document.getElementById(controlId);
  if (!control) return;
  const boundAttr = `data-toggle-group-bound-${attr.replace(/[^a-z0-9]+/gi, '-')}`;
  if (control.getAttribute(boundAttr) !== '1') {
    control.addEventListener('change', () => dpApplyToggleGroups(controlId, attr));
    control.setAttribute(boundAttr, '1');
  }
  dpApplyToggleGroups(controlId, attr);
}

function dpEnhanceResettableSections() {
  document.querySelectorAll('.ctrl-section[data-resettable="true"]').forEach(section => {
    if (section.dataset.resetReady === '1') return;
    const controls = Array.from(section.querySelectorAll('input, select, textarea'))
      .filter(el => !el.disabled && el.type !== 'button' && el.type !== 'submit' && el.type !== 'file');
    if (!controls.length) return;
    const defaults = controls.map(el => [el, dpControlValue(el)]);
    let label = section.querySelector('.ctrl-label');
    if (!label) return;
    if (!label.classList.contains('ctrl-label-row')) {
      label.classList.add('ctrl-label-row');
      if (!label.children.length) {
        const text = label.textContent;
        label.textContent = '';
        const span = document.createElement('span');
        span.textContent = text;
        label.appendChild(span);
      }
    }
    const btn = document.createElement('button');
    btn.className = 'btn-secondary ctrl-section-reset';
    btn.type = 'button';
    btn.title = 'Reset this section to its initial defaults';
    btn.textContent = 'Reset';
    btn.addEventListener('click', () => {
      defaults.forEach(([el, value]) => dpSetControlValue(el, value));
      setStatus('status', 'Section defaults restored', 'ok');
    });
    label.appendChild(btn);
    section.dataset.resetReady = '1';
  });
}

