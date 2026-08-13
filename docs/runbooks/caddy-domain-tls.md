# Hello World Behind Caddy — Domain + TLS Smoke Test

**Goal:** get a containerized FastAPI app serving over HTTPS on a custom domain, with automatic Let's Encrypt certificates, on a `t4g.nano`.

**Why this before the real agent:** this step verifies DNS, firewall rules, the reverse proxy, and TLS all at once. If you deploy the agent first and something breaks, you won't know which of the four layers failed. Get a green padlock on a trivial app, then swap in the real one.

**Prerequisite:** a provisioned EC2 host with Docker and an Elastic IP. See the provisioning runbook.

---

## Table of contents

1. [Configure DNS](#1-configure-dns)
2. [Install the Compose plugin](#2-install-the-compose-plugin)
3. [Project structure](#3-project-structure)
4. [The files](#4-the-files)
5. [Build and run](#5-build-and-run)
6. [Verify](#6-verify)
7. [Record your baseline](#7-record-your-baseline)
8. [Troubleshooting](#8-troubleshooting)
9. [Swapping in the real agent](#9-swapping-in-the-real-agent)
10. [Daily operations](#10-daily-operations)

---

## 1. Configure DNS

Get the instance's public address:

```bash
ssh agent
curl -s ifconfig.me
```

### Namecheap

1. **Domain List** → **Manage** next to your domain
2. On the **Domain** tab, confirm **Nameservers** is set to **Namecheap BasicDNS**. If it says Custom DNS or points elsewhere, change it first — otherwise the records you add are ignored.
3. Go to the **Advanced DNS** tab
4. Delete the defaults. Namecheap ships a `CNAME` to `parkingpage.namecheap.com` and often a URL Redirect record — both will interfere.
5. **Add New Record**:

| Type | Host | Value | TTL |
|---|---|---|---|
| A Record | `@` | your Elastic IP | Automatic |
| A Record | `www` | your Elastic IP | Automatic |

6. Click the **green checkmark** on each row to save. Easy to miss — the rows look saved before you do.

> `@` means the root domain. Do not type the full domain name in the Host field.
>
> Do not enable URL Redirect or the parking page. They intercept port 80 and break the ACME HTTP-01 challenge.
>
> PremiumDNS is an upsell. BasicDNS is fine for this.

### Cloudflare

Same two A records. Set proxy status to **DNS only** (grey cloud) until the certificate is issued — with the proxy on, Let's Encrypt can't reach your origin for the challenge. You can enable it afterwards.

### Wait for propagation

```bash
dig +short yourdomain.com
dig +short www.yourdomain.com

# Bypass your local resolver's cache
dig +short @1.1.1.1 yourdomain.com
```

Both must return your Elastic IP. Namecheap typically takes 5–30 minutes.

> **Do not proceed until this resolves.** Starting Caddy early burns Let's Encrypt rate limit attempts on a challenge that cannot succeed.

---

## 2. Install the Compose plugin

Ubuntu's `docker.io` package does not include Compose v2. Without it you get:

```
unknown shorthand flag: 'd' in -d
```

Docker doesn't recognize `compose` as a subcommand, so it parses the rest as stray flags.

```bash
sudo apt-get update
sudo apt-get install -y docker-compose-v2
docker compose version
```

If that package isn't available, install the plugin directly:

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

> Note `aarch64` in the URL — that's the ARM64 build. On x86 hosts use `docker-compose-linux-x86_64`.

> `docker compose` (space, v2 plugin) is not the same as `docker-compose` (hyphen, the deprecated Python v1). Use the plugin.

---

## 3. Project structure

```
~/stack/
├── Dockerfile
├── docker-compose.yml
├── Caddyfile
└── app/
    ├── main.py
    └── requirements.txt
```

```bash
mkdir -p ~/stack/app && cd ~/stack
```

> **The `Dockerfile` and `docker-compose.yml` go in the parent directory, not inside `app/`.** The Dockerfile does `COPY app/requirements.txt`, and that path is relative to the build context. Running compose from inside `app/` fails to find the files.
>
> Always run `docker compose` from `~/stack`.

---

## 4. The files

### `app/main.py`

```python
import os
import platform
from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="Agent Host — Hello World")


@app.get("/")
def root():
    return {
        "message": "Hello from the agent host",
        "arch": platform.machine(),
        "python": platform.python_version(),
        "hostname": os.uname().nodename,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok"}
```

The `arch` field is the useful part: `aarch64` in the response confirms the ARM64 chain is correct end to end — AMI, instance type, base image, and build.

### `app/requirements.txt`

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
```

> Pin versions. An unpinned `requirements.txt` means your image is not reproducible, which defeats the point of containerizing.

### `Dockerfile`

```dockerfile
# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /build
COPY app/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.12-slim

# Non-root user: least privilege, and it costs nothing
RUN useradd -m -u 1000 appuser

COPY --from=builder /install /usr/local
WORKDIR /app
COPY app/ .

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**Design notes:**

- **Multi-stage build** keeps pip's build toolchain out of the final image. Compare `docker images` before and after to see the difference.
- **`--workers 1`** — each uvicorn worker is a full Python process. On 512 MB, extra workers don't fit. Scale horizontally later, not vertically here.
- **Non-root `appuser`** — if the container is compromised, the attacker isn't root inside it.
- **`HEALTHCHECK`** gives Docker a real signal about whether the app is serving, not just whether the process exists.

### `Caddyfile`

```
yourdomain.com, www.yourdomain.com {
    encode zstd gzip
    reverse_proxy api:8000
}
```

That's the whole TLS configuration. Caddy obtains and renews Let's Encrypt certificates automatically for every hostname listed here.

**If you want `www` to redirect instead of serving the same content** (better for SEO, avoids duplicate content):

```
www.yourdomain.com {
    redir https://yourdomain.com{uri} permanent
}

yourdomain.com {
    encode zstd gzip
    reverse_proxy api:8000
}
```

### `docker-compose.yml`

```yaml
services:
  caddy:
    image: caddy:2-alpine
    container_name: caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - api
    mem_limit: 128m

  api:
    build: .
    container_name: api
    restart: unless-stopped
    expose:
      - "8000"
    mem_limit: 192m

volumes:
  caddy_data:
  caddy_config:
```

**Design notes:**

- **`caddy_data` is not optional.** Certificates live there. Without the volume, every restart requests a fresh certificate and you'll hit Let's Encrypt's rate limit (50 certificates per registered domain per week).
- **`api` uses `expose`, not `ports`.** The container is reachable only from Docker's internal network. Nothing reaches the API without going through Caddy — there's no way to bypass TLS.
- **`mem_limit` on both.** On 512 MB you want a runaway container to die alone rather than take the host with it.
- **Port 80 must stay open**, not just 443. Let's Encrypt's HTTP-01 challenge uses it, and Caddy uses it for the HTTPS redirect.

---

## 5. Build and run

```bash
cd ~/stack
docker compose up -d --build
docker compose logs -f caddy
```

Watch for `certificate obtained successfully`. It takes 10–40 seconds.

`Ctrl+C` exits the log stream without stopping anything.

---

## 6. Verify

```bash
# HTTP redirects to HTTPS
curl -I http://yourdomain.com
# expected: HTTP/1.1 308 Permanent Redirect

# The app responds over TLS
curl https://yourdomain.com
# expected: {"message":"Hello from the agent host","arch":"aarch64",...}

# Health endpoint
curl https://www.yourdomain.com/health
# expected: {"status":"ok"}

# Certificate details
curl -vI https://yourdomain.com 2>&1 | grep -i 'issuer\|subject'
```

Open it in a browser and confirm the padlock.

**What a green result proves:** DNS resolves, the security group allows 80 and 443, Caddy is routing to the container, Let's Encrypt validated the domain, and the ARM64 image runs. Four layers verified in one test.

---

## 7. Record your baseline

```bash
docker stats --no-stream
free -h
```

Reference numbers from a working `t4g.nano` deployment:

| Container | Memory | Limit |
|---|---|---|
| caddy | ~25 MiB | 128 MiB |
| api | ~33 MiB | 192 MiB |
| **Total** | **~58 MiB** | |

With ~408 MiB total RAM on the instance and roughly 180 MiB used by the OS, that leaves **~150 MiB of headroom** for the agent container.

**Write this number down.** When you add LangGraph, the delta against this baseline tells you exactly what the agent costs in memory — and whether the nano holds or you need to move up to `t4g.small`. Right-sizing is a measurement, not a hunch.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `unknown shorthand flag: 'd'` | Compose v2 plugin missing | See §2 |
| Caddy logs `challenge failed` | DNS not propagated, or Cloudflare proxy is on | `dig +short yourdomain.com`; set grey cloud |
| `connection refused` on 443 | No HTTPS rule in the security group | Add inbound 443 from `0.0.0.0/0` |
| `timeout` on port 80 | No HTTP rule | Add inbound 80 — ACME needs it, not just 443 |
| `too many certificates already issued` | Rate limit hit | Use the staging endpoint below |
| `no such file or directory` on build | Running compose from `app/` | `cd ~/stack` first |
| Browser shows the old parking page | DNS cache | Wait, or test with `curl` |

### Debug without burning rate limit

Add to the top of the `Caddyfile` (global options block, before any site blocks):

```
{
    acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```

Staging certificates are untrusted in browsers — they'll show a warning — but they prove the whole chain works. Remove the block and restart once everything is green:

```bash
docker compose restart caddy
```

### Useful commands

```bash
docker compose ps                    # what's running
docker compose logs caddy --tail 50  # proxy and ACME logs
docker compose logs api --tail 50    # application logs
docker compose exec api sh           # shell inside the container
docker compose down                  # stop everything (volumes survive)
docker compose up -d --build         # rebuild and restart
```

---

## 9. Swapping in the real agent

When you replace the hello world with the LangGraph agent, three things change:

**1. The build moves to CI.** A `pip install` of LangChain will not complete in 512 MB. From that point on, GitHub Actions builds the image and the instance only pulls it:

```yaml
  api:
    image: ghcr.io/YOUR_ORG/YOUR_REPO:latest    # replaces `build: .`
```

**2. Secrets come from Parameter Store**, never a `.env` file on disk:

```bash
export ANTHROPIC_API_KEY=$(aws ssm get-parameter \
  --name /agent/anthropic_api_key --with-decryption \
  --query Parameter.Value --output text)
```

**3. Stateful services move off-instance.** With ~150 MiB of headroom, the vector DB and observability stack cannot live here. Use managed free tiers (Supabase/Neon with pgvector, Qdrant Cloud, Langfuse Cloud). The upside: this forces a genuinely stateless container, which is the correct architecture before you talk about horizontal scaling.

The `Caddyfile` barely changes — just point `reverse_proxy` at the new service, or add path routing if the UI and API are separate containers:

```
yourdomain.com {
    encode zstd gzip
    handle_path /api/* {
        reverse_proxy agent-api:8000
    }
    handle {
        reverse_proxy ui:8501
    }
}
```

> **Path-based routing avoids CORS entirely.** If the UI is on one origin and the API on another, the browser demands CORS headers, and you get the classic "works with curl, fails in the browser." Same-origin sidesteps it.

---

## 10. Daily operations

```bash
# Is everything up?
docker compose ps

# Resource usage right now
docker stats --no-stream && free -h

# Disk pressure
df -h / && docker system df

# Reclaim space
docker system prune -af

# Restart after editing the Caddyfile
docker compose restart caddy

# Full redeploy
docker compose down && docker compose up -d --build
```

Certificates renew automatically — Caddy handles it roughly 30 days before expiry with no intervention. As long as the `caddy_data` volume survives, so does your TLS.