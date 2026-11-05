/* app.js — 启动引导（最后加载） */

/* ============================================================
   启动
   ============================================================ */
(async function boot() {
  resize2d();
  setLoading(true, '读取存档');
  try {
    const r = await fetch('/api/info');
    S.info = await r.json();
    $('worldName').textContent = S.info.name;
    $('iVer').textContent = S.info.version;
    $('iSeed').textContent = S.info.seed;
    $('iSpawn').textContent = `${S.info.spawn.x}, ${S.info.spawn.y}, ${S.info.spawn.z}`;
    $('iSpan').textContent = `${Math.round(S.span)} 格`;
    $('iChunk').textContent =
      `${floorDiv(S.info.spawn.x, CH)}, ${floorDiv(S.info.spawn.z, CH)}`;
    S.gx = S.info.spawn.x;
    S.gz = S.info.spawn.z;
  } catch (e) {
    console.error(e);
  } finally {
    setLoading(false);
  }
  updateBar();
  updateAutoHeight();
  await requestTile(true);

  const h = location.hash || '';
  const search = location.search || '';
  const ms = h.match(/sel=(-?\d+),(-?\d+),(-?\d+),(-?\d+)/);
  if (ms) {
    const cx0 = +ms[1], cz0 = +ms[2], cx1 = +ms[3], cz1 = +ms[4];
    S.gx = ((cx0 + cx1 + 1) * CH) / 2;
    S.gz = ((cz0 + cz1 + 1) * CH) / 2;
    S.span = clamp((cx1 - cx0 + 1) * CH * 2.2, MIN_SPAN, MAX_SPAN);
    draw2d();
    await requestTile(true);
    setSelection(cx0, cz0, cx1, cz1);
  }
  const mv = h.match(/view=(2d|3d)/);
  let savedStep = 'selection';
  try { savedStep = sessionStorage.getItem('mc3d-workflow-step') || savedStep; } catch {}
  if (mv) setView(mv[1]);
  else setStep(['selection', 'preview'].includes(savedStep) ? savedStep : 'selection');

  // 如果没有特定测试或视野哈希，默认展示正规项目主页面
  if (!h && !search.includes('skipHub=1') && !search.includes('test=1')) {
    if (typeof showHub === 'function') {
      showHub();
    }
  } else {
    if (typeof hideHub === 'function') {
      hideHub();
    }
  }
})();
