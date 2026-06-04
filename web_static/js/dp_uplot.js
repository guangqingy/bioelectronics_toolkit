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
    const c = charts[containerId];
    if (c) {
      try { c.destroy(); } catch (_) {}
      delete charts[containerId];
    }
  }

  function placeholder(el, text) {
    el.innerHTML = '<div class="plot-placeholder">' + (text || 'No output') + '</div>';
  }

  function dpRenderTrace(containerId, payload, opts) {
    const el = document.getElementById(containerId);
    if (!el) return false;
    if (!dpUplotAvailable()) return false;
    opts = opts || {};

    dpDestroyTrace(containerId);
    el.innerHTML = '';

    const x = (payload && payload.x) || [];
    const y = (payload && payload.y) || [];
    if (!x.length) {
      placeholder(el, 'No output');
      return true;
    }

    const lineColor = opts.color || cssVar('--blue', '#3E6AE1');
    const gridColor = cssVar('--border-muted', '#d9dde6');
    const textColor = cssVar('--pewter', '#5C5E62');

    const width = Math.max(320, Math.floor(el.clientWidth || 760));
    const height = Math.max(220, opts.height || Math.round(width * 0.46));

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
      legend: { live: true },
      scales: { x: { time: false }, y: yScale },
      axes: [
        {
          label: payload.x_label || '',
          stroke: textColor,
          grid: { stroke: gridColor, width: 1 },
          ticks: { stroke: gridColor, width: 1 },
        },
        {
          label: payload.y_label || '',
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

    try {
      charts[containerId] = new window.uPlot(o, [x, y], el);
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
          const w = Math.max(320, Math.floor(el.clientWidth || width));
          c.setSize({ width: w, height: Math.max(220, Math.round(w * 0.46)) });
        });
      });
    }

    return true;
  }

  window.dpUplotAvailable = dpUplotAvailable;
  window.dpRenderTrace = dpRenderTrace;
  window.dpDestroyTrace = dpDestroyTrace;
})();
