---
name: compressor-design
description: >-
  Design or size a centrifugal compressor (single or multi-stage train) with
  the ccp library. Use when the user gives operating specs -- fluid, mass flow,
  rotational speed, inlet pressure/temperature, target overall pressure ratio,
  and number of stages -- and wants predicted per-stage discharge conditions,
  head, polytropic efficiency, power, and optional intercooling. Triggers on
  requests like "design a compressor for...", "size a multistage train",
  "how much power to compress X from P1 to P2", or evaluating intercooling.
---

# Centrifugal compressor design with ccp

This skill drives `ccp.MultiStageCompressor` (forward design: predict the
machine from operating specs) via the `ccp design` CLI. It is the inverse of
ccp's usual performance *analysis* workflow.

## Procedure

1. **Collect the spec** for each case (ask only for what's missing):
   - `fluid` — component → mole fraction. Use `{"co2": 1}`, `{"water": 1}`,
     `{"Argon": 1}`. For air use the pseudo-pure **`{"air": 1}`** (the explicit
     O2/N2/Ar mixture fails CoolProp HEOS convergence).
   - `flow_m` (mass flow), `speed`, suction `p` and `T`, overall
     `pressure_ratio`, `n_stages`.
   - Optional: `eff` (default 0.80, scalar or per-stage list), `intercooling`,
     `geometry` (`b`, `D` — only needed for the `phi`/`psi` coefficients).

2. **Write a case JSON.** One case object, or several under `"cases": [...]`.
   Every quantity is `{"value": <number>, "units": "<pint unit>"}`. See
   `references/example_cases.json` for the schema and seven worked examples.

3. **Run the design:**
   ```bash
   uv run ccp design cases.json -o results.json
   ```
   A per-stage + overall summary prints to stderr; full results JSON goes to
   the output path (use `-` for stdout). For a one-off in Python, see
   `references/design.py`.

4. **Report** per-stage discharge P/T, head, efficiency and power, the overall
   pressure ratio and total shaft power, plus intercooler duties.

## Modelling notes and physical caveats — surface these

- **Equal pressure-ratio split**, compensated for interstage pressure drops so
  the achieved overall ratio matches the target. Override with
  `pressure_ratio_per_stage` if a non-uniform split is intended.
- **Intercooling** (default ON for multi-stage): cools back toward the suction
  temperature with a ~1% interstage pressure drop, **clamped to single-phase
  vapor** — it never cools below saturation + a 5 K margin.
  - **Steam**: the saturation temperature often sits *above* the requested
    intercooler outlet, so cooling is limited (the `clamped` flag is set). Say
    this explicitly rather than reporting impossible cooling.
- **Near-critical CO2** (e.g. ~7.4 MPa / ~31 °C, right at the 7.38 MPa /
  31.0 °C critical point): property derivatives are extreme and CoolProp HEOS
  is sensitive there. **Recommend REFPROP** (set `RPPREFIX`) for production
  numbers and treat single-run results as indicative.
- **Efficiency is an assumption** (0.80 default), not derived from geometry.
- **Mass flow is constant** through the train (no leakage/sidestream modelling,
  unlike `StraightThrough`/`BackToBack`).
