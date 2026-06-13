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

  function chartWidth(el) {
    const outer = el.getBoundingClientRect().width || el.clientWidth || 760;
    const pad = cssPixels(el, 'padding-left') + cssPixels(el, 'padding-right');
    return Math.max(320, Math.floor(outer - pad));
  }

  function chartHeight(el, width, opts) {
    if (opts && opts.height) return Math.max(220, opts.height);
    const outer = el.getBoundingClientRect().height || el.clientHeight || 0;
    const pad = cssPixels(el, 'padding-top') + cssPixels(el, 'padding-bottom');
    const explicitHeight = Math.floor(outer - pad);
    if (explicitHeight > 0) return Math.max(220, explicitHeight);
    return Math.max(220, Math.min(420, Math.round(width * 0.46)));
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
      cursor: { drag: { x: true, y: false }, focus: { prox: 16 } },
      legend: { show: false, live: false },
      scales: { x: { time: false }, y: yScale },
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

    // Keep the chart sized to its container.
    if (!el._dpUplotResizeBound) {
      el._dpUplotResizeBound = true;
      let raf = null;
      window.addEventListener('resize', function () {
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function () {
          const c = charts[containerId];
          if (!c) return;
          const w = chartWidth(el);
          c.setSize({ width: w, height: chartHeight(el, w, opts) });
        });
      });
    }

    return true;
  }

  window.dpUplotAvailable = dpUplotAvailable;
  window.dpRenderTrace = dpRenderTrace;
  window.dpGetTrace = dpGetTrace;
  window.dpDestroyTrace = dpDestroyTrace;
})();
