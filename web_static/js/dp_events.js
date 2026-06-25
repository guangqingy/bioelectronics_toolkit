const DP_DELEGATED_EVENT_TYPES = ['click', 'change', 'input'];
const DP_HANDLER_CACHE = new Map();

function dpSplitTopLevel(text, separator) {
  const parts = [];
  let current = '';
  let quote = '';
  let depth = 0;
  for (let i = 0; i < String(text || '').length; i += 1) {
    const ch = text[i];
    const prev = text[i - 1];
    if (quote) {
      current += ch;
      if (ch === quote && prev !== '\\') quote = '';
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === '(' || ch === '{' || ch === '[') depth += 1;
    if (ch === ')' || ch === '}' || ch === ']') depth = Math.max(0, depth - 1);
    if (ch === separator && depth === 0) {
      if (current.trim()) parts.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

function dpUnquote(value) {
  const text = String(value || '').trim();
  if (
    (text.startsWith("'") && text.endsWith("'")) ||
    (text.startsWith('"') && text.endsWith('"'))
  ) {
    return text.slice(1, -1).replace(/\\(["'])/g, '$1');
  }
  return null;
}

function dpResolveValue(token, element, event) {
  const text = String(token || '').trim();
  const quoted = dpUnquote(text);
  if (quoted !== null) return quoted;
  if (text === 'true') return true;
  if (text === 'false') return false;
  if (text === 'null') return null;
  if (/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(text)) return Number(text);
  if (text === 'this') return element;
  if (text === 'event') return event;
  if (text.startsWith('{') && text.endsWith('}')) {
    const out = {};
    for (const pair of dpSplitTopLevel(text.slice(1, -1), ',')) {
      const idx = pair.indexOf(':');
      if (idx <= 0) continue;
      const key = pair.slice(0, idx).trim().replace(/^['"]|['"]$/g, '');
      out[key] = dpResolveValue(pair.slice(idx + 1), element, event);
    }
    return out;
  }
  if (text.startsWith('this.') || text.startsWith('event.')) {
    return dpResolvePath(text, element, event).value;
  }
  return text;
}

function dpResolvePath(path, element, event) {
  const parts = String(path || '').split('.');
  let obj;
  let start = 1;
  if (parts[0] === 'DP') obj = window.DP;
  else if (parts[0] === 'event') obj = event;
  else if (parts[0] === 'this') obj = element;
  else if (parts[0] === 'document') obj = document;
  else {
    obj = window[parts[0]];
    start = 1;
  }
  for (let i = start; i < parts.length; i += 1) {
    if (obj == null) return {owner: null, key: parts[i], value: undefined};
    if (i === parts.length - 1) return {owner: obj, key: parts[i], value: obj[parts[i]]};
    obj = obj[parts[i]];
  }
  return {owner: window, key: parts[0], value: obj};
}

function dpInvokeStatement(statement, element, event) {
  const text = String(statement || '').trim();
  if (!text) return undefined;
  const conditional = text.match(/^if\s*\((.+)\)\s*(.+)$/);
  if (conditional) {
    const condition = conditional[1].replace(/\s+/g, '');
    if (condition === 'event.target===this' && event.target !== element) return undefined;
    return dpInvokeStatement(conditional[2], element, event);
  }

  const styleAssign = text.match(
    /^document\.getElementById\((.+)\)\.style\.([A-Za-z_$][\w$]*)\s*=\s*(.+)$/
  );
  if (styleAssign) {
    const target = document.getElementById(dpResolveValue(styleAssign[1], element, event));
    if (target) target.style[styleAssign[2]] = dpResolveValue(styleAssign[3], element, event);
    return undefined;
  }

  const call = text.match(/^([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\((.*)\)$/);
  if (!call) {
    throw new Error(`Unsupported delegated handler: ${text}`);
  }
  const ref = dpResolvePath(call[1], element, event);
  if (typeof ref.value !== 'function') {
    throw new Error(`${call[1]} is not a function.`);
  }
  const argsText = call[2].trim();
  const args = argsText
    ? dpSplitTopLevel(argsText, ',').map(arg => dpResolveValue(arg, element, event))
    : [];
  return ref.value.apply(ref.owner || element, args);
}

function dpCompileDelegatedHandler(code) {
  const key = String(code || '').trim();
  if (DP_HANDLER_CACHE.has(key)) return DP_HANDLER_CACHE.get(key);
  const statements = dpSplitTopLevel(key, ';');
  const handler = (element, event) => {
    let result;
    for (const statement of statements) result = dpInvokeStatement(statement, element, event);
    return result;
  };
  DP_HANDLER_CACHE.set(key, handler);
  return handler;
}

function dpRunDelegatedHandler(element, event, code) {
  if (!code) return;
  try {
    const result = dpCompileDelegatedHandler(code)(element, event);
    if (result === false) {
      event.preventDefault();
      event.stopPropagation();
    }
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    if (typeof showErrorBanner === 'function') showErrorBanner(message);
    else console.error(err);
  }
}

function dpBindDelegatedEvent(type) {
  const attr = `data-dp-${type}`;
  document.addEventListener(type, event => {
    const target = event.target && event.target.closest
      ? event.target.closest(`[${attr}]`)
      : null;
    if (!target) return;
    dpRunDelegatedHandler(target, event, target.getAttribute(attr));
  });
}

DP_DELEGATED_EVENT_TYPES.forEach(dpBindDelegatedEvent);
