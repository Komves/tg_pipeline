"""Read-only connectivity check from Vesya's actual Render worker.

Run after a deployment makes GPT_AGENT_* environment and Secret Files available:
    python -m vesya_tools.gpt.check_connection

Does not create users, print credentials, or modify Squid.
"""
import asyncio

from .client import GPTAgentClient, GPTAgentConfig, GPTAgentError


async def main():
    try:
        client = GPTAgentClient(GPTAgentConfig.from_env())
        info = await client.get_service_status()
        if info.get("status") != "ok":
            print("GPT_AGENT_CONNECTIVITY: unexpected status")
            return 1
        print("GPT_AGENT_CONNECTIVITY: OK")
        print("GPT_MANAGED_ACCOUNTS:", info.get("managed_accounts", "unknown"))
        return 0
    except (ValueError, GPTAgentError, OSError):
        print("GPT_AGENT_CONNECTIVITY: FAILED (details omitted to protect credentials)")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
