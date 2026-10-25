"""隐私审计：浅克隆公开仓库，扫描别人真实可见的内容。"""

import re
import subprocess
import tempfile
from pathlib import Path

PATTERNS = {
    "Windows 用户名(a)": re.compile(r"Users[\\/]a[\\/ \b\"]", re.I),
    "真实姓名(chengzhi)": re.compile(r"chengzhi", re.I),
    "GitHub 账号(LT326)": re.compile(r"LT326"),
    "邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    "token/密钥": re.compile(r"(gho_|ghu_|ghp_|github_pat_|sk-|AKIA|BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key|password\s*[=:]|secret\s*[=:])", re.I),
    "IP 地址": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "机器盘符(E:/C: 路径)": re.compile(r"[A-Z]:[\\/][A-Za-z]"),
}

def main():
    tmp = Path(tempfile.mkdtemp(prefix="audit-"))
    repo = tmp / "repo"
    subprocess.run(["git", "-c", "http.sslBackend=schannel", "clone", "-q", "--depth", "1",
                    "https://github.com/LT326/mcp-compress-proxy", str(repo)], check=True)
    files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    print(f"扫描 {len(files)} 个文件（公开仓库真实内容）\n")
    summary = {}
    for p in sorted(files):
        rel = p.relative_to(repo)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, pat in PATTERNS.items():
            for m in pat.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                line = text.splitlines()[line_no - 1].strip()[:110]
                key = (name, str(rel))
                summary.setdefault(key, []).append((line_no, line))
    for (name, rel), hits in sorted(summary.items()):
        print(f"[{name}] {rel}  ({len(hits)} 处)")
        for ln, line in hits[:3]:
            print(f"    L{ln}: {line}")
        if len(hits) > 3:
            print(f"    ... 共 {len(hits)} 处")
    if not summary:
        print("未发现任何匹配项")
    print(f"\n=== 汇总 ===")
    names = {}
    for (name, _) in summary:
        names[name] = names.get(name, 0) + 1
    for name, cnt in names.items():
        print(f"  {name}: {cnt} 个文件")

if __name__ == "__main__":
    main()
