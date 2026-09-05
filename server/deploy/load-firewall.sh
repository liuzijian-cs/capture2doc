#!/bin/sh
set -eu
if /usr/sbin/nft list table inet capture2doc >/dev/null 2>&1; then
    { echo 'delete table inet capture2doc'; cat /etc/capture2doc-firewall.nft; } | /usr/sbin/nft -f -
else
    /usr/sbin/nft -f /etc/capture2doc-firewall.nft
fi
