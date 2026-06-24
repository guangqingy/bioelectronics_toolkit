function dpRunDelegatedHandler(element, event, code) {
  if (!code) return;
  try {
    const result = Function('event', code).call(element, event);
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

[
  'click',
  'change',
  'input',
  'submit',
  'mousedown',
  'mouseup',
  'mousemove',
  'mouseleave',
  'wheel',
].forEach(dpBindDelegatedEvent);
