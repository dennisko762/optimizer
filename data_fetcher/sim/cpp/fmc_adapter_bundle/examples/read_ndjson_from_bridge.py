import json
import subprocess
from pathlib import Path

from optimizer.adapters import FmcAdapterRegistry


def main() -> None:
    registry = FmcAdapterRegistry()

    # Replace this with your PMDG or iniBuilds bridge executable.
    bridge_exe = Path(r".\build\Release\pmdg777_cdu_listener.exe")

    proc = subprocess.Popen(
        [str(bridge_exe)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert proc.stdout is not None

    for raw_line in proc.stdout:
        line = raw_line.strip()

        # Recommended future bridge output:
        # {"aircraft":"PMDG_777","source":"PMDG_SDK","cdu":0,"lines":["..."]}
        if not line.startswith("{"):
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        lines = event.get("lines", [])
        cdu_index = event.get("cdu", 0)

        snapshot = registry.parse_first(lines, cdu_index=cdu_index)
        if snapshot:
            print(json.dumps(snapshot.to_dict(), indent=2))


if __name__ == "__main__":
    main()
