/* Applies the saved theme before the page paints, so dark mode never flashes white.
 * Loaded without defer in <head>. The choice (light, dark or system) is kept per browser. */
(function () {
  'use strict';
  var KEY = 'attendqr-theme';
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function stored() {
    try {
      var value = window.localStorage.getItem(KEY);
      return value === 'light' || value === 'dark' ? value : 'system';
    } catch (err) {
      return 'system';
    }
  }

  function apply(choice) {
    var dark = choice === 'dark' || (choice === 'system' && media && media.matches);
    document.documentElement.classList.toggle('dark', !!dark);
  }

  apply(stored());
  if (media && media.addEventListener) {
    media.addEventListener('change', function () {
      if (stored() === 'system') apply('system');
    });
  }

  window.AttendQRTheme = {
    get: stored,
    set: function (choice) {
      try {
        if (choice === 'system') window.localStorage.removeItem(KEY);
        else window.localStorage.setItem(KEY, choice);
      } catch (err) { /* private mode: the choice lasts for this page only */ }
      apply(choice);
    }
  };
})();
