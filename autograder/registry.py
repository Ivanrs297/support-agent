"""The pieces every check is built from.

A check is a small function that answers one question about one deliverable and
says why when the answer is no. Three kinds, and the difference is the whole
point of this autograder:

- **static** reads a file and looks for a decision. It proves you chose
  something, not that it works. It is the only thing available for the steps
  whose alternative is provisioning AWS on the grader's machine.
- **local** runs your code. It proves the code does what the step said.
- **live** talks to your deployed API over the internet. It proves you actually
  shipped it, which is the one thing neither of the others can tell you.

A step is complete when every check that ran for it passed. Checks that were
skipped — no URL configured, destructive ones not opted into — count as neither,
and the report says so rather than quietly inflating the score.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

STATIC = "static"
LOCAL = "local"
LIVE = "live"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Result:
    status: Status
    detail: str = ""


def ok(detail: str = "") -> Result:
    return Result(Status.PASS, detail)


def bad(detail: str) -> Result:
    """A failure always carries a reason. A bare red mark teaches nothing."""
    return Result(Status.FAIL, detail)


def skip(detail: str) -> Result:
    return Result(Status.SKIP, detail)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

PARTS: dict[str, str] = {
    "I": "The host",
    "II": "The agent",
    "III": "Continuous deployment",
    "IV": "A door, a lock, and a meter",
    "V": "Two providers, and the receipt",
}

# Every step of the README appears here, including the ones with nothing to
# automate. Leaving them out would renumber the report against the guide, and a
# student comparing the two would have to do the arithmetic themselves.
STEPS: dict[int, tuple[str, str]] = {
    1: ("I", "Fork the repository and get it running locally"),
    2: ("I", "An account, a key pair, a security group"),
    3: ("I", "Provision the host from user-data"),
    4: ("I", "Point your domain at it"),
    5: ("I", "Caddy, and a certificate you did not have to think about"),
    6: ("I", "The first container"),
    7: ("II", "Settings that fail at import"),
    8: ("II", "The knowledge base"),
    9: ("II", "Retrieval that knows when to say nothing"),
    10: ("II", "Exact lookup, which fails differently"),
    11: ("II", "The system prompt"),
    12: ("II", "The ReAct loop"),
    13: ("II", "/health and /chat"),
    14: ("II", "Streaming, and the failure that has no status code"),
    15: ("II", "The image"),
    16: ("III", "GitHub OIDC to AWS"),
    17: ("III", "The instance profile"),
    18: ("III", "Build the image in CI"),
    19: ("III", "Roll out over SSM, with a health gate"),
    20: ("III", "Rollback"),
    21: ("IV", "A shared bearer token"),
    22: ("IV", "Five wrong tokens lock the address"),
    23: ("IV", "Two rate limits"),
    24: ("IV", "A face"),
    25: ("V", "A second provider"),
    26: ("V", "The receipt"),
}


@dataclass(frozen=True)
class Check:
    step: int
    name: str
    kind: str
    fn: Callable[["Context"], Result]
    destructive: bool = False


CHECKS: list[Check] = []


def check(step: int, name: str, kind: str, *, destructive: bool = False):
    """Register one check against one step.

    Decorator rather than a list literal so a check lives next to the code that
    explains it, and adding one means touching a single place.
    """

    def register(fn: Callable[["Context"], Result]) -> Callable:
        assert step in STEPS, f"step {step} is not in the guide"
        CHECKS.append(Check(step, name, kind, fn, destructive))
        return fn

    return register


# --------------------------------------------------------------------------
# What a check is handed
# --------------------------------------------------------------------------


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: str

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None


@dataclass
class Context:
    """The repository under test, and the deployment it claims to have.

    `memo` exists for one reason: every live call to /chat spends the student's
    money and their daily quota. Steps 13 and 26 both need one answered
    question, so they share a single call rather than each buying their own.
    """

    root: Path
    base_url: str | None = None
    token: str | None = None
    include_destructive: bool = False
    timeout: float = 30.0
    memo: dict = field(default_factory=dict)

    # ---- files ----------------------------------------------------------

    def read(self, relative: str) -> str | None:
        """File contents, or None when it is not there."""
        path = self.root / relative
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def exists(self, relative: str) -> bool:
        return (self.root / relative).exists()

    # ---- the deployment -------------------------------------------------

    @property
    def host(self) -> str | None:
        if not self.base_url:
            return None
        return self.base_url.split("://", 1)[-1].split("/")[0].split(":")[0]

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        token: str | None = "",
        timeout: float | None = None,
    ) -> Response:
        """One HTTP call against the deployment.

        `token=""` means "use the configured one"; `token=None` means send no
        Authorization header at all, which is what step 21 needs to test.

        urllib rather than requests. This tool grades a repository whose whole
        argument is that a dependency needs a reason that survives being said
        out loud, and "the API is slightly nicer" is not one.
        """
        url = f"{self.base_url.rstrip('/')}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        chosen = self.token if token == "" else token
        if chosen:
            headers["Authorization"] = f"Bearer {chosen}"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return Response(
                    response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    response.read().decode("utf-8", "replace"),
                )
        except urllib.error.HTTPError as error:
            # A 4xx is data here, not an exception. Half of these checks are
            # asserting that the API refuses something correctly.
            return Response(
                error.code,
                {k.lower(): v for k, v in error.headers.items()},
                error.read().decode("utf-8", "replace"),
            )

    def stream(self, path: str, payload: dict, *, limit: int = 400) -> list[dict]:
        """Read a server-sent event stream into a list of parsed payloads."""
        url = f"{self.base_url.rstrip('/')}{path}"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        events: list[dict] = []
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    events.append({"unparseable": line})
                if len(events) >= limit:
                    break
        return events

    def resolves(self, hostname: str) -> str | None:
        """The address a name resolves to, or None."""
        try:
            return socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)[0][4][0]
        except socket.gaierror:
            return None

    def certificate(self, hostname: str) -> dict | None:
        """The TLS certificate the host presents, or None if the handshake fails."""
        context = ssl.create_default_context()
        try:
            with socket.create_connection((hostname, 443), timeout=self.timeout) as raw:
                with context.wrap_socket(raw, server_hostname=hostname) as tls:
                    return tls.getpeercert()
        except (OSError, ssl.SSLError):
            return None

    def port_open(self, hostname: str, port: int) -> bool:
        try:
            with socket.create_connection((hostname, port), timeout=self.timeout):
                return True
        except OSError:
            return False

    # ---- running the student's code -------------------------------------

    def python(self, snippet: str) -> tuple[int, str, str]:
        """Run a snippet inside the student's `project/` directory.

        In a subprocess, always. The work area's modules raise at import until
        they are written, so importing them in this process would take the whole
        run down and report one error instead of twenty-six rows.
        """
        environment = {
            **os.environ,
            "GROQ_API_KEY": "test-key-not-used",
            "API_TOKEN": "test-token-not-real",
            "PYTHONPATH": ".",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=self.root / "project",
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

    def pytest(self, target: str, expression: str | None = None) -> tuple[int, str]:
        """Run part of the student's own suite and return (failures, summary)."""
        environment = {
            **os.environ,
            "GROQ_API_KEY": "test-key-not-used",
            "API_TOKEN": "test-token-not-real",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        command = [sys.executable, "-m", "pytest", "-q", "--no-header", target]
        if expression:
            command += ["-k", expression]
        completed = subprocess.run(
            command,
            cwd=self.root / "project",
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        lines = output.splitlines()

        summary = ""
        for line in reversed(lines):
            if "passed" in line or "failed" in line or "error" in line.lower():
                summary = line.strip().lstrip("= ").rstrip("= ")
                break
        summary = summary or "no output from pytest"

        if completed.returncode == 0:
            return 0, summary

        # "4 errors in 0.20s" tells the reader that something is wrong and
        # nothing about what, which sends them to run pytest by hand — and then
        # the grader was just a slower way of getting there. Carry the first
        # real assertion or exception line with the count.
        reason = next(
            (
                line.strip()[2:].strip()
                for line in lines
                if line.startswith("E ") or line.startswith("E\t")
            ),
            "",
        )
        return completed.returncode, f"{summary} — {reason}" if reason else summary


# --------------------------------------------------------------------------
# Reading source without being fooled by it
# --------------------------------------------------------------------------


def without_comments(text: str) -> str:
    """Drop whole-line comments from shell or YAML.

    Written after the grader failed its own reference solution twice. Both
    `deploy/remote-deploy.sh` and the deploy workflow explain, in a comment, the
    exact mistake they are avoiding — "Not `sed -i`", "`set -o pipefail` is a
    bashism" — and a check searching the raw text finds the warning and reports
    it as the crime.

    Line-based rather than a lexer: a `#` inside a quoted string on its own line
    is not something these files do, and a real parser for two languages is not
    worth carrying to answer this question.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def body_of(text: str, name: str) -> str | None:
    """The source of one Python function, or None if it is not there.

    Indentation-based, which is enough: these are module-level defs in files
    this tool also verifies are syntactically valid by importing them.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(rf"^(async\s+)?def\s+{re.escape(name)}\s*\(", line):
            collected = [line]
            for following in lines[index + 1:]:
                if following.strip() and not following.startswith((" ", "\t")):
                    break
                collected.append(following)
            return "\n".join(collected)
    return None


def is_stub(text: str, name: str) -> bool:
    """Whether a function is still the blank the work area shipped.

    The self-test exists to catch checks that pass against unwritten code, and
    the usual cause is a check reading something the work area was given —
    an import, a decorator, a docstring — rather than something the student
    wrote. Guard those checks with this.
    """
    body = body_of(text, name)
    if body is None:
        return True
    return "raise NotImplementedError" in body
