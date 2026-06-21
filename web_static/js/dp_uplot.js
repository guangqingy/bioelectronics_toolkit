/* dp_uplot.js — client-side interactive trace rendering.
 *
 * Replaces the "server renders a PNG, browser shows an <img>" path for plain
 * line traces. The backend ships decimated x/y arrays (see
 * services/trace_decimate.py + /api/csv/trace_data) and uPlot draws + zooms
 * them locally, so pan/zoom and y-axis changes are instant with no round trip.
 *
 * Progressive enhancement: every entry point degrades gracefully. If uPlot is
 * not loaded, dpUplotAvailable() returns false and callers fall back to the
 * existing PNG endpoint.
 *
 * Public API:
 *   dpUplotAvailable()                       -> bool
 *   dpRenderTrace(containerId, payload, opts) -> bool   (false => fall back)
 *   dpGetTrace(containerId)                  -> uPlot|null
 *   dpDestroyTrace(containerId)
 *
 * payload shape (from trace_data_payload):
 *   { x:[...], y:[...], x_label, y_label, title, y_min, y_max,
 *     n_full, n_points, decimated }
 */
(function () {
  const charts = {};
  const resizeCleanups = {};

  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.body).getPropertyValue(name);
      return (v && v.trim()) || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function dpUplotAvailable() {
    return typeof window.uPlot !== 'undefined';
  }

  function dpDestroyTrace(containerId) {
    const cleanup = resizeCleanups[containerId];
    if (cleanup) {
      try { cleanup(); } catch (_) {}
      delete resizeCleanups[containerId];
    }

    const el = document.getElementById(containerId);
    const c = charts[containerId];
    if (c) {
      try { c.destroy(); } catch (_) {}
      delete charts[containerId];
    }
    if (el) el.classList.remove('is-uplot');
  }

  function dpGetTrace(containerId) {
    return charts[containerId] || null;
  }

  function cssPixels(el, prop) {
    const value = parseFloat(getComputedStyle(el).getPropertyValue(prop));
    return Number.isFinite(value) ? value : 0;
  }

  function inlineStylePixels(el, prop) {
    const raw = (el && el.style && el.style.getPropertyValue(prop) || '').trim();
    if (!raw || !raw.endsWith('px')) return 0;
    const value = parseFloat(raw);
    return Number.isFinite(value) ? value : 0;
  }

  function chartWidth(el) {
    const outer = el.getBoundingClientRect().width || el.clientWidth || 760;
    const pad = cssPixels(el, 'padding-left') + cssPixels(el, 'padding-right');
    return Math.max(320, Math.floor(outer - pad));
  }

  function chartHeight(el, width, opts) {
    if (opts && opts.height) return Math.max(220, opts.height);
    const pad = cssPixels(el, 'padding-top') + cssPixels(el, 'padding-bottom');
    const explicitHeight = inlineStylePixels(el, 'height') - pad;
    if (explicitHeight > 0) return Math.max(220, explicitHeight);
    const minHeight = Math.max(0, cssPixels(el, 'min-height') - pad);
    const aspectHeight = Math.round(width * ((opts && opts.aspect) || 0.46));
    const min = Math.max(220, minHeight);
    const max = Math.max(min, (opts && opts.maxHeight) || 520);
    return Math.min(max, Math.max(min, aspectHeight));
  }

  function formatCursorValue(value) {
    if (!Number.isFinite(value)) return '--';
    const abs = Math.abs(value);
    if (abs >= 1000 || (abs > 0 && abs < 0.001)) return value.toExponential(3);
    return value.toFixed(5).replace(/\.?0+$/, '');
  }

  function ensureCursorReadout(el, opts) {
    if (opts.cursorReadout === false) return null;
    let readout = el.querySelector('.plot-cursor-readout');
    if (!readout) {
      readout = document.createElement('div');
      readout.className = 'plot-cursor-readout';
      readout.textContent = 'x --, y --';
      el.appendChild(readout);
    }
    return readout;
  }

  function updateCursorReadout(readout, x, y) {
    if (!readout) return;
    readout.textContent = 'x ' + formatCursorValue(x) + ', y ' + formatCursorValue(y);
  }

  function cursorInsidePlot(u, left, top) {
    if (!Number.isFinite(left) || !Number.isFinite(top)) return false;
    const over = u && u.root ? u.root.querySelector('.u-over') : null;
    const width = over ? over.clientWidth : (u.bbox && u.bbox.width);
    const height = over ? over.clientHeight : (u.bbox && u.bbox.height);
    if (!Number.isFinite(width) || !Number.isFinite(height)) return true;
    return left >= 0 && top >= 0 && left <= width && top <= height;
  }

  function resizeChart(containerId, el, opts) {
    const c = charts[containerId];
    if (!c) return;
    const width = chartWidth(el);
    c.setSize({ width: width, height: chartHeight(el, width, opts) });
  }

  function bindChartResize(containerId, el, opts) {
    let raf = null;
    const schedule = function () {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () {
        raf = null;
        resizeChart(containerId, el, opts);
      });
    };

    let observer = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedule);
      observer.observe(el);
    }
    window.addEventListener('resize', schedule);

    resizeCleanups[containerId] = function () {
      if (raf) cancelAnimationFrame(raf);
      if (observer) observer.disconnect();
      window.removeEventListener('resize', schedule);
    };

    schedule();
  }

  function placeholder(el, text) {
    el.innerHTML = '<div class="plot-placeholder">' + (text || 'No output') + '</div>';
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function traceTable(payload, x, y) {
    const limit = Math.min(x.length, y.length, 500);
    const rows = [];
    for (let i = 0; i < limit; i += 1) {
      rows.push('<tr><td>' + esc(x[i]) + '</td><td>' + esc(y[i]) + '</td></tr>');
    }
    const note = x.length > limit ? '<div class="trace-data-note">Showing first 500 rendered points.</div>' : '';
    return `
      <details class="trace-data-fallback">
        <summary>View plotted data</summary>
        ${note}
        <div class="data-table-wrap">
          <table class="dp-table">
            <thead><tr><th>${esc(payload.x_label || 'x')}</th><th>${esc(payload.y_label || 'y')}</th></tr></thead>
            <tbody>${rows.join('')}</tbody>
          </table>
        </div>
      </details>
    `;
  }

  function dpRenderTrace(containerId, payload, opts) {
    const el = document.getElementById(containerId);
    if (!el) return false;
    if (!dpUplotAvailable()) return false;
    opts = opts || {};

    dpDestroyTrace(containerId);
    el.innerHTML = '';
    el.classList.add('is-uplot');
    const cursorReadout = ensureCursorReadout(el, opts);

    const x = (payload && payload.x) || [];
    const y = (payload && payload.y) || [];
    if (!x.length) {
      placeholder(el, 'No output');
      return true;
    }

    const lineColor = opts.color || cssVar('--blue', '#3E6AE1');
    const gridColor = cssVar('--border-muted', '#d9dde6');
    const textColor = cssVar('--pewter', '#5C5E62');

    const width = chartWidth(el);
    const height = chartHeight(el, width, opts);

    const pts = payload.decimated
      ? payload.n_points.toLocaleString() + ' / ' + payload.n_full.toLocaleString() + ' pts'
      : ((payload.n_full || x.length).toLocaleString()) + ' pts';
    const title = (payload.title ? payload.title + '  ·  ' : '') + pts;

    // Respect explicit y limits when provided; otherwise auto-fit.
    const yScale = { auto: true };
    const yMin = payload.y_min;
    const yMax = payload.y_max;
    if (yMin != null || yMax != null) {
      yScale.auto = false;
      yScale.range = function (u, dataMin, dataMax) {
        return [yMin != null ? yMin : dataMin, yMax != null ? yMax : dataMax];
      };
    }

    const o = {
      width: width,
      height: height,
      title: title,
      cursor: { drag: { x: opts.dragZoom !== false, y: false }, focus: { prox: 16 } },
      legend: { show: false, live: false },
      scales: { x: { time: false }, y: yScale },
      hooks: {
        setCursor: [
          function (u) {
            const left = u.cursor.left;
            const top = u.cursor.top;
            if (left == null || top == null) return;
            if (!cursorInsidePlot(u, left, top)) {
              updateCursorReadout(cursorReadout, NaN, NaN);
              return;
            }
            const cursor = {
              x: u.posToVal(left, 'x'),
              y: u.posToVal(top, 'y'),
              left: left,
              top: top,
            };
            updateCursorReadout(cursorReadout, cursor.x, cursor.y);
            if (typeof opts.onCursor === 'function') opts.onCursor(cursor);
          },
        ],
      },
      axes: [
        {
          label: payload.x_label || '',
          gap: 8,
          size: 46,
          stroke: textColor,
          grid: { stroke: gridColor, width: 1 },
          ticks: { stroke: gridColor, width: 1 },
        },
        {
          label: payload.y_label || '',
          gap: 8,
          size: 74,
          stroke: textColor,
          grid: { stroke: gridColor, width: 1 },
          ticks: { stroke: gridColor, width: 1 },
        },
      ],
      series: [
        { label: payload.x_label || 'x' },
        {
          label: payload.y_label || 'y',
          stroke: lineColor,
          width: 1,
          points: { show: false },
        },
      ],
    };

    const chartHost = document.createElement('div');
    chartHost.className = 'uplot-chart-host';
    chartHost.setAttribute('role', 'img');
    chartHost.setAttribute('aria-label', payload.title || 'Interactive trace plot');
    el.appendChild(chartHost);

    try {
      charts[containerId] = new window.uPlot(o, [x, y], chartHost);
      if (opts.showDataTable) el.insertAdjacentHTML('beforeend', traceTable(payload, x, y));
    } catch (e) {
      placeholder(el, 'Plot error');
      return false;
    }

    bindChartResize(containerId, el, opts);

    return true;
  }

  window.dpUplotAvailable = dpUplotAvailable;
  window.dpRenderTrace = dpRenderTrace;
  window.dpGetTrace = dpGetTrace;
  window.dpDestroyTrace = dpDestroyTrace;
})();
