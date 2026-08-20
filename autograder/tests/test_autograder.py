"""Tests for the grader itself.

An autograder is a program that tells people they are wrong, so being wrong is
expensive here in a way it is not elsewhere: a false failure sends a student
looking for a bug that does not exist, and a false pass ships a broken deploy
with a green tick on it.

Every case below came from a defect `grade.py --self-test` actually found. The
self-test is the real safety net — it grades the reference solution and the
blank work area and insists on green and red respectively — and these are the
regressions worth pinning individually.

    pytest autograder/tests
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from autograder import checks  # noqa: F401,E402 — importing fills the registry
from autograder.grade import run, summarise  # noqa: E402
from autograder.registry import (  # noqa: E402
    CHECKS,
    STEPS,
    Context,
    Status,
    body_of,
    is_stub,
    without_comments,
)


class TestReadingSource:
    def test_a_comment_warning_about_a_mistake_is_not_the_mistake(self):
        # This is the defect that failed the reference solution twice.
        # remote-deploy.sh explains in a comment why it does not use `sed -i`,
        # and the check found the warning and reported it as the crime.
        script = "\n".join([
            "  # Not `sed -i`: GNU takes no argument for it and BSD requires one.",
            "  sed 's|^IMAGE_TAG=.*|IMAGE_TAG=$1|' .env > \"$tmp\"",
        ])
        assert "sed -i" in script
        assert "sed -i" not in without_comments(script)

    def test_code_survives_comment_stripping(self):
        assert "IMAGE_TAG" in without_comments("# a comment\nIMAGE_TAG=abc\n")

    def test_body_of_stops_at_the_next_definition(self):
        source = "def first():\n    return 1\n\n\ndef second():\n    return 2\n"
        body = body_of(source, "first")
        assert "return 1" in body
        assert "return 2" not in body

    def test_body_of_returns_none_for_a_function_that_is_not_there(self):
        assert body_of("def other(): pass\n", "missing") is None


class TestStubDetection:
    # The other half of the self-test's job: a check that reads an import, a
    # decorator or a docstring passes against code nobody has written yet.
    SOURCE = (
        "from secrets import compare_digest\n"
        "\n"
        "def written(token):\n"
        "    return compare_digest(token, 'x')\n"
        "\n"
        "def blank(token):\n"
        '    """STEP 21.2"""\n'
        "    raise NotImplementedError\n"
    )

    def test_an_unwritten_function_is_a_stub(self):
        assert is_stub(self.SOURCE, "blank")

    def test_a_written_one_is_not(self):
        assert not is_stub(self.SOURCE, "written")

    def test_a_missing_function_counts_as_a_stub(self):
        assert is_stub(self.SOURCE, "never_defined")

    def test_an_import_alone_does_not_prove_the_function_uses_it(self):
        # `compare_digest` appears at the top of the file whether or not the
        # function that has to call it was ever written.
        assert "compare_digest" in self.SOURCE
        assert "compare_digest" not in (body_of(self.SOURCE, "blank") or "")


class TestRegistry:
    def test_every_check_belongs_to_a_step_in_the_guide(self):
        assert all(check.step in STEPS for check in CHECKS)

    def test_the_guide_has_26_steps_and_no_gaps(self):
        assert sorted(STEPS) == list(range(1, 27))

    def test_every_step_is_reported_even_with_nothing_to_automate(self):
        # Dropping the unautomatable steps would renumber the report against the
        # README, leaving the reader to do the arithmetic.
        records = run(Context(root=Path.cwd()))
        assert set(summarise(records)["steps"]) | {2, 4} == set(STEPS) | {2, 4}


class TestOrdering:
    def test_destructive_checks_run_last(self):
        # The lockout check makes the deployment refuse this address for the
        # next quarter of an hour. Anything scheduled after it would be graded
        # against a locked-out API and fail for a reason that is not the
        # student's.
        records = run(Context(root=Path.cwd()))
        flags = [r["destructive"] for r in records]
        assert flags == sorted(flags), "a non-destructive check runs after a destructive one"

    def test_destructive_checks_are_skipped_unless_asked_for(self):
        records = run(Context(root=Path.cwd(), base_url="https://example.invalid", token="x"))
        for record in records:
            if record["destructive"]:
                assert record["status"] == "skip"


class TestScoring:
    def make(self, statuses: list[str]) -> list[dict]:
        return [
            {"step": 9, "part": "II", "title": STEPS[9][1], "check": f"c{i}",
             "kind": "local", "destructive": False, "status": s, "detail": ""}
            for i, s in enumerate(statuses)
        ]

    def test_a_step_is_complete_only_when_everything_that_ran_passed(self):
        assert summarise(self.make(["pass", "pass"]))["steps"][9]["complete"]
        assert not summarise(self.make(["pass", "fail"]))["steps"][9]["complete"]

    def test_a_skipped_check_neither_completes_nor_fails_a_step(self):
        summary = summarise(self.make(["pass", "skip"]))
        assert summary["steps"][9]["complete"]
        assert summary["checks_run"] == 1

    def test_a_step_with_nothing_but_skips_is_not_assessed(self):
        # The one thing an autograder must never do is count a check that did
        # not run towards a score.
        summary = summarise(self.make(["skip", "skip"]))
        assert not summary["steps"][9]["assessed"]
        assert summary["steps_assessed"] == 0
        assert summary["steps_complete"] == 0


class TestResults:
    def test_every_failure_carries_a_reason(self):
        # A bare red mark teaches nothing, and this is the property that makes
        # the report usable without the source open beside it.
        records = run(Context(root=Path.cwd()))
        silent = [r for r in records if r["status"] == "fail" and len(r["detail"]) < 20]
        assert not silent, f"failures with no useful reason: {[r['check'] for r in silent]}"

    def test_a_check_that_raises_is_reported_not_crashed(self):
        from autograder.registry import CHECKS as registry, Check

        def explode(ctx):
            raise RuntimeError("boom")

        broken = Check(step=1, name="deliberately broken", kind="static", fn=explode)
        registry.append(broken)
        try:
            records = run(Context(root=Path.cwd()), only_step=1)
            entry = next(r for r in records if r["check"] == "deliberately broken")
            assert entry["status"] == "fail"
            assert "RuntimeError" in entry["detail"]
        finally:
            registry.remove(broken)
