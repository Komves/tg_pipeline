# GPT Agent on Novosibirsk — controlled deployment review

**Status:** Prepared for review only, not installed. The production Vesya bot, Nginx, Squid and existing proxy routes are unchanged.

## New isolated architecture

- Vesya on Render (Oregon) calls a dedicated **HTTPS + mTLS** gateway, with an additional private X-GPT-Agent-Token.
- Gateway: **as-yet-unselected** management hostname and new port. ops/gpt_mtls_gateway.nginx.example contains intentionally invalid placeholders. It forwards only the six allowlisted account operations to 127.0.0.1:43143.
- gpt-agent.service: **unprivileged** OS user gpt-agent. It writes only to its private SQLite DB and an account projection in /var/lib/squid-gpt-agent/projection/users. It CANNOT write /etc/squid-gpt/users.
- gpt-squid-sync.path monitors changes to that projection; gpt-squid-sync.timer periodically reconciles if a filesystem event was missed. They trigger gpt-squid-sync.service.
- gpt-squid-sync.service: **offline** root-owned oneshot with no network access. It validates only gpt_[16 hexadecimal digits] accounts with bcrypt hashes. It updates only /etc/squid-gpt/users, preserves root:proxy 0640, and preserves unknown accounts including gpt-test.
- No operation restarts or rewrites Nginx, the primary Squid, the old guest proxy agent, 3proxy or Parus.

## Why privilege separation matters

Atomic replacement of root-owned /etc/squid-gpt/users otherwise requires directory write permission or root, neither of which should be granted to the network-facing Python API. The root sync process has fixed input/output paths and no HTTP listener. The private agent has no arbitrary shell operation.

## Safe preflight — READ ONLY

Run ops/gpt_preflight_readonly.sh on Novosibirsk with sufficient permissions and send its output. It prints state, listeners, and permission metadata; it NEVER prints password hashes or API secrets.

Verify:
- Nginx GPT port 4454 routes to Squid GPT 3133, existing Chrome port 4455 stays on Squid 3132. Do not edit 443 or 4453.
- Proposed loopback port 43143 is free; an independently selected new management TLS port is free.
- /etc/squid-gpt/users is still root:proxy mode 0640, and Squid 7.7 reloads authenticator credentials as expected.
- Python3 and htpasswd exist. The systemd version and sandbox capabilities are sufficient.
- Before any activation, new dedicated user gpt-agent and directories are provisioned securely and back-ups exist.

## Deployment procedure — NOT PERFORMED

1. Back up only the new GPT service files and account file and define a rollback command/restore procedure.
2. Create a dedicated static OS principal gpt-agent with no shell, private /var/lib/squid-gpt-agent and projection directory owned by it (0700), private /var/lib/squid-gpt-sync owned by root (0700), plus /etc/squid-gpt-agent/token owned root:gpt-agent (0640). Use a unique token, never the older guest-proxy token.
3. Install the reviewed Python files into root-owned /opt/vesya-gpt-agent (not writable by gpt-agent). Install the new systemd files and run systemd-analyze verify, but do not start any of them before review.
4. Generate a dedicated client CA, client certificate for the Render service, and TLS server certificate with the selected management hostname. Store all private keys outside GitHub and protect them as secrets.
5. Configure a NEW Nginx server block on a reviewed unused management port, with SSL client verification enabled and a narrow API allowlist. Do not modify any existing server block. Check Nginx syntax before an approved reload.
6. Test that no client certificate, wrong client certificate, missing token, invalid method and malformed request all fail without exposing secrets.
7. In a controlled maintenance window, activate only new GPT units. Create a dedicated test user; verify its access through existing GPT client endpoint 4454, then block it and verify new connections fail. Note that already-established CONNECT tunnels may persist until they close.
8. Confirm the Render worker supports secure CA/cert/key file provisioning and outbound mTLS to the new Novosibirsk hostname. Set GPT-specific environment configuration without replacing any current guest proxy values.
9. Test recovery after an interrupted account update and monitor projection synchronization. Release to clients only after payment and secret-code workflows have separate acceptance tests.

## Important operational boundaries

- The new root sync service can rewrite only the dedicated GPT user file; the API worker runs unprivileged.
- Current SQLite -> projection -> root sync is **eventually consistent**. Do not claim new access is usable until an actual proxy authentication check succeeds.
- Agent's API health does not measure Amsterdam route or ChatGPT availability; these require separate network checks.
- User revocation may not close already-established CONNECT tunnels; test Squid auth caches and tunnel lifetime on the real host.
- Client certificates and private tokens must not appear in GitHub, Render logs or Telegram messages.
- API rate limiting, external changes to the live htpasswd file, the exact management port and actual server permissions remain deployment-review items.
- No automatic deployment: keep feature/gpt-proxy-agent-isolated separate from master.

## Local tests

From the repository root, with Python 3 and htpasswd installed:

    python -m unittest discover -s tests -p 'test_gpt*.py' -v

On a Linux machine with systemd-analyze:

    systemd-analyze verify ops/systemd/*

These are offline checks using temporary files; they do not establish real Squid/Nginx compatibility.
