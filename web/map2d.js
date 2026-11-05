/* map2d.js — 2D 俯视地图渲染、交互与选区 */

/* ============================================================
   2D 渲染
   ============================================================ */
let reqSeq = 0, reqTimer = null, tileRequest = null;

function visibleChunks() {
  const halfW = S.span / 2;
  const halfH = viewSpanH() / 2;
  let cx0 = floorDiv(Math.floor(S.gx - halfW), CH);
  let cz0 = floorDiv(Math.floor(S.gz - halfH), CH);
  let cx1 = floorDiv(Math.ceil(S.gx + halfW), CH) + 1;
  let cz1 = floorDiv(Math.ceil(S.gz + halfH), CH) + 1;
  return { cx0, cz0, cx1, cz1 };
}

async function requestTile(immediate) {
  if (reqTimer) clearTimeout(reqTimer);
  if (tileRequest) tileRequest.abort();
  const seq = ++reqSeq;
  const run = async () => {
    const r = visibleChunks();
    const controller = new AbortController();
    tileRequest = controller;
    const px = Math.round(clamp(cv2d.width, 256, 1400));
    const url = `/api/tile?cx0=${r.cx0}&cz0=${r.cz0}&cx1=${r.cx1}&cz1=${r.cz1}` +
                `&px=${px}&layer=${S.layer}`;
    const showLoad = S.view === '2d' && !S.tileImg;
    if (showLoad) setLoading(true, '渲染地形');
    try {
      const res = await fetch(url, { signal: controller.signal });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      if (seq !== reqSeq) return;
      const img = new Image();
      const imageUrl = URL.createObjectURL(blob);
      try { img.src = imageUrl; await img.decode(); }
      finally { URL.revokeObjectURL(imageUrl); }
      if (seq !== reqSeq) return;
      S.tileImg = img;
      S.tileRange = r;
      draw2d();
    } catch (e) {
      if (e.name !== 'AbortError') console.error(e);
    } finally {
      if (tileRequest === controller) tileRequest = null;
      if (showLoad) setLoading(false);
    }
  };
  if (immediate) return run();
  reqTimer = setTimeout(run, 120);
}

function draw2d() {
  if (!cv2d.width) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = CANVAS_THEME.bg2d;
  ctx.fillRect(0, 0, cv2d.width, cv2d.height);

  if (S.tileImg && S.tileRange) {
    const r = S.tileRange;
    const p0 = worldToScreen(r.cx0 * CH, r.cz0 * CH);
    const p1 = worldToScreen(r.cx1 * CH, r.cz1 * CH);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(S.tileImg, p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
  }

  const zoom = cv2d.width / S.span;

  if (S.showGrid && zoom > 1.6) {
    const halfW = S.span / 2, halfH = viewSpanH() / 2;
    const cx0 = floorDiv(Math.floor(S.gx - halfW), CH);
    const cx1 = floorDiv(Math.ceil(S.gx + halfW), CH) + 1;
    const cz0 = floorDiv(Math.floor(S.gz - halfH), CH);
    const cz1 = floorDiv(Math.ceil(S.gz + halfH), CH) + 1;
    ctx.strokeStyle = CANVAS_THEME.grid2d;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let c = cx0; c <= cx1; c++) {
      const x = worldToScreen(c * CH, 0).x;
      ctx.moveTo(x, 0); ctx.lineTo(x, cv2d.height);
    }
    for (let c = cz0; c <= cz1; c++) {
      const y = worldToScreen(0, c * CH).y;
      ctx.moveTo(0, y); ctx.lineTo(cv2d.width, y);
    }
    ctx.stroke();
    if (zoom > 5.5) {
      ctx.fillStyle = CANVAS_THEME.gridText;
      ctx.font = `${11 * dpr}px ui-monospace, monospace`;
      for (let c = cx0; c < cx1; c++)
        for (let d = cz0; d < cz1; d++) {
          const p = worldToScreen(c * CH + 1, d * CH + 1);
          ctx.fillText(`${c},${d}`, p.x + 3 * dpr, p.y + 13 * dpr);
        }
    }
  }

  if (S.info) {
    const p = worldToScreen(S.info.spawn.x, S.info.spawn.z);
    ctx.strokeStyle = CANVAS_THEME.spawnStroke;
    ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 7 * dpr, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(p.x - 11 * dpr, p.y); ctx.lineTo(p.x + 11 * dpr, p.y);
    ctx.moveTo(p.x, p.y - 11 * dpr); ctx.lineTo(p.x, p.y + 11 * dpr);
    ctx.stroke();
  }

  // 已选区块
  if (S.sel) {
    const a = worldToScreen(S.sel.cx0 * CH, S.sel.cz0 * CH);
    const b = worldToScreen((S.sel.cx1 + 1) * CH, (S.sel.cz1 + 1) * CH);
    ctx.fillStyle = CANVAS_THEME.selFill;
    ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
    ctx.strokeStyle = CANVAS_THEME.selStroke;
    ctx.lineWidth = 2 * dpr;
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
  }

  // 框选预览
  if (action && action.type === 'box') {
    const x = Math.min(action.sx, action.ex) * dpr;
    const y = Math.min(action.sy, action.ey) * dpr;
    const w = Math.abs(action.ex - action.sx) * dpr;
    const h = Math.abs(action.ey - action.sy) * dpr;
    ctx.fillStyle = CANVAS_THEME.boxFill;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = CANVAS_THEME.boxStroke;
    ctx.lineWidth = 1.5 * dpr;
    ctx.setLineDash([6 * dpr, 4 * dpr]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
  }
}

/* ============================================================
   交互 —— 拖拽平移 / Shift·右键框选 / 单击选块 / 双击取消
   ============================================================ */
let action = null;

cv2d.addEventListener('contextmenu', e => e.preventDefault());

cv2d.addEventListener('mousedown', e => {
  if (S.view !== '2d') return;
  if (e.button === 1) return;
  e.preventDefault();
  const boxMode = e.shiftKey || e.button === 2;
  if (boxMode) {
    action = { type: 'box', sx: e.offsetX, sy: e.offsetY, ex: e.offsetX, ey: e.offsetY };
  } else {
    action = {
      type: 'pan', sx: e.offsetX, sy: e.offsetY,
      gx: S.gx, gz: S.gz, moved: false,
    };
  }
  draw2d();
});

cv2d.addEventListener('mousemove', e => {
  if (S.view !== '2d') return;
  const w = evWorld(e);
  S.hover = { x: Math.floor(w.x), z: Math.floor(w.z) };
  $('hX').textContent = S.hover.x;
  $('hZ').textContent = S.hover.z;
  $('iChunk').textContent = `${floorDiv(S.hover.x, CH)}, ${floorDiv(S.hover.z, CH)}`;

  if (!action) return;

  if (action.type === 'box') {
    action.ex = e.offsetX;
    action.ey = e.offsetY;
    draw2d();
    return;
  }

  const dx = e.offsetX - action.sx;
  const dy = e.offsetY - action.sy;
  if (Math.abs(dx) + Math.abs(dy) > 3) action.moved = true;
  if (!action.moved) return;
  const zoom = cv2d.width / S.span;
  S.gx = action.gx - dx * dpr / zoom;
  S.gz = action.gz - dy * dpr / zoom;
  draw2d();
});

function finishAction(e) {
  if (!action) return;
  if (S.view !== '2d') { action = null; return; }   // 仅在俯视图生效，避免与 2.5D 框选冲突
  const a = action;
  action = null;

  if (a.type === 'box') {
    const w0 = screenToWorld(a.sx * dpr, a.sy * dpr);
    const w1 = screenToWorld(a.ex * dpr, a.ey * dpr);
    const small = Math.abs(a.ex - a.sx) < 6 && Math.abs(a.ey - a.sy) < 6;
    if (small) {
      pickChunk(w1.x, w1.z);
    } else {
      setSelection(
        floorDiv(Math.floor(Math.min(w0.x, w1.x)), CH),
        floorDiv(Math.floor(Math.min(w0.z, w1.z)), CH),
        floorDiv(Math.floor(Math.max(w0.x, w1.x)), CH),
        floorDiv(Math.floor(Math.max(w0.z, w1.z)), CH),
      );
    }
    draw2d();
    return;
  }

  if (a.type === 'pan') {
    if (a.moved) requestTile();
    else pickChunk(evWorld(e).x, evWorld(e).z);
  }
}

window.addEventListener('mouseup', finishAction);
cv2d.addEventListener('mouseleave', () => {
  if (action && action.type === 'box') { action = null; draw2d(); }
});

function pickChunk(gx, gz) {
  const cx = floorDiv(Math.floor(gx), CH);
  const cz = floorDiv(Math.floor(gz), CH);
  setSelection(cx, cz, cx, cz);
}

cv2d.addEventListener('dblclick', e => {
  e.preventDefault();
  clearSelection();
});

cv2d.addEventListener('wheel', e => {
  e.preventDefault();
  const before = evWorld(e);
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? (cv2d.clientHeight || 900) : 1;
  const dy = clamp(e.deltaY * unit, -480, 480);
  const k = Math.exp(dy * 0.0016);
  S.span = clamp(S.span * k, MIN_SPAN, MAX_SPAN);
  const after = evWorld(e);
  S.gx += before.x - after.x;
  S.gz += before.z - after.z;
  $('iSpan').textContent = `${Math.round(S.span)} 格`;
  draw2d();
  requestTile();
}, { passive: false });

/* ============================================================
   选区
   ============================================================ */
function setSelection(cx0, cz0, cx1, cz1) {
  S.sel = {
    cx0: Math.min(cx0, cx1), cz0: Math.min(cz0, cz1),
    cx1: Math.max(cx0, cx1), cz1: Math.max(cz0, cz1),
  };
  S.box = null;   // 清空旧切片盒，以新选区构建立体世界
  if (typeof fitKey !== 'undefined') fitKey = ''; // 镜头对焦新选区
  // 自动填充 X/Z 范围（方块级），方便在切片输入框里微调
  $('x0In').value = S.sel.cx0 * CH;
  $('x1In').value = (S.sel.cx1 + 1) * CH - 1;
  $('z0In').value = S.sel.cz0 * CH;
  $('z1In').value = (S.sel.cz1 + 1) * CH - 1;
  updateAutoHeight();
  updateBar();
  draw2d();
  if (S.view === '3d') {
    if (typeof loadMini === 'function') loadMini();
    schedule3d(true);
  }
}

function clearSelection() {
  S.sel = null;
  S.box = null;
  updateAutoHeight();
  // X/Z 范围输入保留（清除选区不清空微调值，红框继续显示）
  updateBar();
  draw2d();
  if (S.view === '3d') schedule3d(true);
}

function activeRange() {
  if (S.box) {
    const b = S.box;
    return {
      cx0: floorDiv(b.x0, CH), cz0: floorDiv(b.z0, CH),
      cx1: floorDiv(b.x1, CH) + 1, cz1: floorDiv(b.z1, CH) + 1,
      n: (floorDiv(b.x1, CH) - floorDiv(b.x0, CH) + 1)
        * (floorDiv(b.z1, CH) - floorDiv(b.z0, CH) + 1),
      box: b,
    };
  }
  if (S.sel) {
    return {
      cx0: S.sel.cx0, cz0: S.sel.cz0,
      cx1: S.sel.cx1 + 1, cz1: S.sel.cz1 + 1,
      n: (S.sel.cx1 - S.sel.cx0 + 1) * (S.sel.cz1 - S.sel.cz0 + 1),
    };
  }
  const cx = floorDiv(Math.round(S.gx), CH);
  const cz = floorDiv(Math.round(S.gz), CH);
  return {
    cx0: cx - AUTO_HALF, cz0: cz - AUTO_HALF,
    cx1: cx + AUTO_HALF, cz1: cz + AUTO_HALF,
    n: (AUTO_HALF * 2) ** 2,
  };
}

function updateBar() {
  const r = activeRange();
  const s = S.sel;
  const b = S.box;
  if (b) {
    $('sChunks').textContent =
      `切片 ${b.x1 - b.x0 + 1}×${b.z1 - b.z0 + 1} 方块`;
    $('sCoords').textContent = `x ${b.x0}~${b.x1}  z ${b.z0}~${b.z1}`;
  } else {
    $('sChunks').textContent = s
      ? `${s.cx1 - s.cx0 + 1}×${s.cz1 - s.cz0 + 1} · ${r.n} 区块`
      : `${r.n} 区块（当前视野）`;
    $('sCoords').textContent = `x ${r.cx0 * CH}~${r.cx1 * CH - 1}  z ${r.cz0 * CH}~${r.cz1 * CH - 1}`;
  }

  const slicing = S.step === 'slices';
  const scale = parseFloat($(slicing ? 'sliceScale' : 'scaleSel').value);
  const thickness = parseFloat($('sliceThickness').value);
  $('sliceDimensionHint').textContent = Number.isFinite(scale) && scale > 0 && Number.isFinite(thickness) && thickness > 0
    ? `1 格边长 ${scale} mm · 每片厚 ${thickness} mm` : '请填写有效的比例和片厚';
  const yb = S.y0, yt = S.y1;
  const wBlocks = b ? (b.x1 - b.x0 + 1) : (r.cx1 - r.cx0) * CH;
  const dBlocks = b ? (b.z1 - b.z0 + 1) : (r.cz1 - r.cz0) * CH;
  let wmm = wBlocks * scale;
  let dmm = dBlocks * scale;
  let hmm = (yb !== null && yt !== null) ? (yt - yb + 1) * scale : null;
  $('sSizeLabel').textContent = slicing ? '单片尺寸（平放）' : '成品尺寸';
  if (slicing) {
    const axis = $('sliceAxis').value;
    if (axis === 'x') [wmm, dmm] = [dmm, hmm];
    if (axis === 'z') dmm = hmm;
    hmm = parseFloat($('sliceThickness').value);
  }

  if (![wmm, dmm, hmm ?? 1].every(v => Number.isFinite(v) && v > 0)) {
    $('sSize').textContent = '请填写有效尺寸';
    $('sSize').style.color = 'var(--warn)';
    return;
  }

  if (wmm <= 256 && dmm <= 256) {
    $('sSize').textContent = `${wmm} × ${dmm} mm` + (hmm ? ` × ${hmm} mm` : '');
    $('sSize').style.color = 'var(--text)';
  } else {
    $('sSize').textContent = `${wmm} × ${dmm} mm · 超出床面`;
    $('sSize').style.color = 'var(--warn)';
  }

}
