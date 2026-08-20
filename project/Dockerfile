# STEP 15 — see README §15.
#
# Two stages, and the reason is the memory budget. A single-stage image carries
# pip, its cache, and the build toolchain into production; on a 512 MiB host
# that is not an aesthetic complaint.
#
# What this file has to end up doing:
#
#   1. Build stage: install requirements into a prefix you can copy out of.
#   2. Runtime stage: start from a clean python:3.12-slim, copy the installed
#      packages across, copy `app/`, and nothing else.
#   3. Create a non-root user and USER it before CMD. Least privilege, and it
#      costs nothing.
#   4. EXPOSE 8000 and run uvicorn with `--workers 1`.
#
#      The worker count is not a performance knob here. Every rate limit and
#      lockout counter in app/security.py lives in this process's memory, so two
#      workers means two independent sets of counters and limits that are
#      silently double what they claim.
#
#   5. A HEALTHCHECK that hits /health. This is what the deploy's health gate
#      reads and what tells the rollback whether to fire, so it is load-bearing
#      rather than decorative — but it starts a second Python interpreter inside
#      the container's cgroup every 30 seconds. README §15 has the measurement.
#
# Order the layers so that the ones that change least come first. Copying
# requirements.txt and installing before copying `app/` means an edit to the
# agent does not reinstall LangChain.

FROM python:3.12-slim AS builder

# TODO
