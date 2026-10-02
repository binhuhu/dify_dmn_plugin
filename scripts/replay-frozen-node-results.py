#!/usr/bin/env python3
"""Run a supplied frozen runner unchanged and compare its complete stdout bytes.

Keep private handoffs and output directories outside this repository. The runner
defines serialization; this wrapper never parses or reserializes result bytes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path)
    parser.add_argument("baseline_plugin", type=Path)
    parser.add_argument("candidate_plugin", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runner", default="run_eval.py")
    parser.add_argument("--baseline-output", default="rc1_out.json")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    handoff, output = args.handoff.resolve(), args.output.resolve()
    for path in (handoff, output):
        if path == repo or repo in path.parents:
            parser.error("Private handoff and outputs must be outside the repository")
    runner = (handoff / args.runner).resolve()
    baseline = (handoff / args.baseline_output).resolve()
    if not runner.is_file() or not baseline.is_file():
        parser.error("Supplied runner and baseline output must exist")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "boundary": "unchanged supplied runner stdout",
        "runs": {},
        "comparisons": {},
    }
    report["runner_sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    for label, plugin in (
        ("baseline", args.baseline_plugin),
        ("candidate", args.candidate_plugin),
    ):
        with (
            (output / f"{label}.json").open("wb") as stdout,
            (output / f"{label}.stderr").open("wb") as stderr,
        ):
            result = subprocess.run(
                [sys.executable, str(runner), str(plugin.resolve())],
                cwd=handoff,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        raw = (output / f"{label}.json").read_bytes()
        report["runs"][label] = {
            "exit_code": result.returncode,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if result.returncode or not raw:
            (output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
            return 1
    for label, left, right in (
        ("original_to_baseline", baseline, output / "baseline.json"),
        ("baseline_to_candidate", output / "baseline.json", output / "candidate.json"),
    ):
        result = subprocess.run(
            ["cmp", str(left), str(right)], capture_output=True, check=False
        )
        report["comparisons"][label] = {"cmp_exit_code": result.returncode}
        (output / f"{label}.cmp").write_bytes(result.stdout + result.stderr)
    passed = all(x["cmp_exit_code"] == 0 for x in report["comparisons"].values())
    report["pass"] = passed
    (output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: both raw byte comparisons" if passed else "FAIL: raw bytes differ")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
