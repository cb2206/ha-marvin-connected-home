#!/usr/bin/env bash
#
# Install the mitmproxy CA into the emulator's *system* trust store.
#
# Android 15 keeps the CA store in the Conscrypt APEX, so dropping a cert into
# /system/etc/security/cacerts is not enough on its own — and none of this
# survives a reboot. Re-run after every cold boot of the AVD.
#
# Requires an AVD booted with -writable-system on a non-PlayStore image (so
# `adb root` works).
#
set -euo pipefail

SERIAL="${SERIAL:-emulator-5554}"
CA="${CA:-$HOME/.mitmproxy/mitmproxy-ca-cert.pem}"
ADB="${ANDROID_HOME:-$HOME/Library/Android/sdk}/platform-tools/adb"

[ -f "$CA" ] || { echo "No mitmproxy CA at $CA — run mitmproxy once to generate it" >&2; exit 1; }

# Android looks certs up by the old-style subject hash, filename <hash>.0
HASH=$(openssl x509 -inform PEM -subject_hash_old -in "$CA" -noout)
echo "CA subject hash: $HASH"

"$ADB" -s "$SERIAL" root >/dev/null
sleep 3
"$ADB" -s "$SERIAL" push "$CA" "/data/local/tmp/$HASH.0" >/dev/null

"$ADB" -s "$SERIAL" shell su 0 sh -s <<EOF
set -e

# A writable overlay over the legacy cacerts dir, seeded from the APEX store so
# we keep every stock root and only *add* ours.
mountpoint -q /system/etc/security/cacerts || mount -t tmpfs tmpfs /system/etc/security/cacerts
cp /apex/com.android.conscrypt/cacerts/* /system/etc/security/cacerts/
cp /data/local/tmp/$HASH.0 /system/etc/security/cacerts/

chown root:root /system/etc/security/cacerts/*
chmod 644 /system/etc/security/cacerts/*
chcon u:object_r:system_security_cacerts_file:s0 /system/etc/security/cacerts/*

# Conscrypt reads the APEX path, so shadow it with the overlay.
mount --bind /system/etc/security/cacerts /apex/com.android.conscrypt/cacerts

# Processes already running hold their own mount namespace and would miss the
# bind above — push it into each one. Zygote children included, which is what
# actually matters for the app.
for pid in \$(ls /proc | grep -E '^[0-9]+\$'); do
  nsenter --mount=/proc/\$pid/ns/mnt -- \
    /bin/mount --bind /system/etc/security/cacerts /apex/com.android.conscrypt/cacerts 2>/dev/null || true
done
EOF

echo -n "certs visible to Conscrypt: "
"$ADB" -s "$SERIAL" shell su 0 ls /apex/com.android.conscrypt/cacerts | wc -l
echo -n "mitmproxy CA present: "
"$ADB" -s "$SERIAL" shell su 0 ls "/apex/com.android.conscrypt/cacerts/$HASH.0" 2>/dev/null \
  && echo "yes" || echo "NO — capture will fail on TLS"
