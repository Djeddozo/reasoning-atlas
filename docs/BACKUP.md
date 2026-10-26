# Private backup of your data

The Reasoning Atlas repository is public; your data is not in it. Databases (`memory/hq.db`, Atlas's own notes), `.env` and `runtime/` are git-ignored and stay on the Pi and the PC.

Once a day (03:40, `deploy/schedules.sh`) `./atlas.sh backup run` on the Pi:
1. takes a consistent snapshot of the Engine's database while Atlas keeps running;
2. encrypts it with AES-256 (openssl) using `ATLAS_BACKUP_KEY` from the Pi's `.env`;
3. pushes it to the private repository in `ATLAS_BACKUP_REPO`, as one commit holding the last 7 days and one snapshot per month (replaced daily, so it does not grow).

Without a key, only a local snapshot is made (`runtime/backups`, owner-only permissions) and nothing leaves the Pi. Systems › Self-maintenance shows when the last backup ran and whether it failed.

## Set up once (on the Pi)
```
gh repo create Djeddozo/atlas-private-data --private
echo "ATLAS_BACKUP_KEY=$(openssl rand -base64 32)" >> ~/hq-system/.env
echo "ATLAS_BACKUP_REPO=Djeddozo/atlas-private-data" >> ~/hq-system/.env
cd ~/hq-system && set -a && . ./.env && set +a && ./atlas.sh backup run
```
Store a copy of the key outside the Pi (for example in your password manager): without it a snapshot cannot be decrypted.

## Restore
`./atlas.sh backup restore runtime/backup-repo/hq-YYYY-MM-DD.db.enc` writes `memory/hq.restored.db`; the live database is never overwritten. Stop Atlas, swap the files yourself, start Atlas.

## Not yet covered
Notes and reminders saved under "This computer" on the PC live only in the PC's own database.
