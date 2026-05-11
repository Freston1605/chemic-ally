/**
 * Dark Mode Enabler
 * =================
 * Saves and restores the user's preferred theme using localStorage,
 * and toggles the theme classes on the document body.
 *
 * Expected markup:
 *   <span id="bd-theme-text">Theme</span>
 *
 * The script stores the selected theme under the `theme` key in
 * localStorage and applies one of the CSS classes:
 *   - light-theme
 *   - dark-theme
 */
document.addEventListener('DOMContentLoaded', function () {
        /**
         * Loaded theme preference; defaults to light when missing.
         * @type {string}
         */
        let currentTheme = localStorage.getItem('theme') || 'light';

        // Apply the current theme on page load.
        applyTheme(currentTheme);

        // Toggle theme when the theme text element is clicked.
        document.getElementById('bd-theme-text').addEventListener('click', function () {
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            applyTheme(newTheme);
            currentTheme = newTheme;
            localStorage.setItem('theme', newTheme);
        });

        /**
         * Apply the selected theme by updating body classes.
         * @param {string} theme - Expected `light` or `dark`.
         */
        function applyTheme(theme) {
            // Ensure only the active theme class is present.
            document.body.classList.remove('light-theme', 'dark-theme');
            document.body.classList.add(`${theme}-theme`);
        }
    });
