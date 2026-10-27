#!/usr/bin/env python3
"""Harbor verifier: compare the agent deliverable against golden.json."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def get_path(obj, dotted):
    current = obj
    walked = []
    for part in dotted.split("."):
        walked.append(part)
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise KeyError(".".join(walked) + " expects a list index") from exc
            if index < 0 or index >= len(current):
                raise KeyError(".".join(walked) + " is out of range")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise KeyError(".".join(walked) + " is missing")
            current = current[part]
        else:
            raise KeyError(".".join(walked) + " cannot be indexed")
    return current


def compare(actual, expected, tolerance):
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, (int, float)):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isfinite(float(actual))
            and math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=tolerance)
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(compare(a, e, tolerance) for a, e in zip(actual, expected))
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(compare(actual[key], value, tolerance) for key, value in expected.items())
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--tests", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--reward", default=None)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    tests = Path(args.tests).resolve()
    reward_path = Path(args.reward) if args.reward else workspace / "reward.txt"

    golden = json.loads((tests / "golden.json").read_text(encoding="utf-8"))
    deliverable_path = workspace / golden["deliverable"]
    details = []
    reward = 0.0

    if not deliverable_path.is_file():
        details.append({"path": "<deliverable>", "ok": False, "error": "missing " + golden["deliverable"]})
    else:
        try:
            actual = json.loads(deliverable_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            details.append({"path": "<deliverable>", "ok": False, "error": "invalid JSON: " + str(exc)})
            actual = None
        if actual is not None:
            expected = golden["expected"]
            graded = golden.get("graded_paths") or list(expected)
            tolerances = golden.get("tolerances") or {}
            passed = 0
            for path in graded:
                tolerance = float(tolerances.get(path, 0.0))
                try:
                    want = get_path(expected, path)
                    got = get_path(actual, path)
                    ok = compare(got, want, tolerance)
                    details.append({"path": path, "ok": ok, "expected": want, "actual": got})
                except KeyError as exc:
                    details.append({"path": path, "ok": False, "error": str(exc)})
                passed += 1 if details[-1]["ok"] else 0
            reward = round(passed / len(graded), 4) if graded else 0.0

    reward_path.write_text(f"{reward:.4f}\n", encoding="utf-8")
    (workspace / "verification.json").write_text(
        json.dumps({"reward": reward, "checks": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"reward={reward:.4f} checks={sum(1 for d in details if d['ok'])}/{len(details)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
