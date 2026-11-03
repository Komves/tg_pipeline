# Temporary guest proxy for Vesya

Vesya commands and API in this repository; all proxy traffic goes straight from
the client's Chrome to the existing Timeweb VPS. Neither Render nor the bot
relays browsing traffic.

## Existing VPS setup

- Personal SOCKS5: `43127` (not modified)
- Guest HTTPS proxy (stunnel): `43128`
- Guest HTTP proxy (3proxy, local): `127.0.0.1:43129`
- Private management HTTPS ingress: `43130` (stunnel)
- Private management HTTP daemon: `127.0.0.1:43131`
- Users database: `/var/lib/guest-proxy/users.db`
- Expiry systemd timer: `guest-proxy-sync.timer` (every minute)
- Sync-on-write unit: `guest-proxy-sync.path`

## Setup

Install on the already configured Timeweb VPS, as root:

```bash
curl -fsSLo /root/guest-proxy-setup.sh \
  https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops/install_guest_proxy_agent.sh
bash /root/guest-proxy-setup.sh
```

The installer is re-runnable. It does NOT recreate users, passwords or
change the existing microsocks service. It generates a 256-bit bearer key once,
stored only on VPS at `/etc/guest-proxy-api.secret`.

Configure the Render service running Vesya:

```
GUEST_PROXY_API_URL=https://paruslegal.ru:43130
GUEST_PROXY_API_TOKEN=<copy secret from /etc/guest-proxy-api.secret>
```

Use the already existing `ADMIN_USER_IDS` for administrative commands.
Do not commit either secret to GitHub; do not send screenshots displaying it.

## Commands (private Telegram chat)

- `/proxy` or `Веся, дай мне прокси` — first 30-day access; repeats return
  the same account while active.
- `/start proxy` — can be used as Telegram deep-link payload.
- `/proxy_status` — status without a new registration.
- `/proxy_admin` — admin only, list of numbered accounts.
- `/proxy_renew 3` — admin only, add 30 days (also re-enable).
- `/proxy_disable 3` — admin only, block user.

Only internal proxy account number, Telegram numeric ID, credentials and
timestamps are stored. Expired accounts cannot get another free month via
a second `/start`. Anonymous test accounts user001/user002 remain in SQLite.

The current design intentionally does not restrict requested websites.
The new API requires a secret token and TLS; the backend listens on localhost.
For safety, do not distribute the management API URL or token.

## Testing

```bash
systemctl is-active guest-proxy-api guest-proxy-tls guest-proxy-sync.timer
ss -lntp | grep -E ':43127|:43128|:43129|:43130|:43131'
```

On Render, after setting both environment variables and redeploying, message
Vesya `/proxy` (creates user003 if first two test users still exist).
Telegram returns the username/password and attaches a simple Windows `Open_ChatGPT.cmd` launcher. Download and double-click the file; Chrome opens with a separate profile and requests the issued credentials. The launcher contains no secrets and installs nothing.
Check `/proxy_admin` from the owner Telegram ID.

If credentials are issued but Chrome takes time to accept them, wait for the
minute sync timer. The path watcher usually updates credentials immediately.
