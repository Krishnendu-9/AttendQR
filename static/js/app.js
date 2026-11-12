/* Shared helpers for AttendQR pages: JSON requests, dependent dropdowns and the password meter.
 * Interface behaviour (menus, dialogs, confirmations, toasts) is in ui.js. */
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
   * opts: password, confirm, submit, bar, label, hint, match (elements or ids), extraValid() -> bool,
   * extraProblem() -> text explaining why extraValid() fails (shown under the confirm field), or ''
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

      var problem = opts.extraProblem ? opts.extraProblem() : '';
      if (match) {
        if (problem && password.value) {
          match.className = 'password-match-status is-mismatch';
          match.textContent = problem;
        } else if (!confirm.value) {
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
    bindPasswordMeter: bindPasswordMeter
  };
})();
