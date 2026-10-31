// Fixture spring used by good.html / bad.html (underdamped, zeta 0.5, omega 0.02 rad/ms).
(function () {
  var Z = 0.5, W = 0.02, MODE = document.documentElement.getAttribute('data-mode');
  var el = document.getElementById('cta');
  var seg = { t0: 0, x0: 0, v0: 0, target: 0 };
  function pos(t, s) {
    s = s || seg;
    var dt = Math.max(0, t - s.t0), A = s.x0 - s.target, wd = W * Math.sqrt(1 - Z * Z), e = Math.exp(-Z * W * dt);
    return s.target + e * (A * Math.cos(wd * dt) + ((s.v0 + Z * W * A) / wd) * Math.sin(wd * dt));
  }
  window.addEventListener('pointerdown', function () {
    var now = performance.now(), x = pos(now), v = (pos(now + 0.5) - pos(now - 0.5)) / 1.0;
    seg = { t0: now, x0: x, v0: MODE === 'good' ? v : 0, target: seg.target === 0 ? 200 : 0 };
  });
  function frame(ts) { el.style.transform = 'translateX(' + pos(ts).toFixed(3) + 'px)'; requestAnimationFrame(frame); }
  requestAnimationFrame(frame);
})();
