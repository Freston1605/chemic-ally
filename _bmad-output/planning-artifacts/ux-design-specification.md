---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
inputDocuments:
  - prds/prd-ChemicAlly-2026-05-19/prd.md
---

# UX Design Specification ChemicAlly

**Author:** Franco
**Date:** 2026-05-19

---

## Executive Summary

### Project Vision

ChemicAlly is a chemistry workbench — dark, inviting, and deliberately simple. Democratizes CLI tools for non-tech-savvy scientists by wrapping scientific calculations in an approachable, mobile-friendly web UI. Speed is the primary constraint; the dark dev-tool aesthetic is achieved through CSS only.

### Target Users

- **Francisca** (primary) — a bench scientist performing methods that require quick calculations (dilutions, concentrations, molecular weights). Uses the app on her phone while wearing gloves at the bench. Non-tech-savvy, needs hand-holding via defaults rather than documentation.
- **Students and lab professionals** (secondary) — find the site via search, primarily for the HPLC simulator as an educational tool. More exploratory but equally non-technical in their expectations.

### Key Design Challenges

1. **Approachability gap** — Scientific calculators (equilibria, HPLC) look intimidating. Need to make purpose obvious without over-explaining.
2. **Mobile-first physical context** — Francisca uses the app one-handed at the bench. Thumb-zone layout, large touch targets, results in the same viewport as inputs.
3. **Performance ceiling** — Speed is #1. MathJax and Plotly are the real bottlenecks, not Bootstrap. Load Plotly only on HPLC pages; async MathJax with fragment-only typesetting.

### Design Opportunities

1. **Defaults as tutorial** — Pre-filled forms that produce correct output on first load teach by doing. Tooltips only on deliberate (?) click.
2. **Progressive disclosure** — HPLC simulator starts with 3 fields (Column → Length → Flow rate). Run. Then unlock advanced panels. Never show the full grid at once.
3. **Session as open notebook** — Hybrid localStorage + Django session. Calculators remember state as Francisca hops between tools.
4. **Convention-based expansion** — `BaseCalculatorView`, per-tool `_docs.yaml`, shared `tool_base.html`. Adding a new tool means 15-line template + 40-line view + 3 lines of copy.

### Design Decisions from Party Mode

| Decision | Source | Rationale |
|---|---|---|
| No single-page workspace | Franco | More tools coming; separate pages avoid clutter |
| Progressive-disclosure HPLC | Franco (from Paige) | Start simple, unlock advanced |
| Verb headers ("Find a mass" not "MW Calculator") | Paige | Purpose-obvious at a glance |
| Conversational microcopy ("How much?" not "Target concentration") | Paige | Bench-speak feels natural |
| Dual-tier session (localStorage + Django) | Winston | Speed layer + authoritative store |
| Async MathJax, per-page Plotly | Winston | Performance levers |
| Thumb-zone: Calculate at bottom, results above | Sally | One-handed mobile use |
| Presets over blank forms | Sally | Ramp, not wall |

## Core User Experience

### Defining Experience

Francisca enters parameters into a calculator and taps Calculate. The result appears instantly in the same viewport, above her thumb, confirming the calculation is correct. She copies the result or adjusts a parameter and recalculates. The app feels invisible — she focuses on her bench work, not the interface.

### Platform Strategy

- **Web only** — Django server-rendered, Bootstrap 5.3 responsive
- **Mobile-first** — touch-primary with mouse/keyboard fallback
- **No offline** — always-on web; session state survives via Django sessions
- **No PWA** — deferred; not load-bearing for the experience
- **No native app** — browser is sufficient for quick calculations

### Effortless Interactions

- **Session persistence** — switching between tools never loses form state. Feels like an open notebook.
- **Pre-filled defaults** — every form produces a valid result on first load. Francisca learns by tweaking, not by researching.
- **One-tap calculate** — the primary action is always in the thumb zone, always visible, always instant.
- **Copy result** — clipboard button on every result card. Francisca never transcribes.
- **Verb headers** — "Find a molecule's mass" tells her instantly what the tool does.

### Critical Success Moments

1. **Result appears instantly** — the answer is visible in the same viewport as the inputs. No scroll, no loading spinner.
2. **Result is correct** — she doesn't second-guess her input. The calculation matches her expectation.
3. **She switches tools and returns** — her previous inputs are still there. The open notebook pattern delivers.
4. **First-time HPLC run** — the progressive-disclosure flow (3 fields → run → unlock more) makes a complex tool feel simple on the first try.

### Experience Principles

1. **One thumb, one tap** — Every primary action within reach of a single hand holding a phone. No pinch-zoom, no two-handed navigation.
2. **Results first** — The output of a calculation is the hero. Inputs are secondary scaffolding. Show the answer before asking for more.
3. **Open notebook** — Francisca can switch between tools without losing her place. State persists until she explicitly clears it.
4. **Silent until asked** — The interface explains itself through defaults and examples, not text. Help is a (?) tap away, never inline.
5. **Performance is UX** — A fast page that looks good beats a beautiful page that makes her wait. Every kilobyte and round-trip is accounted for.

## Desired Emotional Response

### Primary Emotional Goals

- **Confident** — Francisca trusts the result implicitly. The UI communicates reliability through clarity and precision, not through decorative reassurance.
- **Empowered** — She can perform calculations that previously required a desktop app or manual formula lookup, right from her phone at the bench.
- **Unburdened** — The app replaces the mental overhead of tracking variables. She focuses on the experiment, not the arithmetic.

### Emotional Journey Mapping

- **Discovery** — *Intrigued.* Verb headers and examples tell her instantly what each tool does without reading.
- **First use** — *Skeptical → Confident.* She enters a value she already knows the answer to, sees it match, and trusts the tool.
- **Core action** — *Focused.* The UI fades away. One tap, result appears, she copies and moves on.
- **Return visit** — *Relieved.* Her previous inputs are still there (session persistence). She doesn't redo work.
- **Paper writing** — *Grateful.* She can reconstruct every variable and result from her session history.

### Micro-Emotions

- **Confidence vs. Skepticism** — The critical axis. Every design decision either builds trust (clear results, visible units, reproducible history) or erodes it (vague error messages, lost state, hidden variables).
- **Trust vs. Doubt** — Francisca must believe the platform is at least as reliable as her pen-and-paper method. Session persistence of the full input-process-output stack is non-negotiable.
- **Accomplishment vs. Frustration** — Each completed calculation is a small win. Errors must explain *what went wrong* in bench-speak, not "invalid input."

### Design Implications

- **Confidence** → Every result card shows the input parameters alongside the output. Francisca can verify at a glance.
- **Reproducibility** → Session history stores the full stack: inputs + calculation type + outputs + timestamp. Accessible from a history panel.
- **Trust** → Units displayed clearly on every result. No ambiguous numbers. Copy-to-clipboard includes units and labels, not just values.
- **No skepticism** → Pre-filled defaults produce correct results. If she changes a value and gets an error, the error message tells her *what to fix*, not just "invalid."

### Emotional Design Principles

1. **Show your work** — Every result card displays the inputs that produced it. Reproducibility is the foundation of trust.
2. **Never lose data** — Session persistence covers inputs, outputs, and process. Francisca can reconstruct any calculation from her session history.
3. **Errors are explanations** — An error message tells her what went wrong and what to change. Blank red text is not acceptable.
4. **Units are sacred** — Every numeric value is displayed with its unit. No naked numbers.

## UX Pattern Analysis & Inspiration

### Inspiring Products Analysis

- **GitHub** — Charcoal dark theme (`#0d1117`), tab-based navigation with active underline, card grid with subtle hover elevation, high information density that remains scannable.
- **Sentry** — Widget-based metrics cards (label + large value + sparkline), sidebar nav with icon+label, issue grouping as "list → detail → actions" flow.
- **BLAST (NCBI)** — Form-centric input design, prominent single action button, sortable results tables with expandable detail rows. Proves that zero-flourish functional UX earns trust.
- **SWISS-MODEL** — Step indicator for multi-stage workflows (Build → Explore → Validate), colored quality gauges (green/yellow/red) for pass/warning/fail, interactive 3D viewer paired with downloadable outputs.
- **AlphaFold DB** — Search-first landing with card results (thumbnail + confidence badge), tabbed detail pages (Structure → Sequence → Download), confidence scoring as a prominent visual badge.

### Transferable UX Patterns

- **Tab navigation** (GitHub) → Active tool indicator in nav, breadcrumbs on all pages.
- **Metric widget cards** (Sentry) → HPLC results: Score, Resolution, Run Time, Status as large stat cards.
- **Colored quality gauges** (SWISS-MODEL, Sentry) → HPLC pressure gauge green→yellow→red gradient. Equilibria pH range indicators.
- **Card + badge** (AlphaFold DB) → Tool cards with difficulty badges. HPLC level cards.
- **Form-centric functional UX** (BLAST) → Calculator pages: form is the hero, results follow. No decorative distractions.

### Anti-Patterns to Avoid

- **Over-widgetization** (Sentry trap) — The landing page should not become a dashboard. It's a tool grid. Avoid adding sparklines, graphs, or activity indicators to the landing.
- **Infinite scroll in results** (BLAST trap) — For ChemicAlly's calculators, results are small. Keep pagination for potential future history views, but never for single-calculation results.
- **Deeply nested navigation** — BLAST's multi-level menu is confusing. ChemicAlly keeps a flat hierarchy: Tool Pages + Nav.
- **Splash screens/walkthroughs** — Common in scientific tools (AlphaFold DB has one). ChemicAlly uses defaults-as-tutorial instead. No first-run overlays.

### Design Inspiration Strategy

**From dev tools (GitHub, Sentry):** Dark theme approach, navigation patterns, metrics display.

**From scientific tools (BLAST, SWISS-MODEL, AlphaFold):** Form-centric workflow, quality visualization, results scannability, multi-step process indicators.

**To avoid:** Overly complex navigation, first-run tutorials, dashboard clutter, infinite scroll for short results.

## Design System Foundation

### 1.1 Design System Choice

**Custom dark theme built on Bootstrap 5.3 CSS variables.** No external design system dependency (no MUI, no Ant Design, no Tailwind). Bootstrap provides grid, components, and utilities; custom `--ca-*` CSS custom properties override the visual identity completely.

### Rationale for Selection

- **Performance** — Bootstrap is already loaded. Adding a second design system would add download time. The dark dev-tool aesthetic is achieved entirely through CSS overrides.
- **Uniqueness** — Custom tokens override Bootstrap's defaults across the board. The result won't look like a generic Bootstrap site.
- **Maintenance** — All design tokens in `static/css/style.css` in one `:root` block. Change `--ca-accent`, everything updates.
- **Mobile-first** — Bootstrap's responsive grid utilities handle the collapse from two-column desktop to single-column mobile.

### Implementation Approach

1. `static/css/style.css` — extend with `--ca-*` custom properties in `:root`
2. Override `--bs-*` vars where possible to reference `--ca-*` values
3. Component-specific styles reference the token set exclusively (no hardcoded colors)
4. `static/hplc_simulator/css/simulator.css` follows the same token pattern
5. No Sass compilation — plain CSS over CDN Bootstrap

### Customization Strategy

- **Design tokens** (FR-22): `--ca-bg-page`, `--ca-bg-card`, `--ca-accent` (teal `#14b8a6`), `--ca-accent-hover`, `--ca-text-primary`, `--ca-text-muted`, `--ca-border`, `--ca-success`, `--ca-warning`, `--ca-danger`
- **Typography** (FR-23): Inter from Google Fonts with `font-display: swap`. System UI fallback stack.
- **Spacing** (FR-24): `--bs-spacer` multiples, unified `--ca-border-radius: 0.75rem` for cards.
- **Components**: Cards, accordions, alerts, breadcrumbs, buttons, range sliders, progress bars, tag inputs — all styled through the token system.

## 2. Core User Experience

### 2.1 Defining Experience

**"Enter parameters → Tap Calculate → See result instantly."** An established form-submission pattern — no novel interaction, no learning curve. Francisca knows exactly what to do on first visit.

### 2.2 User Mental Model

Francisca's current workflow is pen and paper: write formula → compute → write result. The app mirrors this: input parameters → tap → read result. The mental model is "a calculator that remembers." No new paradigm to learn.

### 2.3 Success Criteria

- Result appears in < 500ms from tap (no round-trip spinner for simple calculators)
- Result is in the same viewport as the Calculate button — no scrolling required
- Input parameters displayed alongside output for at-a-glance verification
- Unit displayed on every numeric value
- Copy button includes full context (value + unit + label)

### 2.4 Novel UX Patterns

None needed. The interaction uses established form-submission patterns throughout. Innovation is in the defaults-as-tutorial approach and session persistence — invisible enhancements to a familiar pattern.

### 2.5 Experience Mechanics

1. **Initiation:** Francisca lands on a tool page via nav or landing grid. Form is pre-filled with sensible defaults that produce a correct result.
2. **Interaction:** She adjusts parameters as needed. Calculate button is in the thumb zone at the bottom of the form card.
3. **Feedback:** Result card appears above the Calculate button in the same viewport. Shows output values with units, plus input parameters for verification. Button shows a loading state if there's a round-trip.
4. **Completion:** She copies the result (clipboard button in card) or adjusts parameters and recalculates. Session persists if she switches to another tool.

## Visual Design Foundation

### Color System

**Core palette** (GitHub-inspired charcoal dev-tool dark):

| Token | Value | Usage |
|---|---|---|
| `--ca-bg-page` | `#0d1117` | Page background |
| `--ca-bg-card` | `#161b22` | Card/surface background |
| `--ca-bg-card-alt` | `#21262d` | Alternate/table stripe background |
| `--ca-accent` | `#14b8a6` | Primary accent (teal) |
| `--ca-accent-hover` | `#2dd4bf` | Accent hover (lighter teal) |
| `--ca-text-primary` | `#e6edf3` | Primary body text |
| `--ca-text-muted` | `#8b949e` | Muted/secondary text |
| `--ca-border` | `#30363d` | Subtle borders (matching GitHub) |
| `--ca-success` | `#3fb950` | Pass/completion states |
| `--ca-warning` | `#d29922` | Warning states |
| `--ca-danger` | `#f85149` | Error/overpressure states |

**Semantic mapping:** Each token maps to a specific UI role. No hardcoded colors anywhere — every component references a `--ca-*` variable.

### Typography System

- **Primary font:** Inter (Google Fonts, `font-display: swap`)
- **Fallback stack:** `system-ui, -apple-system, sans-serif`
- **Body size:** `1.125rem` (18px) — bumped from Bootstrap's `1rem` for mobile readability
- **Monospace:** System default (`SF Mono, Fira Code, Consolas, monospace`) — no custom monospace font
- **Scale:** Bootstrap's default type scale preserved (h1–h6, lead, small), with `--bs-body-font-size` overridden to `1.125rem`

### Spacing & Layout Foundation

- **Base unit:** `--bs-spacer` (1rem = 16px default)
- **Card border radius:** `--ca-border-radius: 0.75rem`
- **Responsive grid:** Bootstrap's 12-column with `col-lg-6 col-12` pattern for two-column collapse
- **Content max-width:** `container-xl` (1320px) on desktop
- **Thumb-zone:** Primary actions anchored to bottom of viewport on mobile

### Accessibility Considerations

- **Contrast ratios:** GitHub's charcoal palette exceeds WCAG 2.1 AA for normal text (4.5:1+) with `--ca-text-primary` on `--ca-bg-page`
- **Focus indicators:** All interactive elements use accent-colored focus rings (matching `--ca-accent`)
- **Touch targets:** Minimum 44x44px for all interactive elements (Bootstrap's default button sizing meets this)
- **Skip link** retained from existing base template
- **ARIA attributes** retained from existing templates

## Design Direction Decision

### Design Directions Explored

A single comprehensive design direction was generated as an HTML mockup (`ux-design-directions.html`) showing three key views with all design tokens applied:

- **Landing page:** Hero + 5 tool cards with emoji icons, verb headers, and a teal accent border on the HPLC card
- **Calculator page:** Two-column form+result layout with tag input validation, thumb-zone Calculate button, and copy-to-clipboard in result card
- **HPLC Simulator:** Breadcrumbs, accordion parameter panel, chromatogram canvas, stats row (Score/Resolution/Run Time/Status), colored pressure gauge, and warning alerts

### Chosen Direction

**Dark dev-tool palette** (GitHub-inspired charcoal `#0d1117` + teal `#14b8a6` accent). Single unified direction with no alternative theme — the PRD and UX Discovery converged on one clear visual identity.

### Design Rationale

- GitHub's charcoal palette was validated for scannability, contrast, and dev-tool ethos
- Teal accent provides a science-adjacent color that's energetic but not clinical
- Verb headers and emoji icons reduce cognitive load for non-tech-savvy users
- Inline tool nav (desktop) replaces dropdown for one-tap access to all tools
- Layout patterns (card grid, two-column calculator, accordion HPLC) transfer directly from proven interfaces (GitHub, Sentry, BLAST, SWISS-MODEL)

### Implementation Approach

All static assets go through Django's `static/` system:
- `static/css/style.css` — all `--ca-*` custom properties and component styles
- `static/hplc_simulator/css/simulator.css` — HPLC-specific styles referencing same tokens
- No inline styles in production templates
- No Sass compilation — plain CSS over CDN Bootstrap

## User Journey Flows

### Journey 1: Francisca's First Mobile Calculation

**Entry:** Francisca opens ChemicAlly on her phone from a bookmarked tab or browser search. She sees the landing page with 5 tool cards.

```
flowchart TD
    A[Land on homepage] --> B{Recognizes a tool?}
    B -->|Yes - verb header matches need| C[Taps tool card]
    B -->|No - scrolls or reads| D[Reads example text under header]
    D --> C
    C --> E[Form loads with pre-filled defaults]
    E --> F{Happy with defaults?}
    F -->|Yes| G[Taps Calculate - thumb zone]
    F -->|No| H[Adjusts 1-2 parameters]
    H --> G
    G --> I[Result appears above button]
    I --> J{Result correct?}
    J -->|Yes| K[Copies result - clipboard button]
    J -->|No| L[Adjusts parameter, recalculates]
    L --> G
    K --> M[Closes tab or navigates to another tool]
```

**Key optimizations:**
- Result card appears in the same viewport as the button — no scrolling
- Input parameters displayed alongside output for instant verification
- Error messages in bench-speak ("check your formula" not "invalid syntax")
- Clipboard button copies value + unit + label

### Journey 2: Multi-Tool Session (MW → Dilution)

**Entry:** Francisca calculates molecular weights, then needs a dilution volume using those same substances.

```
flowchart TD
    A[Finds molecule mass - Journey 1] --> B[Copies result]
    B --> C[Taps nav to go to Dilution tool]
    C --> D[Dilution form loads with session state]
    D --> E{Previous substances present?}
    E -->|Yes - session restored| F[Adjusts concentration values]
    E -->|No - default form| G[Enters parameters from scratch]
    F --> H[Taps Calculate]
    G --> H
    H --> I[Result: injection volume]
    I --> J[Copies result]
    J --> K[Switches to another tool - state saved]
```

**Key optimizations:**
- Session remembers substance tags, entered values, and last result across tool switches
- Hybrid localStorage + Django session: instant UI restore from localStorage, async sync to server
- "Reconstruct session" option if localStorage is cleared (server fallback)

### Journey 3: First HPLC Simulator Run

**Entry:** A student finds the HPLC simulator via search and wants to try it.

```
flowchart TD
    A[Searches for HPLC simulator] --> B[Lands on HPLC level index]
    B --> C[Picks a level - difficulty badge visible]
    C --> D[Simulator loads - progressive disclosure]
    D --> E[Sees 3 primary controls: Column, Length, Flow rate]
    E --> F{Accepts defaults?}
    F -->|Yes| G[Taps Inject]
    F -->|No| H[Adjusts 1-2 sliders]
    H --> G
    G --> I[Chromatogram renders + basic metrics appear]
    I --> J{Sees advanced options?}
    J -->|Yes - accordion below plot| K[Opens Mobile Phase accordion]
    J -->|No - happy with result| L[Scores and submits]
    K --> M[Adjusts gradient/pH/temperature]
    M --> N[Taps Inject again]
    N --> I
    L --> O[Score submitted - progress saved]
```

**Key optimizations:**
- Only 3 controls visible on first load (Column type, Length, Flow rate)
- Remaining parameters in collapsible accordions below the chromatogram
- "Show Advanced" toggle for full parameter grid when ready
- Warning alerts (pH over 7.5, sub-2um particles) appear reactively
- Pressure gauge gradient green→yellow→red as pressure approaches limit

### Journey Patterns

**Navigation pattern:** All journeys share a flat navigation model — landing grid → tool page → result → nav back to landing. No deep nesting. Breadcrumbs provide orientation.

**Decision pattern:** Every tool presents defaults that produce a correct result on first load. Francisca always has a valid starting point. She tweaks only what she needs.

**Feedback pattern:** Results appear in the same viewport. Input parameters remain visible alongside output. Copy includes full context. Errors explain what to fix.

### Flow Optimization Principles

1. **Minimize steps to value** — Francisca should get her result in 2 taps (Open tool → Calculate). Defaults eliminate setup.
2. **Reduce cognitive load** — Each decision point has at most 1-2 options. The form does the thinking; she confirms.
3. **Clear progression** — Result card animation (300-400ms fade-in) signals completion. Loading states show a skeleton, not a spinner.
4. **Graceful recovery** — Error messages tell her what to change. Tag input shows inline validation before submission. Session restore never loses data.

## Component Strategy

### Component Taxonomy

Components are organized into three tiers:

- **Global** — Rendered on every page (nav, breadcrumbs, footer)
- **Tool** — Shared across all calculator pages (form card, result card, tag input)
- **HPLC** — Specific to the HPLC simulator (accordion panel, slider, chromatogram canvas, stats row)

### Tier 1: Global Components

#### Top Navigation Bar

| Property | Specification |
|---|---|
| Position | Fixed top, full width, z-index 1030 |
| Background | `--ca-bg-page` (`#0d1117`), bottom border `--ca-border` |
| Height | 56px mobile, 64px desktop |
| Mobile | Left: hamburger icon (inline SVG), Center: site name "ChemicAlly", Right: emoji |
| Desktop | Left: site name link, Right: inline tool links (no dropdown) |
| States | Active link: teal underline (4px). Hover: `--ca-accent-hover` text. Default: `--ca-text-muted` |
| Content | "ChemicAlly" home link. Desktop: Home | Molecular Weight | Dilution | Equilibrium | Reaction | HPLC |

#### Breadcrumbs

| Property | Specification |
|---|---|
| Position | Below nav, above page title, padding-top `1rem` |
| Background | None (transparent on page bg) |
| Separator | `/` (forward slash) in `--ca-text-muted` |
| Color | `--ca-text-muted` for ancestors, `--ca-text-primary` for current |
| Mobile | Full breadcrumbs visible (never truncated — max 3 levels) |
| Pattern | `Home / [Tool Name]` for calculators, `Home / HPLC / [Level Name]` for simulator |

#### Footer

| Property | Specification |
|---|---|
| Content | "ChemicAlly — free chemistry calculators" + copyright year |
| Position | Static bottom (not fixed), top border `--ca-border` |
| Background | `--ca-bg-page` |
| Text | `--ca-text-muted`, `0.875rem` |

### Tier 2: Tool Components (Shared Across Calculators)

#### Calculator Form Card

| Property | Specification |
|---|---|
| Background | `--ca-bg-card` (`#161b22`) |
| Border | `1px solid --ca-border`, `--ca-border-radius` |
| Padding | `1.25rem` (20px) inside, `1rem` gap between card edge and viewport |
| Title | Verb header (`<h1>`), `1.5rem`, `--ca-text-primary`, below breadcrumbs |
| Layout | Single column on mobile, two-column form+result on `md+` |
| Calculate button | Sticky bottom of card, full-width, thumb zone. Green accent bg, white text, `3rem` height, `--ca-border-radius` |
| Loading | Button shows pill spinner inline SVG, disabled state, preserves width |

#### Input Group (Label + Field + Unit)

| Property | Specification |
|---|---|
| Layout | Label above field. Field row: `[ input ] [ unit badge ]` |
| Label | `0.875rem`, `--ca-text-muted`, margin-bottom `0.25rem` |
| Input | `--ca-bg-card-alt` (`#21262d`), `1px solid --ca-border`, `--ca-text-primary`, `1rem` padding, full-width, `--ca-border-radius` |
| Unit badge | `--ca-bg-card-alt`, `--ca-text-muted`, `0.75rem` padding, right-aligned |
| Focus | `box-shadow: 0 0 0 2px --ca-accent`, border becomes `--ca-accent` |
| Error | Border becomes `--ca-danger`, error text below field in `0.8rem --ca-danger` |
| States | Default (filled bg), Focus (accent ring), Error (danger border + message), Disabled (50% opacity, no pointer) |

#### Tag Input (Substance Names)

| Property | Specification |
|---|---|
| Container | Similar to input group, bordered area that grows with tags |
| Tag pills | `--ca-accent` bg at 15% opacity, `--ca-accent` text, `0.75rem` height, `0.5rem` padding. Close (×) button |
| Input inline | No separate input field — type into the container, press Enter/semicolon to create tag |
| Empty state | Placeholder ghost text: "H2O, NaCl, ethanol..." |
| Validation | Tag creation on Enter. Red flash on duplicate. Pasted comma-separated list auto-splits |

#### Calculate Button

| Property | Specification |
|---|---|
| Text | "Calculate" or tool-specific verb ("Weigh", "Balance", "Dilute", "Inject") |
| Full-width | Yes, on mobile. Fits content on desktop with `min-width 200px` |
| Position | Bottom of form card, sticky on mobile |
| Height | `3rem` (48px) — exceeds 44px minimum |
| Background | `--ca-success` (`#3fb950`) — green signals "go" |
| Hover | 10% brightness increase |
| Active | 10% brightness decrease, transform `scale(0.98)` |
| Loading | Show inline SVG spinner, disable pointer, preserve text width with hidden text |

#### Result Card

| Property | Specification |
|---|---|
| Position | Appears above Calculate button in the same card (same viewport) |
| Background | `--ca-bg-card-alt` (`#21262d`) — slightly lighter to distinguish from form |
| Animation | Fade-in 300-400ms, subtle translateY(-4px) |
| Content | Output value (large, `1.5rem`, bold, `--ca-accent`) with unit. Below: input parameters as read-only key-value pairs in `--ca-text-muted` |
| Copy button | Icon-only (clipboard SVG), right-aligned in result header, hover: `--ca-accent` |
| Copied state | Button text flips to "Copied!" for 2 seconds, then reverts |
| Empty state | Not rendered (hidden until first calculation) |
| Layout | Padding `1rem`, top border `1px solid --ca-border`, margin-top `1rem` |

#### Session History Panel

| Property | Specification |
|---|---|
| Trigger | "History" link below result card, `--ca-text-muted` |
| Panel | Slides down (accordion-style) below current result |
| List | Reverse-chronological entries: inputs + output + timestamp. Each entry has a "Use" button to restore those inputs |
| Max items | 20 entries per tool, oldest dropped |
| Empty state | "No previous calculations" in `--ca-text-muted` |
| Data source | localStorage (immediate) + Django session (server authoritative) |

### Tier 3: HPLC Simulator Components

#### Accordion Parameter Panel

| Property | Specification |
|---|---|
| Groups | "Column", "Mobile Phase", "Sample", "System", "Advanced" |
| Default state | Only "Column" group expanded (shows Length, Inner diameter, Particle size, Flow rate) |
| Toggle | Header row with chevron icon (inline SVG, rotates 180° on expand) |
| Header | `--ca-bg-card`, `1rem` padding, `--ca-text-primary`, border `--ca-border` |
| Body | `--ca-bg-card-alt`, `1rem` padding, collapsible with CSS `max-height` transition (`0 → auto`, 300ms) |

#### Slider Input

| Property | Specification |
|---|---|
| Track | `--ca-border`, height 6px, border-radius 3px |
| Thumb | `--ca-accent`, 20px diameter, no default browser outline |
| Filled track | CSS `linear-gradient` from `--ca-accent` to `--ca-accent` for filled portion, `--ca-border` for empty |
| Label above | Current value displayed to the right of the label, `--ca-accent` |
| Min/Max labels | `0.75rem`, `--ca-text-muted`, below track ends |
| Interaction | Active: thumb grows to 24px. Focus: accent ring |

#### Chromatogram Canvas Container

| Property | Specification |
|---|---|
| Background | `--ca-bg-page` (matches page bg — canvas area looks continuous) |
| Border | `1px solid --ca-border`, `--ca-border-radius` |
| Aspect ratio | 16:9 on mobile, 21:9 on desktop. Height constrained to `min(400px, 50vh)` |
| Empty state | Greyed placeholder text "Run a simulation to view chromatogram" centered in `--ca-text-muted` |
| Loading | Skeleton pulse: grey rectangle same dimensions as rendered canvas |
| Plotly | Loaded only on this page (conditional `<script>` block in template). Plotly config: `{displayModeBar: true, modeBarButtons: [['toImage'], ['zoom2d','pan2d','resetScale2d']]}` |

#### Stats Row (Metric Cards)

| Property | Specification |
|---|---|
| Layout | 2x2 grid on mobile (4 cards), single row of 4 on `md+` |
| Card | No border, `--ca-bg-card-alt`, `--ca-border-radius`, padding `0.75rem` |
| Label | `0.75rem`, `--ca-text-muted`, uppercase letter-spacing `0.05em` |
| Value | `1.25rem`, `--ca-text-primary`, bold |
| Unit | `0.75rem`, `--ca-text-muted`, inline after value |
| Status card | "Status" card shows badge: green (pass), yellow (warning), red (fail) with matching `--ca-*` color |

#### Pressure Gauge

| Property | Specification |
|---|---|
| Layout | Horizontal bar, full width, height 8px |
| Background | `--ca-bg-card-alt` |
| Fill | Gradient from `--ca-success` → `--ca-warning` → `--ca-danger` (left to right) |
| Needle | Teal vertical line, 12px tall, positioned at current pressure percentage |
| Labels | "0 bar" (left) and "limit" (right) in `0.75rem --ca-text-muted` |
| Above | Label: "System Pressure: [value] bar" in `--ca-text-primary` |

#### Warning Alert

| Property | Specification |
|---|---|
| Background | `--ca-bg-card` with left border `4px solid --ca-warning` (yellow) |
| Icon | Warning triangle (inline SVG), left-aligned |
| Text | Bench-speak message: "pH over 7.5 may degrade silica columns" |
| Dismiss | × close button if dismissible, else persistent for active warnings |
| Danger variant | Left border `--ca-danger` for critical warnings (e.g., pressure over max) |

#### Level Progress & Score Display

| Property | Specification |
|---|---|
| Layout | Small card in HPLC header row |
| Progress bar | Height 6px, fill `--ca-accent`, background `--ca-bg-card-alt` |
| Score | Badge `--ca-accent` bg, white text, pill shape. Fraction display: "850 / 1000" |
| Stars | 0–5 star rating (inline SVG stars, filled `--ca-warning` empty `--ca-text-muted`), displayed after score for completed levels |

### State Coverage Matrix

| Component | Default | Hover | Focus | Active | Loading | Error | Disabled | Empty |
|---|---|---|---|---|---|---|---|---|
| Nav link | muted text | accent hover | accent ring | underline | — | — | — | — |
| Breadcrumb | muted/primary | — | — | — | — | — | — | — |
| Text input | filled bg | — | accent ring | — | — | danger border | 50% opacity | placeholder text |
| Tag pill | accent bg 15% | bg 30% | — | — | — | — | — | "H₂O, NaCl..." |
| Calculate btn | green bg | lighter | — | scale(0.98) | spinner | — | 50% opacity | — |
| Result card | visible | — | — | — | skeleton | hidden | — | hidden |
| Clipboard btn | muted | accent | ring | flash | — | — | — | — |
| Slider | track+thumb | thumb grows | ring | — | — | — | 50% opacity | — |
| Accordion header | default | accent bg 15% | ring | — | — | — | — | — |
| Metric card | visible | — | — | — | skeleton | — | — | "—" value |
| Stats row | visible | — | — | — | skeleton | — | — | "—" value |
| Warning alert | visible | dismiss hover | — | — | — | — | — | not rendered |
| Chromatogram | canvas | — | — | — | skeleton pulse | — | — | placeholder text |

### Responsive Behavior

| Component | Mobile (<768px) | Desktop (≥768px) |
|---|---|---|
| Nav | Hamburger menu, centered title | Inline tool links |
| Breadcrumbs | Full (never truncated) | Full |
| Calculator layout | Single column (form, result below form) | Two column: form left (col-lg-6), result right |
| Result position | Inside form card, above button | Side-by-side with form |
| Calculate button | Full width, sticky bottom card | `min-width 200px`, right-aligned |
| Chromatogram | 16:9 aspect ratio, auto width | 21:9, max 50vh height |
| Stats row | 2x2 grid | Single row of 4 |
| Tool card grid | Single column | 3 columns |
| History panel | Full width slide-down | Full width slide-down |
| Tag input | Full width | `max-width 500px` |

## Screen-by-Screen Specification

### Screen 1: Landing Page

**URL:** `/`
**Purpose:** Introduce the platform and provide one-tap access to all tools.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | "ChemicAlly" home link. Desktop: inline tool links |
| Hero section | Text block | `1rem` vertical padding. `<h1>`: "Chemistry calculators for the bench". `<p>` lead: "Molecular weight, dilutions, reaction balancing — all in one place." Both `--ca-text-primary` |
| Tool grid | CSS Grid | Row of tool cards. 1 col mobile, 2 col tablet `md`, 3 col `lg+` |
| Tool card (MW) | Card | Emoji: ⚖️. Verb header: "Find a molecule's mass." Subtitle: "Molecular weight from formula." Badge: none. Teal accent top border |
| Tool card (Dilution) | Card | Emoji: 💧. Verb header: "How much stock solution?" Subtitle: "Dilution calculator C1V1 = C2V2." Badge: none |
| Tool card (Equilibrium) | Card | Emoji: ⚗️. Verb header: "Will this reaction shift?" Subtitle: "Equilibrium constant + ICE table." Badge: "beta" |
| Tool card (Reaction) | Card | Emoji: ↔️. Verb header: "Balance this equation." Subtitle: "Reaction balancer with ChemPy." Badge: none |
| Tool card (HPLC) | Card | Emoji: 📊. Verb header: "Simulate a chromatogram." Subtitle: "HPLC column parameters and gradient." Badge: "new" (small `--ca-success` pill top-right) |
| Footer | Global Footer | "ChemicAlly — free chemistry calculators © 2026" |

**States:**
- **Default (pre-loaded):** Grid renders immediately (server-rendered, no JS needed). Cards have no hover until CSS loads.
- **Loaded (fonts + CSS applied):** Inter font renders. Hover states active: card border glows `--ca-accent`, slight elevation (`box-shadow: 0 4px 12px rgba(0,0,0,0.3)`).
- **Mobile:** Single column. Card takes full width minus `1rem` padding on each side.

**Edge cases:**
- No tools available (should never happen — tools are hardcoded): show "More tools coming soon" fallback
- Very long browser history: landing is root, short `/` URL is always fast

### Screen 2: Molecular Weight Calculator

**URL:** `/molecular-weight/`
**Purpose:** Calculate molecular weight and percent composition from a chemical formula.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | Active tool: no visual indicator (breadcrumbs handle it) |
| Breadcrumbs | Global Breadcrumbs | `Home / Molecular Weight` |
| Page title | `<h1>` | "Find a molecule's mass" — verb header, `--ca-text-primary` |
| Form card | Calculator Form Card | Contains formula input + unit selector |
| Formula input | Input Group | Label: "Chemical formula". Placeholder: `H₂O, NaCl, C₆H₁₂O₆...`. Tag input that accepts plain text formula. Validation on submit |
| Unit selector | Dropdown or radio | Label: "Units". Options: g/mol (default), kg/mol, Da. Default selected |
| Calculate button | Calculate Button | Text: "Weigh". Green full-width |
| Result card | Result Card | Shows: molecular weight (large, `--ca-accent`) + unit. Below: elemental breakdown table (element, count, mass, %) |
| History | Session History Panel | "Previous weights" link, accordion list of past formulas + results |
| Footer | Global Footer | Standard |

**States:**
- **Default page load:** Form pre-filled with `H₂O` (or other sensible default). Calculate button produces result immediately.
- **Typing:** User types formula. No validation until submit or Enter key.
- **Validation error:** Invalid formula → error message below input: "We couldn't read that formula. Try H₂O or NaCl." Input border goes `--ca-danger`.
- **Loading:** Button shows spinner while server computes (unlikely >200ms, but present).
- **Result:** Card fades in above button. Elemental breakdown table has alternating row colors (`--ca-bg-card` / `--ca-bg-card-alt`).

**Edge cases:**
- Extremely long formula (>50 chars): truncate display in tag input, full formula visible on hover
- Formula with no elements (empty or whitespace): immediate inline validation "Enter a formula"
- Very large molecule (e.g., protein): MW in kDa displayed with 2 decimal places. Table may overflow — scrollable `<tbody>` with `max-height: 200px`
- Isotope not specified: use average atomic mass (standard ChemPy behavior)

### Screen 3: Dilution Calculator

**URL:** `/dilution/`
**Purpose:** Calculate volumes and concentrations for C1V1 = C2V2 dilutions.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | Standard |
| Breadcrumbs | Global Breadcrumbs | `Home / Dilution` |
| Page title | `<h1>` | "How much stock solution?" |
| Form card | Calculator Form Card | Contains substance tags + 4 parameter inputs |
| Substance input | Tag Input | Label: "Substance". Tags: H₂O, NaCl, ethanol... (pre-filled with H₂O) |
| Param inputs | 4× Input Group | "Stock conc." (C1), "Target conc." (C2), "Target volume" (V2), "Stock volume" (V1). Each with unit badge (`M`, `M`, `mL`, `mL`). C1, C2, V2 active by default. V1 auto-computed |
| Unknown field | Radio group | "What do you want to find?" — C1 / C2 / V1 / V2. Default: V1 (volume of stock needed) |
| Calculate button | Calculate Button | Text: "Dilute". Green full-width |
| Result card | Result Card | Shows computed value with unit. Below: input summary (C1, C2, V1, V2 all displayed) |
| Error conditions | Error Alert | "Stock concentration must be greater than target concentration" (if C1 < C2). |

**States:**
- **Default:** Pre-filled with H₂O, C1=1.0 M, C2=0.1 M, V2=100 mL, V1 is to-find. Result: "Take 10 mL of stock, dilute to 100 mL" (bench-speak output)
- **Changed unknown:** Radio changes which field is auto-computed. Shaded/unshaded indicator on computed field (`--ca-bg-card-alt` bg for computed, `--ca-bg-card` for user-input)

**Edge cases:**
- Same C1 and C2: result V1 = V2. Show note: "Stock and target are the same concentration — no dilution needed"
- Zero or negative values: inline validation "Enter a positive number"
- Volume exceeding practical limits (>10 L): show with appropriate unit (L vs mL)
- Substance tag not needed for calculation (just display), but required for session tracking

### Screen 4: Equilibrium Calculator

**URL:** `/equilibrium/`
**Purpose:** Calculate equilibrium concentrations (ICE table) given Keq and initial conditions.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | Standard |
| Breadcrumbs | Global Breadcrumbs | `Home / Equilibrium` |
| Page title | `<h1>` | "Will this reaction shift?" |
| Form card | Calculator Form Card | |
| Reaction input | Text area | Label: "Balanced reaction". Placeholder: `2A + B ⇌ C`. ChemPy-parsed |
| Keq input | Input Group | Label: "Equilibrium constant (K)." Unit: none. Default: 1.0 |
| Initial concentrations | Dynamic rows | Button: "Add species" (opens row with species name + initial concentration). Pre-filled with A = 1.0 M, B = 1.0 M |
| Calculate button | Calculate Button | Text: "Calculate". Green full-width |
| Result card | Result Card | ICE table (3 columns: Initial, Change, Equilibrium). Below: final concentrations with units. |

**States:**
- **Default:** Pre-filled with `2A + B ⇌ C`, K=1.0, [A]=1.0M, [B]=1.0M
- **Adding species:** New row appears with animation. Row has text input (species) + number input (conc) + minus button
- **Removing species:** Row slides out. At least 2 species must remain

**Edge cases:**
- Reaction with no products: error "Add at least one product to the reaction"
- K < 0: impossible, error "Equilibrium constant must be positive"
- Very large or small K (< 1e-10 or > 1e10): use scientific notation. Note: "This reaction strongly favors [products/reactants]"
- Reaction not balanced: ChemPy auto-balances or shows error "Could not parse this reaction"

### Screen 5: Reaction Balancer

**URL:** `/reaction/`
**Purpose:** Balance chemical equations.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | Standard |
| Breadcrumbs | Global Breadcrumbs | `Home / Reaction` |
| Page title | `<h1>` | "Balance this equation" |
| Form card | Calculator Form Card | |
| Equation input | Text area | Label: "Unbalanced equation". Placeholder: `H₂ + O₂ → H₂O`. Large, monospace font |
| Calculate button | Calculate Button | Text: "Balance". Green full-width |
| Result card | Result Card | Shows balanced equation in large type. Below: atom-by-atom verification table (element, left count, right count, status ✓) |
| Error | Error Alert | "Couldn't balance — check your equation has valid formulas" |

**States:**
- **Default:** Pre-filled with `H₂ + O₂ → H₂O`. Result shows `2H₂ + O₂ → 2H₂O`
- **Loading:** Button spinner (ChemPy balancing is near-instant)

**Edge cases:**
- Already balanced equation: result shows equation unchanged with "This equation is already balanced" note
- Equation with polyatomic ions: treat as individual elements (standard ChemPy)
- Redox without explicit electron balancing: ChemPy handles; note if needed: "Redox balance requires half-reaction input"
- Very long equation: result wraps, monospace font with `word-break: break-all`

### Screen 6: HPLC Level Index

**URL:** `/hplc/`
**Purpose:** Browse and select HPLC simulator levels.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | Standard |
| Breadcrumbs | Global Breadcrumbs | `Home / HPLC Simulator` |
| Page title | `<h1>` | "HPLC Simulator" |
| Subtitle | `<p>` lead | "Learn chromatography by tuning parameters and seeing results." |
| Level cards | Card grid | Same layout as tool grid. Each card: level number, title, difficulty badge (Beginner/Intermediate/Advanced) with matching color, star rating (0–5), progress bar |
| Beginner badge | Badge | `--ca-success` bg | 
| Intermediate badge | Badge | `--ca-warning` bg |
| Advanced badge | Badge | `--ca-danger` bg |
| Locked level | Card | 50% opacity, lock emoji 🔒 overlay. "Complete previous level to unlock" tooltip |
| Footer | Global Footer | Standard |

**States:**
- **Logged-out user:** Only first level unlocked (or all, configurable). Star ratings show 0/5
- **Logged-in user:** Progress bars show completion %. Stars filled for completed levels
- **Empty (no levels):** Should not occur (seeded data). Fallback: "No levels available yet"

### Screen 7: HPLC Simulator (Simulation Page)

**URL:** `/hplc/<level_id>/`
**Purpose:** Run chromatography simulations with parameter adjustment.

| Element | Type | Content / Specification |
|---|---|---|
| Nav | Global Top Nav | Standard |
| Breadcrumbs | Global Breadcrumbs | `Home / HPLC / [Level Title]` |
| Page title | `<h1>` | Level title + difficulty badge |
| Level progress | Level Progress Card | Stars + score + progress bar |
| Parameter panel | Accordion Parameter Panel | Groups: Column, Mobile Phase, Sample, System, Advanced. Default: Column open, rest closed |
| Column group | Accordion body | Column type (select), Length (slider, 50–250mm), ID (slider, 2.1–4.6mm), Particle size (slider, 1.7–5µm), Flow rate (slider, 0.1–2.0 mL/min) |
| Mobile Phase group | Accordion body | A/B composition (sliders), Gradient time (slider), pH (slider, 1–12), Temperature (slider, 10–60°C) |
| Sample group | Accordion body | Injection volume (slider, 1–50 µL), Sample concentration (slider) |
| System group | Accordion body | System volume (slider), Detector wavelength (slider) |
| Advanced group | Accordion body | Van Deemter A/B/C terms, Tailing factor |
| Inject button | Calculate Button | Text: "Inject". Teal `--ca-accent` (not green — differentiates from calculators) |
| Chromatogram | Chromatogram Canvas Container | Plotly chart: x = time (min), y = absorbance (mAU). Peaks labeled with retention times |
| Stats row | Stats Row | Score (0–1000), Resolution, Run Time (min), Status (pass/warning/fail badge) |
| Pressure gauge | Pressure Gauge | Below stats. Shows current system pressure |
| Warning alerts | Warning Alert | Shown for: pH > 7.5 ("pH over 7.5 may degrade silica columns"), Pressure > 400 bar ("System pressure approaching column limit"), Particle size < 2µm with high flow rate |
| Submit score | Button | "Submit Score" — visible only if improved. `--ca-accent` bg. Triggers score save |

**States:**
- **Default (first load):** 3 sliders visible (Column type, Length, Flow rate). Pre-filled defaults produce a valid chromatogram. Inject button enabled.
- **After inject:** Spinner overlay on chromatogram area (300ms minimum for perception). Plotly renders. Stats row populates. Pressure gauge updates.
- **Advanced toggled:** "Show Advanced" link at bottom of parameter panel. Tap reveals full accordion with all groups.
- **Warning triggered:** Alert slides down from below stats row. Dismissible for advisory warnings. Persistent for critical.
- **Score submission:** "Submit Score" appears after inject if current score > saved score. Tap submits via DRF API. Progress bar updates.
- **Loading (Plotly):** Skeleton pulse rectangle replaces canvas. Stats show dashes.

**Edge cases:**
- No chromatogram (zero runs): placeholder text "Run a simulation to view chromatogram"
- All peaks co-elute (resolution < 0.5): status "Poor" in `--ca-danger`. Alert: "Peaks are not resolved — try adjusting gradient or column length"
- Pressure exceeds max: gauge fills to 100% red. Alert: "Pressure exceeds column limit — reduce flow rate"
- Overlap with previous score: "Submit Score" only appears if score > previous best
- Non-numeric slider value entered: clamp to min/max range silently
- pH slider past 10: strong warning: "pH above 10 may cause permanent column damage"
- Temperature > 60°C: warning: "High temperature may degrade thermally labile compounds"
- Gradient time = 0 (isocratic): note: "Isocratic run — no gradient ramp"

## Micro-interactions & Animation

### Design Philosophy

Animations serve a functional purpose only: communicate state changes, reinforce hierarchy, and provide feedback. Duration is kept short (150–400ms) to maintain the speed ethos. No decorative animations, no parallax, no scroll-triggered reveals.

### Animation Tokens

| Token | Value | Usage |
|---|---|---|
| `--ca-anim-fast` | 150ms | Hover states, focus rings, button active press |
| `--ca-anim-normal` | 300ms | Fade-in, slide-down, skeleton pulse |
| `--ca-anim-slow` | 400ms | Result card entrance, accordion expand |
| `--ca-easing` | `cubic-bezier(0.4, 0, 0.2, 1)` | Material-style deceleration, used everywhere |

### Specific Micro-interactions

#### Button Press (Calculate, Inject)
```
Default → Hover (150ms, bg brighten 10%) → Active (150ms, scale 0.98, bg darken 10%) → Release → Result
```
Button text never shifts during loading — hidden duplicate text preserves width, visible text fades out (100ms) as spinner fades in (150ms).

#### Result Card Entrance
```
Trigger: form submission completes
Animation: opacity 0 → 1 (300ms) + translateY(8px) → 0 (400ms)
Timing: starts immediately on response, completes in 400ms
```
Uses `@keyframes fadeInUp` with `animation-fill-mode: both`. Card appears above the button in the same viewport.

#### Accordion Toggle (HPLC Parameter Groups)
```
Trigger: tap header
Header chevron: rotate(0deg) → rotate(180deg) (300ms, --ca-easing)
Body: max-height 0 → max-height [content height] (300ms, --ca-easing)
```
CSS `max-height` transition (not `height` — avoids layout thrash). Content clips with `overflow: hidden`.

#### Skeleton Loading (Chromatogram)
```
Animation: pulse opacity 0.4 → 0.8 → 0.4 (1.5s, infinite, ease-in-out)
Background: linear-gradient(90deg, --ca-bg-card-alt, --ca-border, --ca-bg-card-alt) 
Background-size: 200% 100%
Keyframes: shimmer { 0% { background-position: 200% 0 } 100% { background-position: -200% 0 } }
```
Skeleton matches the aspect ratio of the final canvas (16:9 mobile, 21:9 desktop). Replaced by Plotly canvas once rendered.

#### Copy to Clipboard
```
Trigger: tap clipboard icon
Feedback: icon swaps to checkmark ✓ (200ms), text "Copied!" appears next to icon (2s), reverts
Color: icon transitions from --ca-text-muted to --ca-accent (200ms)
```
Uses `navigator.clipboard.writeText()` with `aria-live="polite"` announcement. Copy content format: `{value} {unit} — {label}`.

#### Slider Thumb
```
Default → Hover: thumb scales 1→1.2 (150ms)
Focus: accent ring appears (150ms)
Active: thumb scales to 1.2 (instant)
Value update: label updates on input event (debounced 50ms)
```
Track fill uses `input[type=range]` with `background-size` trick (webkit pseudo + linear-gradient for filled segment). Filled segment is `--ca-accent`, empty is `--ca-border`.

#### Warning Alert Entry
```
Trigger: condition met (e.g., pH > 7.5)
Animation: slideDown — max-height 0 → [content height] + opacity 0 → 1 (300ms)
Dismiss: slideUp — reverse (200ms)
```
Left border transitions color with the alert type (warning `--ca-warning`, danger `--ca-danger`). No flash on page load if no warnings.

#### Tag Input
```
Trigger: Enter key or semicolon
Tag creation: pill slides in from right, opacity 0→1 + translateX(10px)→0 (200ms)
Duplicate: red flash 200ms, tag not created
Tag removal: × button hover → bg darkens (100ms). On click: pill scales 1→0 + opacity 1→0 (150ms), then DOM removal
```

#### Nav Hover / Active
```
Desktop link hover: text-color transition --ca-text-muted → --ca-accent-hover (150ms)
Underline indicator: width 0 → 100% (200ms) on hover. Active page: permanent underline
Mobile hamburger: three-line icon animates to × on menu open (200ms, middle line fades, top/bottom rotate)
```

#### Breadcrumb
```
No hover animation (static text). Chevron separator is `--ca-text-muted` — no state changes.
Current page text: `--ca-text-primary` with `font-weight: 600` (no animation).
```

#### Stats Row Population (HPLC)
```
Trigger: simulation complete
Each stat card: opacity 0→1 staggered (50ms delay between cards, 200ms each)
Value: counter animation 0 → [value] (300ms) using requestAnimationFrame for integer values
Fractional values: snap to final value (no counting animation)
```

#### Score Submission
```
Trigger: "Submit Score" tap
Button: text changes to "Submitting..." → "Submitted!" → reverts after 3s (success)
Star rating: if improved, stars fill sequentially left-to-right (200ms per star)
Progress bar: animates width from old% → new% (500ms, --ca-easing)
Error: "Could not save score. Check connection." alert (danger variant, auto-dismiss 5s)
```

### Performance Constraints

- All animations use only `transform` and `opacity` properties (GPU-composited) — never `top`, `left`, `width`, `height` for continuous animations. Exception: `max-height` transitions for accordions (single toggle, not continuous).
- Use `will-change: transform, opacity` on animated elements sparingly (only on result card, chromatogram container).
- No `@keyframes` animations that trigger layout (no animating `margin`, `padding`, `width` except max-height accordion).
- Plotly renders the chromatogram — no CSS animation overlap during render.
- Disabled `prefers-reduced-motion`: respect OS setting, reduce all animations to instant (0ms duration, no keyframes).

## Implementation Notes

### File Structure

```
static/
  css/
    style.css                    # All --ca-* tokens, global styles, component classes
    print.css                    # Print-specific overrides
  hplc_simulator/
    css/
      simulator.css              # HPLC-only component styles (accordion, gauge, canvas)
    js/
      chromatogram.js            # Plotly config, render, resize handler
      simulator.js               # Accordion behavior, slider linking, warning logic
  js/
    calculators.js               # Shared: result display, copy-to-clipboard, history
    tag-input.js                 # Tag input component (shared by MW + Dilution)
templates/
  webapp/
    base.html                    # Base template: nav, breadcrumbs, footer, head
    landing.html                 # Landing page with tool card grid
    molecular_weight.html        # MW calculator
    dilution.html                # Dilution calculator
    equilibrium.html             # Equilibrium calculator
    reaction.html                # Reaction balancer
    tool_base.html               # Shared calculator layout (extends base.html)
  hplc_simulator/
    level_index.html             # Level selection grid
    simulation.html              # Simulation screen
```

### CSS Architecture

**`static/css/style.css` structure:**
```
1. @font-face (Inter, font-display: swap)
2. :root — all --ca-* design tokens
3. Override --bs-* vars (body bg, text color, border radius, link color)
4. Body + base typography
5. Navigation (nav, hamburger, desktop inline links)
6. Breadcrumbs
7. Card system (tool cards, form cards, result cards)
8. Form inputs (text, select, textarea, tag input, slider)
9. Buttons (calculate, copy, inject, submit)
10. Result cards + animation
11. Footer
```

**`static/hplc_simulator/css/simulator.css` structure:**
```
1. Accordion panel (header, body, chevron, transitions)
2. Slider (track, thumb, filled segment, labels)
3. Stats row + metric cards
4. Pressure gauge (bar, fill, needle, labels)
5. Warning alerts (info, warning, danger variants)
6. Chromatogram container + skeleton
7. Score display + progress bar
```

No `@import` statements — all CSS loaded via `<link>` in the template.

### JavaScript Architecture

**`static/js/calculators.js` (loaded on all calculator pages):**
```
- resultCard.show(data) — populates + animates result card
- clipboard.copy(text, element) — copy to clipboard with feedback
- history.add(entry) / history.get() — localStorage session history
- skeleton.show(container) / .hide() — skeleton loading overlay
```

**`static/js/tag-input.js` (loaded on MW + Dilution):**
```
- TagInput(el, options) — class. Handles Enter/semicolon creation, backspace delete, paste splitting
- .getTags() → string[] — returns current tags
- .setTags(tags) — replaces all tags
- Events: 'tagadded', 'tagremoved'
```

**`static/hplc_simulator/js/chromatogram.js`:**
```
- chromatogram.render(container, data) — Plotly.newPlot with config
- chromatogram.resize() — Plotly.Plots.resize on container resize
- Config: displayModeBar: true, responsive: true
- Layout: dark theme (paper_bgcolor: '#0d1117', plot_bgcolor: '#0d1117', font: {color: '#e6edf3'})
```

**`static/hplc_simulator/js/simulator.js`:**
```
- Accordion.toggle(group) — CSS class toggle for expand/collapse
- pressureGauge.update(container, pressure, max) — gauge fill + needle position
- warnings.check(parameters) — evaluate conditions, show/hide alerts
- score.submit(score, levelId) — POST to DRF API, handle response
- slider.link(input, display) — bind range input to value display
```

### Performance Budgets

| Metric | Target | Enforcement |
|---|---|---|
| First Contentful Paint | < 1.5s | Server-rendered HTML, async MathJax, no blocking JS |
| Largest Contentful Paint | < 2.5s | No hero images, small CSS payload (~15KB gzipped) |
| Time to Interactive | < 2.0s | Minimal JS, no framework, deferred third-party |
| First Input Delay | < 50ms | No long tasks, event handlers attached on DOMContentLoaded |
| Total JS footprint | < 50KB gzipped | Calculators ~5KB, HPLC simulator ~15KB, Plotly loaded separately |
| Total CSS footprint | < 20KB gzipped | style.css ~8KB, simulator.css ~5KB, Bootstrap CDN ~20KB (shared) |
| Image payload | 0 bytes | No images (emoji + inline SVG only) |
| MathJax load | Deferred, fragment-only | Typeset on visible content, not full page |

### Accessibility Checklist

- Skip link (first focusable element, visible on focus) — retained from existing `base.html`
- All form inputs have `<label>` elements (not placeholders as labels)
- Error messages use `aria-describedby` linking input to error text
- Copy-to-clipboard uses `aria-live="polite"` for "Copied!" announcement
- Plotly chart has `aria-label="Chromatogram showing [N] peaks with retention times"`
- Accordion headers use `button[aria-expanded]` with `aria-controls`
- Slider uses native `<input type="range">` with `aria-valuemin`, `aria-valuemax`, `aria-valuenow`
- Color not used as sole indicator: status cards have icon + text, not just color
- Focus order follows visual order (nav → breadcrumbs → page title → form → result → footer)
- Touch targets minimum 44×44px
- `prefers-reduced-motion` respected (all animations → instant)
- Keyboard navigation: Tab through form, Enter to submit, Escape to dismiss alerts

### Print Styles (`static/css/print.css`)

- Hide nav, breadcrumbs, footer, calculate buttons, copy buttons
- Show result card with full input-output details
- White background, black text (remove dark theme for ink saving)
- Show URL and date in print footer
- Chromatogram: large as possible (full width, no scroll)

### Implementation Priority

**Phase 1 (Core UX — 1-2 sessions):**
1. `--ca-*` CSS tokens in `style.css`
2. Override Bootstrap defaults (`--bs-*` → `--ca-*`)
3. Updated nav (inline tool links desktop, hamburger mobile)
4. Breadcrumbs on all pages
5. Calculator form card + result card + Calculate button restyling
6. Tag input component

**Phase 2 (HPLC Simulator — 1-2 sessions):**
7. Accordion parameter panel with slider components
8. Chromatogram container + Plotly integration with dark theme
9. Stats row + metric cards
10. Pressure gauge
11. Warning alert system

**Phase 3 (Session & Polish — 1 session):**
12. Session history panel (localStorage + Django sync)
13. Copy-to-clipboard on all result cards
14. Print styles
15. Skeleton loading states
16. Error state designs (all calculator error messages)

### Testing Considerations

- **Visual regression:** Take screenshots before/after each phase. Compare on mobile (375px) and desktop (1440px).
- **Contrast check:** Verify all `--ca-*` combinations meet WCAG AA (4.5:1 normal text, 3:1 large text).
- **Touch test:** Verify all interactive elements meet 44×44px minimum on actual mobile device.
- **Keyboard nav:** Tab through every interactive element on every screen. Verify focus order and visible focus indicators.
- **Screen reader:** Test HPLC accordion and result cards with TalkBack (Android) and VoiceOver (iOS).
- **Performance:** Lighthouse mobile audit target: Performance ≥ 90, Accessibility ≥ 95, Best Practices ≥ 95.
- **Plotly edge cases:** Zero peaks (empty chromatogram), single peak, >10 peaks (overlapping labels).
