"""Minimal example: design a multi-stage compressor directly in Python.

Run with: uv run python .claude/skills/compressor-design/references/design.py
"""

import ccp
from ccp import Q_, State

# Suction state of the first stage.
suc = State(p=Q_(1.5, "MPa"), T=Q_(320, "degC"), fluid={"Argon": 1})

mc = ccp.MultiStageCompressor(
    suc=suc,
    flow_m=Q_(17, "kg/s"),
    speed=Q_(12000, "RPM"),
    pressure_ratio=2.0,
    n_stages=4,
    eff=0.80,           # scalar, or a list of length n_stages
    intercooling=True,  # cool back to suction T between stages (clamped to vapor)
)

print(mc)
print(f"achieved overall PR : {mc.pressure_ratio:.4f}")
print(f"total shaft power   : {mc.power.to('kW'):.4g~P}")
print(f"heat removed        : {mc.heat_removed.to('kW'):.4g~P}")
print(f"final discharge     : {mc.disch.p('MPa'):.4g~P}, {mc.disch.T('degC'):.4g~P}")

for i, point in enumerate(mc.points, start=1):
    print(
        f"  stage {i}: head {point.head.to('kJ/kg'):.4g~P}, "
        f"power {point.power_shaft.to('kW'):.4g~P}, "
        f"disch {point.disch.T('degC'):.4g~P}"
    )

for ic in mc.intercoolers:
    flag = " (clamped to saturation)" if ic["clamped"] else ""
    print(
        f"  intercooler after stage {ic['after_stage']}: "
        f"{ic['T_in'].to('degC'):.4g~P} -> {ic['T_out'].to('degC'):.4g~P}{flag}"
    )
