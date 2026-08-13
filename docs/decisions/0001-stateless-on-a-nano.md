# 0001 — Everything stateful stays off the instance

**Status:** accepted
**Date:** 2026-08-13

## Context

The module runs on a `t4g.nano`: 512 MiB of RAM, ~$8.84/month all in. Caddy and
the API together leave a working budget of roughly 100–150 MiB for anything else.

That budget does not fit a self-hosted vector database, a self-hosted trace
collector, or a local embedding model. `sentence-transformers` alone pulls in
PyTorch; importing scikit-learn drags in NumPy and SciPy. Any one of them ends the
conversation.

## Decision

Nothing stateful runs on the instance. Vector storage, trace collection and
embedding are managed services reached over the network, or they are not used at
all. The container holds no state between requests: the conversation arrives in
the request body, and the process can be killed and recreated at any moment —
which is exactly what a deploy does.

v2 takes the stronger version of this and keeps retrieval in-process, because the
corpus is 8 KB of Markdown and IDF term overlap over 28 sections needs no database
at all. The rule is not "always use a managed service"; it is "state does not live
here".

## Consequences

**The tension has to be said out loud.** The module's stated principle is open
source first, self-hosted where possible. This decision sends Langfuse and any
vector database to somebody else's servers. Those two commitments genuinely
conflict, and the resolution is a budget: $8.84/month buys a machine that cannot
host them. Pretending otherwise would teach students an architecture that falls
over the first time they deploy it.

**Statelessness is a feature, not a consolation.** A container that can be
replaced mid-conversation is what makes zero-downtime deploys and honest rollbacks
possible. The constraint forced a property worth having.

**Dependencies get measured, not assumed.** This project carried a rule against
the `langchain` meta-package, inherited from LangChain 0.x, where it dragged in
hundreds of MiB of integrations. In 1.x those moved to `langchain-classic` and the
package costs ~1 MB. The rule outlived its reason by a major version. Measure at
the moment of the decision; the numbers move.
