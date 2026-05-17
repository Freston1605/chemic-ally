# SCIENTIFIC_LOGIC.md — HPLC Simulator

> **Purpose:** This document is an agent-to-agent contract. Any future refactor, feature addition, or bug fix to the HPLC simulator MUST respect the physical laws and mathematical models documented here. Violating these constraints produces physically impossible simulations and breaks user trust.

---

## 1. System Boundaries

### 1.1 Mobile Phase

- **Water (H₂O) in the aqueous mobile phase is in constant excess and is NEVER a limiting reagent.** The simulator models a binary solvent system (Solvent A = aqueous, Solvent B = organic). Solvent A is always available in sufficient quantity.
- **Solvent B (organic modifier, e.g., acetonitrile or methanol)** is modeled as a volume fraction `phi` (φ) ranging from 0.0 to 1.0. The LSS model operates on this fraction.
- **pH range:** Real silica-based C18/C8 columns degrade below pH ~2 and above pH ~8. The simulator enforces `2.0 <= pH <= 8.0`. Hybrid columns may extend to pH 12, but this simulator does not model those.
- **Buffer concentration:** Typical range 5–50 mM. Values outside 1–100 mM are physically unrealistic for HPLC. Note: buffer concentration is stored for display purposes only and is NOT used in retention or pressure calculations.

### 1.2 Column (Stationary Phase)

- **Column chemistry codes:** `C18`, `C8`, `C4` (reversed-phase alkyl), `Phenyl` (phenyl-hexyl) are fully modeled. `HILIC` and `NP` (normal phase) are placeholder labels only — the LSS model is calibrated for reversed-phase retention.
- **Column dimensions:** Length 50–250 mm, ID 2.1–4.6 mm, particle size 1.8–5.0 µm. These are commercially available values.
- **Column porosity (ε):** Fixed at 0.7 for packed columns. This is the total porosity (interparticle + intraparticle).

### 1.3 Analytes

- **LSS model parameters (log_kw, S):** These are empirical constants determined experimentally. They are treated as temperature-independent in the current model (a simplification; in reality S varies ~0.5–1% per °C).
- **pKa:** Used for ionization correction. Analytes with `neutral_charge=True` skip pH correction entirely.
- **Retention factor k:** Must satisfy `k >= 0.01`. A value of k = 0 means the analyte elutes at the dead time (unretained). Values below 0.01 are treated as unretained.
- **Peak height:** Calculated from concentration (mM) and molar extinction coefficient (L·mol⁻¹·cm⁻¹) using a simplified Beer-Lambert model. If extinction coefficient is unknown, a concentration-based default is used.

### 1.4 Operational Parameters

- **Flow rate:** `0.1 < F <= 5.0` mL/min. Zero or negative flow rates are physically impossible and would cause division-by-zero in dead time calculations.
- **Temperature:** `20 <= T <= 80` °C. Most HPLC columns are rated to 60 °C; some to 90 °C. Below 20 °C, viscosity increases dramatically and the model becomes less accurate.
- **Injection volume:** `1 <= V_inj <= 100` µL. Overloading (>100 µL) causes peak distortion not modeled here.
- **Dwell time:** `0.1 <= t_dwell <= 10.0` min. Typical systems range 0.5–5 min. Default is 1.0 min.

---

## 2. Physical Property Rules (Hard Constraints)

### 2.1 Non-Negativity

The following quantities MUST be **strictly non-negative** (≥ 0):

| Quantity | Symbol | Minimum | Reason |
|---|---|---|---|
| Flow rate | F | > 0 (strictly positive) | Zero flow = no elution; negative = backward flow (impossible) |
| Column length | L | > 0 | Physical dimension |
| Column internal diameter | d_c | > 0 | Physical dimension |
| Particle size | d_p | > 0 | Physical dimension |
| Retention time | t_R | ≥ t_0 | Analyte cannot elute before the dead time |
| Dead time | t_0 | > 0 | Requires positive flow and positive column volume |
| Peak width | w | > 0 | Zero width = infinite plate count (impossible) |
| Peak area | A | ≥ 0 | Negative area = negative concentration (impossible) |
| Peak height | h | > 0 | Negative height = negative absorbance (impossible) |
| Resolution | R_s | ≥ 0 | |
| Pressure | ΔP | ≥ 0 | Negative pressure is physically meaningless in this context |
| Concentration | c | ≥ 0 | Negative concentration is impossible |
| Temperature (K) | T | > 0 | Absolute temperature must be positive |
| Viscosity | η | > 0 | |
| Plate count | N | > 0 | |
| Retention factor | k | ≥ 0 | k = 0 means unretained |
| Organic fraction | φ | 0 ≤ φ ≤ 1 | Volume fraction |
| Dwell time | t_dwell | > 0 | System delay volume cannot be negative |
| Score | — | ≥ 0 | |

### 2.2 Gradient Elution Constraints

- `start_b <= end_b` (organic percentage must increase or stay constant in a gradient)
- `ramp_time > 0` (zero ramp time = step change, not a gradient)
- `delta_phi = (end_b - start_b) / 100.0` must satisfy `0 <= delta_phi <= 1.0`

### 2.3 Pressure Constraints

- System pressure MUST NOT exceed the column's rated maximum (`max_pressure_bar` from Level).
- Pressure above 80% of limit triggers a warning; above 100% = overpressure failure.

### 2.4 Resolution Threshold

- **Baseline resolution:** R_s ≥ 1.5 (IUPAC definition)
- **Adequate separation:** R_s ≥ 1.0 (valley between peaks visible)
- **Co-elution:** R_s < 1.0 (peaks overlap significantly)

---

## 3. Chromatographic Mathematical Proofs

### 3.1 Linear Solvent Strength (LSS) Model

The fundamental equation for isocratic retention:

$$\log k = \log k_w - S \cdot \phi$$

Where:
- $k$ = retention factor
- $k_w$ = retention factor in pure water
- $S$ = LSS slope parameter (typically 2–5 for small molecules)
- $\phi$ = volume fraction of organic modifier

Rearranged:

$$k = 10^{\log k_w - S \cdot \phi}$$

**With pH correction for ionizable analytes:**

For acids:
$$k_{\text{observed}} = \frac{k_{\text{neutral}}}{1 + 10^{\text{pH} - \text{p}K_a}}$$

For bases:
$$k_{\text{observed}} = \frac{k_{\text{neutral}}}{1 + 10^{\text{p}K_a - \text{pH}}}$$

The simulator uses a unified ionization factor:
$$f_{\text{ion}} = \frac{1}{1 + 10^{\text{pH} - \text{p}K_a}}$$

Clamped: if `pH - pKa > 3`, factor = 0.01 (fully ionized, minimal retention). If `pH - pKa < -3`, factor = 1.0 (fully neutral).

### 3.2 Gradient Elution — Fundamental Equation

$$t_R = t_0 + \frac{t_G}{b} \cdot \ln(1 + b \cdot k_0)$$

Where:
- $t_0$ = dead time (column hold-up time)
- $t_G$ = gradient time (ramp_time)
- $k_0$ = retention factor at initial mobile phase composition
- $b$ = gradient steepness parameter:

$$b = \frac{S \cdot \Delta\phi \cdot t_0}{t_G}$$

When $b < 0.001$, the gradient is effectively isocratic, and the isocratic equation is used instead.

**Dwell time:** Added to retention time to account for system delay volume. Configurable via `OperationConfig.dwell_time_min` (default 1.0 min).

### 3.3 Dead Time (Column Hold-Up Time)

$$t_0 = \frac{V_m}{F} = \frac{\pi \cdot r^2 \cdot L \cdot \varepsilon}{F}$$

Where:
- $r$ = column radius (cm)
- $L$ = column length (cm)
- $\varepsilon$ = column porosity (0.7)
- $F$ = flow rate (mL/min)

**Units:** $r$ and $L$ in cm → $V_m$ in mL → $t_0$ in minutes.

### 3.4 Column Backpressure (Kozeny-Carman, Empirical)

The simulator uses a reference-based empirical model calibrated to a 150 × 4.6 mm, 5 µm column at 1 mL/min, 30°C producing ~120 bar:

$$\Delta P = 120 \cdot \frac{L}{150} \cdot \frac{F}{1.0} \cdot \left(\frac{5.0}{d_p}\right)^2 \cdot \left(\frac{4.6}{d_c}\right)^2 \cdot \frac{\eta(T)}{\eta(30^\circ\text{C})}$$

Where:
- $L$ = column length (mm)
- $F$ = flow rate (mL/min)
- $d_p$ = particle size (µm)
- $d_c$ = column internal diameter (mm)
- $\eta(T)$ = mobile phase viscosity at temperature T

**Viscosity model (Arrhenius-type):**

$$\eta(T) = \eta_{\text{ref}} \cdot \exp\left[E_a \cdot \left(\frac{1}{T_K} - \frac{1}{298.15}\right)\right]$$

Where $E_a = 1800$ K (empirical activation energy for water/ACN mixtures), $T_K$ = temperature in Kelvin.

### 3.5 Resolution Between Adjacent Peaks

$$R_s = \frac{2 \cdot (t_{R2} - t_{R1})}{w_1 + w_2}$$

Where:
- $t_{R1}, t_{R2}$ = retention times of adjacent peaks (sorted by t_R)
- $w_1, w_2$ = peak widths at baseline (4σ)

The **critical pair** is the adjacent pair with the minimum R_s.

### 3.6 Peak Width (van Deemter)

$$w = 4 \cdot \sigma_{\text{total}}$$

$$\sigma_{\text{total}} = \sqrt{\sigma_{\text{column}}^2 + \sigma_{\text{extra}}^2}$$

$$\sigma_{\text{column}} = \frac{t_R}{\sqrt{N}}$$

$$N = \frac{L}{H}$$

**van Deemter equation:**

$$H = A + \frac{B}{u} + C \cdot u$$

Where:
- $A = 1.5 \cdot d_p$ (eddy diffusion term)
- $B = 2.0 \times 10^{-9}$ m²/s (longitudinal diffusion)
- $C = 0.05 \cdot d_p^2 / D_m$ (mass transfer, $D_m \approx 10^{-9}$ m²/s)
- $u$ = linear velocity (m/s)

**Extra-column band broadening:**

$$\sigma_{\text{extra}} = \frac{V_{\text{inj}} / 1000}{4 \cdot F}$$

Where $V_{\text{inj}}$ is in µL and $F$ in mL/min.

Plate count is clamped: `500 <= N <= 200000`.

### 3.7 Peak Shape (Exponentially Modified Gaussian)

$$I(t) = \frac{h}{2} \cdot \exp\left(\frac{\sigma^2}{2\tau^2} - \frac{t - t_R}{\tau}\right) \cdot \left[1 + \text{erf}\left(\frac{t - t_R}{\sigma\sqrt{2}} - \frac{\sigma}{\tau\sqrt{2}}\right)\right]$$

Where:
- $h$ = peak height (calculated from concentration × extinction coefficient via Beer-Lambert model)
- $\sigma = w / 4$
- $\tau = \sigma \cdot (T_f - 1)$ where $T_f = 1.2$ (tailing factor)
- The peak is evaluated only within $|t - t_R| < 4\sigma$ for computational efficiency.

### 3.8 Peak Height (Beer-Lambert, Simplified)

$$h = h_{\text{ref}} \cdot \frac{c}{c_{\text{ref}}} \cdot \frac{\varepsilon}{\varepsilon_{\text{ref}}} \cdot \frac{V_{\text{inj}}}{V_{\text{ref}}}$$

Where:
- $h_{\text{ref}} = 1000$ mAU (reference height)
- $c_{\text{ref}} = 1.0$ mM (reference concentration)
- $\varepsilon_{\text{ref}} = 10000$ L·mol⁻¹·cm⁻¹ (reference extinction coefficient)
- $V_{\text{ref}} = 10$ µL (reference injection volume)

If extinction coefficient is unknown:
$$h = \max(100, 1000 \cdot \frac{c}{1.0})$$

Height is clamped to `[10, 10000]` mAU.

### 3.9 Scoring Formula

$$\text{Base Score} = \frac{\text{BasePoints}}{t_{\text{total}}}$$

$$\text{Pressure Ratio} = \frac{\Delta P_{\text{max}}}{\Delta P_{\text{limit}}}$$

$$\text{Pressure Penalty} = \begin{cases} (\text{Pressure Ratio} - 0.8) \cdot \text{BasePoints} \cdot 0.5 & \text{if } \text{Pressure Ratio} > 0.8 \\ 0 & \text{otherwise} \end{cases}$$

$$\text{Rs Bonus} = \begin{cases} (R_s - 1.5) \cdot \text{BasePoints} \cdot 0.1 & \text{if } R_s \geq 1.5 \\ 0 & \text{otherwise} \end{cases}$$

$$\text{Final Score} = \max(0, \text{Base Score} - \text{Pressure Penalty} + \text{Rs Bonus})$$

---

## 4. Known Simplifications & Limitations

1. **Peak heights are not uniform.** They are calculated from analyte concentration and molar extinction coefficient using a simplified Beer-Lambert model. This is more realistic than a fixed height but still does not account for full detector physics (wavelength selection, bandwidth, noise characteristics).
2. **Temperature affects only viscosity.** In reality, temperature also changes k (van't Hoff relationship) and selectivity (α). The current model does not include temperature-dependent retention.
3. **Dwell time is configurable** (default 1.0 min) rather than hardcoded. Real systems vary from 0.5–5 mL dwell volume.
4. **No column aging/degradation.** Stationary phase performance is assumed constant.
5. **HILIC and Normal Phase are not implemented.** The LSS model is calibrated for reversed-phase. HILIC requires a different retention model.
6. **Buffer concentration is not used in calculations.** It is stored for display purposes only.
7. **UV absorption max is stored but not used for wavelength selection.** The extinction coefficient is used for peak height, but the detector wavelength is not modeled.

---

## 5. Invariants That MUST Hold After Any Refactor

1. `t_R >= t_0` for every analyte (no analyte elutes before the void)
2. `k >= 0.01` (no negative or zero retention factors)
3. `ΔP >= 0` (no negative pressure)
4. `N >= 500` and `N <= 200000` (plate count within physical bounds)
5. `0 <= φ <= 1` (organic fraction is a valid fraction)
6. `R_s >= 0` (resolution is non-negative)
7. `w > 0` (peak width is strictly positive)
8. `score >= 0` (score is non-negative)
9. `flow_rate > 0` (strictly positive, never zero)
10. `temperature_K > 0` (absolute temperature)
11. `h > 0` (peak height is strictly positive)
12. `t_dwell > 0` (dwell time is strictly positive)
