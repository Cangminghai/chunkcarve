/* Tabler Icons outline subset (24x24).
 * Source: https://github.com/tabler/tabler-icons
 * MIT license and copyright: assets/licenses/tabler-icons.txt
 */
'use strict';

(function() {
  const ICONS = {
    "home": "<path d=\"M5 12l-2 0l9 -9l9 9l-2 0\" /><path d=\"M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-7\" /><path d=\"M10 12h4v4h-4l0 -4\" />",
    "layers": "<path d=\"M12 4l-8 4l8 4l8 -4l-8 -4\" /><path d=\"M4 12l8 4l8 -4\" /><path d=\"M4 16l8 4l8 -4\" />",
    "box": "<path d=\"M21 16.008v-8.018a1.98 1.98 0 0 0 -1 -1.717l-7 -4.008a2.016 2.016 0 0 0 -2 0l-7 4.008c-.619 .355 -1 1.01 -1 1.718v8.018c0 .709 .381 1.363 1 1.717l7 4.008a2.016 2.016 0 0 0 2 0l7 -4.008c.619 -.355 1 -1.01 1 -1.718\" /><path d=\"M12 22v-10\" /><path d=\"M12 12l8.73 -5.04\" /><path d=\"M3.27 6.96l8.73 5.04\" />",
    "map": "<path d=\"M12 18.5l-3 -1.5l-6 3v-13l6 -3l6 3l6 -3v7.5\" /><path d=\"M9 4v13\" /><path d=\"M15 7v5.5\" /><path d=\"M21.121 20.121a3 3 0 1 0 -4.242 0c.418 .419 1.125 1.045 2.121 1.879c1.051 -.89 1.759 -1.516 2.121 -1.879\" /><path d=\"M19 18v.01\" />",
    "grid": "<path d=\"M4 5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4\" /><path d=\"M14 5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4\" /><path d=\"M4 15a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4\" /><path d=\"M14 15a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4\" />",
    "crosshair": "<path d=\"M11.5 12a.5 .5 0 1 0 1 0a.5 .5 0 1 0 -1 0\" fill=\"currentColor\" /><path d=\"M5 12a7 7 0 1 0 14 0a7 7 0 1 0 -14 0\" /><path d=\"M12 3l0 2\" /><path d=\"M3 12l2 0\" /><path d=\"M12 19l0 2\" /><path d=\"M19 12l2 0\" />",
    "maximize": "<path d=\"M4 8v-2a2 2 0 0 1 2 -2h2\" /><path d=\"M4 16v2a2 2 0 0 0 2 2h2\" /><path d=\"M16 4h2a2 2 0 0 1 2 2v2\" /><path d=\"M16 20h2a2 2 0 0 0 2 -2v-2\" />",
    "rotate-ccw": "<path d=\"M9 14l-4 -4l4 -4\" /><path d=\"M5 10h11a4 4 0 1 1 0 8h-1\" />",
    "bookmark": "<path d=\"M18 7v14l-6 -4l-6 4v-14a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4\" />",
    "download": "<path d=\"M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2\" /><path d=\"M7 11l5 5l5 -5\" /><path d=\"M12 4l0 12\" />",
    "scissors": "<path d=\"M4 17a3 3 0 1 0 6 0a3 3 0 1 0 -6 0\" /><path d=\"M14 17a3 3 0 1 0 6 0a3 3 0 1 0 -6 0\" /><path d=\"M9.15 14.85l8.85 -10.85\" /><path d=\"M6 4l8.85 10.85\" />",
    "x": "<path d=\"M18 6l-12 12\" /><path d=\"M6 6l12 12\" />",
    "more-horizontal": "<path d=\"M4 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" /><path d=\"M11 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" /><path d=\"M18 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" />",
    "search": "<path d=\"M3 10a7 7 0 1 0 14 0a7 7 0 1 0 -14 0\" /><path d=\"M21 21l-6 -6\" />",
    "plus": "<path d=\"M12 5l0 14\" /><path d=\"M5 12l14 0\" />",
    "minus": "<path d=\"M5 12l14 0\" />",
    "folder": "<path d=\"M5 4h4l3 3h7a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-11a2 2 0 0 1 2 -2\" />",
    "folder-open": "<path d=\"M5 19l2.757 -7.351a1 1 0 0 1 .936 -.649h12.307a1 1 0 0 1 .986 1.164l-.996 5.211a2 2 0 0 1 -1.964 1.625h-14.026a2 2 0 0 1 -2 -2v-11a2 2 0 0 1 2 -2h4l3 3h7a2 2 0 0 1 2 2v2\" />",
    "chevron-down": "<path d=\"M6 9l6 6l6 -6\" />",
    "chevron-right": "<path d=\"M9 6l6 6l-6 6\" />",
    "keyboard": "<path d=\"M2 8a2 2 0 0 1 2 -2h16a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2l0 -8\" /><path d=\"M6 10l0 .01\" /><path d=\"M10 10l0 .01\" /><path d=\"M14 10l0 .01\" /><path d=\"M18 10l0 .01\" /><path d=\"M6 14l0 .01\" /><path d=\"M18 14l0 .01\" /><path d=\"M10 14l4 .01\" />",
    "info": "<path d=\"M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0\" /><path d=\"M12 9h.01\" /><path d=\"M11 12h1v4h1\" />",
    "check": "<path d=\"M5 12l5 5l10 -10\" />",
    "play": "<path d=\"M7 4v16l13 -8l-13 -8\" />",
    "pause": "<path d=\"M8 5v14\" /><path d=\"M16 5v14\" />",
    "square": "<path d=\"M7 7h10v10h-10z\" />",
    "render": "<path d=\"M8.504 9.426l3 -1.714a1 1 0 0 1 .992 0l3 1.714a1 1 0 0 1 .504 .868v3.411a1 1 0 0 1 -.504 .868l-3 1.715a1 1 0 0 1 -.992 0l-3 -1.715a1 1 0 0 1 -.504 -.868v-3.41a1 1 0 0 1 .504 -.869\" /><path d=\"M15.75 9.964l-3.75 2.036\" /><path d=\"M12 12l-3.75 -2.036\" /><path d=\"M12 12v4.071\" /><path d=\"M3 7v-2a2 2 0 0 1 2 -2h2\" /><path d=\"M3 17v2a2 2 0 0 0 2 2h2\" /><path d=\"M17 3h2a2 2 0 0 1 2 2v2\" /><path d=\"M17 21h2a2 2 0 0 0 2 -2v-2\" />",
    "trash": "<path d=\"M4 7l16 0\" /><path d=\"M10 11l0 6\" /><path d=\"M14 11l0 6\" /><path d=\"M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12\" /><path d=\"M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3\" />",
    "edit": "<path d=\"M7 7h-1a2 2 0 0 0 -2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2 -2v-1\" /><path d=\"M20.385 6.585a2.1 2.1 0 0 0 -2.97 -2.97l-8.415 8.385v3h3l8.385 -8.415\" /><path d=\"M16 5l3 3\" />",
    "package": "<path d=\"M12 3l8 4.5l0 9l-8 4.5l-8 -4.5l0 -9l8 -4.5\" /><path d=\"M12 12l8 -4.5\" /><path d=\"M12 12l0 9\" /><path d=\"M12 12l-8 -4.5\" /><path d=\"M16 5.25l-8 4.5\" />",
    "palette": "<path d=\"M12 21a9 9 0 0 1 0 -18c4.97 0 9 3.582 9 8c0 1.06 -.474 2.078 -1.318 2.828c-.844 .75 -1.989 1.172 -3.182 1.172h-2.5a2 2 0 0 0 -1 3.75a1.3 1.3 0 0 1 -1 2.25\" /><path d=\"M7.5 10.5a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" /><path d=\"M11.5 7.5a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" /><path d=\"M15.5 10.5a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" />",
    "zap": "<path d=\"M13 3l0 7l6 0l-8 11l0 -7l-6 0l8 -11\" />",
    "globe": "<path d=\"M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0\" /><path d=\"M3.6 9h16.8\" /><path d=\"M3.6 15h16.8\" /><path d=\"M11.5 3a17 17 0 0 0 0 18\" /><path d=\"M12.5 3a17 17 0 0 1 0 18\" />",
    "puzzle": "<path d=\"M4 7h3a1 1 0 0 0 1 -1v-1a2 2 0 0 1 4 0v1a1 1 0 0 0 1 1h3a1 1 0 0 1 1 1v3a1 1 0 0 0 1 1h1a2 2 0 0 1 0 4h-1a1 1 0 0 0 -1 1v3a1 1 0 0 1 -1 1h-3a1 1 0 0 1 -1 -1v-1a2 2 0 0 0 -4 0v1a1 1 0 0 1 -1 1h-3a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1h1a2 2 0 0 0 0 -4h-1a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1\" />",
    "lightbulb": "<path d=\"M3 12h1m8 -9v1m8 8h1m-15.4 -6.4l.7 .7m12.1 -.7l-.7 .7\" /><path d=\"M9 16a5 5 0 1 1 6 0a3.5 3.5 0 0 0 -1 3a2 2 0 0 1 -4 0a3.5 3.5 0 0 0 -1 -3\" /><path d=\"M9.7 17l4.6 0\" />",
    "pin": "<path d=\"M15 4.5l-4 4l-4 1.5l-1.5 1.5l7 7l1.5 -1.5l1.5 -4l4 -4\" /><path d=\"M9 15l-4.5 4.5\" /><path d=\"M14.5 4l5.5 5.5\" />",
    "compass": "<path d=\"M8 16l2 -6l6 -2l-2 6l-6 2\" /><path d=\"M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0\" /><path d=\"M12 3l0 2\" /><path d=\"M12 19l0 2\" /><path d=\"M3 12l2 0\" /><path d=\"M19 12l2 0\" />",
    "sparkles": "<path d=\"M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2m0 -12a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2m-7 12a6 6 0 0 1 6 -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6\" />",
    "clock": "<path d=\"M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0\" /><path d=\"M12 7v5l3 3\" />",
    "arrowRight": "<path d=\"M5 12l14 0\" /><path d=\"M13 18l6 -6\" /><path d=\"M13 6l6 6\" />",
    "fileText": "<path d=\"M14 3v4a1 1 0 0 0 1 1h4\" /><path d=\"M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2\" /><path d=\"M9 9l1 0\" /><path d=\"M9 13l6 0\" /><path d=\"M9 17l6 0\" />",
    "settings": "<path d=\"M19.875 6.27a2.225 2.225 0 0 1 1.125 1.948v7.284c0 .809 -.443 1.555 -1.158 1.948l-6.75 4.27a2.269 2.269 0 0 1 -2.184 0l-6.75 -4.27a2.225 2.225 0 0 1 -1.158 -1.948v-7.285c0 -.809 .443 -1.554 1.158 -1.947l6.75 -3.98a2.33 2.33 0 0 1 2.25 0l6.75 3.98h-.033\" /><path d=\"M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0\" />",
    "save": "<path d=\"M6 4h10l4 4v10a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2\" /><path d=\"M10 14a2 2 0 1 0 4 0a2 2 0 1 0 -4 0\" /><path d=\"M14 4l0 4l-6 0l0 -4\" />",
  };

  function getSvgIcon(name, size = 16, cls = '') {
    const body = ICONS[name] || ICONS['info'];
    const c = ` class="ui-icon${cls ? ' ' + cls : ''}"`;
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"${c}>${body}</svg>`;
  }

  function createIconElement(name, size = 16, cls = '') {
    const wrap = document.createElement('div');
    wrap.innerHTML = getSvgIcon(name, size, cls);
    return wrap.firstElementChild;
  }

  function renderIconsIn(container) {
    if (!container) return;
    const els = container.querySelectorAll('[data-icon]');
    els.forEach(el => {
      const name = el.getAttribute('data-icon');
      const size = parseInt(el.getAttribute('data-icon-size') || '16', 10);
      const cls = el.getAttribute('data-icon-class') || '';
      el.innerHTML = getSvgIcon(name, size, cls);
    });
  }

  window.ICONS = ICONS;
  window.getSvgIcon = getSvgIcon;
  window.createIconElement = createIconElement;
  window.renderIconsIn = renderIconsIn;
})();
