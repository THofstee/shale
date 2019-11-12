import argparse
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from util.apps import gather_apps
from util.generate_bitstreams import generate_bitstreams as gen_bitstreams

parser = argparse.ArgumentParser()
parser.add_argument("apps", nargs="*")
parser.add_argument("--width", type=int, default=32)
parser.add_argument("--height", type=int, default=16)
parser.add_argument("--force", action="store_true")
parser.add_argument("--skip-garnet", action="store_true")
parser.add_argument("--app-root", type=str, default="apps",
                    help="Sets the base application directory (default: ./apps)")
parser.add_argument("--power", action="store_true",
                    help="Use this flag if you are using this flow for generating power numbers")
parser.add_argument("--garnet-flow", action="store_true")

# Logging
parser.add_argument('-v', '--verbose',
                    action="store_const", const=logging.INFO, default=logging.WARNING)
parser.add_argument('-d', '--debug',
                    action="store_const", const=logging.DEBUG, default=logging.WARNING)

args = parser.parse_args()

logging.basicConfig(level=min(args.verbose, args.debug))

cwd = os.getcwd()
git_up_to_date = re.compile(r"Already up-to-date.")

if len(args.apps) == 0:
    args.apps = gather_apps(args.app_root)

def generate_garnet():
    subprocess.run(
        [
            "git",
            "checkout",
            "flow",
        ],
        cwd="deps/garnet",
        stdout=subprocess.PIPE,
        text=True,
    )

    p = subprocess.run(
        [
            "git",
            "pull",
        ],
        cwd="deps/garnet",
        stdout=subprocess.PIPE,
        text=True,
    )

    if p.returncode:
        logging.warn("Couldn't fetch latest updates.")
        up_to_date = True
    else:
        up_to_date = git_up_to_date.search(p.stdout)

    if args.force:
        up_to_date = False

    if up_to_date:
        print(f"INFO: `garnet.v` is already up to date.")
    else:
        print(f"INFO: Generating `garnet.v`...")
        extra_args = []
        if args.power:
            extra_args += [
                "--no_sram_stub",
            ]

        subprocess.run(
            [
                "python",
                "garnet.py",
                "--width", f"{args.width}",
                "--height", f"{args.height}",
                "--verilog",
                *extra_args,
            ],
            cwd="deps/garnet",
            stdout=subprocess.PIPE,
            text=True,
        )

    garnet_sv = Path("extras/garnet.sv")
    if not (garnet_sv.is_symlink() or garnet_sv.is_file()):
        os.symlink(f"{cwd}/deps/garnet/garnet.v", garnet_sv)

def generate_makefile():
    with open("extras/Makefile", "w") as f:
        f.write(f"""
VERILOG_SOURCES ?= \\
    {cwd}/deps/garnet/tests/AO22D0BWP16P90.sv \\
    {cwd}/deps/garnet/tests/AN2D0BWP16P90.sv \\
    {cwd}/deps/garnet/global_buffer/genesis/TS1N16FFCLLSBLVTC2048X64M8SW.sv \\
    {cwd}/deps/garnet/memory_core/genesis_new/TS1N16FFCLLSBLVTC512X16M8S.sv \\
    {cwd}/deps/garnet/peak_core/DW_fp_add.v \\
    {cwd}/deps/garnet/peak_core/DW_fp_mult.v \\
    {cwd}/deps/garnet/genesis_verif/memory_core.sv \\
    {cwd}/deps/garnet/genesis_verif/mem_unq1.v \\
    {cwd}/deps/garnet/genesis_verif/sram_stub_unq1.v \\
    {cwd}/deps/garnet/genesis_verif/doublebuffer_control_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/sram_control_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/fifo_control_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/linebuffer_control_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/global_buffer.sv \\
    {cwd}/deps/garnet/genesis_verif/global_buffer_int.sv \\
    {cwd}/deps/garnet/genesis_verif/memory_bank.sv \\
    {cwd}/deps/garnet/genesis_verif/bank_controller.sv \\
    {cwd}/deps/garnet/genesis_verif/glbuf_memory_core.sv \\
    {cwd}/deps/garnet/genesis_verif/cfg_controller.sv \\
    {cwd}/deps/garnet/genesis_verif/cfg_address_generator.sv \\
    {cwd}/deps/garnet/genesis_verif/sram_controller.sv \\
    {cwd}/deps/garnet/genesis_verif/memory.sv \\
    {cwd}/deps/garnet/genesis_verif/io_controller.sv \\
    {cwd}/deps/garnet/genesis_verif/io_address_generator.sv \\
    {cwd}/deps/garnet/genesis_verif/sram_gen.sv \\
    {cwd}/deps/garnet/genesis_verif/host_bank_interconnect.sv \\
    {cwd}/deps/garnet/genesis_verif/global_controller.sv \\
    {cwd}/deps/garnet/genesis_verif/axi_ctrl_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/jtag_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/cfg_and_dbg_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/flop_unq3.sv \\
    {cwd}/deps/garnet/genesis_verif/flop_unq2.sv \\
    {cwd}/deps/garnet/genesis_verif/flop_unq1.sv \\
    {cwd}/deps/garnet/genesis_verif/tap_unq1.sv \\
    {cwd}/extras/CW_tap.v \\
    {cwd}/extras/garnet.sv \\
    {cwd}/extras/garnet_top.sv

TESTCASE?=test_app
TOPLEVEL?=Garnet_TB
TOPLEVEL_LANG=verilog
MODULE=tb
TIMESCALE=1ps/1ps

ifeq ($(SIM), vcs)
    override COMPILE_ARGS += -LDFLAGS -Wl,--no-as-needed
    override COMPILE_ARGS += -top $(TOPLEVEL)
    override COMPILE_ARGS += -timescale=$(TIMESCALE)
else ifeq ($(SIM), ius)
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6
    override SIM_ARGS += -timescale $(TIMESCALE)
else ifeq ($(SIM), xcelium)
    SHM_RESET_DEFAULTS=1
    override SIM_ARGS += -timescale $(TIMESCALE)
endif

include $(shell cocotb-config --makefiles)/Makefile.inc
include $(shell cocotb-config --makefiles)/Makefile.sim
""")


def generate_bitstreams():
    subprocess.run(
        [
            "git",
            "checkout",
            "master",
        ],
        cwd="deps/garnet",
        stdout=subprocess.PIPE,
        text=True,
    )

    p = subprocess.run(
        [
            "git",
            "pull",
        ],
        cwd="deps/garnet",
        stdout=subprocess.PIPE,
        text=True,
    )

    if p.returncode:
        logging.warn("Couldn't fetch latest updates.")
    else:
        if not git_up_to_date.search(p.stdout):
            args.force = True

    gen_bitstreams(args)

def generate_testbenches(apps):
    for app in apps:
        os.makedirs(f"{app}/test", exist_ok=True)
        
        subprocess.run(
            [
                "python",
                "test.py",
                app,
                "--width", f"{args.width}",
            ],
        )

        if os.path.islink(f"{app}/test/Makefile"):
            os.remove(f"{app}/test/Makefile")

        if not os.path.exists(f"{app}/test/Makefile"):
            os.symlink(f"{cwd}/extras/Makefile", f"{app}/test/Makefile")


if args.garnet_flow:
    # We need a different version of Garnet than TBG, so we'll just
    # generate our own.
    generate_garnet()

    # Grab CW_tap.v
    if not os.path.exists("extras/CW_tap.v"):
        cw_tap_f = "/cad/cadence/GENUS17.21.000.lnx86/share/synth/lib/chipware/sim/verilog/CW/CW_tap.v"
        os.symlink(cw_tap_f, "extras/CW_tap.v")

    # Create makefile
    generate_makefile()

    # Create bitstream
    gen_bitstreams(args)

    # Run testbenches
    for app in args.apps:
        # Symlink the GarnetFlow-generated bitstream
        if not os.path.exists(f"{app}/bin/{app}.bs"):
            os.symlink(f"/tmp/{app}.bs", f"{app}/bin/{app}.bs")

        # Create testbench
        subprocess.run(
            [
                "python",
                "test.py",
                app,
                "--width", f"{args.width}",
                "--garnet-flow",
            ],
        )

        if os.path.islink(f"{app}/test/Makefile"):
            os.remove(f"{app}/test/Makefile")

        if not os.path.exists(f"{app}/test/Makefile"):
            os.symlink(f"{cwd}/extras/Makefile", f"{app}/test/Makefile")

        # Run top-level testbench
        p = subprocess.run(
            [
                "make",
                "SIM=vcs",
            ],
            cwd=f"{app}/test",
        )

        # Verify outputs
        subprocess.run(
            [
                "python",
                "test.py",
                app,
                "--verify-trace",
                "--garnet-flow",
            ],
        )
else:
    if not args.skip_garnet:
        generate_garnet()

    generate_makefile()
    generate_bitstreams()
    generate_testbenches(args.apps)

    # We want garnet to be on the flow branch for running testbenches
    # because of the extra Verilog stubs
    subprocess.run(
        [
            "git",
            "checkout",
            "flow",
        ],
        cwd="deps/garnet",
        stdout=subprocess.PIPE,
        text=True,
    )
