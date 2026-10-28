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
                        },
                        {
                            "name": "read_query",
                            "description": "fake sqlite query (Python repr output)",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        },
                        {
                            "name": "list_issues",
                            "description": "fake github issue list",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"owner": {"type": "string"}},
                            },
                        },
                        {
                            "name": "get_me",
                            "description": "fake github get_me",
                            "inputSchema": {"type": "object", "properties": {}},
                        },
                        {
                            "name": "pull_request_read",
                            "description": "fake github PR read (JSON or diff text)",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"method": {"type": "string"}},
                            },
                        },
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
                    {
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                        # MCP 2025-06+ 的机器可读副本：代理必须一起压缩，
                        # 否则完整副本会把 token 收益抵消掉
                        "structuredContent": payload,
                    },
                )
            elif name == "read_query":
                # 真实 mcp-server-sqlite 用 str(rows) 输出 Python repr（单引号）
                rows = [
                    {"id": i, "name": f"item{i}", "payload": "x" * 20}
                    for i in range(60)
                ]
                respond(
                    req_id,
                    {"content": [{"type": "text", "text": str(rows)}]},
                )
            elif name == "list_issues":
                issues = {
                    "issues": [
                        {
                            "number": 1000 + i,
                            "title": f"bug {i}",
                            "body": "\n".join(
                                [f"### issue {i}"] + [f"detail {j} " + "x" * 30 for j in range(40)]
                            ),
                            "state": "OPEN",
                            "user": {"login": f"user{i}"},
                        }
                        for i in range(20)
                    ]
                }
                respond(
                    req_id,
                    {"content": [{"type": "text", "text": json.dumps(issues)}]},
                )
            elif name == "get_me":
                respond(
                    req_id,
                    {"content": [{"type": "text", "text": json.dumps({"login": "octocat"})}]},
                )
            elif name == "pull_request_read":
                method = ((msg.get("params") or {}).get("arguments") or {}).get("method")
                if method == "get_diff":
                    lines = []
                    for i in range(200):
                        lines.append(f"diff --git a/file_{i} b/file_{i}")
                        lines.append(f"@@ -1,3 +1,3 @@")
                        lines.append(f"-old line {i}")
                        lines.append(f"+new line {i}")
                    respond(
                        req_id,
                        {"content": [{"type": "text", "text": "\n".join(lines)}]},
                    )
                else:
                    respond(
                        req_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {"number": 7, "title": "a PR", "body": "body text"}
                                    ),
                                }
                            ]
                        },
                    )
            else:
                respond(req_id, {"content": [{"type": "text", "text": "ok"}]})
        # notifications（无 id）不回应


if __name__ == "__main__":
    main()