/* Hermes — attach button fixes (plain JS, no build step).
 * Upstream's #fileInput carries a 30-entry `accept` list (text/*, .env, .toml, …). macOS Safari maps
 * every entry to a system file type before opening the panel and can hang doing it, freezing the
 * page on the Attach click. The list also left out video and audio. Drop it: any file can be picked
 * (the server still enforces its upload size cap). The camera input keeps its image/* + capture.
 */
(function () {
  'use strict';
  function fix() {
    const input = document.getElementById('fileInput');
    if (input && input.hasAttribute('accept')) input.removeAttribute('accept');
  }
  function start() {
    fix();
    const input = document.getElementById('fileInput');
    if (input) new MutationObserver(fix).observe(input, { attributes: true, attributeFilter: ['accept'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
