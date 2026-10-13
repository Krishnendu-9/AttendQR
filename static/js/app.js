/* Shared client-side helpers for AttendQR pages. */
(function () {
  'use strict';

  function readConfig() {
    var el = document.getElementById('app-config');
    if (!el) return { urls: {}, idPlaceholder: '', minPasswordLength: 8 };
    try {
      return JSON.parse(el.textContent);
    } catch (err) {
      return { urls: {}, idPlaceholder: '', minPasswordLength: 8 };
    }
  }

  var config = readConfig();

  function url(name, id) {
    var template = config.urls[name];
    if (!template) throw new Error('Unknown URL: ' + name);
    return template.replace(config.idPlaceholder, encodeURIComponent(id));
  }

  function getJSON(target) {
    return fetch(target, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) {
            var error = new Error(data.message || ('Request failed with status ' + response.status));
            error.status = response.status;
            error.data = data;
            throw error;
          }
          return data;
        });
      });
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function postJSON(target, body) {
    return fetch(target, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().catch(function () {
        return { success: false, message: 'Unexpected response from the server.' };
      });
    });
  }

  function resolve(select) {
    return typeof select === 'string' ? document.getElementById(select) : select;
  }

  /*
   * Replace a <select>'s options. Labels are set with new Option(), never HTML.
   * options: placeholder, emptyLabel, label(item), value(item), data(item) -> dataset, selected
   */
  function fillSelect(select, items, options) {
    select = resolve(select);
    if (!select) return null;
    options = options || {};
    select.innerHTML = '';
    if (options.placeholder != null) select.add(new Option(options.placeholder, ''));
    if (!items.length && options.emptyLabel) {
      var empty = new Option(options.emptyLabel, '');
      empty.disabled = true;
      select.add(empty);
    }
    items.forEach(function (item) {
      var option = new Option(
        options.label ? options.label(item) : item.name,
        options.value ? options.value(item) : item.id
      );
      if (options.data) {
        var data = options.data(item);
        Object.keys(data).forEach(function (key) { option.dataset[key] = data[key]; });
      }
      select.add(option);
    });
    if (options.selected != null && options.selected !== '') select.value = String(options.selected);
    return select;
  }

  /* Fetch options for a select from one of the JSON endpoints. Resolves with the items. */
  function loadOptions(select, urlName, id, options) {
    select = resolve(select);
    options = options || {};
    if (!select) return Promise.resolve([]);
    if (!id) {
      fillSelect(select, [], options);
      return Promise.resolve([]);
    }
    fillSelect(select, [], { placeholder: options.loadingLabel || 'Loading...' });
    select.disabled = true;
    return getJSON(url(urlName, id))
      .then(function (items) {
        fillSelect(select, items, options);
        return items;
      })
      .catch(function () {
        fillSelect(select, [], { placeholder: options.errorLabel || 'Unable to load options' });
        return [];
      })
      .then(function (items) {
        select.disabled = false;
        return items;
      });
  }

  function loadSemesters(select, departmentId, options) {
    return loadOptions(select, 'semesters', departmentId, Object.assign({ placeholder: 'Select semester' }, options));
  }

  function loadSubjects(select, semesterId, options) {
    return loadOptions(select, 'subjects', semesterId, Object.assign({
      placeholder: 'Select subject',
      label: function (s) { return s.display_name; },
      data: function (s) { return { teacherId: s.teacher_id || '', teacherName: s.teacher_name || '' }; }
    }, options));
  }

  function loadDepartmentSubjects(select, departmentId, options) {
    return loadOptions(select, 'departmentSubjects', departmentId, Object.assign({
      placeholder: 'Assign later',
      label: function (s) {
        return s.display_name + ' (' + s.semester + ')' + (s.teacher_name ? ' - assigned to ' + s.teacher_name : '');
      }
    }, options));
  }

  function loadTeachers(select, departmentId, options) {
    return loadOptions(select, 'teachers', departmentId, Object.assign({ placeholder: 'Select teacher' }, options));
  }

  /* Password strength, mirroring the server-side policy (length + letter + digit). */
  function evaluatePassword(value) {
    var min = config.minPasswordLength || 8;
    var hasLetter = /[A-Za-z]/.test(value);
    var hasDigit = /\d/.test(value);
    if (!value) {
      return { label: 'Not checked', width: '0%', tone: 'is-empty', hint: 'Use at least ' + min + ' characters with letters and numbers.', acceptable: false };
    }
    if (value.length < min) {
      return { label: 'Too short', width: '18%', tone: 'is-weak', hint: 'Password must be at least ' + min + ' characters long.', acceptable: false };
    }
    if (!hasLetter || !hasDigit) {
      return { label: 'Weak', width: '34%', tone: 'is-weak', hint: 'Include at least one letter and one number.', acceptable: false };
    }
    var score = 0;
    if (value.length >= 12) score += 1;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
    if (/[^A-Za-z0-9]/.test(value)) score += 1;
    if (score <= 1) {
      return { label: 'Medium', width: '68%', tone: 'is-medium', hint: 'Mixed case, a symbol, or a longer phrase will make it stronger.', acceptable: true };
    }
    return { label: 'Strong', width: '100%', tone: 'is-strong', hint: '', acceptable: true };
  }

  /*
   * Wire a strength meter and confirm-match indicator to a password form.
   * opts: password, confirm, submit, bar, label, hint, match (elements or ids), extraValid() -> bool
   */
  function bindPasswordMeter(opts) {
    var password = resolve(opts.password);
    var confirm = resolve(opts.confirm);
    var submit = resolve(opts.submit);
    var bar = resolve(opts.bar);
    var label = resolve(opts.label);
    var hint = resolve(opts.hint);
    var match = resolve(opts.match);
    if (!password || !confirm) return;

    function update() {
      var strength = evaluatePassword(password.value);
      var matches = !!confirm.value && password.value === confirm.value;

      if (bar) {
        bar.className = 'password-strength-bar ' + strength.tone;
        bar.style.width = strength.width;
      }
      if (label) label.textContent = 'Strength: ' + strength.label;
      if (hint) hint.textContent = strength.hint;

      if (match) {
        if (!confirm.value) {
          match.className = 'password-match-status';
          match.textContent = 'Re-enter the password to confirm it.';
        } else if (matches) {
          match.className = 'password-match-status is-match';
          match.textContent = 'Confirm password matched.';
        } else {
          match.className = 'password-match-status is-mismatch';
          match.textContent = 'Confirm password does not match.';
        }
      }
      if (submit) {
        submit.disabled = !(strength.acceptable && matches && (!opts.extraValid || opts.extraValid()));
      }
    }

    var inputs = [password, confirm].concat((opts.watch || []).map(resolve).filter(Boolean));
    inputs.forEach(function (input) {
      input.addEventListener('input', update);
    });
    update();
  }

  /* Client-side table search for small lists. rows: selector; fields: data-* attribute names. */
  function bindTableSearch(opts) {
    var input = resolve(opts.input);
    var count = resolve(opts.count);
    // Cell renumbered for visible rows; pass null when the table has no index column.
    var indexSelector = opts.indexSelector === undefined ? 'td:first-child' : opts.indexSelector;
    if (!input) return;
    function apply() {
      var term = input.value.toLowerCase().trim();
      var rows = document.querySelectorAll(opts.rows);
      var visible = 0;
      rows.forEach(function (row) {
        var matches = !term || opts.fields.some(function (field) {
          return (row.getAttribute('data-' + field) || '').indexOf(term) !== -1;
        });
        row.style.display = matches ? '' : 'none';
        if (opts.onToggle) opts.onToggle(row, matches);
        if (matches) {
          visible += 1;
          var index = indexSelector ? row.querySelector(indexSelector) : null;
          if (index) index.textContent = visible;
        }
      });
      if (count) count.textContent = 'Showing: ' + visible + ' of ' + rows.length + ' ' + (opts.noun || 'items');
      if (opts.noMatch) {
        var noMatch = resolve(opts.noMatch);
        if (noMatch) noMatch.classList.toggle('d-none', visible !== 0 || !rows.length);
      }
    }
    input.addEventListener('input', apply);
    return apply;
  }

  // ── Page-wide behaviour ─────────────────────────────────────
  // Confirmation prompts: <form data-confirm="..."> (text is HTML-escaped by the template).
  document.addEventListener('submit', function (event) {
    var message = event.target.getAttribute && event.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) event.preventDefault();
  }, true);

  document.addEventListener('DOMContentLoaded', function () {
    var sidebar = document.getElementById('sidebar');
    var toggle = document.querySelector('.toggle-sidebar');
    if (sidebar && toggle) {
      toggle.addEventListener('click', function () { sidebar.classList.toggle('active'); });
      document.addEventListener('click', function (event) {
        if (window.innerWidth <= 768 && !sidebar.contains(event.target) && !toggle.contains(event.target)) {
          sidebar.classList.remove('active');
        }
      });
    }

    if (window.bootstrap) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
        new window.bootstrap.Tooltip(el);
      });
    }

    document.querySelectorAll('.password-toggle').forEach(function (button) {
      button.addEventListener('click', function () {
        var input = document.getElementById(button.dataset.target);
        var icon = button.querySelector('i');
        var isHidden = input.type === 'password';
        input.type = isHidden ? 'text' : 'password';
        icon.className = isHidden ? 'bi bi-eye-slash' : 'bi bi-eye';
        button.setAttribute('aria-label', isHidden ? 'Hide password' : 'Show password');
      });
    });
  });

  window.AttendQR = {
    config: config,
    url: url,
    getJSON: getJSON,
    postJSON: postJSON,
    fillSelect: fillSelect,
    loadOptions: loadOptions,
    loadSemesters: loadSemesters,
    loadSubjects: loadSubjects,
    loadDepartmentSubjects: loadDepartmentSubjects,
    loadTeachers: loadTeachers,
    evaluatePassword: evaluatePassword,
    bindPasswordMeter: bindPasswordMeter,
    bindTableSearch: bindTableSearch
  };
})();
