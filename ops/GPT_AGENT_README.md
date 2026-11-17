# Separate GPT proxy account-management API (prototype)

**Status:** isolated code and offline tests only. This branch is **not deployed** and does not modify Vesya, Parus, Nginx, Squid, 3proxy or other active services.

## Scope

The Python file ops/gpt_proxy_agent.py is a **loopback-only** HTTP service on proposed port 43141. It manages accounts in **only** the dedicated GPT Squid at 127.0.0.1:3133:

- htpasswd users file: /etc/squid-gpt/users (existing root:proxy 0640)
- private SQLite registry: /var/lib/squid-gpt-agent/accounts.sqlite3
- private API token file: /etc/squid-gpt-agent/token (must be manually provisioned, 0600)
- bind address: **127.0.0.1**, never a public IP address

All API operations require POST, Content-Type application/json, a private X-GPT-Agent-Token header, and a body like {"telegram_id":123}.

- /v1/create_user: create one individual GPT account, return generated plaintext password ONCE. Repeated requests return the existing account **without** a password.
- /v1/get_user_status: inspect that GPT account.
- /v1/disable_user: remove only that account from the dedicated htpasswd file, retain hash privately for a possible re-enable.
- /v1/enable_user: restore the bcrypt hash to the dedicated htpasswd file.

Unknown actions are rejected. No arbitrary commands can be executed by API requests. Account names use a random gpt_ prefix; the old gpt-test account is preserved.

New account passwords are generated using Python's secrets module. bcrypt hashes are produced via htpasswd using stdin, not command-line arguments. The users file is updated through an atomic replacement preserving uid/gid and mode 0640. The SQLite registry and a private audit table are separate from the old guest-proxy system. The plaintext password is returned on initial creation only; it is not stored in the registry.

## Offline tests

Run from repository root on a machine with Python 3 and htpasswd:

    python -m unittest discover -s tests -p 'test_gpt_proxy_agent.py' -v

The tests use a temporary directory. They do not contact live servers.

## Deployment is intentionally NOT performed

Before authorizing a deployment, confirm:

1. The real Vesya hosting/service in Render and a secure channel to Novosibirsk. Do not publicly expose port 43141. Use a separately authenticated and TLS-protected internal tunnel or a strictly isolated gateway after review.
2. A dedicated systemd unit with restrictive sandboxing and **only** necessary write access to /etc/squid-gpt/users and the new registry. Vesya must never receive SSH/root access.
3. A backup of GPT-only files, correct permissions, and htpasswd/bcrypt compatibility on Novosibirsk.
4. That squid-gpt at 3133 observes file changes, including authentication caches; disabling an account may **not** immediately close existing HTTPS CONNECT tunnels.
5. A recovery flow for a lost one-time password response: password rotation has not yet been implemented. Add this before enabling real customers.
6. Reconciliation after crashes between a file update and SQLite commit, external modification detection, API rate limiting, and operational alerting.
7. End-to-end integration tests before offering paid accounts, and separate checks of payment receipts and secret codes.

**Never touch** Nginx 443/4453/4455, old Squid 3132, existing US 3proxy, Parus, or Amsterdam services as part of this stage.

## Subsequent work

Add a safe communication client inside bot/vesya_tools/gpt; a GPT-specific customer registry and state machine; receipt parsing and owner notification; atomic one-time secret code redemption; password rotation and one-time credential delivery; installer link and help; and deployment/test procedures. None of that is wired up in this branch yet.
