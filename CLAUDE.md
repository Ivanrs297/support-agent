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

| Tag | Contents |
|---|---|
| `v1` | AWS host provisioned from user-data, custom domain, Caddy with automatic TLS. No agent yet. |

## Repository layout

| Directory | Contents |
|---|---|
| `project/` | The Support Agent. The only thing that gets deployed. |
| `deploy/` | Compose file, Caddyfile, host bootstrap. |
| `labs/` | One lab per lecture, added alongside its slides. |
| `docs/` | `runbooks/` for procedures, `decisions/` for architecture rationale. |
| `infra/` | Infrastructure definitions. |

Slides are private and are never committed — `.gitignore` blocks them, along with
`.env`, keys, and Terraform state.
