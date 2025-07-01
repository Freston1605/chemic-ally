document.addEventListener('DOMContentLoaded', function () {
        let currentTheme = localStorage.getItem('theme') || 'light';

        // Apply the current theme on page load
        applyTheme(currentTheme);

        // Toggle theme on click
        // When the user clicks the theme text, toggle between light and dark
        document.getElementById('bd-theme-text').addEventListener('click', function () {
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            applyTheme(newTheme);
            currentTheme = newTheme;
            localStorage.setItem('theme', newTheme);
        });

        function applyTheme(theme) {
            // Adjust your styles based on the selected theme
            document.body.classList.remove('light-theme', 'dark-theme');
            document.body.classList.add(`${theme}-theme`);
        }
    });
