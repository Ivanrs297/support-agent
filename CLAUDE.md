# CLAUDE.md

Conventions for this repository. They apply to every contributor, human or
otherwise.

## Language

**Everything committed to this repo is written in English** — code, identifiers,
comments, docstrings, Markdown, commit messages, branch names, PR titles and
descriptions. Conversation about the work can happen in any language; the artifact
is in English.

## Keep it simple

The host is a `t4g.nano`: 512 MiB of RAM, ~150 MiB free once Caddy and the API are
running. Simplicity is not a style preference here, it is what fits.

- Prefer the smallest thing that works. No framework where a function does.
- No abstraction introduced for a second use case that does not exist yet.
- No dependency added without a reason that survives being said out loud. On this
  machine, `pip install` has a memory cost.
- Delete code rather than commenting it out. Git remembers.

## Branching and pull requests

**Never push directly to `main`.** `main` only advances through merged pull
requests.

```bash
git switch -c feat/rag-retriever
# ... work, commit ...
git push -u origin feat/rag-retriever
# open a PR against main
```

One branch per feature or fix. Prefixes: `feat/`, `fix/`, `docs/`, `chore/`,
`refactor/`. Branch names in English, kebab-case.

Keep pull requests small enough to actually review. A PR that touches the agent,
the deploy config and the runbooks at once should have been three.

## Commit messages

English, imperative mood, subject line under ~72 characters. If the change needs
explanation, add a body that says **why**, not what — the diff already says what.

```
Add health check to the retriever client

The container reported healthy while the vector DB connection was
already refusing, so a deploy could go green with a broken agent.
```

**No attribution trailers.** Commit messages carry no `Co-Authored-By` line, no
"generated with" footer, and no tool branding of any kind.

## Versioning

Every version of the project is recorded as a **tag on GitHub**, so the history is
readable at a glance without digging through commits.

```bash
git tag -a v2 -m "v2 — <what this version is>"
git push origin v2
```

Tag `main` after the pull request that completes a version is merged. The tag
message states what the version contains.

**Every version must be described in the root `README.md`.** A tag alone says a
version exists; it does not say what changed or why anyone should care. The README
carries a `## Versions` section, newest first, and it is updated **in the same pull
request that completes the version** — never afterwards, or it never happens.

Each entry states what the version added, and what it deliberately left out. What
is missing at a given point is as instructive as what is present: this repository
is teaching material, and a reader should be able to follow how the system was
built up rather than only see where it landed.

Order of operations when closing a version:

1. Update the `## Versions` section in `README.md` inside the feature branch.
2. Merge the pull request.
3. Tag the resulting commit on `main` and push the tag.

| Tag | Contents |
|---|---|
| `v6` | The repository became the exercise: work areas at the root, `solution/` as the explained reference, and a 26-step guide. |
| `v5` | Groq and Bedrock switchable per request, and a per-run trace of steps, tokens and cost. |
| `v4` | Bearer token, address lockout, per-token and daily rate limits, and a browser interface at `/ui`. |
| `v3` | Continuous deployment: GitHub Actions builds arm64 images to ghcr and rolls them out over SSM, with automatic rollback. |
| `v2` | Hotel Aurora support agent: ReAct over Groq, documentation search and reservation lookup, `/chat` and `/chat/stream`. |
| `v1` | AWS host provisioned from user-data, custom domain, Caddy with automatic TLS. No agent yet. |

## Repository layout

This repository is teaching material, so it holds the system twice.

| Directory | Contents |
|---|---|
| `project/` | The Support Agent. **Work area** — signatures, docstrings, TODOs. |
| `deploy/` | Compose file, Caddyfile, host bootstrap. **Work area.** |
| `infra/` | Infrastructure definitions. **Work area.** |
| `.github/workflows/` | CI/CD. **Work area**, except the two `*-solution.yml` that deploy the reference. |
| `solution/` | The finished system, every folder, explained. **What actually gets deployed.** |
| `labs/` | One lab per lecture, added alongside its slides. |
| `docs/` | `runbooks/` for procedures, `decisions/` for architecture rationale. |

**A change to behaviour lands in `solution/` and in the work area's blank.**
Fixing only the solution leaves a student building something the reference no
longer does; fixing only the blank ships nothing. When the two disagree, the
solution is right — it is what production runs — and the blank's docstring is the
thing to correct.

Every blank carries a `STEP n` marker naming the section of the root `README.md`
that explains it. Renumbering the steps means renumbering the markers.

Slides are private and are never committed — `.gitignore` blocks them, along with
`.env`, keys, and Terraform state.
