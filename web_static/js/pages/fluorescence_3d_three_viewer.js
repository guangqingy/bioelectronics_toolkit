function setVolumeMessage(message) {
  const placeholder = document.getElementById('volumePlaceholder');
  if (placeholder) {
    placeholder.textContent = message || '';
    placeholder.style.display = message ? 'flex' : 'none';
  }
}

function disposeVolume3D() {
  if (_anim) {
    cancelAnimationFrame(_anim);
    _anim = null;
  }
  if (_resizeObserver) {
    _resizeObserver.disconnect();
    _resizeObserver = null;
  }
  if (_scene) {
    _scene.traverse(obj => {
      if (obj.geometry && typeof obj.geometry.dispose === 'function') obj.geometry.dispose();
      if (obj.material) {
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        mats.forEach(mat => mat && typeof mat.dispose === 'function' && mat.dispose());
      }
    });
  }
  if (_renderer) {
    const canvas = _renderer.domElement;
    _renderer.dispose();
    if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }
  _renderer = null;
  _scene = null;
  _camera = null;
  _controls = null;
}

function clearVolume3D(message) {
  disposeVolume3D();
  setVolumeMessage(message || 'Scan a folder or choose a TIFF stack');
  document.getElementById('volumeTitle').textContent = '3D TIFF Stack';
  document.getElementById('volumeMeta').textContent = 'Mouse drag rotates · wheel zooms · right drag pans';
}

function loadThreeModules() {
  if (!_threePromise) {
    _threePromise = Promise.all([
      import('three'),
      import('three/addons/controls/OrbitControls.js'),
    ]).then(([THREE, controls]) => ({THREE, OrbitControls: controls.OrbitControls}));
  }
  return _threePromise;
}

async function renderVolume3D(volume) {
  const viewport = document.getElementById('volumeViewport');
  if (!viewport || !volume || !volume.render) return;
  setVolumeMessage('Loading 3D engine...');
  const {THREE, OrbitControls} = await loadThreeModules();
  disposeVolume3D();

  const positions = volume.render.positions || [];
  const colors = volume.render.colors || [];
  if (!positions.length || !colors.length) throw new Error('No 3D points returned');

  const rect = viewport.getBoundingClientRect();
  const width = Math.max(360, Math.floor(rect.width || 900));
  const height = Math.max(360, Math.floor(rect.height || 620));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x08090c);
  const camera = new THREE.PerspectiveCamera(45, width / height, 0.01, 100000);
  const renderer = new THREE.WebGLRenderer({antialias: true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height);
  viewport.appendChild(renderer.domElement);

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geom.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geom.computeBoundingSphere();
  scene.add(new THREE.Points(geom, new THREE.PointsMaterial({
    size: volume.render.point_size || 1,
    vertexColors: true,
    transparent: true,
    opacity: 0.92,
    sizeAttenuation: true,
  })));

  const sphere = geom.boundingSphere || new THREE.Sphere(new THREE.Vector3(0, 0, 0), 100);
  const radius = Math.max(10, sphere.radius || 100);
  scene.add(new THREE.AxesHelper(radius * 0.7));
  const grid = new THREE.GridHelper(radius * 2.2, 10, 0x2c3445, 0x151923);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);
  scene.add(new THREE.AmbientLight(0xffffff, 1.0));

  camera.position.set(
    sphere.center.x + radius * 1.6,
    sphere.center.y + radius * 1.25,
    sphere.center.z + radius * 1.8
  );
  camera.near = Math.max(0.01, radius / 1000);
  camera.far = Math.max(1000, radius * 12);
  camera.updateProjectionMatrix();

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(sphere.center);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.update();

  _scene = scene;
  _camera = camera;
  _renderer = renderer;
  _controls = controls;

  const dims = volume.dimensions || {};
  const cal = volume.calibration || {};
  const density = volume.render.density_filter || {};
  const densityText = density.mode && density.mode !== 'off' ? ` · Density ${density.mode}` : '';
  const removedText = volume.render.density_removed ? `-${volume.render.density_removed}` : '';
  document.getElementById('volumeTitle').textContent = volume.title || '3D TIFF Stack';
  document.getElementById('volumeMeta').textContent =
    `${volume.render.n_points || 0} points${removedText} · Z ${dims.z_sampled || dims.z || 0}/${dims.z || 0} · C ${(dims.channels_rendered || []).length || 1}/${dims.c || 1} · Fill ${volume.render.interlayer_level || 'middle'}${densityText} · ${formatNumber(cal.pixel_width_um, 4)} um/px · Z ${formatNumber(cal.z_spacing_um, 4)} um`;
  setVolumeMessage('');

  const resize = () => {
    if (!_renderer || !_camera) return;
    const r = viewport.getBoundingClientRect();
    const w = Math.max(360, Math.floor(r.width || width));
    const h = Math.max(360, Math.floor(r.height || height));
    _camera.aspect = w / h;
    _camera.updateProjectionMatrix();
    _renderer.setSize(w, h);
  };
  _resizeObserver = new ResizeObserver(resize);
  _resizeObserver.observe(viewport);
  resize();

  const animate = () => {
    if (!_renderer || !_scene || !_camera) return;
    _controls?.update();
    _renderer.render(_scene, _camera);
    _anim = requestAnimationFrame(animate);
  };
  animate();
}

async function generateVolume3D() {
  if (!_currentInfo || !_currentInfo.can_3d) {
    setStatus('status', 'Load a TIFF Z-stack first', 'error');
    return;
  }
  if (!hasEnabledChannel()) {
    setStatus('status', 'Select at least one channel to render', 'error');
    return;
  }
  btnBusy('btnPreview3D', true, 'Building...');
  setVolumeMessage('Building 3D TIFF stack...');
  setStatus('status', 'Rendering 3D stack preview...', 'loading');
  try {
    const d = await api('/api/fluorescence/3d/volume', currentVolumePayload(false));
    if (d.error) throw new Error(d.error);
    await renderVolume3D(d.volume);
    const removed = d.volume.render.density_removed || 0;
    setStatus('status', `3D preview ready: ${d.volume.render.n_points || 0} points${removed ? `, ${removed} density-filtered` : ''}`, 'ok');
  } catch (e) {
    setVolumeMessage('3D preview error: ' + (e.message || String(e)));
    setStatus('status', '3D preview error: ' + (e.message || String(e)), 'error');
    showLog('3D Preview Error', e.message || String(e));
  } finally {
    btnBusy('btnPreview3D', false, 'Preview 3D Render');
  }
}

async function exportVolume3D() {
  if (!_currentInfo || !_currentInfo.can_3d) {
    setStatus('status', 'Load a TIFF Z-stack first', 'error');
    return;
  }
  if (!hasEnabledChannel()) {
    setStatus('status', 'Select at least one channel to export', 'error');
    return;
  }
  btnBusy('btnExport3D', true, 'Exporting...');
  setStatus('status', 'Exporting standalone 3D viewer...', 'loading');
  try {
    const d = await dpRunJobEndpoint('/api/fluorescence/3d/export_volume_job', {
      ...currentVolumePayload(true),
      output_name: document.getElementById('outputName').value.trim(),
      overwrite: true,
    }, {
      interval_ms: 1000,
      on_update: job => {
        const pct = typeof job.progress === 'number' ? ` ${Math.round(job.progress * 100)}%` : '';
        const msg = job.message ? ` · ${job.message}` : '';
        setStatus('status', `Exporting standalone 3D viewer${pct}${msg}`, 'loading');
      },
    });
    if (d.error) throw new Error(d.error);
    setStatus('status', `3D viewer saved: ${d.output_path}`, 'ok');
    showLog('3D Viewer Export', `Saved ${d.n_points || 0} rendered voxel point(s)\n${d.output_path}`);
    const outputName = document.getElementById('outputName').value.trim();
    recordRunHistory({
      view: 'fluorescence_3d_stacking',
      title: '3D TIFF viewer export',
      project_root: document.getElementById('folderPath').value.trim() || dpPathDir(document.getElementById('tiffPath').value.trim()),
      parameters: Object.assign(currentVolumePayload(true), {
        operation: 'export_volume_3d',
        output_name: outputName,
        volume_quality: document.getElementById('volumeQuality')?.value || 'balanced',
      }),
      input_files: [{path: document.getElementById('tiffPath').value.trim(), type: 'tiff', role: 'source_stack'}],
      outputs: [{path: d.output_path, type: 'html', role: '3d_viewer'}],
      metadata: {
        n_points: d.n_points || 0,
        z_sampled: d.z_sampled || 0,
        channels_rendered: d.channels_rendered || [],
        calibration: d.calibration || {},
      },
    });
    toast('3D viewer export complete');
  } catch (e) {
    setStatus('status', '3D export error: ' + (e.message || String(e)), 'error');
    showLog('3D Export Error', e.message || String(e));
  } finally {
    btnBusy('btnExport3D', false, 'Export 3D HTML');
  }
}

// DP.page exports for template event handlers.
window.DP = window.DP || {};
window.DP.page = window.DP.page || {};
[
  'clearVolume3D',
  'disposeVolume3D',
  'exportVolume3D',
  'generateVolume3D',
  'loadThreeModules',
  'renderVolume3D',
  'setVolumeMessage',
].forEach(name => {
  if (typeof window[name] === 'function') window.DP.page[name] = window[name];
});
