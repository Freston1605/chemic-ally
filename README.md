# ChemicAlly

ChemicAlly is a web platform designed to facilitate quick and efficient calculations in the field of chemistry and related disciplines. The platform serves chemists and other professionals, providing tools for essential calculations in various chemical procedures. ChemicAlly acts as a client for the Chempy library, leveraging its capabilities to deliver precise and reliable solutions.

## Features

- **Molecular Mass Calculations:** Easily calculate the molecular mass of chemical compounds, streamlining the process of analysis and experiment design.

- **Chemical Equation Balancing:** Simplify the process of balancing chemical equations, ensuring reactants and products are balanced according to chemical laws.

- **Intuitive Web Interface:** ChemicAlly features an intuitive and user-friendly web interface, designed for users to perform calculations quickly and efficiently.

- **Integration with Chempy:** Utilizes the powerful Chempy library to carry out calculations, ensuring accurate and reliable results backed by advanced chemical algorithms.

## Getting Started

To run a Django project on your machine, follow these general steps. Note that specific details might vary based on your project structure, Django version, and dependencies.

### Prerequisites

1. **Python Installation:**
   - Ensure Python is installed on your machine. You can download it from [Python's official website](https://www.python.org/downloads/).

2. **Virtual Environment (Optional but Recommended):**
   - Create a virtual environment to isolate project dependencies. Navigate to your project directory in the terminal and run:

     ```bash
     python -m venv venv
     ```

   - Activate the virtual environment:
     - On Windows:

       ```bash
       .\venv\Scripts\activate
       ```

     - On Unix or MacOS:

       ```bash
       source venv/bin/activate
       ```

### Running the Django Project

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/Freston1605/chemic-ally
   cd chemic-ally
   ```

2. **Install Dependencies:**
   - Install the required Python packages:

     ```bash
     pip install -r requirements/development.txt
     ```

3. **Run Database Migrations:**
   - Apply the database migrations to set up the database:

     ```bash
     python manage.py migrate
     ```

4. **Create a Superuser (Optional):**
   - If your Django project includes the Django Admin, you might want to create a superuser account:

     ```bash
     python manage.py createsuperuser
     ```

5. **Run the Development Server:**
   - Start the Django development server:

     ```bash
     python manage.py runserver
     ```

6. **Access the Application:**
   - Open your web browser and go to <http://localhost:8000> or the address provided in the terminal.

### Additional Steps

- **Django Settings:**
  - Ensure your `settings.py` file is correctly configured, including database settings, static files, and other Django configurations.

- **Static Files and Media:**
  - If your project involves static files or media uploads, configure your development server to serve these files. In a production environment, you would use a web server like Nginx or Apache to handle this.

- **Collect Static Files:**
  - Run `python manage.py collectstatic` to gather static assets into the directory specified by `STATIC_ROOT`.

- **Environment Variables:**
  - See the **Environment Variables** section below for required keys.
- **Logging File:**
  - Logs are written to `django.log` in the project directory. Ensure the
    application has write permissions there.

Keep in mind that these are general steps, and you might need to adjust them based on your specific Django project setup and requirements. Always refer to your project's documentation or README for any project-specific instructions.

## Environment Variables

ChemicAlly expects certain configuration values to be supplied via environment variables. These values can be stored in a `.env` file at the project root. The following keys are required:

- `SECRET_KEY` &ndash; Django's secret key used for cryptographic signing.
- `DEBUG` &ndash; set to `"True"` to enable debug mode, otherwise `"False"`. The value is parsed as a boolean.
- `ALLOWED_HOSTS` &ndash; space-separated list of hosts the application can serve.

You can copy `.env.example` to `.env` and fill in your own values.

## Deploying to AWS

A basic AWS deployment follows the same steps as local setup but using environment variables provided by the hosting service. Ensure the values from `.env.example` are configured in your instance or container.

1. Install dependencies:

```bash
pip install -r requirements/development.txt
```

2. Run migrations:

```bash
python manage.py migrate
```

3. Collect static files:

```bash
python manage.py collectstatic --noinput
```

4. Start the application with `gunicorn`:

```bash
gunicorn config.wsgi --bind 0.0.0.0:8000
```

## Continuous Integration

Automated Django tests run via GitHub Actions. The workflow in `.github/workflows/tests.yml` executes the test suite on pushes and pull requests to `main`.
