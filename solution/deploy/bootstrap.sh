#!/bin/bash
set -eux

# Wait for cloud-init and the apt lock to clear
cloud-init status --wait || true
for i in $(seq 1 30); do
  fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
  sleep 10
done

# ---------- SSM agent: Ubuntu AMIs ship it as a snap ----------
# Replace with the .deb and drop snapd entirely (~90 MB of RAM back)
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
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=60'         >  /etc/sysctl.d/99-swap.conf
echo 'vm.vfs_cache_pressure=50' >> /etc/sysctl.d/99-swap.conf
sysctl -p /etc/sysctl.d/99-swap.conf

# ---------- Docker ----------
# Compose v2 is a separate package on Ubuntu. Without it you get
# "unknown shorthand flag: 'd'" on the first `docker compose up -d`.
# git is needed by the deploy: the host checks out the commit being released.
apt-get install -y docker.io docker-compose-v2 git
systemctl enable --now docker
usermod -aG docker ubuntu
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
systemctl restart docker

# ---------- Maintenance ----------
sed -i 's|\(\s/\s\+ext4\s\+\)defaults|\1defaults,noatime|' /etc/fstab
echo '0 3 * * 0 root docker system prune -af --filter "until=168h"' \
  > /etc/cron.d/docker-prune
chmod 644 /etc/cron.d/docker-prune
apt-get install -y unattended-upgrades
systemctl enable --now unattended-upgrades

echo "bootstrap OK $(date -Is)" > /var/log/bootstrap-done.log