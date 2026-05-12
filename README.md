# ChemicAlly

ChemicAlly is a web platform designed to facilitate quick and efficient calculations in the field of chemistry and related disciplines. The platform serves chemists and other professionals, providing tools for essential calculations in various chemical procedures. ChemicAlly acts as a client for the ChemPy library, leveraging its capabilities to deliver precise and reliable solutions.

## Features

- **Molecular Weight Calculator** — Calculate molecular weights of one or more chemical compounds by entering their formulas (e.g., `NH4+ CO2 H2O`). Results are displayed alongside each formula.

- **Chemical Reaction Balancer** — Balance the stoichiometry of any chemical reaction. The balanced equation is rendered as LaTeX via MathJax, with support for both reversible and irreversible reactions.

- **Dilution Calculator** — Perform **C₁V₁ = C₂V₂** dilution calculations. Provide any three of the four variables (initial concentration, initial volume, final concentration, final volume) and the missing value is computed automatically. Supports unit-aware arithmetic via **Pint** (mol/L, mmol/L, mL, L, etc.) and optionally calculates the required solute mass when a molecular weight or chemical formula is supplied.

- **Dashboard** — Consolidated single-page interface that combines all three calculators. Results and form values persist across requests via the user session. Includes a "Use MW" button to copy a molecular weight result directly into the dilution calculator.

- **Substance History** — Previously entered chemical formulas are stored in the session and accessible across pages, making re-use of common substances quick.

- **Dark Mode** — Modern dark-themed UI built with **Bootstrap 5.3**.

- **LaTeX Rendering** — Balanced equations and other chemical expressions are rendered beautifully with **MathJax 3**.

- **Integration with ChemPy** — Utilizes the powerful ChemPy library for molecular parsing and stoichiometric balancing, ensuring accurate and reliable results.

## Tech Stack

| Technology     | Role                                    |
| -------------- | --------------------------------------- |
| Python / Django | Web framework                           |
| Bootstrap 5.3  | Front-end UI / responsive design        |
| ChemPy         | Chemical formula parsing & balancing    |
| Pint           | Unit-safe arithmetic (concentration/volume) |
| MathJax 3      | LaTeX rendering in the browser          |
| Gunicorn       | Production WSGI server                  |
| AWS            | Hosting platform (optional)            |

## Project Structure

```
chemic-ally/
├── apps/webapp/            # Main Django application
│   ├── calculations/       # Calculation engine (MolecularWeight, Reaction, Dilution)
│   ├── utils/              # Utility helpers (session persistence, etc.)
│   ├── forms.py            # Django forms (MolecularFormula, ChemicalReaction, Solution)
│   ├── models.py           # Placeholder (no database models yet)
│   ├── urls.py             # URL routing for webapp
│   └── views.py            # Class-based views for all calculators + dashboard
├── config/                 # Django project configuration
│   ├── settings/           # Environment-specific settings (base, development, production)
│   └── urls.py             # Root URL configuration
├── chemically/             # Additional project-level modules
├── templates/webapp/       # HTML templates
│   └── calculator/         # Per-calculator templates
├── static/                 # Static assets (CSS, JS)
├── tests/                  # Test suite
├── requirements/           # Dependency files per environment
├── manage.py               # Django management script
└── .env.example            # Environment variable template
```

## Getting Started

To run the project on your machine, follow the steps below.

### Prerequisites

1. **Python Installation:**
   - Ensure Python is installed on your machine. You can download it from [Python's official website](https://www.python.org/downloads/).

2. **Virtual Environment (Optional but Recommended):**
   - Create a virtual environment to isolate project dependencies:

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

   ```bash
   pip install -r requirements/development.txt
   ```

3. **Run Database Migrations:**

   ```bash
   python manage.py migrate
   ```

4. **Create a Superuser (Optional):**
   - If you need access to the Django Admin:

     ```bash
     python manage.py createsuperuser
     ```

5. **Run the Development Server:**

   ```bash
   python manage.py runserver
   ```

6. **Access the Application:**
   - Open your web browser and go to <http://localhost:8000>.

### Additional Steps

- **Django Settings:**
  - Settings are split across `config/settings/`:
    - `base.py` — shared configuration.
    - `development.py` — local development overrides.
    - `production.py` — production overrides.
  - Use the `DJANGO_SETTINGS_MODULE` environment variable to select the active settings module (e.g., `config.settings.development`).

- **Static Files:**
  - Run `python manage.py collectstatic` to gather static assets into the directory specified by `STATIC_ROOT`.

- **Environment Variables:**
  - See the **Environment Variables** section below for required keys.

- **Logging:**
  - Logs are written to stdout/stderr via a console handler and are visible in the terminal (development) or platform logs (production, e.g., CloudWatch).

## Environment Variables

ChemicAlly expects certain configuration values to be supplied via environment variables. These values can be stored in a `.env` file at the project root.

You can copy `.env.example` to `.env` and fill in your own values.

### User-Supplied Variables

The following keys must be set by the developer (either in `.env` for local development or as environment properties in the hosting service):

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django's secret key used for cryptographic signing. |
| `DEBUG` | `"True"` to enable debug mode, otherwise `"False"`. |
| `ALLOWED_HOSTS` | Space-separated list of hosts the application can serve. |
| `AWS_ACCESS_KEY_ID` | AWS access key for S3 static/media file storage (production only). |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for S3 static/media file storage (production only). |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket name for static/media files (production only). |
| `AWS_S3_REGION_NAME` | AWS region for the S3 bucket (default: `us-east-2`). |
| `AWS_S3_CUSTOM_DOMAIN` | Optional CloudFront domain for serving static files. |

### AWS RDS (Elastic Beanstalk &mdash; auto-provided)

The RDS variables below are injected automatically by AWS Elastic Beanstalk when a PostgreSQL database is attached to the environment. They are listed here for reference only and should **not** be added manually.

- `RDS_DB_NAME` &ndash; PostgreSQL database name.
- `RDS_USERNAME` &ndash; PostgreSQL username.
- `RDS_PASSWORD` &ndash; PostgreSQL password.
- `RDS_HOSTNAME` &ndash; PostgreSQL host (endpoint).
- `RDS_PORT` &ndash; PostgreSQL port (typically `5432`).

## Deploying to AWS

A basic AWS deployment follows the same steps as local setup but using environment variables provided by the hosting service. Ensure the values from `.env.example` are configured in your instance or container.

1. Install dependencies:

   ```bash
   pip install -r requirements/production.txt
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