/**
 * Clipboard utility for copying calculation results to the clipboard.
 * Provides visual feedback on the trigger element when the copy succeeds.
 */

(function () {
  "use strict";

  /**
   * Extract plain-text representation from a result container element.
   * Handles both <table> elements and plain <div>/<p> elements.
   * @param {HTMLElement} container - The element containing results
   * @returns {string} Plain-text representation of the results
   */
  function extractText(container) {
    if (!container) return "";

    // Check for a <table> inside the container
    const table = container.querySelector("table");
    if (table) {
      const rows = table.querySelectorAll("tbody tr");
      const headers = table.querySelectorAll("thead th");
      const headerText = Array.from(headers)
        .map((th) => th.textContent.trim())
        .join("\t");
      const bodyText = Array.from(rows).map((row) => {
        const cells = row.querySelectorAll("td");
        return Array.from(cells)
          .map((td) => td.textContent.trim())
          .join("\t");
      });
      return [headerText, ...bodyText].join("\n");
    }

    // Fallback: grab all text content directly
    return container.textContent.trim();
  }

  /**
   * Copy text to the clipboard and provide visual feedback on the trigger
   * element (e.g. a button).
   * @param {string} selector - CSS selector for the result container
   * @param {HTMLElement} trigger - The element that triggered the copy
   */
  window.copyToClipboard = function (selector, trigger) {
    const container = document.querySelector(selector);
    if (!container) return;

    const text = extractText(container);
    if (!text) return;

    navigator.clipboard.writeText(text).then(() => {
      // Save the original text and disable the button briefly
      const original = trigger.textContent;
      trigger.textContent = "Copied!";
      trigger.disabled = true;
      setTimeout(function () {
        trigger.textContent = original;
        trigger.disabled = false;
      }, 2000);
    });
  };
})();