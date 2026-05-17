/**
 * Dynamic reaction row UI for the equilibria form.
 *
 * Each row contains:
 *  - Reactants text input (space-separated species)
 *  - Products text input (space-separated species)
 *  - K Mode dropdown (pKa | Ka)
 *  - K Value text input
 *  - Remove button
 *
 * The hidden `reactions` field is updated on every change.
 *
 * An auto-populated concentration table with unit selectors allows
 * setting initial concentrations per species, with unit conversion
 * handled on the server via Pint.
 */
(function () {
  'use strict';

  var CONCENTRATION_UNITS = [
    { value: 'mol/L', label: 'mol/L' },
    { value: 'mmol/L', label: 'mmol/L' },
    { value: '\u00b5mol/L', label: '\u00b5mol/L' },
    { value: 'nmol/L', label: 'nmol/L' },
    { value: 'pmol/L', label: 'pmol/L' },
  ];

  var tbody = document.getElementById('reactions-tbody');
  var reactionsField = document.querySelector('input[name="reactions"]');
  var concTbody = document.getElementById('concentration-tbody');
  var concSection = document.getElementById('concentration-section');
  var hiddenConc = document.querySelector('input[name="concentrations"]');
  var addBtn = document.getElementById('add-reaction-btn');

  function createRow(reactants, products, kMode, kValue) {
    var tr = document.createElement('tr');
    tr.className = 'reaction-row';

    // Reactants
    var tdReactants = document.createElement('td');
    var inpReactants = document.createElement('input');
    inpReactants.type = 'text';
    inpReactants.className = 'form-control form-control-sm reactants-input';
    inpReactants.placeholder = 'e.g. HCO3-';
    inpReactants.value = reactants || '';
    tdReactants.appendChild(inpReactants);

    // Products
    var tdProducts = document.createElement('td');
    var inpProducts = document.createElement('input');
    inpProducts.type = 'text';
    inpProducts.className = 'form-control form-control-sm products-input';
    inpProducts.placeholder = 'e.g. H+ + CO3-2';
    inpProducts.value = products || '';
    tdProducts.appendChild(inpProducts);

    // K Mode
    var tdKMode = document.createElement('td');
    var selKMode = document.createElement('select');
    selKMode.className = 'form-select form-select-sm k-mode-select';
    var optPka = document.createElement('option');
    optPka.value = 'pKa';
    optPka.textContent = 'pKa';
    var optKa = document.createElement('option');
    optKa.value = 'Ka';
    optKa.textContent = 'Ka';
    selKMode.appendChild(optPka);
    selKMode.appendChild(optKa);
    if (kMode === 'Ka') {
      selKMode.value = 'Ka';
    } else {
      selKMode.value = 'pKa';
    }
    tdKMode.appendChild(selKMode);

    // K Value
    var tdKValue = document.createElement('td');
    var inpKValue = document.createElement('input');
    inpKValue.type = 'text';
    inpKValue.className = 'form-control form-control-sm k-value-input';
    inpKValue.placeholder = 'e.g. 10.3';
    inpKValue.value = kValue || '';
    tdKValue.appendChild(inpKValue);

    // Remove button
    var tdRemove = document.createElement('td');
    tdRemove.className = 'text-center';
    var btnRemove = document.createElement('button');
    btnRemove.type = 'button';
    btnRemove.className = 'btn btn-outline-danger btn-sm remove-reaction-btn';
    btnRemove.textContent = '\u00d7';
    btnRemove.setAttribute('aria-label', 'Remove this reaction');
    btnRemove.addEventListener('click', function () {
      tr.remove();
      updateReactionsField();
      updateConcentrationTable();
    });
    tdRemove.appendChild(btnRemove);

    tr.appendChild(tdReactants);
    tr.appendChild(tdProducts);
    tr.appendChild(tdKMode);
    tr.appendChild(tdKValue);
    tr.appendChild(tdRemove);

    // Attach change listeners to inputs
    [inpReactants, inpProducts, inpKValue, selKMode].forEach(function (el) {
      el.addEventListener('input', function () {
        updateReactionsField();
        if (el === inpReactants || el === inpProducts) {
          updateConcentrationTable();
        }
      });
      el.addEventListener('change', function () {
        updateReactionsField();
        if (el === inpReactants || el === inpProducts) {
          updateConcentrationTable();
        }
      });
    });

    return tr;
  }

  function getReactions() {
    var rows = tbody.querySelectorAll('.reaction-row');
    var reactions = [];
    rows.forEach(function (row) {
      var reactants = row.querySelector('.reactants-input').value.trim();
      var products = row.querySelector('.products-input').value.trim();
      var kMode = row.querySelector('.k-mode-select').value;
      var kValue = row.querySelector('.k-value-input').value.trim();
      if (reactants || products || kValue) {
        reactions.push({
          reactants: reactants,
          products: products,
          k_mode: kMode,
          k_value: kValue,
        });
      }
    });
    return reactions;
  }

  function updateReactionsField() {
    var reactions = getReactions();
    reactionsField.value = JSON.stringify(reactions);
  }

  function parseSpeciesFromReactions() {
    var reactions = getReactions();
    var species = [];
    var seen = {};
    reactions.forEach(function (rxn) {
      [rxn.reactants, rxn.products].forEach(function (side) {
        var tokens = side.split(/\s+\+\s+/);
        tokens.forEach(function (token) {
          token = token.trim();
          if (token && !seen[token]) {
            seen[token] = true;
            species.push(token);
          }
        });
      });
    });
    return species;
  }

  function updateConcentrationTable() {
    var species = parseSpeciesFromReactions();

    // Get existing values before rebuilding
    var existing = {};
    var rows = concTbody.querySelectorAll('tr');
    rows.forEach(function (tr) {
      var subs = tr.querySelector('[data-substance]');
      var unitSel = tr.querySelector('.conc-unit-select');
      if (subs && unitSel) {
        existing[subs.dataset.substance] = {
          value: subs.value,
          unit: unitSel.value,
        };
      }
    });

    if (species.length === 0) {
      concSection.style.display = 'none';
      return;
    }

    concSection.style.display = 'block';
    concTbody.innerHTML = '';

    species.forEach(function (substance) {
      var tr = document.createElement('tr');
      var prev = existing[substance] || {};

      // Substance name
      var tdName = document.createElement('td');
      tdName.textContent = substance;
      tdName.style.verticalAlign = 'middle';

      // Concentration value
      var tdInput = document.createElement('td');
      var input = document.createElement('input');
      input.type = 'number';
      input.step = 'any';
      input.min = '0';
      input.className = 'form-control form-control-sm';
      input.placeholder = '0';
      input.dataset.substance = substance;
      input.value = prev.value || '';
      input.addEventListener('input', function () {
        updateConcentrationsHidden();
      });
      tdInput.appendChild(input);

      // Unit selector
      var tdUnit = document.createElement('td');
      var selUnit = document.createElement('select');
      selUnit.className = 'form-select form-select-sm conc-unit-select';
      CONCENTRATION_UNITS.forEach(function (unit) {
        var opt = document.createElement('option');
        opt.value = unit.value;
        opt.textContent = unit.label;
        selUnit.appendChild(opt);
      });
      selUnit.value = prev.unit || 'mol/L';
      selUnit.addEventListener('change', function () {
        updateConcentrationsHidden();
      });
      tdUnit.appendChild(selUnit);

      tr.appendChild(tdName);
      tr.appendChild(tdInput);
      tr.appendChild(tdUnit);
      concTbody.appendChild(tr);
    });

    updateConcentrationsHidden();
  }

  function updateConcentrationsHidden() {
    if (!hiddenConc) return;
    var concData = {};
    var rows = concTbody.querySelectorAll('tr');
    rows.forEach(function (tr) {
      var input = tr.querySelector('input[data-substance]');
      var unitSel = tr.querySelector('.conc-unit-select');
      if (input && unitSel) {
        var val = parseFloat(input.value);
        if (!isNaN(val) && val > 0) {
          concData[input.dataset.substance] = {
            value: val,
            unit: unitSel.value,
          };
        }
      }
    });
    hiddenConc.value = JSON.stringify(concData);
  }

  function addReaction(reactants, products, kMode, kValue) {
    var row = createRow(reactants, products, kMode, kValue);
    tbody.appendChild(row);
    updateReactionsField();
    updateConcentrationTable();
  }

  // Add reaction button
  addBtn.addEventListener('click', function () {
    addReaction('', '', 'pKa', '');
  });

  // Initialize with default rows if no data
  function initForm() {
    var initialValue = reactionsField.value;
    var initialReactions = [];
    if (initialValue && initialValue.trim()) {
      try {
        initialReactions = JSON.parse(initialValue);
      } catch (e) {
        // ignore
      }
    }

    if (initialReactions.length > 0) {
      initialReactions.forEach(function (rxn) {
        addReaction(rxn.reactants, rxn.products, rxn.k_mode, rxn.k_value);
      });
    } else {
      // Add default rows
      addReaction('HCO3-', 'H+ + CO3-2', 'pKa', '10.3');
      addReaction('H2CO3', 'H+ + HCO3-', 'pKa', '6.3');
      addReaction('H2O', 'H+ + OH-', 'pKa', '14.0');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initForm);
  } else {
    initForm();
  }
})();
