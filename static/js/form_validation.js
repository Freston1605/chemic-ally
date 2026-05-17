/**
 * Client-side form validation for calculator forms.
 * Provides immediate feedback before server submission.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('calculator-form');
    if (!form) return;

    form.addEventListener('submit', function (e) {
      var invalidFields = form.querySelectorAll(':invalid');
      if (invalidFields.length > 0) {
        e.preventDefault();
        invalidFields[0].focus();
        invalidFields[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });

    // Real-time validation feedback for number inputs
    var numberInputs = form.querySelectorAll('input[type="number"]');
    numberInputs.forEach(function (input) {
      input.addEventListener('input', function () {
        if (input.validity.rangeUnderflow) {
          input.setCustomValidity('Value must be greater than or equal to ' + input.min);
        } else if (input.validity.badInput && input.value !== '') {
          input.setCustomValidity('Please enter a valid number');
        } else {
          input.setCustomValidity('');
        }
      });
    });

    // Dilution-specific: validate exactly 3 of 4 fields are filled
    var dilutionFields = form.querySelectorAll('input[name="c1"], input[name="v1"], input[name="c2"], input[name="v2"]');
    if (dilutionFields.length === 4) {
      dilutionFields.forEach(function (input) {
        input.addEventListener('input', validateDilution);
      });
    }
  });

  function validateDilution() {
    var form = document.getElementById('calculator-form');
    if (!form) return;
    var fields = ['c1', 'v1', 'c2', 'v2'];
    var filled = 0;
    var lastEmpty = null;
    fields.forEach(function (name) {
      var input = form.querySelector('input[name="' + name + '"]');
      if (input) {
        if (input.value !== '') {
          filled++;
          input.setCustomValidity('');
        } else {
          lastEmpty = input;
        }
      }
    });

    if (filled === 4) {
      if (lastEmpty) lastEmpty.setCustomValidity('Leave exactly one field blank to calculate it');
    } else if (filled < 3) {
      if (lastEmpty) lastEmpty.setCustomValidity('Enter at least 3 of the 4 values');
    }
  }
})();
