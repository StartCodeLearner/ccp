"""Tests for the multi-stage compressor design module."""

import json
import subprocess
import sys

import CoolProp.CoolProp as CP
import pytest

from ccp import Q_, State
from ccp.compressor_design import (
    MultiStageCompressor,
    design_from_case,
    results_to_dict,
    run_cases,
)

# (name, fluid, flow_m kg/s, speed rpm, Pin MPa, Tin degC, target PR, n_stages)
CASES = [
    ("co2_1_near_critical", {"co2": 1.0}, 115.5, 32000, 7.4, 31.1, 3.48, 1),
    ("co2_2", {"co2": 1.0}, 3.53, 75000, 7.687, 32.15, 1.8213, 1),
    ("co2_3", {"co2": 1.0}, 15.8, 27400, 7.6, 37.0, 1.5, 1),
    ("argon_4stage", {"Argon": 1.0}, 17.0, 12000, 1.5, 320.0, 2.0, 4),
    ("air_8stage", {"air": 1.0}, 250.0, 6500, 3.0, 320.0, 5.0, 8),
    ("water_2stage", {"water": 1.0}, 0.94, 23000, 0.054, 87.0, 2.0, 2),
    ("water_4stage", {"water": 1.0}, 2.8, 34500, 0.1, 100.0, 10.0, 4),
]


def _build(case):
    name, fluid, flow_m, speed, p_in, t_in, pr, n = case
    suc = State(p=Q_(p_in, "MPa"), T=Q_(t_in, "degC"), fluid=fluid)
    return MultiStageCompressor(
        suc=suc,
        flow_m=Q_(flow_m, "kg/s"),
        speed=Q_(speed, "RPM"),
        pressure_ratio=pr,
        n_stages=n,
    )


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_design_case(case):
    name, fluid, flow_m, speed, p_in, t_in, target_pr, n = case
    mc = _build(case)

    # The interstage-loss-compensated split should recover the target overall
    # pressure ratio. The first CO2 case sits essentially at the critical point
    # (7.4 MPa / 31.1 degC vs 7.38 MPa / 31.0 degC); CoolProp HEOS is sensitive
    # there and REFPROP is recommended for production use, so we allow a wider
    # band for it.
    rel_tol = 0.03 if name == "co2_1_near_critical" else 0.01
    assert mc.pressure_ratio == pytest.approx(target_pr, rel=rel_tol)

    assert mc.n_stages == n
    assert len(mc.points) == n
    assert mc.power.to("W").m > 0

    for point in mc.points:
        # Each stage compresses: discharge hotter and higher pressure than suction.
        assert point.disch.T().m > point.suc.T().m
        assert point.disch.p().m > point.suc.p().m
        assert point.head.to("J/kg").m > 0
        # Suction must be single-phase vapor (above saturation) for pure fluids
        # below their critical point.
        _assert_single_phase_vapor(point.suc)

    # Intercoolers (n-1 when intercooling is on) remove heat by cooling.
    assert len(mc.intercoolers) == n - 1
    for ic in mc.intercoolers:
        assert ic["T_out"].m <= ic["T_in"].m
        assert ic["duty"].to("W").m >= 0


def _assert_single_phase_vapor(state):
    if len(state.fluid) > 1:
        return
    if state.p() >= state.p_critical():
        return
    if state.T().to("K") >= state.T_critical().to("K"):
        return
    t_sat = Q_(CP.PropsSI("T", "P", state.p("Pa").m, "Q", 1, state._fluid), "K")
    assert state.T().to("K").m >= t_sat.m - 1e-6


def test_water_intercooler_clamped_to_saturation():
    # Steam cannot be cooled below its saturation temperature: requesting an
    # intercooler outlet at the (sub-saturation) inlet temperature must clamp.
    mc = _build(CASES[5])  # water_2stage, suction 87 degC
    assert any(ic["clamped"] for ic in mc.intercoolers)
    for ic in mc.intercoolers:
        assert ic["T_out"].to("degC").m > 87.0  # raised above the requested 87 degC


def test_no_intercooling_chains_discharge_to_next_suction():
    suc = State(p=Q_(1.5, "MPa"), T=Q_(320, "degC"), fluid={"Argon": 1})
    mc = MultiStageCompressor(
        suc=suc,
        flow_m=Q_(17, "kg/s"),
        speed=Q_(12000, "RPM"),
        pressure_ratio=2.0,
        n_stages=3,
        intercooling=False,
    )
    assert mc.intercoolers == []
    # Without cooling, each stage starts from the previous discharge.
    for prev, nxt in zip(mc.points[:-1], mc.points[1:]):
        assert nxt.suc.T().m == pytest.approx(prev.disch.T().m)
        assert nxt.suc.p().m == pytest.approx(prev.disch.p().m)
    # No interstage drops, so equal split gives exactly the target.
    assert mc.pressure_ratio == pytest.approx(2.0, rel=1e-6)


def test_state_json_round_trip():
    s = State(p=Q_(7.6, "MPa"), T=Q_(37, "degC"), fluid={"co2": 1})
    assert State.from_dict(s.to_dict()) == s


def test_design_from_case_and_results_round_trip():
    case = {
        "name": "rt",
        "fluid": {"Argon": 1.0},
        "flow_m": {"value": 17, "units": "kg/s"},
        "speed": {"value": 12000, "units": "RPM"},
        "suction": {
            "p": {"value": 1.5, "units": "MPa"},
            "T": {"value": 320, "units": "degC"},
        },
        "pressure_ratio": 2.0,
        "n_stages": 4,
        "geometry": {
            "b": {"value": 30, "units": "mm"},
            "D": {"value": 0.5, "units": "m"},
        },
    }
    mc = design_from_case(case)
    results = results_to_dict(mc)
    assert results["name"] == "rt"
    assert results["overall"]["n_stages"] == 4
    assert results["overall"]["pressure_ratio"] == pytest.approx(2.0, rel=0.01)
    # Geometry supplied -> flow/head coefficients reported.
    assert "phi" in results["stages"][0]
    assert "psi" in results["stages"][0]
    # JSON serializable.
    json.dumps(results)


def test_run_cases_collection():
    data = {
        "cases": [
            {
                "name": "a",
                "fluid": {"co2": 1.0},
                "flow_m": {"value": 15.8, "units": "kg/s"},
                "speed": {"value": 27400, "units": "RPM"},
                "suction": {
                    "p": {"value": 7.6, "units": "MPa"},
                    "T": {"value": 37, "units": "degC"},
                },
                "pressure_ratio": 1.5,
                "n_stages": 1,
            }
        ]
    }
    out = run_cases(data)
    assert "results" in out
    assert len(out["results"]) == 1
    assert out["results"][0]["name"] == "a"


def test_cli_design_stdin_to_file(tmp_path):
    case = {
        "fluid": {"co2": 1.0},
        "flow_m": {"value": 15.8, "units": "kg/s"},
        "speed": {"value": 27400, "units": "RPM"},
        "suction": {
            "p": {"value": 7.6, "units": "MPa"},
            "T": {"value": 37, "units": "degC"},
        },
        "pressure_ratio": 1.5,
        "n_stages": 1,
    }
    out = tmp_path / "results.json"
    proc = subprocess.run(
        [sys.executable, "-m", "ccp.cli", "design", "-", "-o", str(out)],
        input=json.dumps(case),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out.read_text())
    assert result["overall"]["pressure_ratio"] == pytest.approx(1.5, rel=0.01)
