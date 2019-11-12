import argparse
import csv
import logging
import os
from pathlib import Path
import re
import subprocess
from util.apps import gather_apps

cgra_utilization = re.compile(r"PE: (?P<PE>\d+) IO: (?P<IO>\d+) MEM: (?P<MEM>\d+) REG: (?P<REG>\d+)")

def should_run_app(entry, args):
    name = entry.parts[-1]

    # We haven't created the bitstream yet
    if args.force or not (entry/"bin"/f"{name}.bs").exists():
        return True

    # Check if the design has been modified more recently than the
    # bitstream.
    design_mtime = os.path.getmtime(f"{entry}/bin/design_top.json")
    bitstream_mtime = os.path.getmtime(f"{entry}/bin/{name}.bs")
    if bitstream_mtime < design_mtime:
        logging.info(f"`{name}` is already up to date.")
        return False

    return True


def generate_bitstreams(args):
    if args.garnet_flow:
        raise NotImplementedError("This probably isn't correct.")

        for entry in gather_apps("apps"):
            name = entry.parts[-1]

            if should_run_app(entry, args):
                for collateral in os.scandir("/GarnetFlow/scripts/garnet/temp"):
                    os.rename(collateral.path, f"{entry.resolve()}/bin/{collateral.name}")
    else:
        if args.dry_run:
            for entry in args.apps:
                name = entry.parts[-1]

                if should_run_app(entry, args):
                    logging.info(f"Generating bitstream for {entry}")
            return

        with open(f"{args.app_root}/utilization.csv", "w") as f:
            w = csv.DictWriter(f, fieldnames=["name", "PE", "IO", "MEM", "REG"])
            w.writeheader()

            # If there are apps within a folder, grab the apps that are inside that folder
            for entry in args.apps:
                name = entry.parts[-1]

                if should_run_app(entry, args):
                    logging.info(f"Generating bitstream for {entry}")

                    # create mapped.json
                    p = subprocess.run(
                        [
                            "mapper",
                            f"{entry.resolve()}/bin/design_top.json",
                            f"{entry.resolve()}/bin/mapped.json",
                        ],
                        stdout=subprocess.PIPE,
                        text=True,
                    )

                    # create bitstream
                    p = subprocess.run(
                        [
                            "python",
                            "garnet.py",
                            "--width", f"{args.width}",
                            "--height", f"{args.height}",
                            "--no-pd",
                            "--interconnect-only",
                            "--input-app", f"{entry.resolve()}/bin/design_top.json",
                            "--input-file", f"{entry.resolve()}/{name}_input.raw",
                            "--gold-file", f"{entry.resolve()}/{name}_gold.raw",
                            "--output-file", f"{entry.resolve()}/bin/{name}.bs",
                        ],
                        cwd="deps/garnet",
                        stdout=subprocess.PIPE,
                        text=True,
                    )

                    with open (f"{entry.resolve()}/bin/garnet.log", "w") as log:
                        log.write(p.stdout)

                    if p.returncode:
                        logging.error(f"Garnet failed to map `{name}`")
                        logging.debug(p.stdout)
                    else:
                        for collateral in os.scandir("deps/garnet/temp"):
                            os.rename(collateral.path, f"{entry.resolve()}/bin/{collateral.name}")

                        util = cgra_utilization.search(p.stdout)
                        if util:
                            d = util.groupdict()
                            d["name"] = name
                            w.writerow(d)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("apps", nargs="*")
    parser.add_argument("--width", type=int, default=32)
    parser.add_argument("--height", type=int, default=16)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--app-root", type=str, default="apps")
    parser.add_argument("--garnet-flow", action="store_true")
    args = parser.parse_args()
    args.apps = list(map(Path, args.apps))
    args.apps = list(map(lambda x: Path(args.app_root) / x, args.apps))

    generate_bitstreams(args)
