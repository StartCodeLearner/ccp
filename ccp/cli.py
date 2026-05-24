"""Command-line interface for ccp.

Currently exposes the multi-stage compressor design workflow::

    ccp design case.json -o results.json

``case.json`` may hold a single case object or a ``{"cases": [...]}`` list. Use
``-`` for the input/output path to read from stdin / write to stdout.
"""

import argparse
import json
import sys


def _fmt(q):
    return f"{q['value']:.4g} {q['units']}"


def _summarize(result, stream):
    name = result.get("name") or "case"
    overall = result["overall"]
    print(f"\n=== {name} ===", file=stream)
    print(
        f"stages: {overall['n_stages']}  "
        f"overall PR: {overall['pressure_ratio']:.4g}  "
        f"power: {_fmt(overall['power'])}  "
        f"heat removed: {_fmt(overall['heat_removed'])}",
        file=stream,
    )
    print(
        f"discharge: {_fmt(overall['disch']['p'])}, {_fmt(overall['disch']['T'])}",
        file=stream,
    )
    for s in result["stages"]:
        line = (
            f"  stage {s['index']}: PR {s['pressure_ratio']:.3f}  "
            f"head {_fmt(s['head'])}  eff {s['eff']:.3f}  "
            f"power {_fmt(s['power'])}  "
            f"disch {_fmt(s['disch']['T'])}"
        )
        print(line, file=stream)
    for ic in result["intercoolers"]:
        flag = " (clamped to saturation)" if ic["clamped"] else ""
        print(
            f"  intercooler after stage {ic['after_stage']}: "
            f"{_fmt(ic['T_in'])} -> {_fmt(ic['T_out'])}, "
            f"duty {_fmt(ic['duty'])}{flag}",
            file=stream,
        )


def _cmd_design(args):
    # Import here so `ccp --help` stays fast and does not pull in CoolProp.
    from ccp.compressor_design import run_cases

    if args.case == "-":
        data = json.load(sys.stdin)
    else:
        with open(args.case) as f:
            data = json.load(f)

    output = run_cases(data)

    results = output["results"] if "results" in output else [output]
    for result in results:
        _summarize(result, sys.stderr)

    text = json.dumps(output, indent=2)
    if args.output == "-":
        print(text)
    else:
        with open(args.output, "w") as f:
            f.write(text + "\n")
        print(f"\nWrote results to {args.output}", file=sys.stderr)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="ccp", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    design = sub.add_parser(
        "design",
        help="design a multi-stage compressor from a JSON case file",
    )
    design.add_argument("case", help="path to case JSON (or '-' for stdin)")
    design.add_argument(
        "-o",
        "--output",
        default="-",
        help="path to write results JSON (default '-' for stdout)",
    )
    design.set_defaults(func=_cmd_design)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
