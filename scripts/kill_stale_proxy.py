"""列出并杀掉 ZCode 拉起的旧代理进程（跑的还是 resultType 修复前的代码）。"""
import subprocess

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

QUERY = (
    "Get-CimInstance Win32_Process | Where-Object { "
    "$_.CommandLine -like '*zcode_entry.py*' -or $_.Name -eq 'mcp-server-git.exe' } | "
    "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json"
)


def query():
    out = subprocess.run(
        [PS, "-NoProfile", "-Command", QUERY],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise SystemExit(f"query failed: {out.stderr[:500]}")
    text = out.stdout.strip()
    if not text:
        return []
    import json
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


procs = query()
print(f"matched processes: {len(procs)}")
for p in procs:
    print(f"  pid={p['ProcessId']} name={p['Name']}")
    print(f"    cmd: {(p.get('CommandLine') or '')[:200]}")

for p in procs:
    subprocess.run(["taskkill", "/PID", str(p["ProcessId"]), "/T", "/F"], capture_output=True)
    print(f"killed pid={p['ProcessId']} ({p['Name']})")

left = query()
print(f"remaining matched: {len(left)}")
