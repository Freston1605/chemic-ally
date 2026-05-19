---
title: ChemicAlly UX Redesign
status: final
created: 2026-05-19
updated: 2026-05-19
---

# PRD: ChemicAlly UX Redesign

## 0. Document Purpose

This PRD defines the UX redesign of ChemicAlly — a Django web application providing chemistry calculators (molecular weight, reaction balancing, dilution, equilibria) and an interactive HPLC chromatography simulator. The audience is the solo developer (Franco). The PRD uses Glossary-anchored vocabulary, features grouped with FRs nested, and inline `[ASSUMPTION]` tags where decisions were inferred.

## 1. Vision

ChemicAlly is a chemistry workbench — dark, inviting, and deliberately simple. Users land on a page that feels like a well-organized lab bench: tools are visible at a glance, each one is a card you can reach for, and the experience of using them is fluid and focused.

The current Bootstrap default dark theme is functional but generic — cards sit on a plain background, forms feel cramped, and the HPLC simulator (the most visually rich tool) deserves a stage that matches its interactivity.

The redesign transforms the generic Bootstrap look into a cohesive, dev-tool-inspired dark environment. Every tool gets the same level of visual attention. The page hierarchy is flat: land → pick a tool → work → back. No clutter, no unnecessary chrome, no multi-column layouts that fight the content.

Chemistry is inherently visual — structural formulas, chromatograms, reaction equations. The UI should fade into the background and let the science speak.

## 2. Target User

### 2.1 Primary Persona

Franco — the solo developer and primary user. A chemistry-adjacent developer who built the tools for his own use. He knows what each calculator does, he wants them to look as good as they work, and he's willing to invest in the aesthetic because he uses this site regularly.

**Secondary persona:** Chemistry students and lab professionals who find the site through search or GitHub. They come for a specific calculator (typically HPLC simulator or molecular weight) and stay if the experience is pleasant enough to return.

### 2.2 Jobs To Be Done

- **JTBD-1:** Calculate a molecular weight quickly without opening a desktop app.
- **JTBD-2:** Balance a chemical reaction and get a clean LaTeX-rendered equation.
- **JTBD-3:** Find the right dilution volume or concentration with unit-safe arithmetic.
- **JTBD-4:** Solve an equilibria system and understand the pH result.
- **JTBD-5:** Experiment with HPLC parameters and see the chromatogram update — like a real instrument's software, but free and in-browser.
- **JTBD-6:** Navigate between tools without losing state or orientation.

### 2.3 Key User Journeys

**UJ-1. Franco balances a reaction before a lab meeting.**
Franco opens ChemicAlly on his phone during transit. The landing page shows five tool cards in a clean grid. He taps "Chemical Reaction Balancer." The form loads instantly — a single textarea for reactants and products. He types the equation, hits Calculate, and the balanced reaction renders as LaTeX below. He copies the LaTeX to his clipboard and closes the tab. Total time: 45 seconds.

**UJ-2. A student explores HPLC parameters interactively.**
A chemistry student finds ChemicAlly via search. They land on the HPLC simulator index page, pick a level, and enter the simulator. The left panel has accordion parameter sections (Mobile Phase, Column, Operation, Advanced); the right panel shows a large chromatogram canvas. They adjust pH, flow rate, and gradient ramp, clicking Inject after each change. The chromatogram rerenders with new peak positions and widths. The score and resolution metrics update. They submit their best score. **Edge case:** if they over-pressurize the column, a clear warning appears with suggested fixes.

**UJ-3. Franco checks a molecular weight between experiments.**
Franco has ChemicAlly open in a pinned tab at work. He clicks "Molecular Weight Calculator" from the persistent top nav, types "C6H12O6 H2O NaCl" in the substance tag input, and sees three rows of results appear. The copy button puts the table in his clipboard. He returns to the landing via the nav logo. **Edge case:** if he types an invalid formula, the tag input shows a red badge and a tooltip explaining the parse error.

## 3. Glossary

- **Tool** — A distinct calculator or simulator page (Molecular Weight, Reaction Balancer, Dilution, Equilibria, HPLC Simulator).
- **Tool Card** — A clickable card on the landing page that links to a tool.
- **Result Card** — The right-side card on calculator pages displaying the computation output.
- **Substance Tag** — A chip-style input widget for entering chemical formulas/compounds.
- **Chromatogram** — The Plotly-generated interactive plot on the HPLC simulator page.
- **Level** — A pre-configured HPLC simulation challenge with specific analytes and constraints.

## 4. Features

### 4.1 Landing Page Redesign

The landing page is the first impression — a dark, inviting tool grid that feels more like a product dashboard than a generic Bootstrap page.

**Description:** A hero section with ChemicAlly's identity (brand mark, tagline) transitions into a tool grid. Each tool card has a consistent height, a prominent Unicode emoji icon, a short description, and a call-to-action button. Cards have subtle hover elevation and a border accent. The page is fully responsive: 1 column on mobile, 2 on tablet, 3 on desktop (matching the current grid but with richer styling). No icon library is needed — emoji are used throughout. `[ASSUMPTION: HPLC card gets special visual treatment — a badge or a slightly different accent — to signal it's the most interactive tool.]`

**Functional Requirements:**

#### FR-1: Tool card grid

The landing page displays all 5 tools as visually consistent cards. Each card uses a shared template partial. Cards have equal height via flexbox. The grid is 1/2/3 columns at breakpoints. Realizes UJ-1, UJ-3.

**Consequences (testable):**
- All 5 tool cards render on page load.
- Cards in the same row have equal height regardless of content length.
- Grid layout collapses to single column at viewport < 576px.

#### FR-2: Card hover interaction

Tool cards elevate on hover (`box-shadow: 0 4px 12px rgba(0,0,0,0.3)` + `translateY(-2px)` transition) and show an accent border color. Realizes UJ-1.

**Consequences (testable):**
- Hover state is a CSS-only transition (no JS).
- Transition duration is 200ms-300ms.
- Accent color card border matches the design system's primary color.

#### FR-3: Hero section

The landing hero displays the ChemicAlly brand mark, a short tagline, and a subtle call-to-action linked to the tool grid below. `[ASSUMPTION: the brand mark is a simple SVG logo or text treatment, not an image asset.]` Realizes UJ-1.

**Consequences (testable):**
- Hero renders before the tool grid.
- CTA link scrolls to `#tool_grid` on same page (no navigation).

#### FR-4: Responsive landing

The landing page renders without horizontal overflow on viewports from 320px to 2560px. No content is cut or overlapped. Realizes UJ-1.

**Consequences (testable):**
- Tested at 320px, 576px, 768px, 992px, 1200px, 1400px widths.
- All text remains readable without zoom.

#### FR-5: Landing page load performance

The landing page loads and becomes interactive within 2 seconds on a typical broadband connection (excluding initial uncached MathJax download). `[ASSUMPTION: no heavy assets are added — icons are inline SVG or Unicode, no new JS frameworks.]`

**Consequences (testable):**
- Lighthouse performance score >= 90 for the landing page.
- No render-blocking resources beyond Bootstrap CSS and the site's `style.css`.

**Notes:**
- `[NOTE FOR PM]` Animated page transitions between landing and tool pages could be added in a follow-up; v1 uses instant navigation.

---

### 4.2 Persistent Navigation

The navbar is the user's compass — always visible, always consistent, always one click away from the landing page.

**Description:** The existing sticky-top dark navbar is refined. The Tools dropdown is replaced with a row of inline links on desktop (reducing clicks). A subtle active state shows which tool page the user is on. The theme toggle button is retained. On mobile, the hamburger menu opens an offcanvas panel. Breadcrumbs are added across all tool pages for navigation context.

**Functional Requirements:**

#### FR-6: Active tool indicator

The current tool page is visually indicated in the navigation (e.g., underline, active class, or different weight). Realizes UJ-2.

**Consequences (testable):**
- Navigating to `/calculate/molecular_weight` shows "Molecular Weight" as active in the nav.
- The active indicator uses the design system's accent color.

#### FR-7: Mobile navigation usability

The hamburger menu opens a full-width overlay or offcanvas panel with all tool links and the theme toggle. `[ASSUMPTION: Bootstrap 5.3's offcanvas component is used for the mobile menu.]` Realizes UJ-1.

**Consequences (testable):**
- Menu opens/closes via a single tap on the hamburger icon.
- All tool links are visible without scrolling on a typical mobile viewport.
- Tapping a link closes the menu and navigates.

#### FR-8: Breadcrumb navigation

All tool pages display breadcrumbs showing the current location (e.g., Home > Molecular Weight Calculator). HPLC simulator pages retain their existing level breadcrumb. Realizes UJ-1, UJ-2.

**Consequences (testable):**
- Breadcrumbs render on all calculator pages and HPLC pages.
- Breadcrumb "Home" links to the landing page.
- Breadcrumb styling uses muted text with accent-colored separators.

---

### 4.3 Calculator Pages — Unified Layout

The four chemistry calculators (Molecular Weight, Reaction Balancer, Dilution, Equilibria) share a common two-column layout on desktop: form on the left, results on the right. On mobile, results stack below the form.

**Description:** The existing `_calculator_base.html` and CSS grid layout are retained but given richer visual treatment. The form card gets a subtle background distinction from the result card. Input fields are better spaced. The Calculate button is more prominent. Results tables inherit the card styling with striped rows that are readable on the dark background.

`[ASSUMPTION: the two-column layout (1fr 1fr) at lg breakpoint stays — it works well. The cards themselves get the visual upgrade, not the grid structure.]`

**Functional Requirements:**

#### FR-9: Unified calculator card styling

All four calculator pages use the same card pattern: form card (inputs) + result card (outputs) with consistent padding, border radius, background tint, and shadow. Realizes UJ-1, UJ-3.

**Consequences (testable):**
- All four calculators have visually identical card components.
- Cards use `--bs-card-*` CSS custom properties for theming.
- Cards have a subtle background tint (e.g., `bg-body-tertiary` or custom) to distinguish them from the page background.

#### FR-10: Improved input field styling

Form inputs (text, select, number, tag input) have consistent focus rings, label positioning, and spacing. Realizes UJ-1.

**Consequences (testable):**
- All input types share the same focus ring color (accent).
- Labels are visually associated with inputs (proper `for` attribute).
- Spacing between fields is `--bs-spacer` multiples.

#### FR-11: Prominent calculate button

The Calculate button `[ASSUMPTION: uses the accent color as its background]`, fills the form card width on mobile, aligns to the bottom-right on desktop. Realizes UJ-1.

**Consequences (testable):**
- Button has `btn-accent` or equivalent class.
- Button shows a loading spinner during calculation.
- Button is disabled after click until navigation.

#### FR-12: Result card animation

The result card fades in with a subtle translate-up transition when results appear. Realizes UJ-3.

**Consequences (testable):**
- Animation duration is 300ms-400ms.
- Animation triggers on result render, not on initial page load (no result).

#### FR-13: Copy-to-clipboard consistency

All calculator result pages that display tabular data (MW, Reaction Balancer, Dilution, Equilibria) have a copy button using the same styling and position. Realizes UJ-1, UJ-3.

**Consequences (testable):**
- Clipboard button uses the same position (bottom-right of result card) and style across all four calculator pages.
- Clipboard button copies the full result table content.

---

### 4.4 Molecular Weight Calculator — Specific

**Description:** The substance tag input (`_tag_input_field.html`) remains the primary input widget. Results render as a two-column table (Molecule, Molecular Weight). Empty/error states are handled visually.

**Functional Requirements:**

#### FR-14: Tag input visual refinement

The substance tag container has the same visual treatment as other form inputs (focus ring, background, border). Tag chips are rounded with a remove button that has a hover state. Realizes UJ-3.

**Consequences (testable):**
- Tag container focus ring matches the accent color.
- Tag chips use `--bs-secondary-bg` or a custom dark chip background.
- Remove button has a visible hover state.

#### FR-15: Invalid formula visual feedback

If a formula cannot be parsed, the tag chip shows an error state (e.g., red border, error icon) and a tooltip with the error message. Realizes UJ-3.

**Consequences (testable):**
- Error state is visible without requiring a form submission.
- Error tooltip disappears when the user removes the invalid tag.

---

### 4.5 HPLC Simulator — Visual Upgrade

The HPLC simulator is the anchor experience. Its UI receives the most attention: the parameter panel, chromatogram canvas, and results panel should feel like a professional instrument software, not a Bootstrap admin panel.

**Description:** The existing accelerator-based layout (left: controls, right: chromatogram + results) is retained. The parameter accordion gets styled with a subtle divider and richer header treatment. The chromatogram container is the hero element — full-width on its column, with a dedicated card header showing the toolbar (Auto-Scale / Inspect toggle). Results metrics are displayed as large stat cards. The peak table inherits the card's dark styling.

`[ASSUMPTION: the HDL simulator already uses Alpine.js + Plotly — no changes to the JS framework. All changes are CSS/theming only.]`

**Functional Requirements:**

#### FR-16: Simulator page layout polish

The simulator page uses a `col-lg-4 col-xl-3` (controls) + `col-lg-8 col-xl-9` (chromatogram) layout. The control panel card has a flush accordion inside. The chromatogram container has no extra padding so the plot fills the card. Realizes UJ-2.

**Consequences (testable):**
- Control panel width is consistent across all levels.
- Chromatogram plot fills the card body width (no gutters).
- Card spacing matches the calculator card spacing.

#### FR-17: Parameter accordion styling

Accordion headers have a hover state, a subtle icon or chevron, and the active section's header uses the accent color. Realizes UJ-2.

**Consequences (testable):**
- Accordion header background changes on hover.
- Active accordion section has an accent-colored left border or header text.
- `[ASSUMPTION]` — the existing `accordion-flush` class is retained but custom CSS overrides Bootstrap's default border colors.

#### FR-18: Range sliders styling

All range sliders (`<input type="range">`) are styled with the accent color for the filled track portion. The current value is displayed alongside the slider. Realizes UJ-2.

**Consequences (testable):**
- Slider track fill uses the accent color.
- Current value text updates in real time as the slider moves.
- Slider thumb is styled to match the design system.

#### FR-19: Results metric cards

The four result metrics (Score, Min Resolution, Run Time, Status) display as large centered stat cards in a row of 4 on desktop, 2 on tablet, stacked on mobile. Realizes UJ-2.

**Consequences (testable):**
- Metric values are displayed in a larger font weight and size.
- Resolution and Status values change color based on pass/fail.
- Layout collapses correctly on smaller viewports.

#### FR-20: Pressure gauge styling

The system pressure progress bar is styled with a gradient fill: green → yellow → red as pressure approaches the limit. Realizes UJ-2.

**Consequences (testable):**
- Progress bar color transitions are CSS-only.
- The pressure fill percentage matches `results.metrics.max_pressure_bar / level.max_pressure_bar`.

#### FR-21: Warning alerts styling

pH warnings, overpressure alerts, and sub-2µm particle warnings use the same alert pattern with consistent icons and colors. Realizes UJ-2.

**Consequences (testable):**
- All warning types are visually distinct (warning vs danger).
- Warnings appear/disappear reactively (Alpine.js `x-show`).

---

### 4.6 Design System

A set of CSS custom properties that unify the entire application under one visual language.

**Description:** A `design-system.css` file (or a section in `style.css`) defines all color tokens, spacing, typography, border radius, and transition values using CSS custom properties. Every component references these tokens — no hardcoded colors or values.

`[ASSUMPTION: Bootstrap 5.3's CSS vars (`--bs-*`) provide the baseline; the design system overrides or supplements them. No Sass compilation — plain CSS over CDN Bootstrap.]`

**Functional Requirements:**

#### FR-22: Color tokens

The design system defines the following color tokens as CSS custom properties:
- `--ca-bg-page` — page background (very dark, e.g., `#0d1117`)
- `--ca-bg-card` — card background (one step lighter, e.g., `#161b22`)
- `--ca-bg-card-alt` — alternate card/table stripe background
- `--ca-accent` — primary accent (teal `#14b8a6`)
- `--ca-accent-hover` — accent hover (lighter or darker teal)
- `--ca-text-primary` — primary text
- `--ca-text-muted` — muted/secondary text
- `--ca-border` — subtle border color
- `--ca-success` — green for pass states
- `--ca-warning` — amber for warning states
- `--ca-danger` — red for error/overpressure states

Realizes FR-9 through FR-21.

**Consequences (testable):**
- Every color value in `style.css` references a `--ca-*` variable directly or indirectly through Bootstrap overrides.
- Changing `--ca-accent` propagates to all accent-colored elements.

#### FR-23: Typography scale

The design system uses the existing Bootstrap type scale but sets `--bs-body-font-family` to `Inter, system-ui, -apple-system, sans-serif` for body text. `[ASSUMPTION: Inter is loaded from Google Fonts or a CDN, with swap font-display for performance.]`

**Consequences (testable):**
- Body text renders in Inter (or fallback).
- Font loading does not block page render.

#### FR-24: Spacing and border radius

All component spacing uses `--bs-spacer` multiples (or a custom `--ca-spacer`). Card border radius is unified (e.g., `--ca-border-radius: 0.75rem`).

**Consequences (testable):**
- All cards share the same border radius.
- Spacing between sections is consistent.

---

### 4.7 Footer

**Description:** The existing three-column footer is retained but restyled to match the new dark palette. Links are more visible. The "Powered By" section is better scannable.

**Functional Requirements:**

#### FR-25: Footer restyling

The footer uses `--ca-bg-card` as background, `--ca-border` as top border, and `--ca-text-muted` for secondary text. Links use `--ca-accent` on hover. Realizes UJ-1.

**Consequences (testable):**
- Footer background matches the card color, not the default `bg-body-secondary`.
- Hovering over a footer link shows the accent color.

## 5. Non-Goals (Explicit)

- No changes to the Django backend logic, views, URLs, or calculation engines.
- No changes to the HPLC simulator API endpoints.
- No migration away from Bootstrap 5.3.
- No user accounts or authentication system.
- No light mode theme toggle (deferred to v2).
- No animations or transitions beyond subtle, purposeful ones (no decorative motion).
- No icon library dependency (Unicode emoji or inline SVG only).
- No changes to the DRF serializer or simulation engine logic.
- No modification to the existing session persistence pattern.
- No new pages or tools — strictly a visual redesign of the existing 7 pages.

## 6. MVP Scope

### 6.1 In Scope

- Landing page hero + tool card grid restyling (FR-1 through FR-5)
- Persistent navigation refinement (FR-6 through FR-8)
- All 4 calculator page card restyling (FR-9 through FR-13)
- Substance tag input visual refinement (FR-14, FR-15)
- HPLC simulator page visual upgrade (FR-16 through FR-21)
- Design system CSS custom properties (FR-22 through FR-24)
- Footer restyling (FR-25)

### 6.2 Out of Scope for MVP

- Theme toggle / light mode — deferred to v2.
- Page transition animations — deferred to v2.
- User accounts or score persistence beyond session — deferred to v2. `[NOTE FOR PM: score persistence is the most-requested "next feature" — revisit if engagement grows.]`
- Comprehensive accessibility audit beyond existing skip-link and ARIA attributes.
- New tool pages or calculators.
- Mobile app or PWA.

## 7. Success Metrics

**Primary**
- **SM-1**: The site looks and feels cohesive across all 7 pages — no page feels like a different app. Validates FR-9, FR-22.
- **SM-2**: I (Franco) browse the site and find it visually appealing enough to use regularly without irritation. Validates the entire PRD.

**Secondary**
- **SM-3**: All interactive elements (buttons, sliders, accordions, tag inputs) have consistent hover/focus/active states. Validates FR-10, FR-17, FR-18.

**Counter-metrics (do not optimize)**
- **SM-C1**: Time spent on landing page before clicking a tool — we are not optimizing for dwell time. Users should find their tool and leave the landing quickly.

## 8. Open Questions

*All items from the draft review were resolved. See decision log for confirmed answers.*

1. `[RESOLVED]` **Accent color** — teal `#14b8a6` confirmed.
2. `[RESOLVED]` **Nav links** — inline links on desktop confirmed.
3. `[RESOLVED]` **Font** — Inter from Google Fonts confirmed.
4. `[RESOLVED]` **Breadcrumbs** — add to all calculator pages, consistent with HPLC.
5. `[RESOLVED]` **Tool card icons** — Unicode emoji confirmed.
6. `[RESOLVED]` **Copy-to-clipboard** — all four calculator pages, not just MW and Reaction Balancer.

## 9. Assumptions Index

*Resolved assumptions have been removed from this section. Remaining items are pending confirmation.*

- **§4.1 (FR-1):** HPLC card gets a special visual treatment (badge or accent) to signal it's the most interactive tool.
- **§4.1 (FR-3):** The brand mark is a simple SVG logo or text treatment, not an image asset.
- **§4.3 (FR-9):** The two-column grid layout stays — cards get the visual upgrade, not the grid structure.
- **§4.5 (FR-16):** No changes to Alpine.js or Plotly — CSS/theming only.
- **§4.5 (FR-17):** The existing `accordion-flush` class is retained but with custom CSS overrides.
- **§4.6 (FR-22):** Bootstrap 5.3's CSS vars provide the baseline; design system overrides them.
