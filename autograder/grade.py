#!/usr/bin/env python3
"""Grade one checkout of the Support Agent, step by step.

    python autograder/grade.py                       # everything that needs no deployment
    GRADE_BASE_URL=https://yourdomain.com \
    GRADE_API_TOKEN=... python autograder/grade.py   # and the live API too
    python autograder/grade.py --step 9              # while you work on one step
    python autograder/grade.py --json                # for a marking script
    python autograder/grade.py --self-test           # grade the grader

Run it from a virtual environment that has the project's dependencies, because
the local checks import the student's modules.

Nothing here writes to the repository it is grading.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autograder import checks  # noqa: F401,E402 — importing fills the registry
from autograder.registry import (  # noqa: E402
    CHECKS,
    LIVE,
    PARTS,
    STEPS,
    Context,
    Status,
)

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m",
)

MARK = {Status.PASS: "ok  ", Status.FAIL: "FAIL", Status.SKIP: "skip"}
COLOUR = {Status.PASS: GREEN, Status.FAIL: RED, Status.SKIP: YELLOW}


def paint(text: str, colour: str, enabled: bool) -> str:
    return f"{colour}{text}{RESET}" if enabled else text


def run(ctx: Context, only_step: int | None = None) -> list[dict]:
    """Run every applicable check and return one record each.

    Destructive checks run last, whatever step they belong to. The lockout check
    makes the deployment refuse this address for the next quarter of an hour, so
    anything scheduled after it would be graded against a locked-out API and fail
    for a reason that has nothing to do with the student.
    """
    selected = [c for c in CHECKS if only_step is None or c.step == only_step]
    ordered = sorted(selected, key=lambda c: (c.destructive, c.step, c.kind))

    records = []
    for entry in ordered:
        try:
            result = entry.fn(ctx)
        except Exception as failure:  # noqa: BLE001 — a broken check is a result, not a crash
            from autograder.registry import bad

            result = bad(f"the check itself raised {type(failure).__name__}: {failure}")
        records.append(
            {
                "step": entry.step,
                "part": STEPS[entry.step][0],
                "title": STEPS[entry.step][1],
                "check": entry.name,
                "kind": entry.kind,
                "destructive": entry.destructive,
                "status": result.status.value,
                "detail": result.detail,
            }
        )
    return records


def summarise(records: list[dict]) -> dict:
    """Per-step and overall totals.

    A step is complete when every check that actually ran for it passed. A step
    whose checks were all skipped is "not assessed" and counts towards neither
    total — inflating the score with checks that never ran is the one thing an
    autograder must not do.
    """
    steps: dict[int, dict] = {}
    for record in records:
        entry = steps.setdefault(
            record["step"],
            {"step": record["step"], "part": record["part"], "title": record["title"],
             "passed": 0, "failed": 0, "skipped": 0, "checks": []},
        )
        entry["checks"].append(record)
        entry[{"pass": "passed", "fail": "failed", "skip": "skipped"}[record["status"]]] += 1

    for entry in steps.values():
        ran = entry["passed"] + entry["failed"]
        entry["assessed"] = ran > 0
        entry["complete"] = ran > 0 and entry["failed"] == 0

    assessed = [s for s in steps.values() if s["assessed"]]
    return {
        "steps": steps,
        "steps_complete": sum(1 for s in assessed if s["complete"]),
        "steps_assessed": len(assessed),
        "steps_total": len(STEPS),
        "checks_passed": sum(1 for r in records if r["status"] == "pass"),
        "checks_run": sum(1 for r in records if r["status"] != "skip"),
        "checks_skipped": sum(1 for r in records if r["status"] == "skip"),
    }


def report(records: list[dict], summary: dict, ctx: Context, colour: bool) -> None:
    print()
    print(paint("Support Agent — step by step", BOLD, colour))
    print(f"  repository  {ctx.root}")
    print(f"  deployment  {ctx.base_url or paint('not configured — live checks will be skipped', YELLOW, colour)}")
    print()

    # Padded to the longest title rather than a fixed width: truncating
    # "Streaming, and the failure that has no status code" to fit a column
    # costs the reader more than a ragged right edge does.
    width = max(len(t) for _, t in STEPS.values())

    current_part = None
    for step in sorted(summary["steps"]):
        entry = summary["steps"][step]
        if entry["part"] != current_part:
            current_part = entry["part"]
            print(paint(f"Part {current_part} — {PARTS[current_part]}", BOLD, colour))

        if not entry["assessed"]:
            status, tally = Status.SKIP, "not assessed"
        elif entry["complete"]:
            status, tally = Status.PASS, f"{entry['passed']}/{entry['passed']}"
        else:
            status, tally = Status.FAIL, f"{entry['passed']}/{entry['passed'] + entry['failed']}"

        mark = paint(MARK[status], COLOUR[status], colour)
        print(f"  {mark} {step:>2}. {entry['title']:<{width}}  {tally}")

        # Only failures and skipped-for-a-reason are worth the reader's lines.
        # A passing check that says nothing useful is noise between the ones
        # that do.
        for record in entry["checks"]:
            if record["status"] == "pass":
                continue
            child = paint(MARK[Status(record["status"])], COLOUR[Status(record["status"])], colour)
            print(f"       {child} {record['check']}")
            if record["detail"]:
                for line in wrap(record["detail"], 88):
                    print(paint(f"            {line}", DIM, colour))
        print()

    print(paint("─" * 72, DIM, colour))
    print(
        f"  {summary['steps_complete']}/{summary['steps_assessed']} steps complete"
        f"  ·  {summary['checks_passed']}/{summary['checks_run']} checks passed"
        + (f"  ·  {summary['checks_skipped']} skipped" if summary["checks_skipped"] else "")
    )
    if summary["steps_assessed"] < summary["steps_total"]:
        not_assessed = summary["steps_total"] - summary["steps_assessed"]
        print(paint(f"  {not_assessed} steps could not be assessed at all — see the skipped checks above.", YELLOW, colour))
    print()


def wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


def self_test(root: Path) -> int:
    """Grade the reference solution and the blank work area, and compare.

    This is the only evidence that the checks mean anything. A grader that
    passes an empty directory is worse than no grader, and a grader that fails
    the reference solution is worse still — it teaches students to distrust it,
    and then the failures that matter get ignored too.

    Live checks are excluded: they need a deployment, and the point here is the
    checks themselves rather than anybody's host.
    """
    if not (root / "solution" / "project").exists():
        print("no solution/ to compare against; --self-test needs the reference in place")
        return 1

    print("grading the reference solution ...")
    reference = run(Context(root=root / "solution"))
    print("grading the blank work area ...")
    blank = run(Context(root=root))

    offline = lambda rs: [r for r in rs if r["kind"] != LIVE]  # noqa: E731
    reference, blank = offline(reference), offline(blank)

    problems = []

    for record in reference:
        if record["status"] == "fail":
            problems.append(
                f"  the reference FAILS step {record['step']} · {record['check']}\n"
                f"      {record['detail']}"
            )

    passing_on_blank = [r for r in blank if r["status"] == "pass"]
    # One exception, and it is a real one: step 1 checks that the directories
    # exist, which is true of the work area by construction. Anything else
    # passing against unwritten code means the check is not looking at the code.
    surprises = [r for r in passing_on_blank if r["step"] != 1]
    for record in surprises:
        problems.append(
            f"  step {record['step']} · {record['check']} PASSES against the blank work area\n"
            f"      {record['detail']}"
        )

    total = len(reference)
    print()
    if problems:
        print(f"{len(problems)} problem(s) in {total} offline checks:\n")
        print("\n".join(problems))
        return 1
    print(f"{total} offline checks: all pass on solution/, all fail on the blank work area.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default=".", help="the checkout to grade (default: the current directory)")
    parser.add_argument("--url", default=os.environ.get("GRADE_BASE_URL"), help="the deployed API, e.g. https://yourdomain.com")
    parser.add_argument("--token", default=os.environ.get("GRADE_API_TOKEN"), help="its API_TOKEN")
    parser.add_argument("--step", type=int, help="grade one step only")
    parser.add_argument(
        "--destructive",
        action="store_true",
        help="also run the checks with consequences: the lockout check blocks this "
        "address for the configured window, and the rate limit check spends the "
        "budget it measures",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--self-test", action="store_true", help="grade solution/ and the blank work area, and compare")
    parser.add_argument("--no-colour", action="store_true")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if args.self_test:
        return self_test(root)

    if args.step is not None and args.step not in STEPS:
        parser.error(f"step {args.step} is not in the guide; steps run 1 to {max(STEPS)}")

    ctx = Context(
        root=root,
        base_url=args.url.rstrip("/") if args.url else None,
        token=args.token,
        include_destructive=args.destructive,
    )

    records = run(ctx, args.step)
    summary = summarise(records)

    if args.json:
        print(json.dumps({
            "repository": str(root),
            "deployment": ctx.base_url,
            "steps_complete": summary["steps_complete"],
            "steps_assessed": summary["steps_assessed"],
            "steps_total": summary["steps_total"],
            "checks_passed": summary["checks_passed"],
            "checks_run": summary["checks_run"],
            "checks_skipped": summary["checks_skipped"],
            "checks": records,
        }, indent=2))
    else:
        report(records, summary, ctx, colour=not args.no_colour and sys.stdout.isatty())

    # Non-zero while anything is still failing, so this can gate a submission.
    return 0 if summary["checks_passed"] == summary["checks_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
