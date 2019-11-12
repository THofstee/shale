from pathlib import Path

def gather_apps(app_root):
    apps = []

    for entry in Path(app_root).iterdir():
        if entry.is_dir() and not entry.is_symlink():
            apps += gather_apps(entry)

            for subdir in entry.iterdir():
                if subdir.name == 'bin':
                    apps.append(entry)
                    break

    return apps
