/* Default preview window follows terrain; explicit height edits own the range. */
const autoHeight = { enabled: true, key: null, pending: null, request: 0, controller: null };

function resetAutoHeight() {
  autoHeight.enabled = true;
  autoHeight.key = null;
  autoHeight.request++;
  autoHeight.controller?.abort();
  autoHeight.pending = null;
  S.y0 = null; S.y1 = null;
  $('yBotIn').value = ''; $('yTopIn').value = '';
}

function useManualHeight() {
  autoHeight.enabled = false;
  autoHeight.request++;
  autoHeight.controller?.abort();
  autoHeight.pending = null;
  autoHeight.key = null;
}

function heightRangeKey(range) {
  return JSON.stringify([S.info?.regionDir, range.cx0, range.cz0, range.cx1, range.cz1]);
}

function ensureAutoHeight(range = activeRange()) {
  if (!autoHeight.enabled || !S.info || range.n > 64) return Promise.resolve(true);
  const key = heightRangeKey(range);
  if (key === autoHeight.key && autoHeight.pending) return autoHeight.pending;
  if (key === autoHeight.key && S.y0 !== null && S.y1 !== null) return Promise.resolve(true);
  autoHeight.controller?.abort();
  const controller = new AbortController(), request = ++autoHeight.request;
  autoHeight.controller = controller;
  autoHeight.key = key;
  const query = new URLSearchParams({ cx0: range.cx0, cz0: range.cz0, cx1: range.cx1, cz1: range.cz1 });
  autoHeight.pending = (async () => {
    try {
      const response = await fetch('/api/height-range?' + query, { signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '无法读取地表高度');
      if (request !== autoHeight.request || !autoHeight.enabled || key !== heightRangeKey(activeRange())) return false;
      S.y0 = result.y0; S.y1 = result.y1;
      $('yBotIn').value = S.y0; $('yTopIn').value = S.y1;
      for (const id of ['yBotIn', 'yTopIn']) {
        $(id).min = result.min_y; $(id).max = result.max_y;
        $(id).title = `${result.source === 'chunk-centres' ? '区块中心地表平均' : '无地表数据，使用出生点高度'} Y=${result.surface_y.toFixed(1)} · 默认 ${result.height_blocks} 格，可手动调整`;
      }
      updateBar();
      return true;
    } catch (error) {
      if (error.name === 'AbortError' || request !== autoHeight.request) return false;
      autoHeight.key = null;
      throw error;
    } finally {
      if (request === autoHeight.request) autoHeight.pending = null;
    }
  })();
  return autoHeight.pending;
}

function updateAutoHeight() {
  ensureAutoHeight().catch(error => showErr('自动探测地表高度失败：' + error.message));
}
