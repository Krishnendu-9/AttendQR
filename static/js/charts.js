// Hover and keyboard tooltips for the server-rendered SVG charts (templates/analytics.html).
// Every value is also in the visible labels or the "Show as table" view; tooltips only add convenience.
(function () {
  'use strict';

  function show(chart, target, event) {
    const tip = chart.querySelector('[data-chart-tip]');
    if (!tip) return;
    // Labels come from the page, but are set as text, never parsed as HTML.
    tip.querySelector('.chart-tip-value').textContent = target.dataset.tipValue || '';
    tip.querySelector('.chart-tip-label').textContent = target.dataset.tipLabel || '';
    tip.hidden = false;

    const box = chart.getBoundingClientRect();
    const mark = target.getBoundingClientRect();
    let x = event && event.clientX ? event.clientX - box.left : mark.left - box.left + mark.width / 2;
    const y = mark.top - box.top;
    // Keep the tooltip inside the chart's box.
    x = Math.min(Math.max(x, tip.offsetWidth / 2), box.width - tip.offsetWidth / 2);
    tip.style.left = x + 'px';
    tip.style.top = Math.max(y - tip.offsetHeight - 8, 0) + 'px';

    const crosshair = chart.querySelector('[data-crosshair]');
    if (crosshair && target.dataset.x) {
      crosshair.setAttribute('x1', target.dataset.x);
      crosshair.setAttribute('x2', target.dataset.x);
      crosshair.classList.remove('opacity-0');
    }
    target.classList.add('is-active');
  }

  function hide(chart, target) {
    const tip = chart.querySelector('[data-chart-tip]');
    if (tip) tip.hidden = true;
    const crosshair = chart.querySelector('[data-crosshair]');
    if (crosshair) crosshair.classList.add('opacity-0');
    if (target) target.classList.remove('is-active');
  }

  document.querySelectorAll('[data-chart]').forEach(function (chart) {
    chart.querySelectorAll('[data-tip]').forEach(function (target) {
      target.addEventListener('pointermove', function (event) { show(chart, target, event); });
      target.addEventListener('pointerleave', function () { hide(chart, target); });
      target.addEventListener('focus', function () { show(chart, target, null); });
      target.addEventListener('blur', function () { hide(chart, target); });
    });
  });
})();
