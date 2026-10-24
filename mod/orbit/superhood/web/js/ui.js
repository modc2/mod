/* ui.js — the page around the canvas: the KEYS panel and the city picker.
 * Everything here talks to the game through window.HOOD, which game.js
 * defines before this file runs.
 */
(function (global) {
  'use strict';

  var H = global.HOOD;
  if (!H) return;

  var LABELS = {
    left: 'Walk left', right: 'Walk right', down: 'Duck / subway',
    jump: 'Jump', run: 'Run / throw', start: 'Start',
    pause: 'Pause', mute: 'Mute'
  };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }

  /* ── city ───────────────────────────────────────────────────────────── */

  var sel = $('city');
  H.cities.forEach(function (c) {
    var o = el('option', null, titleCase(c.name));
    o.value = c.id;
    sel.appendChild(o);
  });
  sel.addEventListener('change', function () { H.setCity(sel.value); sel.blur(); });

  function titleCase(s) {
    return s.toLowerCase().replace(/\b\w/g, function (a) { return a.toUpperCase(); });
  }
  H.onCity(function (c) {
    sel.value = c.id;
    $('city-label').textContent = titleCase(c.levels['1-3']) + ', ' + titleCase(c.name);
    try {                                  // keep the URL shareable
      var u = new URL(location.href);
      u.searchParams.set('city', c.id);
      history.replaceState(null, '', u);
    } catch (e) {}
  });

  /* ── key bindings ───────────────────────────────────────────────────── */

  var panel = $('keys-panel'), rows = $('keys-rows');
  var capturing = null;                     // the action waiting for a key

  function render(binds) {
    rows.textContent = '';
    H.actions.forEach(function (a) {
      var row = el('div', 'kp-row');
      row.appendChild(el('span', 'kp-name', LABELS[a] || a));
      var keys = el('span', 'kp-keys');
      (binds[a] || []).forEach(function (code) {
        var chip = el('span', 'kp-key');
        chip.appendChild(el('kbd', null, H.keyLabel(code)));
        var x = el('button', 'kp-x', '×');
        x.title = 'unbind ' + H.keyLabel(code);
        x.addEventListener('click', function () { H.unbind(a, code); });
        chip.appendChild(x);
        keys.appendChild(chip);
      });
      if (!(binds[a] || []).length) keys.appendChild(el('span', 'kp-none', 'unbound'));
      row.appendChild(keys);
      var add = el('button', 'kp-add' + (capturing === a ? ' wait' : ''),
                   capturing === a ? 'press a key…' : '+ add');
      add.addEventListener('click', function () {
        capturing = capturing === a ? null : a;
        render(H.binds());
      });
      row.appendChild(add);
      rows.appendChild(row);
    });
    paintHints(binds);
  }

  // The footer hints are live buttons; their caps show the first bound key.
  function paintHints(binds) {
    var kbds = document.querySelectorAll('footer kbd[data-key]');
    for (var i = 0; i < kbds.length; i++) {
      var a = kbds[i].dataset.key;
      if (binds[a]) kbds[i].textContent = binds[a].length ? H.keyLabel(binds[a][0]) : '—';
    }
  }

  function openPanel() {
    H.holdInput();
    capturing = null;
    panel.hidden = false;
    render(H.binds());
  }
  function closePanel() {
    capturing = null;
    panel.hidden = true;
    H.holdInput();                          // nothing held over from the panel
  }

  $('keys-btn').addEventListener('click', function () {
    this.blur();                            // so space/enter stay the game's
    if (panel.hidden) openPanel(); else closePanel();
  });
  $('keys-close').addEventListener('click', closePanel);
  $('keys-link').addEventListener('click', function (ev) { ev.preventDefault(); openPanel(); });
  $('keys-reset').addEventListener('click', function () { capturing = null; H.resetBinds(); });
  panel.addEventListener('pointerdown', function (ev) {
    if (ev.target === panel) closePanel();  // a click on the backdrop
  });

  // Capture phase on window runs before the game's own listener, so while the
  // panel is open the game never sees a key.
  function swallow(ev) {
    if (panel.hidden) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
  }
  global.addEventListener('keydown', function (ev) {
    if (panel.hidden) return;
    swallow(ev);
    if (ev.code === 'Escape') {
      if (capturing) { capturing = null; render(H.binds()); } else closePanel();
      return;
    }
    if (capturing && !ev.repeat) {
      var a = capturing;
      capturing = null;
      H.bind(a, ev.code);                   // re-renders through onBinds
    }
  }, true);
  global.addEventListener('keyup', swallow, true);

  H.onBinds(function (b) { if (panel.hidden) paintHints(b); else render(b); });
})(window);
