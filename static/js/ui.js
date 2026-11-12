/* AttendQR interface behaviour: Alpine components and page-wide helpers.
 *
 * The app's Content Security Policy forbids eval and inline handlers, so this uses
 * Alpine's CSP build: components are registered here with Alpine.data() and templates
 * only name them (x-data="dropdown") and call their methods.
 * Load order: app.js, htmx, this file, then Alpine (all deferred except app.js).
 *
 * Inside a method, this.$el is the element whose attribute called it (often a child
 * button), not the component root. Methods use this.$root, or values saved in init().
 */
(function () {
  'use strict';

  function storageGet(store, key) {
    try { return window[store].getItem(key); } catch (err) { return null; }
  }

  function storageSet(store, key, value) {
    try { window[store].setItem(key, value); } catch (err) { /* storage blocked: keep defaults */ }
  }

  function focusables(root) {
    return Array.prototype.filter.call(
      root.querySelectorAll('a[href], button:not([disabled]), [role="menuitem"], [role="menuitemradio"], input:not([disabled]), select:not([disabled])'),
      function (el) { return el.offsetParent !== null; }
    );
  }

  // ── Toasts ────────────────────────────────────────────────
  var TOAST_ICONS = { success: 'circle-check', danger: 'circle-alert', warning: 'triangle-alert', info: 'info' };

  /* Client-side toast (server messages are rendered by the layout). Text only, never HTML. */
  function toast(message, tone) {
    var region = document.getElementById('toast-region');
    var template = document.getElementById('toast-template-' + (TOAST_ICONS[tone] ? tone : 'info'));
    if (!region || !template) return;
    var node = template.content.firstElementChild.cloneNode(true);
    node.querySelector('[data-toast-message]').textContent = message;
    region.appendChild(node);
  }

  // ── Confirmation dialog ───────────────────────────────────
  /* <form data-confirm="Message" data-confirm-title="Delete Rahul Das?" data-confirm-label="Delete"
   *       data-confirm-tone="danger"> asks first, then submits with a pending state. */
  function confirmForm(form, submitter) {
    var dialog = document.getElementById('confirm-dialog');
    if (!dialog || typeof dialog.showModal !== 'function') {
      if (window.confirm(form.dataset.confirm)) submitConfirmed(form, submitter);
      return;
    }
    var tone = form.dataset.confirmTone || 'danger';
    var accept = dialog.querySelector('[data-confirm-accept]');
    dialog.querySelector('[data-confirm-title]').textContent = form.dataset.confirmTitle || 'Are you sure?';
    dialog.querySelector('[data-confirm-message]').textContent = form.dataset.confirm;
    dialog.querySelector('[data-confirm-label]').textContent = form.dataset.confirmLabel || 'Confirm';
    dialog.querySelector('[data-confirm-icon-danger]').hidden = tone !== 'danger';
    dialog.querySelector('[data-confirm-icon-info]').hidden = tone === 'danger';
    accept.className = 'btn ' + (tone === 'danger' ? 'btn-destructive' : 'btn-primary');
    accept.disabled = false;

    accept.onclick = function () {
      accept.disabled = true;
      accept.classList.add('is-busy');
      dialog.querySelector('[data-confirm-cancel]').disabled = true;
      submitConfirmed(form, submitter);
    };
    dialog.addEventListener('close', function reset() {
      dialog.removeEventListener('close', reset);
      accept.classList.remove('is-busy');
      dialog.querySelector('[data-confirm-cancel]').disabled = false;
    });
    dialog.showModal();
    dialog.querySelector('[data-confirm-cancel]').focus();
  }

  function submitConfirmed(form, submitter) {
    form.dataset.confirmed = '1';
    if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
    else form.submit();
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.matches || !form.matches('form[data-confirm]')) return;
    if (form.dataset.confirmed === '1') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    confirmForm(form, event.submitter);
  }, true);

  // ── Pending state on submit ───────────────────────────────
  /* A submit button with data-busy-label="Saving..." is disabled and shows a spinner while the form posts. */
  document.addEventListener('submit', function (event) {
    if (event.defaultPrevented) return;
    var form = event.target;
    if (form.hasAttribute('hx-get') || form.hasAttribute('hx-post')) return;
    var button = (event.submitter && event.submitter.hasAttribute('data-busy-label'))
      ? event.submitter
      : form.querySelector('[type="submit"][data-busy-label]');
    if (!button) return;
    window.setTimeout(function () {
      button.disabled = true;
      button.classList.add('is-busy');
      var label = button.querySelector('[data-busy-text]');
      if (label) label.textContent = button.getAttribute('data-busy-label');
    }, 0);
  });

  // Coming back with the browser's back button restores a page from cache; never leave it stuck as busy.
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    document.querySelectorAll('.is-busy').forEach(function (button) {
      button.classList.remove('is-busy');
      button.disabled = false;
    });
    document.querySelectorAll('form[data-confirmed]').forEach(function (form) { delete form.dataset.confirmed; });
  });

  // ── Clickable rows ────────────────────────────────────────
  /* <tr data-href="..."> opens the record. The title cell holds a real link, so keyboard users,
   * Ctrl/Cmd-click and middle-click behave as on any link. */
  function rowTarget(event) {
    var row = event.target.closest && event.target.closest('tr[data-href]');
    if (!row) return null;
    if (event.target.closest('a, button, input, select, textarea, label, form, [data-row-ignore]')) return null;
    if (window.getSelection && String(window.getSelection())) return null;
    return row;
  }

  document.addEventListener('click', function (event) {
    var row = rowTarget(event);
    if (!row) return;
    if (event.ctrlKey || event.metaKey || event.shiftKey) window.open(row.dataset.href, '_blank', 'noopener');
    else window.location.assign(row.dataset.href);
  });

  document.addEventListener('auxclick', function (event) {
    if (event.button !== 1) return;
    var row = rowTarget(event);
    if (row) window.open(row.dataset.href, '_blank', 'noopener');
  });

  // ── htmx integration ──────────────────────────────────────
  document.addEventListener('htmx:configRequest', function (event) {
    var detail = event.detail;
    if (detail.verb !== 'get' || !detail.formData) return;
    // Defaults stay out of the URL: drop empty filters.
    var empty = [];
    detail.formData.forEach(function (value, key) { if (value === '') empty.push(key); });
    empty.forEach(function (key) { detail.formData.delete(key); });
  });

  document.addEventListener('htmx:beforeSwap', function (event) {
    var xhr = event.detail.xhr;
    // Signed out meanwhile (or any redirect away from this page): load the new page properly.
    if (xhr && xhr.responseURL) {
      var requested = new URL(event.detail.requestConfig.path, window.location.href).pathname;
      var landed = new URL(xhr.responseURL).pathname;
      if (requested !== landed) {
        event.detail.shouldSwap = false;
        window.location.assign(xhr.responseURL);
      }
    }
  });

  function showLoadError(event) {
    var target = event.detail.target || event.target;
    var region = target && target.closest ? target.closest('[data-error-template]') : null;
    var template = region ? document.getElementById(region.getAttribute('data-error-template')) : null;
    if (template) {
      region.replaceChildren(template.content.cloneNode(true));
    } else {
      toast('Could not load this view. Check your connection and try again.', 'danger');
    }
  }
  document.addEventListener('htmx:responseError', showLoadError);
  document.addEventListener('htmx:sendError', showLoadError);

  document.addEventListener('click', function (event) {
    if (event.target.closest && event.target.closest('[data-reload-page]')) window.location.reload();
  });

  // ── Add / edit dialogs ────────────────────────────────────
  /* <button data-record-dialog="subject-dialog" data-action="/subjects/edit/3" data-title="Edit subject"
   *         data-submit="Save changes" data-values='{"name": "DBMS", "semester_id": 2}'>
   * opens <dialog id="subject-dialog"> with its form reset, pointed at data-action and filled by field name.
   * The form then receives a "record:filled" event (detail = values) for dependent selects. */
  function openRecordDialog(opener) {
    var dialog = document.getElementById(opener.getAttribute('data-record-dialog'));
    if (!dialog) return;
    var form = dialog.querySelector('form');
    var values = {};
    try { values = JSON.parse(opener.getAttribute('data-values') || '{}'); } catch (err) { values = {}; }
    form.reset();
    form.action = opener.getAttribute('data-action');
    var title = dialog.querySelector('[data-record-title]');
    if (title) title.textContent = opener.getAttribute('data-title') || title.textContent;
    var submit = dialog.querySelector('[data-record-submit]');
    if (submit) submit.textContent = opener.getAttribute('data-submit') || submit.textContent;
    dialog.querySelectorAll('[data-record-mode]').forEach(function (el) {
      el.hidden = el.getAttribute('data-record-mode') !== (opener.getAttribute('data-mode') || 'create');
    });
    Object.keys(values).forEach(function (name) {
      var field = form.elements[name];
      if (field && field.type !== 'hidden') field.value = values[name] == null ? '' : values[name];
    });
    form.dispatchEvent(new CustomEvent('record:filled', { detail: values }));
    dialog.showModal();
    var first = form.querySelector('input:not([type="hidden"]):not([readonly]), select, textarea');
    if (first) first.focus();
  }

  document.addEventListener('click', function (event) {
    if (!event.target.closest) return;
    var opener = event.target.closest('[data-record-dialog]');
    if (opener) {
      event.preventDefault();
      openRecordDialog(opener);
      return;
    }
    var closer = event.target.closest('[data-dialog-close]');
    if (closer) {
      closer.closest('dialog').close();
      return;
    }
    // A click on the dimmed backdrop lands on the <dialog> itself.
    if (event.target.matches('dialog.dialog[open]') && !event.target.hasAttribute('data-keep-open')) event.target.close();
  });

  // ── Alpine components ─────────────────────────────────────
  document.addEventListener('alpine:init', function () {
    var Alpine = window.Alpine;

    /* App shell: phone drawer and the Ctrl K palette shortcut. */
    Alpine.data('shell', function () {
      return {
        sidebarOpen: false,
        openSidebar: function () { this.sidebarOpen = true; },
        closeSidebar: function () { this.sidebarOpen = false; },
        onKeydown: function (event) {
          if ((event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K')) {
            event.preventDefault();
            window.dispatchEvent(new CustomEvent('palette:open'));
          } else if (event.key === 'Escape' && this.sidebarOpen) {
            this.sidebarOpen = false;
          }
        }
      };
    });

    /* Foldable sidebar group. The group holding the current page is always open;
     * other choices are remembered for this tab. */
    Alpine.data('navGroup', function () {
      return {
        open: true,
        key: '',
        locked: false,
        init: function () {
          this.key = 'attendqr-nav:' + this.$el.dataset.group;
          this.locked = this.$el.dataset.current === 'true';
          if (!this.locked) this.open = storageGet('sessionStorage', this.key) !== 'closed';
        },
        toggle: function () {
          if (this.locked) return;
          this.open = !this.open;
          storageSet('sessionStorage', this.key, this.open ? 'open' : 'closed');
        }
      };
    });

    /* Dropdown menu. The panel is positioned with fixed coordinates so a table's
     * scroll container never clips it; it flips upward near the bottom of the screen. */
    Alpine.data('dropdown', function () {
      return {
        open: false,
        toggle: function () { this.open ? this.close() : this.show(); },
        show: function () {
          this.open = true;
          var self = this;
          this.$nextTick(function () {
            self.place();
            var items = focusables(self.$refs.panel);
            if (items.length) items[0].focus();
          });
          this._onViewportChange = function (event) {
            if (event && event.type === 'scroll' && self.$refs.panel.contains(event.target)) return;
            self.close(false);
          };
          window.addEventListener('resize', this._onViewportChange);
          window.addEventListener('scroll', this._onViewportChange, true);
        },
        close: function (restoreFocus) {
          if (!this.open) return;
          this.open = false;
          window.removeEventListener('resize', this._onViewportChange);
          window.removeEventListener('scroll', this._onViewportChange, true);
          if (restoreFocus !== false && this.$refs.button) this.$refs.button.focus();
        },
        place: function () {
          var panel = this.$refs.panel;
          var anchor = this.$refs.button.getBoundingClientRect();
          var width = panel.offsetWidth;
          var height = panel.offsetHeight;
          var gap = 6;
          var alignStart = this.$root.dataset.align === 'start';
          var left = alignStart ? anchor.left : anchor.right - width;
          left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
          var top = anchor.bottom + gap;
          if (top + height > window.innerHeight - 8 && anchor.top - gap - height > 8) top = anchor.top - gap - height;
          panel.style.left = left + 'px';
          panel.style.top = top + 'px';
        },
        /* Choosing an item closes the menu (its link, form or dialog then takes over). */
        onPanelClick: function (event) {
          if (event.target.closest('[role="menuitem"]')) this.close(false);
        },
        onKeydown: function (event) {
          if (!this.open) return;
          var items = focusables(this.$refs.panel);
          var index = items.indexOf(document.activeElement);
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            this.close();
          } else if (this.$root.dataset.menu === 'false') {
            // A popover with form controls: arrow keys belong to the controls.
          } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            if (items.length) items[(index + 1) % items.length].focus();
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            if (items.length) items[(index - 1 + items.length) % items.length].focus();
          } else if (event.key === 'Home' && items.length) {
            event.preventDefault();
            items[0].focus();
          } else if (event.key === 'End' && items.length) {
            event.preventDefault();
            items[items.length - 1].focus();
          } else if (event.key === 'Tab') {
            this.close(false);
          }
        }
      };
    });

    /* Light / Dark / System choice in the user menu (see theme.js). */
    Alpine.data('themePicker', function () {
      return {
        theme: 'system',
        init: function () { this.theme = window.AttendQRTheme ? window.AttendQRTheme.get() : 'system'; },
        is: function (choice) { return this.theme === choice; },
        choose: function (choice) {
          this.theme = choice;
          if (window.AttendQRTheme) window.AttendQRTheme.set(choice);
        }
      };
    });

    /* A toast. Success and info hide after a few seconds (paused while hovered);
     * errors, warnings and anything marked sticky (one-time passwords) stay until closed. */
    Alpine.data('toast', function () {
      return {
        visible: true,
        sticky: false,
        timer: null,
        init: function () {
          this.sticky = this.$el.dataset.sticky === 'true';
          this.schedule();
        },
        schedule: function () {
          var self = this;
          if (this.sticky) return;
          this.timer = window.setTimeout(function () { self.dismiss(); }, 5000);
        },
        pause: function () { window.clearTimeout(this.timer); },
        dismiss: function () {
          var el = this.$root;
          this.visible = false;
          window.setTimeout(function () { el.remove(); }, 200);
        }
      };
    });

    /* Ctrl K command palette over the pages and actions listed in the layout. */
    Alpine.data('palette', function () {
      return {
        query: '',
        init: function () {
          var self = this;
          window.addEventListener('palette:open', function () { self.show(); });
        },
        show: function () {
          var dialog = this.$refs.dialog;
          if (dialog.open) return;
          this.query = '';
          this.$refs.input.value = '';
          this.filter();
          dialog.showModal();
          this.$refs.input.focus();
        },
        hide: function () { this.$refs.dialog.close(); },
        onBackdrop: function (event) { if (event.target === this.$refs.dialog) this.hide(); },
        items: function () {
          return Array.prototype.slice.call(this.$refs.list.querySelectorAll('[data-palette-item]'));
        },
        visibleItems: function () {
          return this.items().filter(function (item) { return !item.hidden; });
        },
        filter: function () {
          var terms = this.$refs.input.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
          var anyVisible = false;
          this.items().forEach(function (item) {
            var haystack = item.getAttribute('data-search');
            item.hidden = !terms.every(function (term) { return haystack.indexOf(term) !== -1; });
            anyVisible = anyVisible || !item.hidden;
          });
          this.$refs.list.querySelectorAll('[data-palette-group]').forEach(function (group) {
            group.hidden = !group.querySelector('[data-palette-item]:not([hidden])');
          });
          this.$refs.empty.hidden = anyVisible;
          this.select(0);
        },
        select: function (index) {
          var items = this.visibleItems();
          items.forEach(function (item, i) { item.setAttribute('aria-selected', i === index ? 'true' : 'false'); });
          if (items[index]) {
            items[index].scrollIntoView({ block: 'nearest' });
            this.$refs.input.setAttribute('aria-activedescendant', items[index].id);
          }
        },
        onKeydown: function (event) {
          var items = this.visibleItems();
          var current = items.findIndex(function (item) { return item.getAttribute('aria-selected') === 'true'; });
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            this.select(items.length ? (current + 1) % items.length : 0);
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            this.select(items.length ? (current - 1 + items.length) % items.length : 0);
          } else if (event.key === 'Enter' && items[current]) {
            event.preventDefault();
            items[current].click();
          }
        },
        onHover: function (event) {
          var item = event.target.closest('[data-palette-item]');
          if (item) this.select(this.visibleItems().indexOf(item));
        }
      };
    });

    /* List toolbar: search applies 300 ms after typing stops (clearing applies at once);
     * filter changes apply immediately. The form uses htmx to swap just the list. */
    Alpine.data('listFilters', function () {
      return {
        timer: null,
        hasQuery: false,
        formId: '',
        init: function () {
          this.formId = this.$el.id;
          var input = this.$el.querySelector('input[type="search"]');
          this.hasQuery = !!(input && input.value);
        },
        form: function () { return document.getElementById(this.formId); },
        submit: function () {
          window.clearTimeout(this.timer);
          var form = this.form();
          if (window.htmx) window.htmx.trigger(form, 'submit');
          else form.requestSubmit();
        },
        onSearch: function (event) {
          var self = this;
          this.hasQuery = !!event.target.value;
          window.clearTimeout(this.timer);
          this.timer = window.setTimeout(function () { self.submit(); }, event.target.value ? 300 : 0);
        },
        clearSearch: function () {
          var input = this.form().querySelector('input[type="search"]');
          input.value = '';
          this.hasQuery = false;
          input.focus();
          this.submit();
        },
        /* Department changed: its semesters differ, so the semester filter starts again. */
        resetAndSubmit: function (event) {
          var target = event.target.getAttribute('data-resets');
          var dependent = target ? this.form().querySelector('[name="' + target + '"]') : null;
          if (dependent) dependent.value = '';
          this.submit();
        }
      };
    });

    /* Search and filters for lists that are loaded in full (departments, subjects, ...).
     * <form id="..." x-data="clientFilter" data-target="#list">: items inside the target carry
     * data-search="lower-case text" and data-filter-<name>="value" for each <select name="...">.
     * Phone-card copies of rows carry data-search-mirror so they are not counted twice.
     * The target may hold [data-search-results], [data-search-empty] and [data-search-count].
     * The search and filters are kept in the URL, so a refresh shows the same view. */
    Alpine.data('clientFilter', function () {
      return {
        hasQuery: false,
        formId: '',
        init: function () {
          this.formId = this.$el.id;
          var form = this.$el;
          var params = new URLSearchParams(window.location.search);
          Array.prototype.forEach.call(form.elements, function (field) {
            if (field.name && params.has(field.name)) field.value = params.get(field.name);
          });
          this.apply();
        },
        form: function () { return document.getElementById(this.formId); },
        apply: function () {
          var form = this.form();
          var target = document.querySelector(form.getAttribute('data-target'));
          var search = form.elements.q;
          var terms = (search ? search.value : '').toLowerCase().trim().split(/\s+/).filter(Boolean);
          var selects = Array.prototype.filter.call(form.querySelectorAll('select[name]'), function (s) { return s.value; });
          var total = 0;
          var visible = 0;
          this.hasQuery = terms.length > 0;
          target.querySelectorAll('[data-search]').forEach(function (item) {
            var text = item.getAttribute('data-search');
            var match = terms.every(function (term) { return text.indexOf(term) !== -1; }) &&
              selects.every(function (s) { return item.getAttribute('data-filter-' + s.name) === s.value; });
            item.hidden = !match;
            if (!item.hasAttribute('data-search-mirror')) {
              total += 1;
              if (match) visible += 1;
            }
          });
          var results = target.querySelector('[data-search-results]');
          var empty = target.querySelector('[data-search-empty]');
          if (results) results.hidden = total > 0 && visible === 0;
          if (empty) empty.hidden = !(total > 0 && visible === 0);
          target.querySelectorAll('[data-search-count]').forEach(function (el) {
            el.textContent = visible === total ? total + ' ' + el.getAttribute('data-noun')
              : 'Showing ' + visible + ' of ' + total + ' ' + el.getAttribute('data-noun');
          });
          form.querySelectorAll('[data-filter-count]').forEach(function (el) {
            el.textContent = selects.length;
            el.hidden = selects.length === 0;
            el.closest('button').classList.toggle('filters-active', selects.length > 0);
          });
          var url = new URL(window.location.href);
          Array.prototype.forEach.call(form.elements, function (field) {
            if (!field.name) return;
            if (field.value) url.searchParams.set(field.name, field.value);
            else url.searchParams.delete(field.name);
          });
          window.history.replaceState(window.history.state, '', url);
        },
        clearSearch: function () {
          var input = this.form().elements.q;
          input.value = '';
          input.focus();
          this.apply();
        },
        clearAll: function () {
          this.form().reset();
          Array.prototype.forEach.call(this.form().elements, function (field) {
            if (field.name && field.type !== 'hidden') field.value = '';
          });
          this.apply();
        }
      };
    });

    /* Columns menu: hide table columns; remembered per browser and reapplied after htmx swaps. */
    Alpine.data('columnPicker', function () {
      return {
        hidden: [],
        key: '',
        table: '',
        init: function () {
          var self = this;
          this.table = this.$el.dataset.table;
          this.key = 'attendqr-columns:' + this.table;
          try { this.hidden = JSON.parse(storageGet('localStorage', this.key) || '[]'); } catch (err) { this.hidden = []; }
          this.apply();
          document.addEventListener('htmx:afterSettle', function () { self.apply(); });
        },
        isShown: function (column) { return this.hidden.indexOf(column) === -1; },
        toggle: function (column) {
          var index = this.hidden.indexOf(column);
          if (index === -1) this.hidden.push(column);
          else this.hidden.splice(index, 1);
          storageSet('localStorage', this.key, JSON.stringify(this.hidden));
          this.apply();
        },
        apply: function () {
          var hidden = this.hidden;
          document.querySelectorAll('table[data-table="' + this.table + '"] [data-col]').forEach(function (cell) {
            cell.classList.toggle('hidden', hidden.indexOf(cell.getAttribute('data-col')) !== -1);
          });
        }
      };
    });

    /* Show / hide password. */
    Alpine.data('passwordField', function () {
      return {
        shown: false,
        toggle: function () {
          this.shown = !this.shown;
          this.$refs.input.type = this.shown ? 'text' : 'password';
        }
      };
    });

    /* A dialog opened by a button: <div x-data="modal"> <button @click="show"> <dialog x-ref="dialog">. */
    Alpine.data('modal', function () {
      return {
        show: function () { this.$refs.dialog.showModal(); },
        hide: function () { this.$refs.dialog.close(); },
        onBackdrop: function (event) { if (event.target === this.$refs.dialog) this.hide(); }
      };
    });
  });

  window.AttendQRUI = { toast: toast, confirmForm: confirmForm };
})();
