# Deploying an Agent Host on AWS — Step-by-Step Runbook

**Target:** a single `t4g.nano` EC2 instance running Ubuntu 24.04 (arm64), Docker, and a custom domain with automatic TLS — running 24/7 for roughly **$9/month**.

**Audience:** AI Engineering Fellowship — Deployment module, Lecture 1 & 3.

---

## Table of contents

1. [Before you start](#1-before-you-start)
2. [Cost guardrails](#2-cost-guardrails)
3. [Register a domain](#3-register-a-domain)
4. [Create the IAM instance role](#4-create-the-iam-instance-role)
5. [Create the security group](#5-create-the-security-group)
6. [Create an SSH key pair](#6-create-an-ssh-key-pair)
7. [Launch the instance](#7-launch-the-instance)
8. [Allocate an Elastic IP](#8-allocate-an-elastic-ip)
9. [Point the domain at the instance](#9-point-the-domain-at-the-instance)
10. [Configure SSH on your laptop](#10-configure-ssh-on-your-laptop)
11. [Verify the bootstrap](#11-verify-the-bootstrap)
12. [Store secrets in Parameter Store](#12-store-secrets-in-parameter-store)
13. [Set up OIDC for GitHub Actions](#13-set-up-oidc-for-github-actions)
14. [Troubleshooting](#14-troubleshooting)
15. [Cost breakdown](#15-cost-breakdown)
16. [Teardown](#16-teardown)

---

## 1. Before you start

You need:

- An AWS account with billing enabled
- A terminal with `ssh` (macOS/Linux native; Windows: PowerShell or WSL)
- A GitHub account (for the CI/CD pipeline later)

**Pick one region and never leave it.** Security groups, AMIs, key pairs, and Elastic IPs are all regional — resources scattered across regions is the single most common source of "I can't find the thing I just created."

This runbook uses `us-east-1` (N. Virginia). If you prefer `us-east-2` (Ohio), that works too — pricing for `t4g` is effectively identical. Just be consistent.

> **Console in Spanish?** AWS localization is inconsistent — some menus are translated, others are not, sometimes on the same screen. A glossary is in [Appendix A](#appendix-a--console-labels-english--spanish).

---

## 2. Cost guardrails

**Do this first, before launching anything.**

### 2.1 Create a budget

Console → **Billing and Cost Management** → **Budgets** → **Create budget**

| Field | Value |
|---|---|
| Budget type | Cost budget |
| Period | Monthly |
| Amount | `$15` |
| Alert thresholds | 50%, 80%, 100% |
| Email | your address |

On Free Plan accounts (created on or after July 15, 2025), creating a budget also counts as one of the onboarding activities that earn additional credits.

### 2.2 Enable free tier alerts

**Billing and Cost Management** → **Billing preferences** → check **Receive AWS Free Tier alerts**.

### 2.3 Know which plan you're on

- **Account created before July 15, 2025** → legacy Free Tier. Check remaining eligibility under **Billing** → **Free Tier**. The 12-month window cannot be extended.
- **Account created on or after July 15, 2025** → new model. Check **Billing** → **Credits** for your balance and expiry. The Free Plan lasts up to 6 months or until credits run out, whichever comes first, and **AWS closes the account automatically** at that point. Upgrade to the Paid Plan before then if you want to keep the deployment.

---

## 3. Register a domain

**Recommended: Cloudflare Registrar** — sells at cost (~$10–11/year for a `.com`, no renewal markup) and includes free DNS hosting. This avoids the $0.50/month Route 53 hosted zone charge.

**Alternative: Route 53** → **Registered domains** → **Register domains**. A `.com` runs ~$14/year plus $0.50/month for the hosted zone.

This is the only up-front purchase in the whole setup. Everything else on AWS is pay-as-you-go, billed at the end of the month.

---

## 4. Create the IAM instance role

This role lets the instance talk to Systems Manager (for Session Manager access and CI/CD deploys) and read secrets.

**IAM** → **Roles** → **Create role**

1. **Trusted entity type:** AWS service
2. **Use case:** EC2 → **Next**
3. **Permissions:** attach the managed policy `AmazonSSMManagedInstanceCore`
4. **Role name:** `ec2-agent-role` → **Create role**

Then add an inline policy for secrets. Open the role → **Permissions** → **Add permissions** → **Create inline policy** → **JSON** tab:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:us-east-1:*:parameter/agent/*"
    }
  ]
}
```

Name it `agent-read-secrets`.

> Adjust the region in the ARN if you are not using `us-east-1`. The `/agent/*` prefix is what scopes this to least privilege — the instance cannot read any other parameter in the account.

---

## 5. Create the security group

**EC2** → **Network & Security** → **Security groups** → **Create security group**

- **Name:** `agent-host-sg`
- **Description:** `HTTP/HTTPS + SSH from my IP`
- **VPC:** default

**Inbound rules:**

| Type | Port | Source | Why |
|---|---|---|---|
| HTTP | 80 | Anywhere-IPv4 (`0.0.0.0/0`) | Let's Encrypt HTTP-01 challenge + redirect |
| HTTPS | 443 | Anywhere-IPv4 (`0.0.0.0/0`) | the agent API and UI |
| SSH | 22 | **My IP** | admin access |

**Leave outbound rules at the default** (all traffic allowed).

> **Never open port 22 to `0.0.0.0/0`.** Automated login attempts start within minutes and consume CPU and RAM you don't have on a nano. "My IP" pins your current address — if your ISP gives you a dynamic IP, you'll need to update this rule when it changes. Check yours with `curl ifconfig.me`.

> **Session Manager works without port 22 at all.** Keeping SSM configured alongside SSH gives you a way back in if you lose the key, change networks, or break `sshd_config`.

---

## 6. Create an SSH key pair

**EC2** → **Network & Security** → **Key pairs** → **Create key pair**

| Field | Value |
|---|---|
| Name | `agent-host-key` |
| Key pair type | ED25519 |
| Private key format | `.pem` |

The private key downloads **once**. If you lose it, you cannot re-download it.

```bash
mv ~/Downloads/agent-host-key.pem ~/.ssh/
chmod 400 ~/.ssh/agent-host-key.pem
```

> A key pair can only be attached **at launch**. If you launch without one, your only way in is Session Manager — from which you can append a public key to `~/.ssh/authorized_keys` manually.

---

## 7. Launch the instance

**EC2** → **Instances** → **Launch instances**

### 7.1 Basic configuration

| Field | Value |
|---|---|
| Name | `agent-host` |
| AMI | **Ubuntu Server 24.04 LTS** |
| Architecture | **64-bit (Arm)** ← critical |
| Instance type | `t4g.nano` |
| Key pair | `agent-host-key` |

> **The architecture is not a preference.** `t4g` instances are AWS Graviton (ARM). If you select an x86 AMI, `t4g.nano` disappears from the instance type list with no explanation. Everything downstream must match: Docker images built for `linux/amd64` will die on this host with `exec format error`.

> Select **Ubuntu Server**, not **Ubuntu Pro**. Pro adds a per-hour licensing charge that on a nano can approach the cost of the instance itself.

### 7.2 Network settings → Edit

| Field | Value |
|---|---|
| VPC | default |
| Subnet | any public subnet |
| Auto-assign public IP | **Enable** |
| Firewall | Select existing security group → `agent-host-sg` |

> **No NAT Gateway.** A NAT Gateway costs ~$33/month on its own — more than everything else here combined. A public subnet with a public IP is the right choice for this workload.

### 7.3 Configure storage

| Field | Value |
|---|---|
| Size | **15** GiB |
| Volume type | **gp3** |

The default is 8 GiB `gp2`. Change both. `gp3` is ~20% cheaper and faster at baseline.

15 GiB breaks down as roughly: Ubuntu base ~2.5 GB, swapfile 2 GB, Docker ~5–6 GB, logs and headroom ~4 GB.

> Leave **File systems** set to **None**. That section is for network-attached storage (EFS, FSx, Mountpoint for S3) — all billed separately and all unnecessary here. The root volume is already EBS, formatted `ext4` by the AMI. There is no filesystem to choose.

### 7.4 Advanced details

Scroll down and set:

| Field | Value |
|---|---|
| IAM instance profile | `ec2-agent-role` |
| Credit specification | **Standard** (not Unlimited) |
| User data | the script in §7.5 |

> **Credit specification matters.** In Unlimited mode, sustained CPU above baseline is billed separately at ~$0.05/vCPU-hour and can multiply your bill without warning. Standard mode throttles instead — the right trade-off for a fixed education budget.

> If you forget the IAM instance profile, you can attach it later while the instance is running. If you forget the user data, you can run the script by hand over SSH.

### 7.5 User data (bootstrap script)

Paste this into the **User data** field. It must start with `#!/bin/bash` on line 1 — a leading space or blank line makes cloud-init ignore the whole thing.

Save it in your repo as `bootstrap.sh` so it's version-controlled.

```bash
#!/bin/bash
set -eux

# Wait for cloud-init and the apt lock to clear.
# unattended-upgrades and apt-daily fire on first boot and will otherwise
# collide with this script.
cloud-init status --wait || true
for i in $(seq 1 30); do
  fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
  sleep 10
done

# ---------- SSM agent ----------
# Ubuntu AMIs ship the SSM agent as a snap. Replace it with the .deb and
# drop snapd entirely — that reclaims ~90 MB of RAM, roughly a quarter of
# what this instance has.
snap remove amazon-ssm-agent || true
apt-get update -y
curl -fsSL -o /tmp/ssm.deb \
  https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_arm64/amazon-ssm-agent.deb
dpkg -i /tmp/ssm.deb
systemctl enable --now amazon-ssm-agent
rm -f /tmp/ssm.deb
apt-get purge -y snapd || true
rm -rf /var/cache/snapd /root/snap /home/ubuntu/snap

# ---------- 2 GB swap ----------
# Non-negotiable on 512 MB of RAM. Turns an OOM kill (service down) into
# latency degradation (service slow).
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=60'          >  /etc/sysctl.d/99-swap.conf
echo 'vm.vfs_cache_pressure=50'  >> /etc/sysctl.d/99-swap.conf
sysctl -p /etc/sysctl.d/99-swap.conf

# ---------- Docker ----------
apt-get install -y docker.io
systemctl enable --now docker
usermod -aG docker ubuntu

# Cap container logs — unbounded json-file logs will fill the disk.
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
systemctl restart docker

# ---------- Maintenance ----------
# Reduce unnecessary writes on the root filesystem
sed -i 's|\(\s/\s\+ext4\s\+\)defaults|\1defaults,noatime|' /etc/fstab

# Weekly cleanup of dangling images (Sundays 03:00)
echo '0 3 * * 0 root docker system prune -af --filter "until=168h"' \
  > /etc/cron.d/docker-prune
chmod 644 /etc/cron.d/docker-prune

# Automatic security patches
apt-get install -y unattended-upgrades
systemctl enable --now unattended-upgrades

# Sentinel file — the single signal that the whole script completed.
echo "bootstrap OK $(date -Is)" > /var/log/bootstrap-done.log
```

**For x86 (`t3a.nano`):** change `debian_arm64` to `debian_amd64` in the download URL.

> **Why the sentinel file matters.** `set -eux` aborts on the first error. A partially-executed bootstrap produces an instance that boots fine and accepts SSH while silently missing Docker or swap. The sentinel is the only cheap way to know the difference.

Click **Launch instance**.

---

## 8. Allocate an Elastic IP

**EC2** → **Network & Security** → **Elastic IPs** → **Allocate Elastic IP address** → **Allocate**

Select it → **Actions** → **Associate Elastic IP address** → choose `agent-host` → **Associate**.

> Since 2024, **all** public IPv4 addresses are billed at ~$0.005/hour — Elastic and auto-assigned alike. There is no price difference. The only distinction: an Elastic IP keeps billing while the instance is stopped, an auto-assigned one does not.
>
> For a 24/7 host, use an Elastic IP: same cost, and the address survives stop/start so your DNS record never breaks.
>
> For an instance that runs only during class hours, use the auto-assigned IP plus dynamic DNS (DuckDNS, or a startup script that updates the record via API).

---

## 9. Point the domain at the instance

In your DNS provider (Cloudflare, or Route 53), create an **A record**:

| Field | Value |
|---|---|
| Type | A |
| Name | `agent` (gives you `agent.yourdomain.com`) |
| Value | your Elastic IP |
| Proxy status (Cloudflare) | **DNS only** (grey cloud) |
| TTL | Auto |

> Keep it **DNS only** until TLS is issued — Caddy needs the Let's Encrypt HTTP-01 challenge to reach the origin directly. You can enable the proxy afterwards.

Verify propagation:

```bash
dig +short agent.yourdomain.com
```

### TLS

Use **Caddy** as a reverse proxy in a container — it obtains and renews Let's Encrypt certificates automatically with a two-line config. Alternatively, **Cloudflare Tunnel** gives you free TLS, hides the origin IP, and lets you close inbound ports entirely.

> **Do not use ACM.** The certificate is free, but it requires an ALB or CloudFront in front — that's $16+/month, which destroys the budget.

---

## 10. Configure SSH on your laptop

Add an alias so you don't retype the key path every time.

**macOS / Linux** — edit `~/.ssh/config`:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
nano ~/.ssh/config
```

**Windows** — `C:\Users\YOUR_USER\.ssh\config` (no extension; watch out for Notepad appending `.txt`). If you use WSL, edit WSL's `~/.ssh/config`, not Windows'.

```
Host agent
    HostName YOUR.ELASTIC.IP.HERE
    User ubuntu
    IdentityFile ~/.ssh/agent-host-key.pem
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

```bash
chmod 600 ~/.ssh/config
```

Connect:

```bash
ssh agent
```

**What the non-obvious options do:**

- `IdentitiesOnly yes` — offer only this key. Without it, SSH tries every key in your agent one by one, and the server may cut you off with "too many authentication failures" before reaching the right one.
- `ServerAliveInterval` / `ServerAliveCountMax` — keepalive every 60s, disconnect after 3 failures. Stops idle sessions from freezing mid-debug.

**The alias also works for file transfer:**

```bash
scp docker-compose.yml agent:~/
rsync -avz ./app/ agent:~/app/
```

**Tunneling internal dashboards** — to reach something bound to localhost on the instance (Langfuse, for example) without exposing a port publicly, add to the block:

```
    LocalForward 3000 localhost:3000
```

Then open `http://localhost:3000` in your browser. This is the correct way to access internal tooling without touching the security group.

> The username is `ubuntu` on Ubuntu AMIs, `ec2-user` on Amazon Linux. Never `root`.

---

## 11. Verify the bootstrap

Give the instance 2–3 minutes after launch, then:

```bash
ssh agent
```

Run all five checks:

```bash
# 1. Did the bootstrap complete?
cat /var/log/bootstrap-done.log
# expected: bootstrap OK 2026-08-13T11:04:22+00:00

# 2. Is swap active?
free -h
# expected: Swap: 2.0Gi

# 3. Is Docker working, and is it the right architecture?
docker run --rm hello-world
# expected: "Hello from Docker!" and "(arm64v8)"

# 4. Is Session Manager available as a fallback?
sudo systemctl is-active amazon-ssm-agent
# expected: active

# 5. Confirm architecture and filesystem
uname -m          # expected: aarch64
df -Th /          # expected: ext4, ~15G
```

If `docker run` returns a permission error, the `docker` group hasn't applied to your session yet — `exit` and reconnect.

**Measure your baseline before deploying anything:**

```bash
free -h
docker stats --no-stream
```

Note the `available` column, not `free` — buff/cache is reclaimable under pressure. That number tells you how much room the agent container actually has, and whether the nano holds or you need to move up to `t4g.small`. Right-sizing is a measurement, not a hunch.

---

## 12. Store secrets in Parameter Store

**Systems Manager** → **Application Management** → **Parameter Store** → **Create parameter**

| Field | Value |
|---|---|
| Name | `/agent/anthropic_api_key` |
| Tier | **Standard** (free) |
| Type | **SecureString** |
| Value | your key |

Repeat for each secret: `/agent/langfuse_public_key`, `/agent/langfuse_secret_key`, etc.

> Create these **in the console**, not via CLI — otherwise the key values land in your shell history.

> Standard-tier Parameter Store is free. Secrets Manager charges $0.40/secret/month. For this workload, Parameter Store is the correct choice.

Read them from the instance (the IAM role from §4 authorizes this):

```bash
aws ssm get-parameter --name /agent/anthropic_api_key --with-decryption \
  --query Parameter.Value --output text
```

---

## 13. Set up OIDC for GitHub Actions

This lets the CI/CD pipeline deploy without storing long-lived AWS access keys in GitHub.

### 13.1 Register the identity provider

**IAM** → **Identity providers** → **Add provider** → **OpenID Connect**

| Field | Value |
|---|---|
| Provider URL | `https://token.actions.githubusercontent.com` |
| Audience | `sts.amazonaws.com` |

### 13.2 Create the deploy role

**IAM** → **Roles** → **Create role** → **Web identity** → select the provider you just created.

Then edit the **Trust relationship**, scoping it to your repository:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:*"
        }
      }
    }
  ]
}
```

> The `sub` condition is what stops **any** GitHub repository on the internet from assuming this role. Do not omit it.

Attach a permissions policy allowing only `ssm:SendCommand` against your instance. Name the role `github-deploy-role`.

### 13.3 Build workflow

Images are built in GitHub Actions, never on the instance — a `pip install` of LangChain in 512 MB will fail or take forever.

Save as `.github/workflows/deploy.yml`:

```yaml
name: build-and-deploy
on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-24.04-arm    # native ARM runner: 5-10x faster than QEMU
    permissions:
      contents: read
      packages: write
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        with:
          platforms: linux/arm64
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

> **Use `ubuntu-24.04-arm`, not QEMU emulation.** Native ARM runners are free on public repositories and 5–10x faster than emulating `arm64` on an x86 runner.

> **Use `ghcr.io`, not ECR.** ECR's free tier is 500 MB for private repos — a Python image with LangChain eats that alone. GitHub Container Registry is free for public images and needs no extra credentials from Actions.

---

## 14. Troubleshooting

### SSH

| Symptom | Cause | Fix |
|---|---|---|
| `Connection timed out` | Port 22 not open, or your IP changed | Compare `curl ifconfig.me` against the SG rule |
| `Connection refused` | Instance still booting | Wait 1–2 minutes |
| `Permission denied (publickey)` | Wrong username | Use `ubuntu`, not `ec2-user` or `root` |
| `UNPROTECTED PRIVATE KEY FILE` | Key permissions | `chmod 400 ~/.ssh/agent-host-key.pem` |
| `Host key verification failed` | Instance recreated on the same IP | `ssh-keygen -R YOUR_IP` |

Add `-v` for verbose output showing exactly where the handshake fails:

```bash
ssh -v agent
```

### Bootstrap didn't run

```bash
# Where did it die? -x echoes every command.
sudo tail -50 /var/log/cloud-init-output.log
sudo grep -iE 'error|failed|E:' /var/log/cloud-init-output.log | tail -20

# Did the user data upload at all?
sudo head -5 /var/lib/cloud/instance/user-data.txt

# What actually made it?
free -h                                  # swap?
which docker && docker --version         # docker?
systemctl is-active amazon-ssm-agent     # ssm?
```

**Known failure — SSM agent already installed as a snap.** Ubuntu AMIs ship it via snap, and `dpkg -i` refuses to install over it:

```
-> Amazon-ssm-agent is installed in this instance by snap, please use snap to update or uninstall.
dpkg: error processing archive /tmp/ssm.deb (--install)
```

With `set -e` this kills the rest of the script — no swap, no Docker. The script in §7.5 handles it with `snap remove amazon-ssm-agent || true` before the `dpkg`. To fix a live instance:

```bash
sudo snap remove amazon-ssm-agent
sudo dpkg -i /tmp/ssm.deb
sudo systemctl enable --now amazon-ssm-agent
sudo apt-get purge -y snapd && sudo rm -rf /var/cache/snapd ~/snap
```

**Known failure — apt lock contention.** `unattended-upgrades` fires on first boot and collides with the script:

```
Could not get lock /var/lib/dpkg/lock-frontend
```

The wait loop at the top of §7.5 prevents this.

**Re-run the bootstrap** — the script is idempotent:

```bash
sudo bash /var/lib/cloud/instance/user-data.txt
```

### Disk full

```bash
df -h /
docker system df           # how much is Docker holding?
docker system prune -af    # reclaim it
```

`gp3` volumes resize live, no reboot:

```bash
# Change the size in the console first, then:
sudo growpart /dev/nvme0n1 1
sudo resize2fs /dev/nvme0n1p1
```

> EBS volumes **cannot be shrunk**. Growing 15 → 30 GB is trivial; going back means creating a new volume and migrating. Start small.

### Out of memory

```bash
dmesg | grep -i 'killed process'    # was the OOM killer involved?
docker stats --no-stream
free -h
```

If the container is being killed repeatedly, either trim dependencies (`langgraph` + `langchain-core` + the provider SDK — never the `langchain` meta-package) or move up to `t4g.small`. The difference is $9/month; debugging OOM in front of a live class costs more.

---

## 15. Cost breakdown

24/7 operation, `us-east-1`, on-demand Linux, prices as of August 2026:

| Item | Config | $/month |
|---|---|---|
| EC2 `t4g.nano` | 730 hrs | $3.07 |
| EBS `gp3` | 15 GB | $1.20 |
| Public IPv4 | Elastic IP, 730 hrs | $3.65 |
| Domain | ~$11/year amortized | $0.92 |
| DNS (Cloudflare) | — | $0 |
| TLS (Let's Encrypt via Caddy) | — | $0 |
| SSM Parameter Store | Standard tier | $0 |
| CloudWatch Logs | < 5 GB | $0 |
| Data transfer out | < 100 GB | $0 |
| IAM, security groups, VPC | — | $0 |
| GitHub Actions + ghcr.io | public repo | $0 |
| **Total** | | **~$8.84** |

**Note that the public IP costs more than the machine.** That's the most useful cost lesson in this entire setup: in cloud infrastructure, compute is rarely the dominant line item.

### If you need more room

| Instance | RAM | $/month 24/7 |
|---|---|---|
| `t4g.nano` | 0.5 GiB | $3.07 |
| `t4g.small` | 2 GiB | $12.26 |
| `t4g.medium` | 4 GiB | $24.53 |

Changing instance type is stop → change → start. Two minutes, and fully reversible.

### What the nano forces

With ~250 MB usable after the OS and dockerd, **everything stateful must live off-instance**:

- Vector DB → managed free tier (Supabase/Neon with pgvector, Qdrant Cloud)
- Observability → Langfuse Cloud, not self-hosted (Postgres alone won't fit)
- Dependencies → `langgraph` + `langchain-core` + provider SDK only
- No local embeddings, no `sentence-transformers`

This is a real trade-off, not an oversight: it pushes you toward a **stateless container**, which is the right architecture before talking about horizontal scaling — but it does mean the vector DB and observability layers become managed SaaS rather than self-hosted open source.

### Excluded

LLM token costs are not included here. Budget those separately, ideally covered by education credits from AWS, Anthropic, or OpenAI.

> Verify all prices against the official AWS Pricing Calculator before committing. AWS pricing has changed more than once in the past year.

---

## 16. Teardown

**Do this when the module ends.** Several resources bill whether or not the instance is running.

1. **Terminate the instance** — EC2 → Instances → Instance state → Terminate
2. **Release the Elastic IP** — Elastic IPs → Actions → Release. *An unassociated Elastic IP keeps billing.*
3. **Delete the EBS volume** if it wasn't deleted with the instance — Volumes → check for `available` status
4. **Delete snapshots** — Snapshots
5. **Check Cost Explorer** a few days later to confirm charges dropped to zero

> Phantom charges from released-but-not-deleted resources are the most common way a "$9/month" project turns into a surprise three months later. Make teardown a checklist item, not an afterthought.

---

## Appendix A — Console labels, English ↔ Spanish

AWS's Spanish localization is partial and inconsistent. This maps the labels used in this runbook.

| English | Español |
|---|---|
| Billing and Cost Management | Facturación y administración de costos |
| Budgets | Presupuestos |
| Free Tier | Capa gratuita |
| Billing preferences | Preferencias de facturación |
| Security groups | Grupos de seguridad |
| Inbound / Outbound rules | Reglas de entrada / salida |
| Anywhere-IPv4 | Cualquier lugar-IPv4 |
| My IP | Mi IP |
| Key pair (login) | Par de claves (inicio de sesión) |
| Proceed without a key pair | Continuar sin un par de claves |
| Application and OS Images (AMI) | Imágenes de aplicaciones y sistema operativo (AMI) |
| Architecture → 64-bit (Arm) | Arquitectura → 64 bits (Arm) |
| Instance type | Tipo de instancia |
| Network settings | Configuración de red |
| Auto-assign public IP | Asignar automáticamente la IP pública |
| Configure storage | Configurar almacenamiento |
| File systems → None | Sistemas de archivos → Ninguno |
| Advanced details | Detalles avanzados |
| IAM instance profile | Perfil de instancia de IAM |
| Credit specification → Standard | Especificación de crédito → Estándar |
| User data | Datos de usuario |
| Elastic IP | IP elástica |
| Allocate / Associate | Asignar / Asociar |
| Parameter Store | Almacén de parámetros |
| Session Manager | Administrador de sesiones |
| Identity providers | Proveedores de identidad |
| Trust relationship | Relación de confianza |
| Inline policy | Política insertada |
| Roles | Roles |
| Connect | Conectar |
| Launch instance | Lanzar instancia |
| Instance state → Terminate | Estado de la instancia → Terminar |

Policy names (`AmazonSSMManagedInstanceCore`), types (`SecureString`), and instance types (`t4g.nano`) are never translated.

---

## Appendix B — Quick reference

```bash
# Connect
ssh agent

# Health check
cat /var/log/bootstrap-done.log && free -h && docker ps

# Debug a failed bootstrap
sudo tail -50 /var/log/cloud-init-output.log

# Re-run the bootstrap (idempotent)
sudo bash /var/lib/cloud/instance/user-data.txt

# Reclaim disk
docker system prune -af

# Read a secret
aws ssm get-parameter --name /agent/anthropic_api_key --with-decryption \
  --query Parameter.Value --output text

# Grow the disk (after resizing in the console)
sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1
```