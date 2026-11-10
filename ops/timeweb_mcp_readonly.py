#!/usr/bin/env python3
"""Read-only, local-stdio MCP server for the Timeweb VPS.

Intentionally no HTTP listener, shell, arbitrary paths, restarts, or writes.
Run as an unprivileged OS user through an OpenAI Secure MCP Tunnel.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

VERSION = "1.0.0"
SERVICES = (
    "guest-proxy",
    "guest-proxy-tls",
    "guest-proxy-api",
    "guest-proxy-sync.timer",
    "guest-proxy-sync.path",
    "microsocks",
)
PORTS = (43127, 43128, 43129, 43130, 43131)
PORT_LABELS = {
    43127: "personal SOCKS5",
    43128: "guest HTTPS proxy",
    43129: "guest HTTP upstream (loopback)",
    43130: "guest management HTTPS API",
    43131: "management API upstream (loopback)",
}
TOOLS = [
    {
        "name": "get_server_status",
        "description": "Read uptime, load, disk usage, service states and proxy listening ports on the Timeweb VPS. Does not modify anything.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "get_proxy_ports",
        "description": "Read TCP listening status of only the five known proxy/API ports (43127-43131). No secrets.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "get_recent_errors",
        "description": "Read bounded warning/error journal entries from one allowlisted proxy service only. No arbitrary file access.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": list(SERVICES)},
                "minutes": {"type": "integer", "minimum": 5, "maximum": 240, "default": 60},
                "lines": {"type": "integer", "minimum": 1, "maximum": 60, "default": 25},
            },
            "required": ["service"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]

ENV = {"PATH": "/usr/sbin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}


def command(argv: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=ENV, check=False)
        return {"exit_code": r.returncode, "stdout": r.stdout[:12000], "stderr": r.stderr[:800]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": -1, "stdout": "", "stderr": type(exc).__name__}


def safe_ports() -> list[dict[str, Any]]:
    result = command(["/usr/bin/ss", "-H", "-lnt"])
    listen: dict[int, set[str]] = {p: set() for p in PORTS}
    for line in result["stdout"].splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        address = cols[3]
        try:
            port = int(address.rsplit(":", 1)[-1])
        except ValueError:
            continue
        if port in listen:
            if address.startswith(("127.0.0.1:", "[::1]:", "::1:")):
                scope = "loopback"
            elif address.startswith(("0.0.0.0:", "*:", "[::]:")):
                scope = "all interfaces"
            else:
                scope = "specific interface"
            listen[port].add(scope)
    return [
        {"port": p, "purpose": PORT_LABELS[p], "listening": bool(listen[p]), "scope": sorted(listen[p])}
        for p in PORTS
    ]


def server_status() -> dict[str, Any]:
    uptime = None
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            uptime = int(float(f.readline().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        load = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        load = []
    disk = shutil.disk_usage("/")
    units = []
    for service in SERVICES:
        result = command(["/usr/bin/systemctl", "is-active", service])
        state = result["stdout"].strip() or "unknown"
        units.append({"service": service, "state": state})
    return {
        "uptime_seconds": uptime,
        "load_average": load,
        "root_disk_percent_used": round(100 * disk.used / disk.total, 1),
        "services": units,
        "ports": safe_ports(),
    }


def sanitized_log(text: str) -> str:
    text = re.sub(r"(?i)(authorization|proxy-authorization|x-proxy-token|password|passwd|secret|token)\s*[:=]\s*[^\s,;]+",
                  r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    return text[:11000]


def logs(args: dict[str, Any]) -> dict[str, Any]:
    service = args.get("service")
    minutes = args.get("minutes", 60)
    lines = args.get("lines", 25)
    if service not in SERVICES:
        raise ValueError("service must be one of the listed proxy services")
    if type(minutes) is not int or not 5 <= minutes <= 240:
        raise ValueError("minutes must be an integer between 5 and 240")
    if type(lines) is not int or not 1 <= lines <= 60:
        raise ValueError("lines must be an integer between 1 and 60")
    raw = command(
        ["/usr/bin/journalctl", "--no-pager", "--output=short-iso", "-p", "warning",
         "-u", service, "--since", f"-{minutes} minutes", "-n", str(lines)],
        timeout=8,
    )
    return {"service": service, "minutes": minutes, "exit_code": raw["exit_code"],
            "output": sanitized_log(raw["stdout"]), "error": sanitized_log(raw["stderr"])}


def rpc_result(request_id: Any, data: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": data}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def process(msg: Any) -> dict[str, Any] | None:
    if not isinstance(msg, dict):
        return rpc_error(None, -32600, "Invalid request")
    request_id = msg.get("id")
    method = msg.get("method")
    if not isinstance(method, str):
        return rpc_error(request_id, -32600, "Invalid request")
    if request_id is None:
        return None  # MCP notifications need no response
    if method == "initialize":
        params = msg.get("params") or {}
        ver = params.get("protocolVersion") if isinstance(params, dict) else None
        supported = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
        return rpc_result(request_id, {
            "protocolVersion": ver if ver in supported else "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "timeweb-readonly", "version": VERSION},
        })
    if method == "ping":
        return rpc_result(request_id, {})
    if method == "tools/list":
        return rpc_result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params")
        if not isinstance(params, dict):
            return rpc_error(request_id, -32602, "Invalid parameters")
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return rpc_error(request_id, -32602, "arguments must be an object")
        try:
            if name == "get_server_status" and not args:
                payload = server_status()
            elif name == "get_proxy_ports" and not args:
                payload = {"ports": safe_ports()}
            elif name == "get_recent_errors":
                payload = logs(args)
            else:
                raise ValueError("Unknown tool or unexpected arguments")
        except (ValueError, OSError) as exc:
            return rpc_result(request_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        return rpc_result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
        })
    return rpc_error(request_id, -32601, "Method not found")


def main() -> None:
    for raw_line in sys.stdin.buffer:
        if len(raw_line) > 65536:
            continue
        try:
            obj = json.loads(raw_line)
            reply = process(obj)
            if reply is not None:
                sys.stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception:
            # No tracebacks to stdout; preserve the MCP stdio protocol.
            sys.stdout.write(json.dumps(rpc_error(None, -32603, "Internal error")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
