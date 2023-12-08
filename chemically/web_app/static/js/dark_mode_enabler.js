document.addEventListener('DOMContentLoaded', function () {
        const currentTheme = localStorage.getItem('theme') || 'light';

        // Apply the current theme on page load
        applyTheme(currentTheme);

        // Toggle theme on click
        document.getElementById('bd-theme-text').addEventListener('click', function () {
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            applyTheme(newTheme);
            localStorage.setItem('theme', newTheme);
        });

        function applyTheme(theme) {
            // Adjust your styles based on the selected theme
            document.body.classList.remove('light-theme', 'dark-theme');
            document.body.classList.add(`${theme}-theme`);
        }
    });