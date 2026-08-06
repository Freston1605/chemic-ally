---
phase: 1
slug: close-the-rce
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-05
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.4.0 + pytest-django 4.11.1 |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest -q tests/chemistry_calculators` |
| **Full suite command** | `pytest -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest -q tests/chemistry_calculators`
- **After every plan wave:** Run `pytest -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | SEC-05 | T-1-01 | Shared `security.py` holds all regexes + eval globals | unit | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | SEC-01 | T-1-01 | `_is_safe_equation` rejects non-2-segment / charset violations | unit | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | SEC-02 | T-1-01 | `EqSystem.from_string` called with `globals_ is SAFE_EVAL_GLOBALS` | unit (pin test) | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-04 | 01 | 1 | SEC-03 | T-1-01 | Non-finite / non-numeric K rejected in form | unit | `pytest -q tests/chemistry_calculators` | ✅ | ⬜ pending |
| 01-01-05 | 01 | 1 | SEC-04 | T-1-01 | Hidden fields capped at 5000 chars | unit | `pytest -q tests/chemistry_calculators` | ✅ | ⬜ pending |
| 01-01-06 | 01 | 1 | SEC-06 | T-1-01 | No side effects for param-path payloads | unit (engine) | `pytest -q tests/chemistry_calculators` | ✅ | ⬜ pending |
| 01-01-07 | 01 | 1 | SEC-06, TEST-03 | T-1-01 | No side effects for kwargs-path payloads (3-segment) | unit + view | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-08 | 01 | 1 | D-06 | T-1-02 | Engine returns safe generic error, never `str(e)` | unit | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-09 | 01 | 1 | SEC-05 | T-1-01 | Both layers import SAME compiled regex (drift guard) | unit | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-10 | 01 | 1 | UI-SPEC a11y | — | `role="alert"` on error containers | view | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |
| 01-01-11 | 01 | 1 | UI-SPEC copy | T-1-02 | Generic "Reactions data could not be read." (no `str(e)`) | unit | `pytest -q tests/chemistry_calculators` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/chemistry_calculators/test_security.py` — stubs for SEC-01/02/05/06
- [ ] `tests/chemistry_calculators/conftest.py` — shared payload fixtures (both eval paths)
- [ ] Existing infra covers form-layer, length caps, and engine marker tests

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual a11y `role="alert"` rendering | UI-SPEC | DOM/ARIA not asserted by pytest | Load `/equilibria`, submit bad reaction, confirm alert region announced |
| Legitimate equilibria still compute end-to-end | SEC-01 regression | Solver numeric behavior | Run `/equilibria`, submit `HCO3- = H+ + CO3-2; 10**-10.3`, confirm pH + species table |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
