# Multi-stage compressor design

While most of ccp is used to *analyze* measured performance, the
{py:class}`~ccp.MultiStageCompressor` class works the other way around: it
*designs* (sizes) a compressor train from operating specifications.

Given the suction state, mass flow, speed, an overall target pressure ratio and
a number of stages, it predicts, for every stage, the discharge conditions,
polytropic head, efficiency and power, with optional intercooling between
stages.

Unlike `StraightThrough` / `BackToBack` (test-data analysis classes that model
leakage), this is a forward design tool: the mass flow is assumed constant
through the train and each stage discharge state is predicted from its suction
state, a per-stage pressure ratio and a polytropic efficiency.

## Basic usage

```python
import ccp
from ccp import Q_, State

suc = State(p=Q_(1.5, "MPa"), T=Q_(320, "degC"), fluid={"Argon": 1})

mc = ccp.MultiStageCompressor(
    suc=suc,
    flow_m=Q_(17, "kg/s"),
    speed=Q_(12000, "RPM"),
    pressure_ratio=2.0,
    n_stages=4,
    eff=0.80,            # scalar, or a list of length n_stages
    intercooling=True,
)

mc.pressure_ratio   # achieved overall pressure ratio
mc.power            # total shaft power
mc.disch            # final discharge State
mc.points           # one ccp.Point per stage
mc.intercoolers     # one record per intercooler
```

Each entry in `mc.points` is a regular {py:class}`~ccp.Point`, so the usual
attributes (`head`, `eff`, `power_shaft`, `disch`, `phi`, `psi`, ...) are
available per stage.

## Intercooling

When `intercooling=True` (the default for multi-stage trains), an intercooler is
placed between each pair of stages. It cools the gas back towards
`intercooler_T` (default: the suction temperature) and applies a small
interstage pressure drop (`intercooler_pressure_drop`, default 1%).

The equal pressure-ratio split is **compensated for these interstage pressure
drops**, so the achieved overall pressure ratio matches the requested target.

The cooled temperature is **clamped to single-phase vapor**: it is never taken
below the saturation temperature plus a superheat margin
(`intercooler_superheat_margin`, default 5 K). This matters for fluids such as
steam, where the saturation temperature at the interstage pressure can sit
above the desired intercooler outlet temperature — in that case cooling is
limited and the intercooler record's `clamped` flag is set.

## JSON and command line

A design case can be described as JSON and run from the command line:

```bash
ccp design case.json -o results.json
```

The input may be a single case object or a list under `"cases"`. Every quantity
is a `{"value", "units"}` pair:

```json
{
  "fluid": {"co2": 1.0},
  "flow_m": {"value": 15.8, "units": "kg/s"},
  "speed": {"value": 27400, "units": "RPM"},
  "suction": {"p": {"value": 7.6, "units": "MPa"}, "T": {"value": 37, "units": "degC"}},
  "pressure_ratio": 1.5,
  "n_stages": 1
}
```

In Python the same helpers are available as
`ccp.compressor_design.design_from_case` and
`ccp.compressor_design.results_to_dict`.

## Caveats

- **Near-critical CO2** (for example ~7.4 MPa / ~31 °C, right at the
  7.38 MPa / 31.0 °C critical point): property derivatives are extreme and the
  CoolProp HEOS backend is sensitive there. REFPROP is strongly recommended for
  production numbers.
- **Efficiency** (0.80 by default) is an assumption, not derived from geometry.
- Impeller width and diameter (`b`, `D`) are needed only for the flow/head
  coefficients `phi`/`psi`; they do not affect the thermodynamic design.
