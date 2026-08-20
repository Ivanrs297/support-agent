# The autograder

Grades one checkout of the Support Agent, step by step against the
[26 steps of the guide](../README.md).

```bash
# from a virtual environment that has the project's dependencies
python autograder/grade.py

# and against the API you deployed
GRADE_BASE_URL=https://yourdomain.com GRADE_API_TOKEN=... python autograder/grade.py

python autograder/grade.py --step 9        # while you are working on one step
python autograder/grade.py --json          # for a marking script
python autograder/grade.py --self-test     # grade the grader
```

It never writes to the repository it is grading. It exits non-zero while
anything is still failing, so it can gate a submission.

---

## What it can prove, and what it cannot

Three kinds of check, and confusing them is worse than having no autograder at
all.

| Kind | What it does | What it proves |
|---|---|---|
| `static` | reads a file and looks for a decision | you **chose** something — not that it works |
| `local` | runs your code | the code **does** what the step said |
| `live` | talks to your deployed API | you **shipped** it |

The static checks exist because the alternative for steps 3 and 16–20 is
provisioning AWS from the grading machine. They are the weakest evidence here,
and the report labels them so you can see how much of a green step rests on
them. A `static` pass on step 19 means `remote-deploy.sh` contains a rollback;
it does not mean the rollback works. Only breaking it on purpose proves that,
and the guide tells you to.

The live checks are the strongest, and they are why this module insists on a
real host and a real domain. DNS, the certificate, the security group, the
container, the token, the limits, the trace — all of it is observable from
outside, and observing it proves more than reading any file.

**A step is complete when every check that ran for it passed.** Checks that were
skipped count towards neither total. A step whose checks were all skipped reads
"not assessed" rather than passing quietly, because the one thing an autograder
must never do is count a check that did not run.

---

## The checks with consequences

Two live checks change the state of your deployment, so neither runs without
`--destructive`:

- **Step 22, the lockout.** It sends wrong tokens on purpose until the API
  refuses. That locks **the grading machine's address** out for the configured
  window — fifteen minutes by default. Run the grader again inside that window
  and everything after step 21 fails for a reason that has nothing to do with
  your code. All destructive checks run last, whatever step they belong to, for
  exactly this reason. The way out is `docker compose restart api`, which resets
  the in-memory counters.
- **Step 23, the rate limit.** It spends the daily budget it is measuring.

Everything else is read-only. The whole run makes **two calls that reach a model
provider** — one `/chat` and one `/chat/stream` — and steps 13 and 26 share the
first between them rather than each buying their own.

---

## `--self-test`

```
$ python autograder/grade.py --self-test
grading the reference solution ...
grading the blank work area ...

47 offline checks: all pass on solution/, all fail on the blank work area.
```

This is the only evidence that any of the checks mean anything. It grades
`solution/` and the untouched work area and insists on green and red. A grader
that passes an empty directory is worse than no grader; one that fails the
reference solution is worse still, because it teaches students to distrust the
report and then the failures that matter get ignored too.

It found eight defects the first time it ran, and two of them are worth
repeating because they are the same mistake in different clothes:

- **A comment warning about a mistake is not the mistake.**
  `deploy/remote-deploy.sh` explains, in a comment, why it does not use
  `sed -i`. The check searched the raw text, found the warning, and reported
  the correct file as wrong. Hence `without_comments()`.
- **An import is not a use.** The token check looked for `compare_digest`
  anywhere in `security.py`. It is imported at the top of the *blank*, so the
  check passed against a file where `require_token` was still
  `raise NotImplementedError`. Hence `is_stub()`, and the rule that any check
  reading something the work area was *given* — an import, a decorator, a
  docstring — must first confirm the student wrote the thing that uses it.

Live checks are excluded from the self-test: they need a deployment, and the
point is the checks rather than anybody's host.

---

## Layout

```
autograder/
├── grade.py            the runner, the report, and --self-test
├── registry.py         Check, Result, Context, and the helpers for reading source
├── checks/
│   ├── part1_host.py        steps 1-6
│   ├── part2_agent.py       steps 7-15
│   ├── part3_deploy.py      steps 16-20
│   ├── part4_access.py      steps 21-24
│   └── part5_providers.py   steps 25-26
└── tests/              pytest autograder/tests
```

Adding a check is one decorated function next to the ones it belongs with:

```python
@check(9, "the relevance floor was calibrated, not left at zero", STATIC)
def floor_is_set(ctx: Context) -> Result:
    ...
    return bad(
        "MIN_RELEVANCE is still 0.0, so nothing is ever below the floor and "
        "'babysitting service' is answered out of the room service section"
    )
```

**Every failure carries a reason, and a test enforces it.** A red mark that only
says "missing" sends the reader to run the command by hand, and then the grader
was a slower way of getting there. Say what is wrong and what it costs.

The only dependencies are the standard library and pytest — `urllib` rather than
`requests`, `ssl` and `socket` rather than a certificate library. This grades a
repository whose argument is that a dependency needs a reason that survives
being said out loud, and "the API is slightly nicer" is not one.
