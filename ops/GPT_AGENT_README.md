# Separate GPT proxy account-management API (prototype)

**Status:** isolated code and offline tests only. This branch is **not deployed** and does not modify Vesya, Parus, Nginx, Squid, 3proxy or other active services.

## Scope

The Python file ops/gpt_proxy_agent.py is a **loopback-only** HTTP service on proposed port 43143. It manages accounts in **only** the dedicated GPT Squid at 127.0.0.1:3133:

- htpasswd users file: /etc/squid-gpt/users (existing root:proxy 0640)
- private SQLite registry: /var/lib/squid-gpt-agent/accounts.sqlite3
- private API token file: /etc/squid-gpt-agent/token (must be manually provisioned, 0600)
- bind address: **127.0.0.1**, never a public IP address

All API operations require POST, Content-Type application/json, a private X-GPT-Agent-Token header, and a body like {"telegram_id":123}, except get_service_status uses an empty body. Client access must also pass mTLS at the gateway.

Committed SQLite state is authoritative for the managed gpt_ accounts. The agent reconciles the dedicated Squid users file at startup and before/after account operations, under a cross-process file lock. If the process crashes between SQLite commit and htpasswd update, the next startup or request repairs the htpasswd projection. This does not make an operation with a lost response automatically return its lost plaintext password; use rotate_password explicitly.

- /v1/create_user: create one individual GPT account, return generated plaintext password ONCE. Repeated requests return the existing account **without** a password.
- /v1/get_user_status: inspect that GPT account.
- /v1/disable_user: remove only that account from the dedicated htpasswd file, retain hash privately for a possible re-enable.
- /v1/enable_user: restore the bcrypt hash to the dedicated htpasswd file.
- /v1/rotate_password: generate and return a new one-time plaintext password, rotate its saved bcrypt hash (including recovery from a lost first response).
- /v1/get_service_status: check the registry and file projection; returns status and number of managed accounts. **This does not prove the Squid process or Amsterdam tunnel is healthy.**

Unknown actions are rejected. No arbitrary commands can be executed by API requests. Account names use a random gpt_ prefix; the old gpt-test account is preserved.

New account passwords are generated using Python's secrets module. bcrypt hashes are produced via htpasswd using stdin, not command-line arguments. The users file is updated through an atomic replacement preserving uid/gid and mode 0640. The SQLite registry and a private audit table are separate from the old guest-proxy system. The plaintext password is returned on initial creation only; it is not stored in the registry.

## Offline tests

Run from repository root on a machine with Python 3 and htpasswd:

    python -m unittest discover -s tests -p 'test_gpt_proxy_agent.py' -v

The tests use a temporary directory. They do not contact live servers.

## Deployment is intentionally NOT performed

Before authorizing a deployment, confirm:

1. The real Vesya hosting/service in Render and a secure channel to Novosibirsk. Do not publicly expose port 43143. Use a separately authenticated and TLS-protected internal tunnel or a strictly isolated gateway after review.
2. A dedicated systemd unit with restrictive sandboxing and **only** necessary write access to /etc/squid-gpt/users and the new registry. Vesya must never receive SSH/root access.
3. A backup of GPT-only files, correct permissions, and htpasswd/bcrypt compatibility on Novosibirsk.
4. That squid-gpt at 3133 observes file changes, including authentication caches; disabling an account may **not** immediately close existing HTTPS CONNECT tunnels.
5. Verify the new explicit rotate_password recovery flow, including first-time delivery, interrupted sessions, and safe handling of credentials in Vesya.
6. Reconciliation under simulated crashes has unit tests, but still requires process-kill and live Squid tests. Also review external modification detection, directory/owner permissions, API rate limiting, and operational alerting.
7. End-to-end integration tests before offering paid accounts, and separate checks of payment receipts and secret codes.

**Never touch** Nginx 443/4453/4455, old Squid 3132, existing US 3proxy, Parus, or Amsterdam services as part of this stage.

## Subsequent work

Connect the prepared mTLS client in bot/vesya_tools/gpt to a GPT-specific customer registry and state machine; receipt parsing and owner notification; atomic one-time secret code redemption; password rotation and one-time credential delivery; installer link and help; and deployment/test procedures. None of that is wired up in this branch yet.


## Prepared mTLS gateway and Vesya client (not deployed)

- ops/gpt_mtls_gateway.nginx.example is an intentionally invalid **example**, with REVIEW_NEW_PORT / MANAGEMENT_ONLY_FQDN placeholders. Do not include it in a running Nginx configuration.
- bot/vesya_tools/gpt/client.py uses verified TLS server identity (system public CA trust for the Let's Encrypt server certificate), a Render client certificate+private key, an application token, no redirects, no inherited proxy environment, and sanitized errors. GPT_AGENT_CA_FILE is an optional public-server-CA override, not the Nginx private client CA.
- The agent binds only to 127.0.0.1:43143. It does not open a public HTTP port; the separate HTTPS/mTLS gateway must be reviewed and deployed independently.
- Render credentials should be supplied as restricted secret files, never committed. Expected variables: GPT_AGENT_API_URL, GPT_AGENT_API_TOKEN, GPT_AGENT_CLIENT_CERT_FILE, GPT_AGENT_CLIENT_KEY_FILE. Optionally GPT_AGENT_CA_FILE for a public server CA bundle override. The private client CA certificate stays at Nginx. Never upload the private client CA key to Render.
- Do not reuse credentials for the older guest-proxy agent. No Vesya message handlers call the new client yet.
- Before enabling a public management endpoint: verify port availability; procure a suitable DNS name and server certificate; create a private client CA and issue a tightly scoped client certificate; apply request limits; verify TLS and token rejection tests; and inspect logs for credential leakage.
- The new agent requires carefully sandboxed permissions to replace only /etc/squid-gpt/users while maintaining root:proxy ownership/mode. Running the Python API as root or giving it broad filesystem permissions is **not approved** by this prototype.
- The old public port 4454 serves customers; do not reuse it for management. Never repoint 4455, 4453 or 443.

Offline test command from repository root:

    python -m unittest discover -s tests -p 'test_gpt*.py' -v

Local tests cover CRUD, credential-loss recovery, simulated interrupted DB/file writes, API client HTTPS validation and error sanitization. They do not constitute end-to-end acceptance.
