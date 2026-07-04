#!/usr/bin/env bash
# ============================================================================
# deploy/init-letsencrypt.sh — one-time TLS bootstrap for the new-stack deploy.
#
# nginx can't start without a cert file, and certbot can't issue one without
# nginx answering the ACME challenge. This breaks the cycle: seed a throwaway
# self-signed cert → start nginx → swap in the real Let's Encrypt cert.
#
# Run ONCE on the server, from deploy/, after DNS for $DOMAIN points at the box
# and deploy/.env is filled in. Afterwards `docker compose -f
# docker-compose.prod.yml up -d` just works; the certbot service auto-renews.
#
#   TIP: set LETSENCRYPT_STAGING=1 in .env for a dry run first (avoids hitting
#   Let's Encrypt rate limits while you shake out DNS / firewall issues).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

[ -f .env ] || { echo "ERROR: deploy/.env is missing — copy .env.example → .env and fill it in."; exit 1; }
set -a; . ./.env; set +a
: "${DOMAIN:?set DOMAIN in .env}"
: "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL in .env}"

cert_path="/etc/letsencrypt/live/$DOMAIN"
staging_arg=""
[ "${LETSENCRYPT_STAGING:-0}" != "0" ] && staging_arg="--staging"

echo "### [1/6] Building images (api + web)…"
$COMPOSE build

echo "### [2/6] Seeding a throwaway self-signed cert for $DOMAIN…"
$COMPOSE run --rm --entrypoint "sh -c \
  'mkdir -p $cert_path && openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
     -keyout $cert_path/privkey.pem -out $cert_path/fullchain.pem -subj /CN=$DOMAIN'" certbot

echo "### [3/6] Starting nginx…"
$COMPOSE up -d web

echo "### [4/6] Removing the throwaway cert…"
$COMPOSE run --rm --entrypoint "rm -rf $cert_path /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

echo "### [5/6] Requesting the real Let's Encrypt certificate…"
$COMPOSE run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot \
  $staging_arg --email $LETSENCRYPT_EMAIL -d $DOMAIN \
  --rsa-key-size 4096 --agree-tos --no-eff-email --force-renewal" certbot

echo "### [6/6] Reloading nginx with the real cert…"
$COMPOSE exec web nginx -s reload

echo
echo "✅ TLS ready for https://$DOMAIN"
echo "   Now bring up the full stack:  $COMPOSE up -d"
