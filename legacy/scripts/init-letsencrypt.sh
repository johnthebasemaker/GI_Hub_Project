#!/bin/bash
# ============================================================================
# init-letsencrypt.sh — first-boot TLS bootstrap (run ONCE on the live server)
# ----------------------------------------------------------------------------
# Why this exists: nginx's :443 server block references the Let's Encrypt cert
# files. On a brand-new box those files don't exist yet, so nginx can't start —
# which means it can't answer the ACME HTTP-01 challenge — which means certbot
# can't issue a cert. Deadlock. This script breaks it:
#   1) drop a self-signed DUMMY cert so nginx can boot
#   2) start nginx (now serving the ACME challenge on :80)
#   3) delete the dummy + request the REAL cert via certbot webroot
#   4) reload nginx to pick up the real cert
#
# After this runs once, the cert lives in the `certbot-etc` volume and ordinary
# `docker compose up -d` works forever; the certbot service auto-renews and the
# nginx reload loop applies the renewal. Re-run only to re-issue from scratch.
#
# PREREQUISITES (do these first):
#   • DNS A/AAAA records for the domain(s) below point at THIS server's IP.
#   • If the domain is on Cloudflare, set it to DNS-only (grey cloud) OR use
#     "Full (strict)" SSL — an orange-cloud proxy can block HTTP-01.
#   • Ports 80 and 443 open in the firewall.
# ============================================================================
set -e

# ---- EDIT THESE ------------------------------------------------------------
domains=(giinventory.com www.giinventory.com)
email="admin@giinventory.com"     # Let's Encrypt expiry notices go here
staging=1                          # 1 = test (no rate limits). Set to 0 for REAL certs.
# ---------------------------------------------------------------------------
rsa_key_size=4096
compose="docker compose"          # use "docker-compose" if on the v1 plugin

if ! $compose version >/dev/null 2>&1; then
  echo "❌ '$compose' not found. Install Docker Compose v2 (or set compose=docker-compose)."; exit 1
fi

primary="${domains[0]}"
live="/etc/letsencrypt/live/$primary"
echo "### Bootstrapping TLS for: ${domains[*]}  (staging=$staging)"

echo "### 1/4 — creating a temporary dummy certificate for $primary …"
$compose run --rm --entrypoint "/bin/sh" certbot -c "\
  mkdir -p '$live' && \
  openssl req -x509 -nodes -newkey rsa:1024 -days 1 \
    -keyout '$live/privkey.pem' -out '$live/fullchain.pem' -subj '/CN=localhost'"

echo "### 2/4 — starting nginx (serves the ACME challenge on :80) …"
$compose up -d nginx
sleep 3

echo "### 3/4 — deleting dummy + requesting the real certificate …"
$compose run --rm --entrypoint "/bin/sh" certbot -c "\
  rm -rf /etc/letsencrypt/live/$primary \
         /etc/letsencrypt/archive/$primary \
         /etc/letsencrypt/renewal/$primary.conf"

domain_args=""; for d in "${domains[@]}"; do domain_args="$domain_args -d $d"; done
staging_arg=""; [ "$staging" != "0" ] && staging_arg="--staging"

$compose run --rm --entrypoint "certbot" certbot \
  certonly --webroot -w /var/www/certbot \
  $staging_arg $domain_args \
  --email "$email" --rsa-key-size "$rsa_key_size" \
  --agree-tos --no-eff-email --non-interactive --force-renewal

echo "### 4/4 — reloading nginx with the real certificate …"
$compose exec nginx nginx -s reload || $compose restart nginx

echo "✅ Done."
if [ "$staging" != "0" ]; then
  echo "⚠️  These are STAGING certs (browsers will warn). When the flow works,"
  echo "    set staging=0 in this script and re-run to get trusted certificates."
fi
echo "→ Now bring the full stack up:  $compose up -d"
