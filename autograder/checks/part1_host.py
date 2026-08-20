"""Steps 1-6 — a machine on the internet, with a certificate, serving a container.

Most of this part cannot be run: the alternative to a static check is
provisioning an EC2 instance from the grader's machine. What *can* be run is
everything downstream of the deployment, and once a student has a domain that is
a great deal — DNS, the certificate, the security group and the container are all
observable from outside, and observing them proves more than reading any file.
"""

import re
from datetime import datetime, timezone

from ..registry import LIVE, STATIC, Context, Result, bad, check, ok, skip


def _needs_url(ctx: Context) -> Result | None:
    if not ctx.base_url:
        return skip("no deployment configured — set GRADE_BASE_URL")
    return None


# --------------------------------------------------------------------------
# 1. The repository
# --------------------------------------------------------------------------


@check(1, "the work areas are all present", STATIC)
def layout(ctx: Context) -> Result:
    expected = [
        "project/app/main.py",
        "project/tests",
        "project/requirements-dev.txt",
        "deploy/docker-compose.yml",
        "infra",
        ".github/workflows",
    ]
    missing = [path for path in expected if not ctx.exists(path)]
    if missing:
        return bad(f"missing: {', '.join(missing)}")
    return ok("every directory the guide refers to exists")


# --------------------------------------------------------------------------
# 2. The security group
# --------------------------------------------------------------------------


@check(2, "port 443 is reachable from the internet", LIVE)
def https_reachable(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    if not ctx.port_open(ctx.host, 443):
        return bad(f"nothing accepts a connection on {ctx.host}:443")
    return ok("the security group lets HTTPS in")


@check(2, "port 80 is reachable, which ACME needs", LIVE)
def http_reachable(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    if not ctx.port_open(ctx.host, 80):
        return bad(
            "nothing accepts a connection on port 80. Certificates will renew "
            "into a wall in 60 days, and the failure will look like Caddy's fault"
        )
    return ok("the HTTP-01 challenge has a way in")


# --------------------------------------------------------------------------
# 3. The bootstrap
# --------------------------------------------------------------------------

# Each entry is (what the guide asked for, a pattern that shows it was done,
# what goes wrong when it was not). The third column is the whole value of a
# static check: a red mark that only says "missing" is a worse teacher than the
# comment it was checking for.
BOOTSTRAP_DECISIONS = [
    (
        "waits for cloud-init or the apt lock",
        r"cloud-init\s+status|lock-frontend|fuser",
        "an unguarded apt-get fails with a lock error that reads like a network problem",
    ),
    (
        "removes the snap SSM agent",
        r"snap\s+remove|purge\s+-y\s+snapd|apt-get\s+purge.*snapd",
        "snapd alone costs ~90 MiB of RAM, which on 512 MiB decides whether this fits",
    ),
    (
        "creates swap",
        r"mkswap|swapon|fallocate",
        "without swap an out-of-memory moment is a kill, not a slow minute",
    ),
    (
        "installs compose v2 as its own package",
        r"docker-compose-v2|docker-compose-plugin",
        "'docker compose up -d' fails with \"unknown shorthand flag: 'd'\"",
    ),
    (
        "installs git",
        r"apt-get install[^\n]*\bgit\b",
        "the deploy checks out the commit being released and cannot",
    ),
    (
        "caps the Docker log driver",
        r"max-size",
        "unbounded json-file logs fill a 15 GiB disk quietly, then everything fails at once",
    ),
    (
        "writes a completion sentinel",
        r"bootstrap-done",
        "nothing distinguishes a finished bootstrap from one that died halfway",
    ),
    (
        "traces itself with set -eux",
        r"set\s+-eux",
        "you read this back from cloud-init-output.log with no terminal; without -x nothing says which line failed",
    ),
]


@check(3, "bootstrap.sh makes every decision the step listed", STATIC)
def bootstrap(ctx: Context) -> Result:
    text = ctx.read("deploy/bootstrap.sh")
    if not text or "TODO" in text and len(text) < 3000:
        return bad("deploy/bootstrap.sh is still the work area stub")
    missing = [
        f"{name} ({why})"
        for name, pattern, why in BOOTSTRAP_DECISIONS
        if not re.search(pattern, text, re.IGNORECASE)
    ]
    if missing:
        return bad("; ".join(missing[:3]) + (f"; +{len(missing) - 3} more" if len(missing) > 3 else ""))
    return ok(f"all {len(BOOTSTRAP_DECISIONS)} decisions present")


# --------------------------------------------------------------------------
# 4. The domain
# --------------------------------------------------------------------------


@check(4, "the apex and www both resolve", LIVE)
def dns(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    host = ctx.host
    apex = ctx.resolves(host)
    if apex is None:
        return bad(f"{host} does not resolve")
    bare = host[4:] if host.startswith("www.") else host
    www = ctx.resolves(f"www.{bare}")
    if www is None:
        return bad(f"{host} resolves to {apex}, but www.{bare} does not resolve")
    return ok(f"{host} and www.{bare} both resolve to {apex}")


# --------------------------------------------------------------------------
# 5. Caddy and TLS
# --------------------------------------------------------------------------


@check(5, "the Caddyfile serves two names and proxies by service name", STATIC)
def caddyfile(ctx: Context) -> Result:
    text = ctx.read("deploy/Caddyfile")
    if not text or "TODO" in text:
        return bad("deploy/Caddyfile is still the work area stub")

    # The domain itself is not checkable — every student brings their own. What
    # is checkable is that there are two of them, which is what makes the
    # certificate cover www as well as the apex.
    site = re.search(r"^\s*([^\s#{][^{]*)\{", text, re.MULTILINE)
    if not site:
        return bad("no site block found")
    names = [n.strip() for n in site.group(1).split(",") if n.strip()]
    if len(names) < 2:
        return bad(f"only one hostname in the site block ({names[0]}); www will have no certificate")

    proxy = re.search(r"reverse_proxy\s+(\S+)", text)
    if not proxy:
        return bad("no reverse_proxy directive")
    target = proxy.group(1)
    if re.match(r"^\d+\.\d+\.\d+\.\d+", target) or target.startswith("localhost"):
        return bad(
            f"reverse_proxy points at {target}. Proxy to the compose service name "
            "instead — that is what Docker's embedded DNS resolves on the network"
        )
    return ok(f"{len(names)} hostnames, proxying to {target}")


@check(5, "compose gives Caddy port 80, port 443 and a volume for its certificates", STATIC)
def caddy_service(ctx: Context) -> Result:
    text = ctx.read("deploy/docker-compose.yml")
    if not text or "TODO" in text:
        return bad("deploy/docker-compose.yml is still the work area stub")
    problems = []
    if '"80:80"' not in text and "- 80:80" not in text:
        problems.append("port 80 is not published, so the ACME HTTP-01 challenge cannot complete")
    if '"443:443"' not in text and "- 443:443" not in text:
        problems.append("port 443 is not published")
    if not re.search(r":/data\b", text):
        problems.append(
            "nothing is mounted on Caddy's /data, so every restart requests a fresh "
            "certificate and burns through Let's Encrypt's weekly rate limit"
        )
    if not re.search(r"Caddyfile:/etc/caddy/Caddyfile", text):
        problems.append("the Caddyfile is not mounted into the container")
    return bad("; ".join(problems)) if problems else ok("ports, config and certificate volume all present")


@check(5, "both names present a valid certificate", LIVE)
def certificate(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason

    # Checked per hostname rather than by reading one certificate's SAN list.
    # Caddy issues a separate certificate per subject, so the apex certificate
    # naming only the apex is correct — and a check that demanded both names on
    # one certificate would report a working deployment as broken.
    host = ctx.host
    bare = host[4:] if host.startswith("www.") else host
    found = []
    for name in (bare, f"www.{bare}"):
        cert = ctx.certificate(name)
        if cert is None:
            return bad(
                f"the TLS handshake with {name} failed — no certificate, one that does "
                "not verify, or nothing listening on 443"
            )
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (expires - datetime.now(timezone.utc)).days
        if days < 0:
            return bad(f"{name}'s certificate expired {-days} days ago")
        covered = {value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"}
        if name not in covered:
            return bad(f"{name} presents a certificate for {', '.join(sorted(covered)) or 'nothing'}")
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "unknown issuer")
        found.append(f"{name} ({issuer}, {days}d)")
    return ok(", ".join(found))


# --------------------------------------------------------------------------
# 6. The first container
# --------------------------------------------------------------------------


@check(6, "the api service is not published, not named, and capped", STATIC)
def api_service(ctx: Context) -> Result:
    text = ctx.read("deploy/docker-compose.yml")
    if not text or "TODO" in text:
        return bad("deploy/docker-compose.yml is still the work area stub")
    problems = []
    if re.search(r"^\s*container_name:", text, re.MULTILINE):
        problems.append(
            "container_name is declared. Docker container names are global rather "
            "than scoped to the compose project, so a stack left running from an "
            "earlier lecture owns that name and every deploy after it dies on a conflict"
        )
    if not re.search(r"^\s*expose:", text, re.MULTILINE):
        problems.append(
            "the api service does not use `expose`. If it publishes a port instead, "
            "the API is on the public internet beside Caddy and the X-Forwarded-For "
            "the lockout depends on becomes attacker-controlled"
        )
    if not re.search(r"^\s*env_file:", text, re.MULTILINE):
        problems.append("no env_file, so a missing configuration produces a running container that fails on the first guest")
    if len(re.findall(r"mem_limit:", text)) < 2:
        problems.append("both services need a mem_limit — it is what stops one of them taking the host down")
    return bad("; ".join(problems)) if problems else ok("expose, env_file, mem_limit, and no container_name")


@check(6, "/health answers over TLS", LIVE)
def health(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    response = ctx.request("/health", token=None)
    if response.status != 200:
        return bad(f"GET /health returned {response.status}")
    body = response.json()
    if not isinstance(body, dict) or "status" not in body:
        return bad(f"/health did not return an object with a status field: {response.body[:120]}")
    return ok(f"200, {body}")


@check(6, "/health needs no token", LIVE)
def health_unauthenticated(ctx: Context) -> Result:
    if (reason := _needs_url(ctx)) is not None:
        return reason
    if ctx.request("/health", token=None).status != 200:
        return bad(
            "/health refuses an unauthenticated caller. The container healthcheck "
            "calls it, so it would fail for the wrong reasons and a deploy would "
            "roll itself back over a working image"
        )
    return ok("open, as the healthcheck requires")
