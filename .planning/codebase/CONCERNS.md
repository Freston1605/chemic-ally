# Codebase Concerns

**Analysis Date:** 2026-08-05

## Critical Security Issue

### RCE via chempy `eval()` in the Equilibria calculator

- Issue: `EquilibriaCalculator.calculate` calls `EqSystem.from_string(reaction_string)` at `apps/chemistry_calculators/calculations/equilibria.py:102` **without** passing `rxn_parse_kwargs={"globals_": False}`. chempy's `Reaction.from_string` (`Reaction.from_string` in chempy, `chempy/chemistry.py`) explicitly documents: *"to_reaction is used which in turn calls `eval` which is a severe security concern for untrusted input"* — and offers the `globals_=False` opt-out ("useful for web-apps").
- The reaction string is fully user-controlled: `EquilibriumSystemForm.clean` at `apps/chemistry_calculators/forms.py:205-219` builds `f"{rxn['reactants']} = {rxn['products']}; {k_expr}"` where `k_expr = f"10**-{rxn['k_value']}"` or `str(rxn["k_value"])` — and `rxn['k_value']` comes from the client-side hidden `reactions` JSON field (validated only for non-emptiness at `forms.py:199-202`). Any string after the `;` is evaluated as Python.
- Impact: **Unauthenticated remote code execution** in the Lambda/Django process. The Function URL is public (`AuthType: NONE`, `template.yaml:128-132`) and the endpoint `POST /hplc/api/simulate/`-adjacent equilibria form (`/equilibria` route via `apps/chemistry_calculators/urls.py`) is reachable by anyone. An attacker can submit `k_value` like `__import__('os').system(...)` inside the JSON and execute arbitrary commands.
- Files: `apps/chemistry_calculators/calculations/equilibria.py:102`, `apps/chemistry_calculators/forms.py:169-219`, `apps/chemistry_calculators/views.py:390-443` (`CalculateEquilibriaView`)
- Fix approach: (1) pass `rxn_parse_kwargs={"globals_": False}` to `EqSystem.from_string` so chempy never evals; (2) validate `k_value` as a float server-side (`float(...)` with rejection of anything else) before interpolation; (3) add a max length to the hidden `reactions`/`concentrations` CharFields; (4) add a regression test with a payload like `"; __import__('os')"` asserting no evaluation.

## Known Bugs

### `LevelProgressSerializer` misconfigured against wrong model → `/hplc/api/progress/` always 500s

- Issue: `LevelProgressSerializer` at `apps/hplc_simulator/serializers.py:199-209` declares `model = UserScore` but lists `LevelProgress`-only fields (`best_score`, `best_resolution`, `best_run_time`, `attempts`, `completed`, `last_attempt_at`). DRF's `build_field` → `build_unknown_field` raises `ImproperlyConfigured` for fields not on the declared model (`serializers.py: get_fields` loop), so the serializer cannot be instantiated.
- Manifestation: `LevelProgressView` at `apps/hplc_simulator/views.py:253-263` calls `LevelProgressSerializer(progress, many=True)` on `GET /hplc/api/progress/` → HTTP 500 for any request that has a session (the `if not session_key: return Response([])` guard at `views.py:258` only protects first-time visitors).
- Files: `apps/hplc_simulator/serializers.py:199-209`, `apps/hplc_simulator/views.py:253-263`
- Fix approach: set `model = LevelProgress` in the serializer (fields already match). Add an API test that hits `/hplc/api/progress/` after a score submission.

### `BalanceChemicalReaction.process_calculation` returns `None` implicitly

- Issue: The `except Exception` handler at `apps/chemistry_calculators/views.py:210-216` logs and sets a message but never returns, so the method (annotated `-> str`) falls through and returns `None`. `form_valid` (`views.py:73-84`) then renders the template with `result=None`.
- Compounding: `calculator.calculate(...)` at `views.py:200-204` is destructured (`reactants_balanced, products_balanced = ...`) but `ReactionBalancer.calculate` at `apps/chemistry_calculators/calculations/base.py:62-72` returns `None` on failure — the destructure raises `TypeError` that the broad handler then catches.
- Files: `apps/chemistry_calculators/views.py:177-216`, `apps/chemistry_calculators/calculations/base.py:62-72`
- Fix approach: return an explicit dict (e.g. `{"error": ...}`) from the exception path and make the template handle a missing result; have `ReactionBalancer.calculate` raise a typed exception instead of returning `None`.

### `ChemicalReactionForm.clean` allows missing reactants OR products

- Issue: `apps/chemistry_calculators/forms.py:94-95` only rejects when *both* are empty (`if not reactant and not product`). Submitting only products (or only reactants) passes validation and then fails inside chempy balancing — surfacing as the generic "Could not balance the reaction" error.
- Fix approach: require both sides non-empty.

## Security Considerations

### Hardcoded fallback `SECRET_KEY` committed to the repo

- Risk: `config/settings/base.py:32-34` ships a real-looking fallback key `hg&fx+3xp@0sf2s^#(hi#tqrbim!q473umn#k+i!ov)55dv5v*`. Production overrides it via `_required_env("SECRET_KEY")` (`config/settings/production.py:35`), so the main exposure is any code path that imports `base.py` standalone (e.g. scripts, ad-hoc settings imports) plus general hygiene.
- Files: `config/settings/base.py:32-34`, `config/settings/development.py:6` (`"dev-secret-key"` fallback)
- Recommendation: remove the fallback value and raise `ImproperlyConfigured` in `base.py` too when unset (mirroring `production.py`).

### Client-supplied scores can be forged — no anti-cheat

- Risk: `ScoreSubmissionView` at `apps/hplc_simulator/views.py:164-235` accepts `score`, `min_resolution`, `total_run_time`, `max_pressure_bar`, `is_successful`, `overpressure` directly from the request body and persists them (`views.py:183-203`) without recomputing against the simulation. `LevelProgress` (`views.py:205-230`) then records them as best scores. A user can POST `score: 999999999` and top every level's progress; the `overpressure` flag lets them bypass the only cross-field consistency check in `UserScore.clean` (`apps/hplc_simulator/models.py:179-186`).
- Files: `apps/hplc_simulator/views.py:164-235`, `apps/hplc_simulator/models.py:107-214`
- Recommendation: recompute score server-side from `min_resolution`/`total_run_time`/`max_pressure_bar` (the engine's `calculate_score` in `apps/hplc_simulator/simulation/engine.py:549`), or sign/issue simulation results server-side.

### No rate limiting, throttling, or WAF

- Risk: `REST_FRAMEWORK` in `config/settings/base.py:158-165` defines no `DEFAULT_THROTTLE_CLASSES`. The Function URL is publicly invokable (`template.yaml:128-156`, `AuthType: NONE`, `Principal: "*"`). `SimulateView` (`apps/hplc_simulator/views.py:51-161`) is CPU-bound (numpy `np.linspace(0, ..., 5000)` + per-analyte EMG peaks, `engine.py:387-419`) and can be hammered for free → Lambda cost abuse / DoS. `ScoreSubmissionView` allows unbounded `UserScore` row creation per session → RDS bloat.
- Recommendation: enable DRF `ScopedRateThrottle`/`AnonRateThrottle`; consider CloudFront WAF rate rules.

### SimulateView leaks internal exception details

- Risk: `apps/hplc_simulator/views.py:109-114` returns `{'error': 'Simulation failed', 'detail': str(e)}` — raw exception messages (paths, library internals) are exposed to anonymous callers.
- Recommendation: log the exception server-side and return a generic message.

### `{{ result|safe }}` on user-derived LaTeX

- Risk: `templates/chemistry_calculators/calculator/reaction_balancer.html:30` renders the balance result with `|safe`. The string embeds user-supplied formula tokens inside `\ce{...}` (`ReactionBalancer.to_latex`, `apps/chemistry_calculators/calculations/base.py:74-88`). chempy's `Substance.from_formula` validation limits the charset, so direct XSS is unlikely today, but the combination of user input + `|safe` is fragile (any parser loosening becomes XSS).
- Recommendation: escape the result or build the LaTeX via structured tokens rather than raw interpolation.

### Lambda security group allows all ingress from VPC CIDR

- Risk: `template.yaml:166-168` — `SecurityGroupIngress: IpProtocol: -1, CidrIp: 172.31.0.0/16` (the default VPC CIDR) plus `SecurityGroupEgress: 0.0.0.0/0` all protocols. Lambda does not listen on ports, so ingress is mostly inert, but this violates least privilege and is a latent hazard if a sidecar/extension ever binds a socket.
- Recommendation: restrict ingress to RDS SG only; tighten egress.

### Secrets passed as plaintext SAM parameter overrides

- Risk: `RDSPassword` and `DjangoSecretKey` are `NoEcho` SAM parameters (`template.yaml:34-41`) supplied via `--parameter-overrides` on the CLI in `.github/workflows/deploy.yml:213-214`. GitHub masks them in logs, but they are still plaintext in the deploy invocation; SSM Parameter Store secure strings or AWS Secrets Manager would be stronger. `RDSDBName`/`RDSUsername`/`RDSHostname` already come from SSM (`template.yaml:16-27`) — move the password/secret key the same way.
- Files: `template.yaml:33-41`, `.github/workflows/deploy.yml:213-214`

### Non-blocking security scan

- Risk: `.github/workflows/deploy.yml` `security-scan` job runs `pip-audit ... || true` and `safety check ... || true` — failures are swallowed and only uploaded as artifacts. Known-vulnerability findings never gate the deploy.
- Recommendation: fail the job on `pip-audit` non-zero (or at least on high/critical severities).

### `check --deploy` runs against development settings

- Risk: the workflow sets `DJANGO_SETTINGS_MODULE: config.settings.development` at `.github/workflows/deploy.yml:16`, so `python manage.py check --deploy` (`deploy.yml` test job) runs with `DEBUG=True` and skips the security checks that only fire in production mode.
- Recommendation: run the check with `DJANGO_SETTINGS_MODULE=config.settings.production` and the required env vars injected.

## Performance Bottlenecks

### Session-based substance history grows unboundedly

- Problem: `add_previous_substances` at `apps/chemistry_calculators/utils/__init__.py:7-13` appends every unique formula ever entered, forever. The list is stored in the session (default DB-backed engine — no `SESSION_ENGINE` override in `config/settings/base.py`), so each session row grows monotonically, and each append is an O(n) containment scan.
- Impact: session table bloat in RDS and ever-larger session payloads written on every request for active users.
- Improvement path: cap the list (e.g. last 20), and/or move history to a per-session table; schedule `clearsessions` (no cron/management command exists in the codebase).

### Unseeded RNG in chromatogram generation

- Problem: `generate_chromatogram` adds `np.random.normal(0, noise_level, time_points)` at `apps/hplc_simulator/simulation/engine.py:418` with no fixed seed. The API response (`views.py:128-161`) is therefore non-deterministic run-to-run for identical inputs.
- Impact: unreproducible simulations, no golden-value testing, and slightly different "same setup" results for users (perceived flakiness).
- Improvement path: accept a `seed` parameter (derive from request or fixed default) and pass to `np.random.default_rng(seed)`.

## Fragile Areas

### Equilibria calculation chain (view → form → chempy)

- Files: `apps/chemistry_calculators/views.py:390-443`, `apps/chemistry_calculators/forms.py:100-254`, `apps/chemistry_calculators/calculations/equilibria.py:58-137`
- Why fragile: The hidden-JSON form contract (`reactions`/`concentrations`) is client-generated (`equilibria.html` JS) and reconstructed into strings server-side; the reconstruction (`10**-{k_value}`) is both an injection vector and brittle against non-numeric values; `EqSystem.root` can diverge/raise for pathological inputs (caught broadly at `equilibria.py:129-137`, which hides the real failure reason from the UI).
- Safe modification: keep the string reconstruction in one place (`forms.py:clean`), validate `k_value` numerically, disable chempy eval, and test with the payload set in the RCE section.
- Test coverage: `tests/chemistry_calculators/test_webapp.py` covers valid systems and one bad-equation case (`test_no_success_on_bad_equation`, `test_empty_equations`) but no adversarial/malicious inputs and no view-level POST of the hidden JSON.

### `ReactionBalancer` uses sets, losing duplicates and ordering

- Files: `apps/chemistry_calculators/views.py:198-199` (`reactants_dict = {reactant for reactant in reactants}`)
- Why fragile: duplicate formulas collapse (typing `H2 H2` ≠ `2 H2`) and set iteration order is arbitrary, so the balanced-equation output ordering is non-deterministic across runs/Python hash seeds. chempy balancing itself may also raise on unsupported formulas — swallowed to `None` at `calculations/base.py:70-72`.
- Safe modification: pass lists (order-preserving) and rely on chempy to fold stoichiometry; surface a specific error message on failure.

### API layer has zero test coverage

- Files: all six endpoints in `apps/hplc_simulator/urls.py` (`api/levels/`, `api/levels/<slug>/`, `api/simulate/`, `api/scores/`, `api/scores/history/`, `api/progress/`)
- Why fragile: no test exercises `apps/hplc_simulator/views.py` — which is exactly why the broken `LevelProgressSerializer` shipped. Serializer boundary changes (like adding `level` fields or pagination) will go unnoticed.
- Test coverage: `tests/hplc_simulator/` covers engine (`test_engine.py`), model constraints (`test_models.py`), and serializer/validation logic (`test_validation.py`) only.

## Scaling Limits

### Database (RDS PostgreSQL via Lambda)

- Current: `CONN_MAX_AGE=0` (`config/settings/production.py:77`) — every request opens a fresh connection (correct for Lambda but connection-heavy). No connection pooler (PgBouncer/RDS Proxy) configured.
- Limit: Lambda concurrency × connection-per-request will exhaust RDS `max_connections` under load (1024MB Lambda, default concurrency burst).
- Scaling path: add RDS Proxy in front of the DB and point `RDS_HOSTNAME` at it.

### Unbounded `UserScore` rows

- Current: one row per submitted run (`ScoreSubmissionView`), no retention/cleanup.
- Limit: anonymous sessions can POST unlimited scores; table grows without bound.
- Scaling path: cap history per session/level (keep top N), add a cleanup job.

### Chromatogram payload size

- Current: `time_points=5000` → ~10k floats serialized per `SimulateView` response (`engine.py:387`, `views.py:129-131`), no pagination/decimation for the response.
- Limit: fine today; becomes a Lambda payload/CloudFront latency issue if `time_points` is ever raised or multiple simulations are batched.

## Dependencies at Risk

### chempy 0.9.0 (pinned) with `eval`-based parsing

- Risk: the library's parsing path uses `eval` for reaction strings (`chempy/chemistry.py` `Reaction.from_string` docstring states this explicitly). The app must pass `globals_=False`; it currently does not. See the Critical Security Issue above.
- Migration plan: keep chempy for the physics but gate all string parsing; or build `EqSystem` from parsed dicts instead of strings.

### Unpinned ranges + observed drift

- Risk: `requirements/base.txt` pins `Django==5.2.3`, `chempy==0.9.0`, `Pint==0.24.4`, `djangorestframework==3.15.2`, but leaves `numpy>=2.1.0,<3` and `scipy>=1.14.0,<2` loose. The local venv already shows drift: `djangorestframework==3.17.1` installed vs `3.15.2` pinned (`.venv/lib/python3.14/site-packages/rest_framework/__init__.py`). The engine requires `np.trapezoid` (numpy ≥ 2.0 only — satisfied by the floor) but scipy behavior can shift within its range.
- Impact: CI and local environments can differ from the pinned set; serializer/API behavior changes between DRF versions.
- Fix approach: fully pin the transitive set (or lockfile) and verify the venv matches `requirements/*.txt`.

### gunicorn in production requirements but unused on Lambda

- Risk: `requirements/production.txt` includes `gunicorn==22.0.0`; the Lambda runtime uses Mangum (`config/asgi.py:16`), so gunicorn is dead weight in the image (`Dockerfile` installs `requirements-lambda.txt` which references `production.txt` → `base.txt` plus `psycopg2-binary` and `mangum`; gunicorn is excluded from `requirements-lambda.txt` — but the root `requirements.txt` still pulls it for any local `pip install -r requirements.txt`).
- Impact: minimal, but the requirements graph (`requirements.txt` → `production.txt` → `base.txt`, plus `requirements-lambda.txt`) is confusing and drift-prone. Simplify to a single file set per environment.

## Missing Critical Features

### Database migrations never run in CI/CD

- Problem: `.github/workflows/deploy.yml` runs lint → test → security scan → Docker build → `sam deploy`, but there is **no `manage.py migrate` step** anywhere. New migrations (e.g. future `hplc_simulator` schema changes) are never applied to RDS; the first deploy also requires manual schema bootstrap.
- Blocks: any model change silently fails in production (missing tables/columns), and `seed_hplc_data` (`apps/hplc_simulator/management/commands/seed_hplc_data.py`) must be run manually against RDS.
- Fix approach: add a migrate step in the deploy job (run against production settings with RDS env vars from secrets) and document the seed step in `OPS_PLAYBOOK.md`.

### No auth for the HPLC API or leaderboard

- Problem: all `/hplc/api/*` endpoints use `permission_classes = [AllowAny]` (`apps/hplc_simulator/views.py:34,44,52,166,240,255`) and identity is just the session key. Scores are anonymous, forgeable (see Security), and lost when the session cookie expires.
- Blocks: any future leaderboard/global ranking needs a real identity model.

### No error-reporting/APM integration

- Problem: logging is console-only (`config/settings/base.py:173-192`); no Sentry/CloudWatch-alarm wiring in code. Exceptions like the `LevelProgressSerializer` 500s surface only as CloudWatch logs with no alerting.
- Fix approach: add Sentry or CloudWatch alarms on `5XXError` metrics for the Function URL.

## Test Coverage Gaps

### Untested: all DRF API views

- What's not tested: `LevelListView`, `LevelDetailView`, `SimulateView`, `ScoreSubmissionView`, `UserScoresView`, `LevelProgressView` (`apps/hplc_simulator/views.py`)
- Risk: the `LevelProgressSerializer` 500 and the score-forging hole shipped and remain undetected.
- Priority: High

### Untested: malicious/robustness inputs to chempy

- What's not tested: injection payloads in `EquilibriumSystemForm`'s `k_value`/reactions, pathological formulas for `Substance.from_formula`, oversized JSON payloads
- Files: `apps/chemistry_calculators/forms.py:100-254`, `apps/chemistry_calculators/calculations/equilibria.py`
- Risk: the RCE vector is live with no regression guard.
- Priority: High

### Untested: error paths of calculator views

- What's not tested: `BalanceChemicalReaction` failure branch (`views.py:210-216`), `CalculateDilutionView` exception branch (`views.py:360-367`), `ReactionBalancer.calculate` returning `None`
- Risk: implicit `None` results render in templates (`{{ result|safe }}`) — the failure UX is unverified.
- Priority: Medium

### Untested: session behavior

- What's not tested: `add_previous_substances` growth, session creation on anonymous calculator use, `LevelProgressView` behavior with/without a session
- Risk: unbounded session payloads and the progress-endpoint 500 for existing sessions.
- Priority: Medium

---

*Concerns audit: 2026-08-05*
