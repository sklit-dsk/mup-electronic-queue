#!/bin/sh
# Run from the repository root. Preserve this directory securely, including file keys.
set -eu
destination=${1:-backups}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$destination"
chmod 700 "$destination"
docker compose exec -T db pg_dump -U queue -d queue -Fc > "$destination/db-$stamp.dump"
docker compose exec -T api tar -C /data -czf - . > "$destination/files-and-keys-$stamp.tar.gz"
chmod 600 "$destination/db-$stamp.dump" "$destination/files-and-keys-$stamp.tar.gz"
echo "Backup created. Contains sensitive files and keys: protect both archives."
