# Addendum — ChemicAlly UX Redesign PRD

*Overflow content: technical details, rejected alternatives, options-considered matrices.*

---

## Rejected Alternatives

- **Complete framework swap** (Tailwind, shadcn/ui) — rejected to minimize rebuild effort; Bootstrap 5.3 custom dark theme achieves the goal with less risk.
- **Light mode toggle** — deferred to v2; hobby scope doesn't justify the complexity for v1.
- **Headless component library** — rejected; project has no existing component abstraction layer, adding one would inflate scope.

## Options Considered

### Accent Color Palette

| Option | Pros | Cons |
|--------|------|------|
| Teal/cyan | Natural chemistry association (HPLC, glassware), good contrast on dark | May feel clinical |
| Amber/orange | Warm, inviting, high energy | Could clash with dark chrome |
| Indigo/purple | Modern dev-tool feel, pairs well with charcoal | Less "organic" |
| **Teal (#14b8a6)** | **Chosen: balanced — energetic but not clinical, fits science context** | — |
