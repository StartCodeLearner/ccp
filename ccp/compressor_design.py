"""Multi-stage centrifugal compressor design (sizing).

Given operating specifications -- fluid, mass flow, speed, suction state, an
overall target pressure ratio and a number of stages -- this module predicts
the per-stage discharge conditions, head, polytropic efficiency and power, with
optional intercooling between stages.

Unlike :class:`ccp.compressor.StraightThrough` / ``BackToBack`` (which are
*test-data analysis* classes that model leakage), :class:`MultiStageCompressor`
is a *forward design* tool: mass flow is assumed constant through the train and
each stage discharge state is predicted from its suction state, a per-stage
pressure ratio and a polytropic efficiency, reusing the design mode already
present in :class:`ccp.point.Point`.

Intercoolers between stages cool the gas back towards a target temperature
(default: the train suction temperature) while applying a small interstage
pressure drop. The cooled temperature is clamped to keep the gas single phase:
it is never taken below the saturation temperature (plus a superheat margin),
which matters for fluids such as steam where the saturation temperature can sit
above the desired intercooler outlet temperature.
"""

import json

import CoolProp.CoolProp as CP

from ccp.config.units import Q_
from ccp.point import Point
from ccp.state import State

DEFAULT_EFF = 0.80
DEFAULT_PRESSURE_DROP = 0.01
DEFAULT_SUPERHEAT_MARGIN = Q_(5, "delta_degC")


def _as_list(value, n, name):
    """Return ``value`` as a list of length ``n`` (broadcasting scalars)."""
    if isinstance(value, (list, tuple)):
        if len(value) != n:
            raise ValueError(f"'{name}' has length {len(value)} but n_stages is {n}.")
        return list(value)
    return [value] * n


def _clamp_to_vapor(ref_state, p, t_target, margin):
    """Clamp an intercooler outlet temperature to keep the gas single phase.

    Parameters
    ----------
    ref_state : ccp.State
        A state with the same fluid as the intercooler outlet (used to read the
        fluid name and critical point).
    p : pint.Quantity
        Intercooler outlet pressure.
    t_target : pint.Quantity
        Desired outlet temperature.
    margin : pint.Quantity
        Superheat margin kept above the saturation temperature.

    Returns
    -------
    (pint.Quantity, bool)
        The (possibly raised) outlet temperature and whether it was clamped.
    """
    # Mixtures and supercritical pressures have no single saturation boundary in
    # range; a target above the critical temperature cannot condense either.
    if len(ref_state.fluid) > 1:
        return t_target, False
    if p >= ref_state.p_critical():
        return t_target, False
    if t_target.to("K") >= ref_state.T_critical().to("K"):
        return t_target, False

    try:
        t_sat = Q_(CP.PropsSI("T", "P", p.to("Pa").m, "Q", 1, ref_state._fluid), "K")
    except (ValueError, RuntimeError):
        return t_target, False

    t_min = t_sat + margin.to("kelvin")
    if t_target.to("K") < t_min:
        return t_min, True
    return t_target, False


class MultiStageCompressor:
    """Forward design of a multi-stage centrifugal compressor train.

    Parameters
    ----------
    suc : ccp.State
        Suction state of the first stage.
    flow_m : pint.Quantity
        Mass flow (constant through the train).
    speed : pint.Quantity
        Rotational speed.
    pressure_ratio : float
        Overall pressure ratio (final discharge pressure / first suction
        pressure).
    n_stages : int
        Number of compression stages.
    eff : float or list, optional
        Polytropic efficiency per stage. A scalar is applied to every stage.
        Default is ``0.80``.
    pressure_ratio_per_stage : list, optional
        Per-stage pressure ratios. Their product should equal
        ``pressure_ratio``. Default splits the overall ratio equally
        (``pressure_ratio ** (1 / n_stages)`` each).
    intercooling : bool, optional
        Whether to insert intercoolers between stages. Default ``True``.
    intercooler_T : pint.Quantity, optional
        Target intercooler outlet temperature. Default is the suction
        temperature ``suc.T()``.
    intercooler_pressure_drop : float, optional
        Fractional pressure drop across each intercooler. Default ``0.01``.
    intercooler_superheat_margin : pint.Quantity, optional
        Margin kept above saturation when clamping the intercooler outlet to
        single-phase vapor. Default ``Q_(5, "delta_degC")``.
    b, D : pint.Quantity, optional
        Impeller width and diameter per stage (scalar or list). Required only
        for the flow/head coefficients ``phi``/``psi``; they do not affect the
        thermodynamic design.
    polytropic_method : str, optional
        Polytropic method passed to :class:`ccp.point.Point`. Default is
        ``ccp.config.POLYTROPIC_METHOD``.

    Attributes
    ----------
    points : list of ccp.point.Point
        One design point per stage.
    intercoolers : list of dict
        One record per intercooler (T_in, T_out, duty, clamped flag).
    disch : ccp.State
        Final discharge state.
    power : pint.Quantity
        Total shaft power (sum over stages).
    pressure_ratio : float
        Achieved overall pressure ratio.
    """

    def __init__(
        self,
        suc,
        flow_m,
        speed,
        pressure_ratio,
        n_stages,
        eff=DEFAULT_EFF,
        pressure_ratio_per_stage=None,
        intercooling=True,
        intercooler_T=None,
        intercooler_pressure_drop=DEFAULT_PRESSURE_DROP,
        intercooler_superheat_margin=DEFAULT_SUPERHEAT_MARGIN,
        b=None,
        D=None,
        polytropic_method=None,
    ):
        if n_stages < 1:
            raise ValueError("n_stages must be at least 1.")

        self.suc = suc
        self.flow_m = flow_m
        self.speed = speed
        self.target_pressure_ratio = float(pressure_ratio)
        self.n_stages = int(n_stages)
        self.eff = _as_list(eff, self.n_stages, "eff")
        self.intercooling = intercooling
        self.intercooler_T = intercooler_T if intercooler_T is not None else suc.T()
        self.intercooler_pressure_drop = intercooler_pressure_drop
        self.intercooler_superheat_margin = intercooler_superheat_margin
        self.polytropic_method = polytropic_method
        self._geometry_given = b is not None and D is not None

        if pressure_ratio_per_stage is None:
            # Equal split, compensated for the interstage pressure drops so the
            # achieved overall pressure ratio matches the target. With n-1
            # intercoolers each dropping a fraction dp, the final discharge is
            # p0 * r**n * (1 - dp)**(n-1); solving for r recovers the target.
            n_coolers = self.n_stages - 1 if intercooling else 0
            drop_factor = (1 - intercooler_pressure_drop) ** n_coolers
            pr = (self.target_pressure_ratio / drop_factor) ** (1.0 / self.n_stages)
            self.pressure_ratio_per_stage = [pr] * self.n_stages
        else:
            self.pressure_ratio_per_stage = _as_list(
                pressure_ratio_per_stage, self.n_stages, "pressure_ratio_per_stage"
            )

        b_list = (
            _as_list(b, self.n_stages, "b") if b is not None else [None] * self.n_stages
        )
        D_list = (
            _as_list(D, self.n_stages, "D") if D is not None else [None] * self.n_stages
        )

        self.points = []
        self.intercoolers = []

        stage_suc = suc
        for i in range(self.n_stages):
            disch_p = stage_suc.p() * self.pressure_ratio_per_stage[i]
            stage_kwargs = dict(
                suc=stage_suc,
                disch_p=disch_p,
                eff=self.eff[i],
                flow_m=flow_m,
                speed=speed,
                polytropic_method=polytropic_method,
            )
            if b_list[i] is not None:
                stage_kwargs["b"] = b_list[i]
            if D_list[i] is not None:
                stage_kwargs["D"] = D_list[i]
            point = Point(**stage_kwargs)
            self.points.append(point)

            if i < self.n_stages - 1:
                if self.intercooling:
                    stage_suc = self._build_intercooler(point, i)
                else:
                    stage_suc = point.disch

        self.disch = self.points[-1].disch
        self.pressure_ratio = (self.disch.p() / suc.p()).to("dimensionless").m
        self.power = sum((p.power_shaft for p in self.points), Q_(0, "watt"))
        self.head_total = sum((p.head for p in self.points), Q_(0, "joule/kilogram"))
        self.heat_removed = sum((ic["duty"] for ic in self.intercoolers), Q_(0, "watt"))

    def _build_intercooler(self, point, stage_index):
        """Cool ``point.disch`` and return the next stage suction state."""
        disch = point.disch
        cooled_p = disch.p() * (1 - self.intercooler_pressure_drop)
        t_out, clamped = _clamp_to_vapor(
            disch, cooled_p, self.intercooler_T, self.intercooler_superheat_margin
        )
        cooled = State(p=cooled_p, T=t_out, fluid=disch.fluid)
        duty = (self.flow_m * (disch.h() - cooled.h())).to("watt")
        self.intercoolers.append(
            {
                "after_stage": stage_index + 1,
                "T_in": disch.T(),
                "T_out": cooled.T(),
                "duty": duty,
                "clamped": clamped,
            }
        )
        return cooled

    def __repr__(self):
        return (
            f"MultiStageCompressor(n_stages={self.n_stages}, "
            f"pressure_ratio={self.pressure_ratio:.4g}, "
            f"power={self.power.to('kW'):.4g~P})"
        )


def _q(quantity, units):
    """Serialize a pint quantity to a ``{"value", "units"}`` dict."""
    return {"value": quantity.to(units).m, "units": units}


def _parse_q(value, default_units):
    """Parse a number or ``{"value", "units"}`` dict into a pint quantity."""
    if isinstance(value, dict):
        return Q_(value["value"], value["units"])
    return Q_(value, default_units)


def design_from_case(case):
    """Build a :class:`MultiStageCompressor` from a case dictionary.

    See the module/CLI documentation for the case schema. Only ``fluid``,
    ``flow_m``, ``speed``, ``suction``, ``pressure_ratio`` and ``n_stages`` are
    required.

    Parameters
    ----------
    case : dict

    Returns
    -------
    MultiStageCompressor
    """
    suction = case["suction"]
    suc = State(
        p=_parse_q(suction["p"], "Pa"),
        T=_parse_q(suction["T"], "K"),
        fluid=case["fluid"],
    )

    kwargs = dict(
        suc=suc,
        flow_m=_parse_q(case["flow_m"], "kg/s"),
        speed=_parse_q(case["speed"], "RPM"),
        pressure_ratio=case["pressure_ratio"],
        n_stages=case["n_stages"],
    )
    if "eff" in case:
        kwargs["eff"] = case["eff"]
    if "pressure_ratio_per_stage" in case:
        kwargs["pressure_ratio_per_stage"] = case["pressure_ratio_per_stage"]
    if "polytropic_method" in case:
        kwargs["polytropic_method"] = case["polytropic_method"]

    geometry = case.get("geometry", {})
    if "b" in geometry:
        kwargs["b"] = _parse_q(geometry["b"], "m")
    if "D" in geometry:
        kwargs["D"] = _parse_q(geometry["D"], "m")

    ic = case.get("intercooling", {})
    if isinstance(ic, bool):
        kwargs["intercooling"] = ic
    elif ic:
        kwargs["intercooling"] = ic.get("enabled", True)
        if "T" in ic:
            kwargs["intercooler_T"] = _parse_q(ic["T"], "K")
        if "pressure_drop" in ic:
            kwargs["intercooler_pressure_drop"] = ic["pressure_drop"]
        if "superheat_margin" in ic:
            kwargs["intercooler_superheat_margin"] = _parse_q(
                ic["superheat_margin"], "delta_degC"
            )

    mc = MultiStageCompressor(**kwargs)
    mc._name = case.get("name")
    return mc


def results_to_dict(mc):
    """Serialize a designed :class:`MultiStageCompressor` to a results dict."""
    stages = []
    for i, p in enumerate(mc.points):
        stage = {
            "index": i + 1,
            "suc": {"p": _q(p.suc.p(), "MPa"), "T": _q(p.suc.T(), "degC")},
            "disch": {"p": _q(p.disch.p(), "MPa"), "T": _q(p.disch.T(), "degC")},
            "pressure_ratio": (p.disch.p() / p.suc.p()).to("dimensionless").m,
            "head": _q(p.head, "kJ/kg"),
            "eff": float(p.eff),
            "power": _q(p.power_shaft, "kW"),
            "volume_ratio": p.volume_ratio.to("dimensionless").m,
        }
        if mc._geometry_given:
            stage["phi"] = p.phi.to("dimensionless").m
            stage["psi"] = p.psi.to("dimensionless").m
        stages.append(stage)

    intercoolers = [
        {
            "after_stage": ic["after_stage"],
            "T_in": _q(ic["T_in"], "degC"),
            "T_out": _q(ic["T_out"], "degC"),
            "duty": _q(ic["duty"], "kW"),
            "clamped": ic["clamped"],
        }
        for ic in mc.intercoolers
    ]

    return {
        "name": getattr(mc, "_name", None),
        "overall": {
            "pressure_ratio": mc.pressure_ratio,
            "n_stages": mc.n_stages,
            "power": _q(mc.power, "kW"),
            "heat_removed": _q(mc.heat_removed, "kW"),
            "disch": {"p": _q(mc.disch.p(), "MPa"), "T": _q(mc.disch.T(), "degC")},
        },
        "stages": stages,
        "intercoolers": intercoolers,
    }


def run_case(case):
    """Design a single case and return its results dict."""
    return results_to_dict(design_from_case(case))


def run_cases(data):
    """Run one case dict or a ``{"cases": [...]}`` collection.

    Returns
    -------
    dict
        Either a single results dict or ``{"results": [...]}``.
    """
    if isinstance(data, dict) and "cases" in data:
        return {"results": [run_case(c) for c in data["cases"]]}
    return run_case(data)


def load_json(text):
    """Parse case JSON text and return the results dict."""
    return run_cases(json.loads(text))
