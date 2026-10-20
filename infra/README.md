# Infra: mothership, tunnel, push-to-main deploy

Everything runs on `mothership` (Proxmox VM 100, user `tomas`) at
`~/containers/groknet`, which is a plain clone of this repo. There is no CI and
no registry: the host builds the images itself.

## One-time server setup

```bash
ssh mothership
git clone git@github.com:sudoTomas/groknet.git ~/containers/groknet
cd ~/containers/groknet
cp .env.example .env && chmod 600 .env
# fill CLOUDFLARE_TUNNEL_TOKEN, GITHUB_WEBHOOK_SECRET, SESSION_SECRET and
# GROKNET_ENCRYPTION_KEY in .env (see "Encryption key" below)

sudo cp infra/deploy/groknet-deploy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now groknet-deploy
docker compose up -d --build
```

Cloudflare side (done through the API with the keychain token, see the
`groknet` tunnel in the `tomas@eastwood.ai` account):

- Tunnel `groknet`, remotely managed. Ingress, in order:
  1. `groknet.eastwood.ai` path `^/_deploy(/.*)?$` -> `http://host.docker.internal:9797`
  2. `groknet.eastwood.ai` path `^/(api|mcp)(/.*)?$` -> `http://api:8000`
  3. `groknet.eastwood.ai` -> `http://dashboard:80`
  4. fallback `http_status:404`
- DNS: proxied CNAME `groknet` -> `<tunnel-id>.cfargotunnel.com`.

GitHub side: repository webhook, content type `application/json`, secret equal to
`GITHUB_WEBHOOK_SECRET`, event `push` only, URL
`https://groknet.eastwood.ai/_deploy/github`.

## How a deploy happens

1. You merge to `main`.
2. GitHub POSTs to `/_deploy/github`. `groknet-deploy.py` checks the HMAC, that the
   event is `push` and the ref is `refs/heads/main`, answers 202, and starts
   `deploy.sh` in the background. A second push during a build is queued once.
3. `deploy.sh` does `git fetch` + `git reset --hard origin/main` and
   `docker compose up -d --build --remove-orphans`.
4. Watch it: `https://groknet.eastwood.ai/_deploy/status` shows the last 40 log
   lines, or `tail -f ~/containers/groknet/deploy.log` on the host.

The receiver runs on the host, not in Compose, so a broken compose file cannot
take down the thing that fixes it. `cloudflared` reaches it via
`host.docker.internal` (`extra_hosts: host-gateway` in the compose file).

## Manual deploy or rollback

```bash
ssh mothership 'bash ~/containers/groknet/infra/deploy/deploy.sh'
# rollback: on the Mac, revert the commit on main and push; the webhook does the rest
```

## Secrets

`~/containers/groknet/.env` only, mode 600. Never in git, never in the compose
file, never echoed in a shell you are recording.

## Encryption key

`GROKNET_ENCRYPTION_KEY` encrypts every bot's `webhook_url` and `webhook_key` in
SQLite (Fernet, stored as `enc:v1:<token>`). The API refuses to start without it
(compose also fails fast with `set in .env`). Generate it once and put it in `.env`:

```bash
python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
```

On the first boot with the key, rows stored before encryption are encrypted in place
and the log says `Encrypted the webhook credentials of N plaintext user row(s).`
Later boots find nothing to do.

Back the key up outside the server (password manager). **Losing it means every bot
must re-register**: the stored inbox credentials cannot be decrypted, so nothing can
be delivered and every user load fails until the rows are re-registered or deleted.

### Rotating the key

```bash
ssh mothership
cd ~/containers/groknet
NEW=$(python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
docker compose stop api
docker compose run --rm --no-deps -v "$PWD/scripts:/scripts:ro" \
  -e OLD_GROKNET_ENCRYPTION_KEY="$(grep ^GROKNET_ENCRYPTION_KEY= .env | cut -d= -f2-)" \
  -e NEW_GROKNET_ENCRYPTION_KEY="$NEW" \
  api uv run --no-dev python /scripts/rotate_encryption_key.py /data/groknet.db
# prints "re-encrypted N user row(s) ...". Only then switch .env to the new key:
sed -i "s|^GROKNET_ENCRYPTION_KEY=.*|GROKNET_ENCRYPTION_KEY=$NEW|" .env
docker compose up -d api
```

The script decrypts everything with OLD before writing anything; if a value does not
decrypt (wrong OLD key) it exits non-zero and changes nothing. Keep the old key until
the API is up and deliveries work with the new one.
