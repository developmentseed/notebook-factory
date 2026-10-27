#!/usr/bin/env bash
# Create the namespace and Secrets for a test deploy on the OVH cluster.
# Safe to re-run: generated values (Postgres password, SECRET_KEY) are kept if they already exist.
#
#   S3_ACCESS_KEY=… S3_SECRET_KEY=… deploy/ovh/secrets.sh
set -euo pipefail
NS=${NS:-notebook-factory}
: "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -

existing() { kubectl -n "$NS" get secret "$1" -o "jsonpath={.data.$2}" 2>/dev/null | base64 -d || true; }
apply_secret() { kubectl -n "$NS" create secret "$@" --dry-run=client -o yaml | kubectl apply -f -; }

PG_PASSWORD=$(existing notebook-factory-postgis POSTGRES_PASSWORD)
PG_PASSWORD=${PG_PASSWORD:-$(openssl rand -hex 24)}
SECRET_KEY=$(existing notebook-factory-secrets SECRET_KEY)
SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32)}

apply_secret generic notebook-factory-postgis --from-literal=POSTGRES_PASSWORD="$PG_PASSWORD"

apply_secret generic notebook-factory-secrets \
  --from-literal=DATABASE_URL="postgis://notebook_factory:${PG_PASSWORD}@postgis:5432/notebook_factory" \
  --from-literal=SECRET_KEY="$SECRET_KEY" \
  --from-literal=S3_ACCESS_KEY="$S3_ACCESS_KEY" \
  --from-literal=S3_SECRET_KEY="$S3_SECRET_KEY"
