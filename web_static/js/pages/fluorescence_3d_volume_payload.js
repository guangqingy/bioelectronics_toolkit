function volumeQualitySettings(forExport) {
  const quality = document.getElementById('volumeQuality')?.value || 'middle';
  const thresholdRaw = parseFloat(document.getElementById('volumeThreshold')?.value || (forExport ? '98.6' : '98.8'));
  const threshold = Math.max(80, Math.min(99.95, Number.isFinite(thresholdRaw) ? thresholdRaw : 98.8));
  const table = {
    low: forExport
      ? {max_points: 65000, max_xy: 170, max_z: 80}
      : {max_points: 45000, max_xy: 140, max_z: 55},
    middle: forExport
      ? {max_points: 110000, max_xy: 220, max_z: 120}
      : {max_points: 70000, max_xy: 180, max_z: 80},
    high: forExport
      ? {max_points: 210000, max_xy: 340, max_z: 180}
      : {max_points: 135000, max_xy: 260, max_z: 140},
  };
  const aliases = {fast: 'low', balanced: 'middle'};
  const key = aliases[quality] || quality;
  return {...(table[key] || table.middle), threshold_percentile: threshold};
}

function channelRangesPayload() {
  const d = stackDims(_currentInfo);
  const cCount = Math.max(1, Number(d.c || 1));
  const globalSignal = Math.max(0, Math.min(99.95, parseFloat(document.getElementById('volumeThreshold')?.value || '98.8') || 98.8));
  const ranges = {};
  for (let i = 0; i < cCount; i += 1) {
    let low = parseFloat(document.getElementById(`chanLow_${i}`)?.value || '1');
    let high = parseFloat(document.getElementById(`chanHigh_${i}`)?.value || '99.7');
    let signal = parseFloat(document.getElementById(`chanSignal_${i}`)?.value || String(globalSignal));
    const color = document.getElementById(`chanColor_${i}`)?.value || '';
    const enabled = document.getElementById(`chanEnabled_${i}`)?.checked !== false;
    if (!Number.isFinite(low)) low = 1;
    if (!Number.isFinite(high)) high = 99.7;
    if (!Number.isFinite(signal)) signal = globalSignal;
    low = Math.max(0, Math.min(99, low));
    high = Math.max(low + 0.1, Math.min(100, high));
    signal = Math.max(0, Math.min(99.95, signal));
    ranges[String(i)] = {enabled, low, high, signal, color};
  }
  return ranges;
}

function hasEnabledChannel() {
  const ranges = channelRangesPayload();
  return Object.values(ranges).some(r => r && r.enabled);
}

function currentVolumePayload(forExport) {
  const scaleBarRaw = parseFloat(document.getElementById('scaleBarUm')?.value || '20');
  const densityRadiusRaw = document.getElementById('volumeDensityRadius')?.value ?? '';
  const densityMinRaw = document.getElementById('volumeDensityMin')?.value ?? '';
  const densityRadius = densityRadiusRaw === '' ? null : parseFloat(densityRadiusRaw);
  const densityMin = densityMinRaw === '' ? null : parseInt(densityMinRaw, 10);
  return {
    ...currentPreviewPayload(),
    ...volumeQualitySettings(!!forExport),
    channel_mode: document.getElementById('volumeChannelMode')?.value || 'composite',
    denoise: 'Off',
    interlayer_level: document.getElementById('interlayerLevel')?.value || 'middle',
    density_mode: document.getElementById('volumeDensityMode')?.value || 'off',
    density_radius_um: Number.isFinite(densityRadius) ? Math.max(0, densityRadius) : null,
    density_min_neighbors: Number.isFinite(densityMin) ? Math.max(1, Math.min(100, densityMin)) : null,
    channel_ranges: channelRangesPayload(),
    show_scale_bar: document.getElementById('showScaleBar')?.checked !== false,
    scale_bar_um: Number.isFinite(scaleBarRaw) ? Math.max(0, scaleBarRaw) : 0,
  };
}

window.dpApplyRunManifest = async manifest => {
  dpApplyRunManifestFallback(manifest);
  const params = manifest.parameters || {};
  const inputPath = params.path || dpFirstManifestInput(manifest);
  if (inputPath) {
    document.getElementById('tiffPath').value = inputPath;
    document.getElementById('folderPath').value = manifest.project_root || dpPathDir(inputPath);
  }
  if (params.volume_quality) {
    const qualityMap = {fast: 'low', balanced: 'middle'};
    document.getElementById('volumeQuality').value = qualityMap[params.volume_quality] || params.volume_quality;
  }
  if (params.interlayer_level) document.getElementById('interlayerLevel').value = params.interlayer_level;
  if (params.density_mode) document.getElementById('volumeDensityMode').value = params.density_mode;
  if (params.density_radius_um !== undefined && params.density_radius_um !== null) {
    document.getElementById('volumeDensityRadius').value = params.density_radius_um;
  }
  if (params.density_min_neighbors !== undefined && params.density_min_neighbors !== null) {
    document.getElementById('volumeDensityMin').value = params.density_min_neighbors;
  }
  if (params.output_name) document.getElementById('outputName').value = params.output_name;
  if (inputPath && HAS_TIFF) {
    await loadTiffStack();
    if (params.channel_ranges && typeof params.channel_ranges === 'object') {
      Object.entries(params.channel_ranges).forEach(([idx, rec]) => {
        if (!rec || typeof rec !== 'object') return;
        const enabled = document.getElementById(`chanEnabled_${idx}`);
        const color = document.getElementById(`chanColor_${idx}`);
        const low = document.getElementById(`chanLow_${idx}`);
        const high = document.getElementById(`chanHigh_${idx}`);
        const signal = document.getElementById(`chanSignal_${idx}`);
        if (enabled) enabled.checked = rec.enabled !== false;
        if (color && rec.color) color.value = rec.color;
        if (low && rec.low !== undefined) low.value = rec.low;
        if (high && rec.high !== undefined) high.value = rec.high;
        if (signal && rec.signal !== undefined) signal.value = rec.signal;
      });
    }
  }
  setStatus('status', '3D stacking parameters loaded from run manifest', 'ok');
};
