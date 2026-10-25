"""测试用最小 MCP 子进程 server：stdin/stdout 按行收发 JSON-RPC。

用于 test_proxy_smoke 的被代理端，不依赖 mcp SDK。
"""

import json
import sys


def respond(req_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            respond(
                req_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-git", "version": "0.0.1"},
                },
            )
        elif method == "tools/list":
            respond(
                req_id,
                {
                    "tools": [
                        {
                            "name": "git_log",
                            "description": "fake git log",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"repo": {"type": "string"}},
                            },
                        }
                    ]
                },
            )
        elif method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            if name == "git_log":
                payload = {
                    "commits": [
                        {
                            "hash": f"c{i:04x}",
                            "message": f"commit {i}",
                            "tree": "t" * 40,
                            "diff": "..." * 100,
                        }
                        for i in range(8)
                    ]
                }
                respond(
                    req_id,
                    {"content": [{"type": "text", "text": json.dumps(payload)}]},
                )
            else:
                respond(req_id, {"content": [{"type": "text", "text": "ok"}]})
        # notifications（无 id）不回应


if __name__ == "__main__":
    main()
