/**
 * Substance Tag Input Widget
 * ===========================
 *
 * Provides an autocomplete tag/chip input for chemical formulas with
 * undo/redo support. This widget is intended to replace a plain text
 * input with a more usable tokenized substance entry field.
 *
 * Usage:
 *   new SubstanceTagInput(element, options)
 *
 * Options:
 *   - hiddenInput: selector or DOM element for the hidden input to keep in sync
 *   - placeholder: placeholder text for the input field
 *   - name: name for a generated hidden input when `hiddenInput` is not provided
 *
 * Public API:
 *   - getTags(): Array<string>
 *   - clear(): void
 *   - setTags(tags: Array<string>): void
 *
 * Keyboard shortcuts:
 *   - Enter or Tab: accept current typed text as a tag
 *   - Backspace (empty input): remove the last tag
 *   - Up/Down arrows: navigate autocomplete suggestions
 *   - Enter/Tab on suggestion: select it
 *   - Esc: close suggestion list
 *   - Ctrl+Z / Cmd+Z: undo
 *   - Ctrl+Shift+Z / Cmd+Shift+Z or Ctrl+Y: redo
 */

(function () {
  "use strict";

  // ── Common chemical substances for autocomplete ──
  const COMMON_SUBSTANCES = [
    // Diatomic / elemental
    "H2", "O2", "N2", "F2", "Cl2", "Br2", "I2", "O3", "P4", "S8",
    // Elements (ions / single atoms)
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "Pt", "Au", "Hg", "Pb", "Bi", "Rn",
    "U", "Pu",
    // Ions
    "H+", "Na+", "K+", "Ca2+", "Mg2+", "Fe2+", "Fe3+", "Cu+", "Cu2+",
    "Zn2+", "Ag+", "Al3+", "Pb2+", "Hg2+", "NH4+",
    "OH-", "Cl-", "Br-", "I-", "F-", "O2-", "S2-", "N3-", "P3-",
    "NO3-", "NO2-", "SO4-2", "SO3-2", "CO3-2", "PO4-3", "ClO4-", "ClO3-",
    "MnO4-", "Cr2O7-2", "C2O4-2", "CH3COO-", "HCO3-", "HSO4-",
    // Common compounds (inorganic)
    "H2O", "H2O2", "D2O", "NH3", "N2H4", "NO", "NO2", "N2O", "N2O3", "N2O4", "N2O5",
    "HNO3", "HNO2", "CO", "CO2", "H2CO3", "CH4", "C2H6", "C2H4", "C2H2",
    "H2SO4", "H2SO3", "SO2", "SO3", "H2S", "SF6",
    "HCl", "HClO", "HClO2", "HClO3", "HClO4", "HF", "HBr", "HI",
    "H3PO4", "H3PO3", "PH3", "P2O5",
    "NaOH", "KOH", "Ca(OH)2", "Mg(OH)2", "Al(OH)3", "NH4OH",
    "NaCl", "KCl", "CaCl2", "MgCl2", "FeCl2", "FeCl3", "AlCl3",
    "Na2CO3", "NaHCO3", "CaCO3", "K2CO3",
    "NaNO3", "KNO3", "Ca(NO3)2", "AgNO3",
    "Na2SO4", "K2SO4", "CaSO4", "MgSO4", "BaSO4", "CuSO4", "FeSO4",
    "Na3PO4", "K3PO4", "Ca3(PO4)2",
    "NaClO", "NaClO2", "KClO3", "KClO4",
    "KMnO4", "K2Cr2O7", "K2CrO4",
    "NH4Cl", "NH4NO3", "(NH4)2SO4", "(NH4)2CO3",
    "Na2O", "MgO", "CaO", "Al2O3", "Fe2O3", "Fe3O4", "CuO", "Cu2O", "ZnO",
    "SiO2", "TiO2", "P2O5", "SO2", "SO3",
    // Common acids
    "CH3COOH", "HCOOH", "C6H5COOH", "C2H5OH", "CH3OH",
    "C6H6", "C6H14", "C8H18",
    // Organic compounds
    "CH3CHO", "CH3COCH3", "C2H5OC2H5", "CHCl3", "CCl4",
    "C2H2O4", "C4H10O", "C5H12", "C6H5OH", "C6H5NH2",
    // More specific
    "C6H8O6", // ascorbic acid
    "C6H8O7", // citric acid
    "C9H8O4", // aspirin
    "C8H10N4O2", // caffeine
    "C21H30O2", // testosterone
    "H2NCH2COOH", // glycine
    "H2NC(CH3)2COOH", // alanine
    "HClO4", "HBrO4", "HIO4",
    "H2Se", "H2Te",
    "LiOH", "RbOH", "CsOH", "Sr(OH)2", "Ba(OH)2",
    "NaBr", "NaI", "KF", "KBr", "KI",
    "Na2S", "K2S", "CaS", "FeS", "CuS",
    "NaNO2", "KNO2",
    "Na2SO3", "K2SO3",
    "Na2SiO3", "Na2B4O7",
  ];

  // ── Widget ──
  /**
   * A tag input widget for entering chemical substances.
   * @param {HTMLElement|string} container - Container element or selector for the widget.
   * @param {Object} options - Widget configuration options.
   * @param {string|HTMLElement|null} [options.hiddenInput] - Hidden input to synchronize with tag values.
   * @param {string} [options.placeholder] - Placeholder text for the input field.
   * @param {string} [options.name] - Name for a generated hidden input if hiddenInput is not provided.
   */
  class SubstanceTagInput {
    constructor(container, options) {
      this.container =
        typeof container === "string"
          ? document.querySelector(container)
          : container;
      if (!this.container) return;

      this.options = Object.assign(
        {
          placeholder: "Type a substance and press Enter",
          hiddenInput: null,
          name: null,
        },
        options
      );

      // State
      this.tags = [];
      this.undoStack = [];
      this.redoStack = [];
      this.suggestionsVisible = false;
      this.suggestionIndex = -1;

      // Build DOM
      this._buildDOM();

      // Load initial values from hidden input
      this._loadInitial();

      // Bind events
      this._bindEvents();
    }
    
    _buildDOM() {
      // 1. Capture the ID from the fallback before it's deleted
      const fallbacks = this.container.querySelectorAll(".substance-tag-fallback");
      const fallbackId = fallbacks.length > 0 ? fallbacks[0].id : null;

      // Remove any server-rendered fallback inputs before building the widget
      fallbacks.forEach((el) => el.remove());

      // Wrap existing content
      this.container.classList.add("substance-tag-container");
      this.container.style.cssText =
        "display:flex;flex-wrap:wrap;align-items:center;gap:4px;border:1px solid #6c757d;border-radius:0.375rem;padding:4px 8px;min-height:38px;cursor:text;background:var(--bs-body-bg,#212529);color:var(--bs-body-color,#dee2e6);";

      // Create hidden input if needed
      if (this.options.hiddenInput) {
        this.hiddenInput =
          typeof this.options.hiddenInput === "string"
            ? document.querySelector(this.options.hiddenInput)
            : this.options.hiddenInput;
      } else if (this.options.name) {
        this.hiddenInput = document.createElement("input");
        this.hiddenInput.type = "hidden";
        this.hiddenInput.name = this.options.name;
        this.container.parentNode.insertBefore(
          this.hiddenInput,
          this.container.nextSibling
        );
      }

      // Tag list area
      this.tagList = document.createElement("div");
      this.tagList.className = "substance-tag-list";
      this.tagList.style.cssText =
        "display:flex;flex-wrap:wrap;gap:4px;align-items:center;";

      // Text input
      this.textInput = document.createElement("input");
      this.textInput.type = "text";
      
      // 2. Adopt the old ID so the <label> stays linked!
      if (fallbackId) {
        this.textInput.id = fallbackId;
      }

      this.textInput.className = "substance-tag-input";
      this.textInput.style.cssText =
        "border:none;outline:none;flex:1;min-width:120px;padding:2px 4px;font-size:0.9rem;background:transparent;";
      this.textInput.placeholder = this.options.placeholder;

      // Suggestions dropdown
      this.suggestEl = document.createElement("div");
      this.suggestEl.className = "substance-suggestions";
      this.suggestEl.style.cssText =
        "display:none;position:absolute;z-index:1000;background:var(--bs-body-bg,#212529);color:var(--bs-body-color,#dee2e6);border:1px solid #6c757d;border-radius:0.25rem;max-height:200px;overflow-y:auto;box-shadow:0 2px 8px rgba(0,0,0,0.5);width:100%;left:0;top:100%;";

      this.container.appendChild(this.tagList);
      this.container.appendChild(this.textInput);
      this.container.appendChild(this.suggestEl);

      // Make the whole container clickable to focus input
      this.container.addEventListener("click", (e) => {
        if (e.target === this.container || e.target === this.tagList) {
          this.textInput.focus();
        }
      });
    }

    /**
     * Load initial tags from the hidden input value.
     * Expected format is whitespace-separated substance tokens.
     * @private
     */
    _loadInitial() {
      if (!this.hiddenInput || !this.hiddenInput.value) return;
      const values = this.hiddenInput.value
        .split(/\s+/)
        .map((s) => s.trim())
        .filter(Boolean);
      values.forEach((v) => this._addTag(v, false));
    }

    /**
     * Synchronize the hidden input value with the current tag list.
     * Also dispatches a change event to support form listeners.
     * @private
     */
    _syncHidden() {
      if (this.hiddenInput) {
        this.hiddenInput.value = this.tags.join(" ");
        // Trigger change event for form
        this.hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    /**
     * Add a new substance tag to the widget.
     * Duplicate values are ignored.
     * @private
     * @param {string} text - Substance formula to add.
     * @param {boolean} [recordHistory=true] - Whether to record undo history.
     * @returns {boolean} True if the tag was added.
     */
    _addTag(text, recordHistory = true) {
      text = text.trim();
      if (!text) return false;
      if (this.tags.includes(text)) return false;

      if (recordHistory) {
        this._pushHistory({ type: "add", value: text, index: this.tags.length });
      }

      this.tags.push(text);
      this._renderTags();
      this._syncHidden();
      return true;
    }

    /**
     * Remove a tag by index and update the hidden input.
     * @private
     * @param {number} index - Position of the tag to remove.
     * @param {boolean} [recordHistory=true] - Whether to record undo history.
     */
    _removeTag(index, recordHistory = true) {
      if (index < 0 || index >= this.tags.length) return;
      const removed = this.tags.splice(index, 1)[0];

      if (recordHistory) {
        this._pushHistory({
          type: "remove",
          value: removed,
          index: index,
        });
      }

      this._renderTags();
      this._syncHidden();
    }

    _formatFormula(text) {
      // Wraps numbers in <sub> and charges in <sup>
      return text
        .replace(/(\d+)/g, '<sub>$1</sub>')
        .replace(/(\+|\-)(\d+)?/g, (match, p1, p2) => `<sup>${p2 || ''}${p1}</sup>`);
    }

    _renderTags() {
      this.tagList.innerHTML = "";
      this.tags.forEach((tag, i) => {
        const chip = document.createElement("span");
        chip.className = "substance-tag";
        chip.style.cssText = "display:inline-flex;align-items:center;gap:4px;background:var(--bs-secondary-bg,#495057);border-radius:16px;padding:2px 8px;font-size:0.875rem;line-height:1.5;white-space:nowrap;color:var(--bs-body-color,#dee2e6);";

        const label = document.createElement("span");
        
        // Use the new helper function here!
        label.innerHTML = this._formatFormula(tag);
        
        chip.appendChild(label);

        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.innerHTML = "&times;";
        closeBtn.style.cssText =
          "border:none;background:none;cursor:pointer;font-size:1.1rem;line-height:1;padding:0 0 0 2px;color:var(--bs-secondary-color,#adb5bd);display:flex;align-items:center;";
        closeBtn.setAttribute("aria-label", `Remove ${tag}`);
        closeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          this._removeTag(i);
        });
        chip.appendChild(closeBtn);

        this.tagList.appendChild(chip);
      });
    }


    /**
     * Push a change action onto the undo stack.
     * Clearing redo ensures redo only works after an undo.
     * @private
     * @param {{type:string,value:string,index:number}} action
     */
    _pushHistory(action) {
      this.undoStack.push(action);
      this.redoStack = []; // Clear redo stack on new action
    }

    /**
     * Undo the last tag action.
     * Restores the previous tag state and updates the hidden input.
     */
    undo() {
      if (this.undoStack.length === 0) return;
      const action = this.undoStack.pop();
      this.redoStack.push(action);

      if (action.type === "add") {
        // Undo add = remove (but don't record)
        const idx = this.tags.lastIndexOf(action.value);
        if (idx >= 0) {
          this.tags.splice(idx, 1);
        }
      } else if (action.type === "remove") {
        // Undo remove = add back at original index
        this.tags.splice(action.index, 0, action.value);
      }

      this._renderTags();
      this._syncHidden();
    }

    /**
     * Redo the most recently undone tag action.
     * @returns {void}
     */
    redo() {
      if (this.redoStack.length === 0) return;
      const action = this.redoStack.pop();
      this.undoStack.push(action);

      if (action.type === "add") {
        this.tags.push(action.value);
      } else if (action.type === "remove") {
        const idx = this.tags.indexOf(action.value);
        if (idx >= 0) {
          this.tags.splice(idx, 1);
        }
      }

      this._renderTags();
      this._syncHidden();
    }

    _getSuggestions(query) {
      if (!query || query.length < 1) return [];
      const q = query.toLowerCase();
      return COMMON_SUBSTANCES.filter(
        (s) => s.toLowerCase().startsWith(q) && !this.tags.includes(s)
      ).slice(0, 10);
    }

    _showSuggestions(suggestions) {
      this.suggestEl.innerHTML = "";
      if (suggestions.length === 0) {
        this._hideSuggestions();
        return;
      }

      this.suggestionsVisible = true;
      this.suggestionIndex = -1;
      this.suggestEl.style.display = "block";

      suggestions.forEach((s, i) => {
        const item = document.createElement("div");
        item.className = "substance-suggestion-item";
        item.textContent = s;
        item.style.cssText =
          "padding:6px 12px;cursor:pointer;font-size:0.9rem;color:var(--bs-body-color,#dee2e6);";
        item.addEventListener("mouseenter", () => {
          this._highlightSuggestion(i);
        });
        item.addEventListener("mousedown", (e) => {
          e.preventDefault();
          this._selectSuggestion(s);
        });
        this.suggestEl.appendChild(item);
      });
    }

    _hideSuggestions() {
      this.suggestionsVisible = false;
      this.suggestionIndex = -1;
      this.suggestEl.style.display = "none";
    }

    _highlightSuggestion(index) {
      this.suggestionIndex = index;
      const items = this.suggestEl.querySelectorAll(".substance-suggestion-item");
      items.forEach((el, i) => {
        el.style.background = i === index ? "#007bff" : "";
        el.style.color = i === index ? "#fff" : "";
      });
    }

    _selectSuggestion(value) {
      this._addTag(value);
      this.textInput.value = "";
      this._hideSuggestions();
      this.textInput.focus();
    }

    _positionSuggestions() {
      this.suggestEl.style.left = "0";
      this.suggestEl.style.top = "100%";
    }

    _bindEvents() {
      const input = this.textInput;

      input.addEventListener("input", () => {
        const value = input.value;
        const suggestions = this._getSuggestions(value);
        this._showSuggestions(suggestions);

        // Remove last tag if input starts with space (after a tag was added)
        if (value.endsWith(" ") && value.trim()) {
          const trimmed = value.trim();
          if (trimmed) {
            this._addTag(trimmed);
          }
          input.value = "";
        }
      });

      input.addEventListener("keydown", (e) => {
        // Undo: Ctrl+Z / Cmd+Z (without Shift)
        if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
          e.preventDefault();
          this.undo();
          return;
        }
        // Redo: Ctrl+Shift+Z / Cmd+Shift+Z or Ctrl+Y
        if (
          ((e.ctrlKey || e.metaKey) && e.key === "z" && e.shiftKey) ||
          ((e.ctrlKey || e.metaKey) && e.key === "y")
        ) {
          e.preventDefault();
          this.redo();
          return;
        }

        if (this.suggestionsVisible) {
          // Navigate suggestions
          if (e.key === "ArrowDown") {
            e.preventDefault();
            const items = this.suggestEl.querySelectorAll(
              ".substance-suggestion-item"
            );
            const next =
              this.suggestionIndex < items.length - 1
                ? this.suggestionIndex + 1
                : 0;
            this._highlightSuggestion(next);
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            const items = this.suggestEl.querySelectorAll(
              ".substance-suggestion-item"
            );
            const prev =
              this.suggestionIndex > 0
                ? this.suggestionIndex - 1
                : items.length - 1;
            this._highlightSuggestion(prev);
            return;
          }
          if (e.key === "Enter" || e.key === "Tab") {
            if (this.suggestionIndex >= 0) {
              e.preventDefault();
              const items = this.suggestEl.querySelectorAll(
                ".substance-suggestion-item"
              );
              if (items[this.suggestionIndex]) {
                this._selectSuggestion(
                  items[this.suggestionIndex].textContent
                );
                return;
              }
            }
          }
          if (e.key === "Escape") {
            this._hideSuggestions();
            return;
          }
        }

        // Enter with no visible suggestions: add current typed text as tag
        if (e.key === "Enter" || e.key === "Tab") {
          const value = input.value.trim();
          if (value) {
            e.preventDefault();
            this._addTag(value);
            input.value = "";
            this._hideSuggestions();
          }
          return;
        }

        // Backspace: remove last tag if input is empty
        if (e.key === "Backspace" && !input.value && this.tags.length > 0) {
          e.preventDefault();
          this._removeTag(this.tags.length - 1);
          return;
        }

        // Comma: add current text as tag
        if (e.key === ",") {
          const value = input.value.trim();
          if (value) {
            e.preventDefault();
            this._addTag(value);
            input.value = "";
            this._hideSuggestions();
          }
          return;
        }
      });

      // Close suggestions on blur (with delay to allow mousedown on suggestion)
      input.addEventListener("blur", () => {
        setTimeout(() => this._hideSuggestions(), 150);
      });

      // Close suggestions on scroll
      window.addEventListener("scroll", () => this._hideSuggestions(), true);
    }

    // Public API
    /**
     * Get the current tags as an array of strings.
     * @returns {string[]}
     */
    getTags() {
      return [...this.tags];
    }

    /**
     * Remove all tags and reset widget state.
     * @returns {void}
     */
    clear() {
      this.tags = [];
      this.undoStack = [];
      this.redoStack = [];
      this.textInput.value = "";
      this._renderTags();
      this._syncHidden();
    }

    /**
     * Replace the current tags with a new set of values.
     * @param {string[]} tags - New tags to set.
     * @returns {void}
     */
    setTags(tags) {
      this.tags = [];
      this.undoStack = [];
      this.redoStack = [];
      (tags || []).forEach((t) => this._addTag(t, false));
      this._renderTags();
      this._syncHidden();
    }
  }

  // Expose globally
  window.SubstanceTagInput = SubstanceTagInput;
})();