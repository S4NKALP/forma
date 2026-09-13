"""
Reads `hyprctl workspacerules -j` into `by_monitor: {name: sorted ws ids}`. Empty
map on a single monitor (no rules): fabric's HyprlandWorkspaces already shows
every open workspace, so the rule range only matters for multi-monitor layouts
or monitor-keyed dot presets.
"""

import json
import subprocess

from fabric.utils import logger


class Workspacerules:
    by_monitor: dict[str, list[int]] = {}

    def __init__(self):
        self.refresh()

    def refresh(self):
        try:
            out = subprocess.run(
                ["hyprctl", "workspacerules", "-j"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            raw = json.loads(out.stdout)
        except Exception as exc:  # noqa: BLE001 - mirror Quickshell's lifetime
            logger.warning(f"[Workspacerules] failed to read rules: {exc}")
            return

        by_monitor: dict[str, list[int]] = {}
        for rule in raw or []:
            mon = rule.get("monitor")
            ws = rule.get("workspaceString") or rule.get("workspace")
            if not mon or not ws:
                continue
            try:
                ws_id = int(ws)
            except TypeError, ValueError:
                continue
            if ws_id < 1:
                continue
            if mon not in by_monitor:
                by_monitor[mon] = []
            if ws_id not in by_monitor[mon]:
                by_monitor[mon].append(ws_id)
        self.by_monitor = {mon: sorted(v) for mon, v in by_monitor.items()}

    def rules_for(self, monitor: str) -> list[int]:
        return list(self.by_monitor.get(monitor, ()))


workspacerules = Workspacerules()
