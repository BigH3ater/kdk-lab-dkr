#!/bin/sh
# lego --deploy-hook: runs inside the lego container after api-slave.kmkdp.com is
# obtained or renewed. Copy the issued cert into the files the roborock server
# reads (config.toml [tls] mode="provided": /tls/tls.crt + /tls/tls.key),
# root-owned 0644/0600 as the hand-copied originals were. roborock reads these
# only at start, so the roborock-cert-refresh procedure restarts the roborock
# service after this stack runs. Not compose-interpolated (a real file), so no $$.
set -e
CRT=/data/certificates/api-slave.kmkdp.com.crt
KEY=/data/certificates/api-slave.kmkdp.com.key
cp "$CRT" /tls/tls.crt
cp "$KEY" /tls/tls.key
chmod 0644 /tls/tls.crt
chmod 0600 /tls/tls.key
touch /data/.renewed
