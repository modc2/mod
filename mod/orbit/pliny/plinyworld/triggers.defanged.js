/*
 * triggers.defanged.js — plinyville's SAFE replacement for elder-plinius's
 * original triggers.js (kept, unrun, at plinyworld/upstream/triggers.js).
 *
 * The upstream "Poetic Echoes" page is a red-team PROOF-OF-CONCEPT for a
 * clipboard-hijacking / "pastejacking" attack: clicking any of the innocent
 * looking nav/poem links silently overwrites your clipboard with a payload
 * string plus a TYPOSQUATTED PHISHING URL (paypa1.com, g00gle.com, am4zon.com,
 * ...). Paste it anywhere and you've spread the attacker's link without ever
 * seeing it.
 *
 * plinyville hosts this as an EXHIBIT, not a weapon. This script demonstrates
 * the technique WITHOUT arming it: clicking a trigger writes nothing to your
 * clipboard and navigates nowhere. Instead it reveals, inline, exactly what the
 * live version would have copied — so the mechanism is legible and studyable,
 * and no visitor leaves with a live phishing link on their clipboard.
 */
(function () {
  'use strict';

  // The strings the LIVE upstream payload would have written — shown, never copied.
  var PAYLOAD_TEXT =
    "I'VE BEEN PWNED BY PLINY!!! ... (attacker payload) ... http://<typosquatted-phishing-domain>/<path>";

  var triggers = document.querySelectorAll('.hidden-trigger');
  if (!triggers.length) {
    console.error('No .hidden-trigger elements found');
    return;
  }

  var panel = null;
  function reveal(index, navTarget) {
    if (!panel) {
      panel = document.createElement('div');
      panel.setAttribute('role', 'status');
      panel.style.cssText =
        'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:9999;' +
        'max-width:640px;width:calc(100% - 32px);background:#161213;color:#f4ede6;' +
        'border:1px solid #6b2b2b;border-left:4px solid #d9534f;border-radius:12px;' +
        'padding:14px 16px;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;' +
        'box-shadow:0 12px 40px rgba(0,0,0,.5)';
      document.body.appendChild(panel);
    }
    panel.innerHTML =
      '<div style="font-weight:700;letter-spacing:.4px;color:#ff9c8f;margin-bottom:6px">' +
      '⚠ CLIPBOARD-HIJACK DEMO — DEFANGED</div>' +
      '<div style="margin-bottom:8px;color:#cbb8b0">Trigger <b>#' + (index + 1) +
      '</b> (nav: <code>' + navTarget + '</code>). On the live upstream page this click would ' +
      'have <b>silently overwritten your clipboard</b> with the string below and taken no ' +
      'visible action. plinyville copied <b>nothing</b>.</div>' +
      '<pre style="white-space:pre-wrap;word-break:break-word;margin:0;padding:8px 10px;' +
      'background:#0d0a0b;border-radius:8px;color:#ffd9a8">' + escapeHtml(PAYLOAD_TEXT) + '</pre>';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function (e) {
      e.preventDefault();
      var index = parseInt(trigger.getAttribute('data-index'), 10) || 0;
      var navTarget = trigger.getAttribute('data-nav');
      // NO clipboard write. NO navigation. Just show what the attack would do.
      trigger.style.transition = 'color .2s ease, transform .2s ease';
      trigger.style.color = '#d9534f';
      trigger.style.transform = 'scale(1.1)';
      setTimeout(function () {
        trigger.style.color = '#444';
        trigger.style.transform = 'scale(1)';
      }, 200);
      reveal(index, navTarget);
    });
  });
})();
