"""Kill processes listening on the dev ports, IPv4 + IPv6 (scratch helper)."""
import re
import subprocess

def listeners(port):
    out = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, check=False
    ).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if "LISTENING" not in line:
            continue
        local = parts[1]
        if re.search(rf":{port}$", local):
            pids.add(parts[4])
    return pids

def kill(pid):
    r = subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True)
    print("  kill", pid, "->", (r.stdout or r.stderr).strip()[:80])

for port in (5173, 5174, 8000):
    pids = listeners(port)
    print(port, sorted(pids))
    for p in pids:
        kill(p)
print("done")
