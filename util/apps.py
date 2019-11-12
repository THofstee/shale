from pathlib import Path
import os

# TODO: should this take apps and then filter to the apps specified?
def gather_apps(app_root):
    apps = []
    for root, dirs, files in os.walk(app_root):
        entry = Path(root)
        if entry.is_dir() and (entry/"bin").is_dir():
            apps.append(entry)
    return apps
