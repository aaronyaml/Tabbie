/* Hermes login — Apple skin loader. Caddy serves this at /static/login.js; it adds the
 * skin stylesheet and then loads the untouched upstream script (static/login.js?orig=1),
 * which Caddy passes through to the WebUI. */
(function () {
  var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/hx-brand/login.css?v=1';
  document.head.appendChild(l);
  var m = document.querySelector('meta[name="viewport"]');
  if (m) m.setAttribute('content', 'width=device-width,initial-scale=1,viewport-fit=cover');
  var logo = document.querySelector('.logo'); if (logo) logo.setAttribute('aria-hidden', 'true');
  document.write('<script src="static/login.js?orig=1"><\/script>');
})();
