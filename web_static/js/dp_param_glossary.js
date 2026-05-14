(function () {
  window.DP = window.DP || {};
  window.DP.params = window.DP.params || {};

  const GLOSSARY = {
    'Bar \u00b5m': 'Scale bar length in micrometers.',
    'BG mode': 'Background subtraction mode used before ROI or kymograph analysis.',
    'BG ROI': 'ROI used as the background reference.',
    'Bins': 'Number of spatial bins used for kymograph summaries.',
    'Dist ms': 'Minimum spacing between detected peaks, in milliseconds.',
    'DPI': 'Raster export resolution in dots per inch.',
    'Envelope ms': 'Smoothing window applied to the signal envelope, in milliseconds.',
    'F0 stat': 'Statistic used to estimate the F0 reference value.',
    'FFT Max Hz': 'Maximum frequency shown in the FFT plot.',
    'FFT Window': 'Window function applied before FFT calculation.',
    'Fit Deg': 'Polynomial degree used for trend fitting.',
    'FPS': 'Frames per second for GIF rendering.',
    'GIF frame ms': 'Frame duration for exported ROI overlay GIFs, in milliseconds.',
    'High Hz': 'Low-pass cutoff frequency in hertz.',
    'Hi %': 'Upper percentile used for contrast stretching.',
    'I Ch': 'Current channel index.',
    'IIR Order': 'Infinite impulse response filter order.',
    'k_height': 'Multiplier applied to the baseline height threshold.',
    'k_prom': 'Multiplier applied to the baseline prominence threshold.',
    'Line p%': 'Percentile lines overlaid on kymograph summaries.',
    'Lo %': 'Lower percentile used for contrast stretching.',
    'Low Hz': 'High-pass cutoff frequency in hertz.',
    'LUT': 'Lookup table used to map intensity to display color.',
    'Main Tok': 'Filename token that identifies the main/control recording group.',
    'Manual px/\u00b5m': 'Manual pixel-per-micrometer calibration.',
    'Max ms': 'Maximum allowed peak width in milliseconds.',
    'Metric': 'Signal statistic used for summaries or plots.',
    'Min pts': 'Minimum neighboring points required by density filtering.',
    'Min width ms': 'Minimum allowed peak width in milliseconds.',
    'Notch Hz': 'Notch filter center frequency in hertz.',
    'Notch Q': 'Notch filter quality factor; higher values make a narrower notch.',
    'Padding px': 'Extra pixels included around a crop rectangle.',
    'Prom': 'Prominence: minimum peak height relative to surrounding signal.',
    'Radius \u00b5m': 'Neighborhood radius in micrometers.',
    'Range %': 'Lower and upper percentiles for contrast stretching.',
    'Ref frame': 'Reference frame used for normalization.',
    'Ref seq': 'Reference sequence used for normalization.',
    'Ring px': 'Outer ring radius for concentric ROI analysis, in pixels.',
    'Ring um': 'Outer ring radius for concentric ROI analysis, in micrometers.',
    'Scale bar um': 'Scale bar length in micrometers.',
    'SG poly': 'Savitzky-Golay smoothing polynomial order.',
    'SG win (ms)': 'Savitzky-Golay smoothing window size in milliseconds.',
    'Signal %': 'Intensity percentile threshold used for point-cloud rendering.',
    'Smooth I': 'Kymograph smoothing strength along the intensity axis.',
    'Smooth ms': 'Smoothing window size in milliseconds.',
    'Smooth T': 'Kymograph smoothing strength along the time axis.',
    'STFT Cmap': 'Colormap used for the STFT magnitude plot.',
    'STFT Max Hz': 'Maximum frequency shown in the STFT plot.',
    'STFT ms': 'Short-time Fourier transform window size in milliseconds.',
    'STFT Overlap %': 'Overlap percentage between consecutive STFT windows.',
    'Top mean %': 'Top intensity percentile averaged for summary lines.',
    'Treat Tok': 'Filename token that identifies the treatment recording group.',
    'V Ch': 'Voltage channel index.',
    'Win ms': 'Analysis window size in milliseconds.',
    'wlen ms': 'Window length used for peak width estimation, in milliseconds.',
    'X Max': 'Right edge of the displayed x-axis window.',
    'X Min': 'Left edge of the displayed x-axis window.',
    'Y Max': 'Top edge of the displayed y-axis window.',
    'Y Min': 'Bottom edge of the displayed y-axis window.',
    '\u00b1Win (ms)': 'Symmetric export window around each detected event, in milliseconds.',
  };

  function normalizeLabel(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function applyParamGlossary(root) {
    const scope = root || document;
    scope.querySelectorAll('.param-row').forEach(row => {
      if (row.title) return;
      const label = row.querySelector('.param-label');
      if (!label) return;
      const tooltip = GLOSSARY[normalizeLabel(label.textContent)];
      if (!tooltip) return;
      row.title = tooltip;
      if (!label.title) label.title = tooltip;
    });
  }

  Object.assign(window.DP.params, {
    glossary: GLOSSARY,
    applyGlossary: applyParamGlossary,
  });

  document.addEventListener('DOMContentLoaded', () => applyParamGlossary());
})();
