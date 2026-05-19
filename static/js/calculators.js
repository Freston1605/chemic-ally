(function () {
  "use strict";

  /* ── Skeleton loader ── */
  window.CaSkeleton = {
    show: function (container) {
      if (!container) return;
      container.classList.add('ca-skeleton');
      container.setAttribute('aria-busy', 'true');
    },
    hide: function (container) {
      if (!container) return;
      container.classList.remove('ca-skeleton');
      container.removeAttribute('aria-busy');
    },
  };

  /* ── Session history (localStorage) ── */
  var STORAGE_KEY_PREFIX = 'ca_history_';

  window.CaHistory = {
    get: function (toolName) {
      try {
        var data = localStorage.getItem(STORAGE_KEY_PREFIX + toolName);
        return data ? JSON.parse(data) : [];
      } catch () { return []; }
    },

    add: function (toolName, entry) {
      var history = this.get(toolName);
      entry.timestamp = new Date().toISOString();
      history.unshift(entry);
      if (history.length > 20) history.pop();
      try {
        localStorage.setItem(STORAGE_KEY_PREFIX + toolName, JSON.stringify(history));
      } catch () {}
    },

    render: function (toolName, container) {
      var history = this.get(toolName);
      if (!container) return;
      if (history.length === 0) {
        container.innerHTML = '<div class="small" style="color:var(--ca-text-muted);padding:0.5rem 0;">No previous calculations.</div>';
        return;
      }
      var html = '';
      history.forEach(function (entry) {
        var time = new Date(entry.timestamp);
        var timeStr = time.toLocaleString();
        html += '<div class="d-flex justify-content-between align-items-center py-1" style="border-bottom:1px solid var(--ca-border);font-size:0.8125rem;">';
        html += '<div><span style="color:var(--ca-text-muted);font-size:0.75rem;">' + timeStr + '</span>';
        if (entry.summary) html += '<div style="color:var(--ca-text-primary);">' + entry.summary + '</div>';
        html += '</div></div>';
      });
      container.innerHTML = html;
    },
  };

})();
