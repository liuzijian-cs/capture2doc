#!/bin/sh
# Run from the checkout after installing dependencies and service.toml.
set -eu
if [ "$(id -u)" != 0 ]; then
    echo 'Run with sudo on WSL; no password belongs in repository/config.' >&2
    exit 1
fi
deploy_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -m 644 "$deploy_dir/capture2doc-api.service" /etc/systemd/system/
install -m 644 "$deploy_dir/capture2doc-worker.service" /etc/systemd/system/
install -m 644 "$deploy_dir/capture2doc-firewall.nft" /etc/capture2doc-firewall.nft
install -m 755 "$deploy_dir/load-firewall.sh" /usr/local/sbin/capture2doc-firewall
cat > /etc/systemd/system/capture2doc-firewall.service <<'UNIT'
[Unit]
Description=Capture2Doc ingress restrictions
Before=capture2doc-api.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/capture2doc-firewall
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable capture2doc-firewall capture2doc-api capture2doc-worker
systemctl restart capture2doc-firewall
systemctl restart capture2doc-api capture2doc-worker
