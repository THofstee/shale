import cocotb
from cocotb.clock import Clock
from cocotb.drivers.amba import AXI4LiteMaster
from cocotb.result import TestFailure
from cocotb.triggers import Timer, First, Combine, Join, RisingEdge, FallingEdge, Edge, ReadOnly, with_timeout
from cocotb.utils import get_sim_time
import csv
import json
import numpy as np
from pathlib import Path
import sys
from shale.extras.garnet_driver import GlobalBuffer
from shale.util.rdl import defs as rdl
from shale.util.trace import get_inputs
from vcd import VCDWriter

CLK_PERIOD = 10

def gc_cfg_addr(offset):
    return offset

def gb_cfg_addr(offset, controller=0):
    return int(f"0b1{controller:04b}{offset:08b}", 2)

async def monitor_vcd(entity, signals, filename=None):
    with open(filename, "w") as f:
        with VCDWriter(f) as writer:
            vcd_vars = {}
            triggers = []
            for signal in signals:
                handle = getattr(entity, signal)
                vcd_vars[signal] = writer.register_var(
                    str(entity),
                    signal,
                    "wire",
                    size=len(handle),
                )
                triggers.append(Edge(handle))

            while True:
                await First(*triggers)

                time = get_sim_time()
                for signal in signals:
                    writer.change(vcd_vars[signal], time, getattr(entity, signal).value.binstr)

async def monitor_csv(inst, ports, filename=None):
    ports.remove("clk")
    if filename is None: filename = str(inst) + ".csv"
    with open(filename, "w") as f:
        w = csv.DictWriter(f, fieldnames=ports)
        w.writeheader()
        while True:
            await RisingEdge(inst.clk)
            await ReadOnly()

            step = {}
            for port in ports:
                try:
                    step[port] = int(getattr(inst, port))
                except:
                    step[port] = 0
            w.writerow(step)


@cocotb.test()
async def test_app(dut):
    should_fail = False
    gc = AXI4LiteMaster(dut, "GC", dut.clk)
    gb = GlobalBuffer(dut, "GB", dut.clk)

    instance = "Tile_X01_Y03"
    inst = getattr(dut.DUT.Interconnect_inst0, instance)
    signals = get_inputs((Path(__file__)/"../../shale/extras/garnet.sv").resolve(), instance)
    # cocotb.fork(monitor_vcd(inst, signals, filename="Tile_X01_Y03.vcd"))
    cocotb.fork(monitor_csv(inst, signals, filename="Tile_X01_Y03.csv"))

    # reset
    cocotb.fork(Clock(dut.clk, CLK_PERIOD, 'ns').start())
    dut.JTAG_TCK = 0
    dut.JTAG_TDI = 0
    dut.JTAG_TMS = 0
    dut.JTAG_TRSTn = 1
    dut.reset = 0
    dut.GC_ARADDR = 0
    dut.GC_AWADDR = 0
    dut.GC_WDATA = 0
    dut.GC_WSTRB = 0
    await Timer(CLK_PERIOD * 10, 'ns')
    dut.JTAG_TRSTn = 0
    dut.reset = 1
    await Timer(CLK_PERIOD * 10, 'ns')
    dut.JTAG_TRSTn = 1
    dut.reset = 0

    await gc.write(gc_cfg_addr(rdl['glc.global_reset']), 10)
    await Timer(CLK_PERIOD * 20, 'ns')

    # # stall cgra
    # await gc.write(gc_cfg_addr(rdl['glc.stall']), 0b11)

    # for c in range(16):
    #     await gc.write(gb_cfg_addr(rdl['glb.ld_dma_header_0.start_addr'], controller=c), c)

    # for c in range(16):
    #     res = await gc.read(gb_cfg_addr(rdl['glb.ld_dma_header_0.start_addr'], controller=c))
    #     if not res == c:
    #         print("wat")

    await gb.write(0, 0xdeadbeef)
    res = await gb.read(0)

    h2h = Path("/aha/Halide-to-Hardware/apps/hardware_benchmarks")
    test = h2h / "tests/conv_3_3"
    config_file = test / "bin/conv_3_3.bs.json"

    with open(config_file) as f:
        config = json.load(f)

    def load_bitstream(filename):
        bs = np.loadtxt(filename, converters={
            0: lambda x: int(x, 16),  # addr
            1: lambda x: int(x, 16),  # data
        }, dtype=np.uint32)

        # glb wants addr in upper 32 bits, data in lower 32 bits
        bs = np.fliplr(bs)

        # flatten to 1d and view as packed 64-bit value
        return bs.flatten().view(np.uint64)

    bitstream = load_bitstream(config['bitstream'])

    dut._log.info("Loading bitstream into glb")

    # write bitstream one at a time
    for k, data in enumerate(bitstream):
        addr = k * 8
        await gb.write(addr, int(data))

    # # write bitstream pipelined
    # tasks = [
    #     Join(cocotb.fork(gb.write(idx * 8, int(data))))
    #     for idx, data in enumerate(bitstream)
    # ]
    # await Combine(*tasks)

    # # verify bitstream
    # for k, data in enumerate(bitstream):
    #     addr = k * 8
    #     res = await gb.read(addr)
    #     if res != int(data):
    #         dut._log.error(f"Expected `{data}` but got `{res}`.")
    #         raise TestFailure()

    # set up parallel config
    await gc.write(gb_cfg_addr(rdl['glb.tile_ctrl']), 1<<10)
    await gc.write(gb_cfg_addr(rdl['glb.pc_dma_header.start_addr']), 0)
    await gc.write(gb_cfg_addr(rdl['glb.pc_dma_header.num_cfg']), len(bitstream))

    # enable interrupts
    await gc.write(gc_cfg_addr(rdl['glc.global_ier']), 0b100)
    await gc.write(gc_cfg_addr(rdl['glc.par_cfg_g2f_ier']), 0b1)

    dut._log.info("Configuring tiles")

    # configure tiles
    await gc.write(gc_cfg_addr(rdl['glc.pc_start_pulse']), 1)  # TODO: 1 or ???

    # wait for completion
    await RisingEdge(dut.GC_interrupt)

    # clear interrupt
    await gc.write(gc_cfg_addr(rdl['glc.par_cfg_g2f_isr']), 0b1)

    # wait for interrupt to clear
    await FallingEdge(dut.GC_interrupt)

    dut._log.info("Verifying configuration")

    # verify configuration is as expected
    addrs = bitstream.view(np.uint32)[1::2]
    datas = bitstream.view(np.uint32)[0::2]
    for addr, gold in zip(addrs, datas):
        await gc.write(gc_cfg_addr(rdl['glc.cgra_config.addr']), int(addr))
        await gc.write(gc_cfg_addr(rdl['glc.cgra_config.read']), 10)
        data = await gc.read(gc_cfg_addr(rdl['glc.cgra_config.rd_data']))
        if data != int(gold):
            should_fail = True
            dut._log.error(f"[0x{int(addr):08x}] Expected `0x{int(gold):08x}` but got `0x{int(data):08x}`")

    if should_fail:
        await Timer(CLK_PERIOD * 100, 'ns')
        raise TestFailure()

    def load_image(filename):
        # image is stored as 8bpp raw binary data
        im = np.fromfile(filename, dtype=np.uint8)

        # garnet data bus width is 16-bit
        return im.astype(np.uint16)

    async def glb_cfg_ld(addr,
                         size,
                         stride=1,
                         validate=True,
                         num_active=1,
                         num_inactive=0,
                         controller=0):
        await gc.write(gb_cfg_addr(rdl['glb.ld_dma_header_0.start_addr'], controller), addr)
        await gc.write(gb_cfg_addr(rdl['glb.ld_dma_header_0.active_ctrl'], controller),
                       num_inactive << 16 | num_active)
        await gc.write(gb_cfg_addr(rdl['glb.ld_dma_header_0.iter_ctrl_0'], controller),
                       stride << 21 | size)
        await gc.write(gb_cfg_addr(rdl['glb.ld_dma_header_0.validate'], controller),
                       1 if validate else 0)

    async def glb_cfg_st(addr,
                         size,
                         validate=True,
                         controller=0):
        await gc.write(gb_cfg_addr(rdl['glb.st_dma_header_0.start_addr'], controller), addr)
        await gc.write(gb_cfg_addr(rdl['glb.st_dma_header_0.num_words'], controller), size)
        await gc.write(gb_cfg_addr(rdl['glb.st_dma_header_0.validate'], controller),
                       1 if validate else 0)

    # configure io
    im = load_image(config['input_filename'])
    gold = load_image(config['gold_filename'])

    await glb_cfg_ld(0, len(im))
    await gc.write(gb_cfg_addr(rdl['glb.tile_ctrl']),
                   0b01 << 6 | 0b01<<2)

    await glb_cfg_st(0x80000, len(gold), controller=2)
    await gc.write(gb_cfg_addr(rdl['glb.tile_ctrl'], controller=2),
                   0b01 << 8 | 0b10<<4)

    dut._log.info("Loading input into glb")

    # load data
    for k, data in enumerate(im.view(np.uint64)):
        addr = k * 8
        await gb.write(addr, int(data))

    # enable interrupts
    await gc.write(gc_cfg_addr(rdl['glc.global_ier']), 0b001)
    await gc.write(gc_cfg_addr(rdl['glc.strm_f2g_ier']), 0b100)

    dut._log.info("Running application")

    # execute app
    await gc.write(gb_cfg_addr(rdl['glb.tile_ctrl'], controller=0x1F),
                   0b10 << 11)
    await gc.write(gc_cfg_addr(rdl['glc.stall']), 0b00)
    await gc.write(gc_cfg_addr(rdl['glc.soft_reset']), 17)
    await gc.write(gc_cfg_addr(rdl['glc.stream_start_pulse']), 1)

    # wait for completion
    await with_timeout(RisingEdge(dut.GC_interrupt), 50000, 'ns')

    dut._log.info("Verifying output")

    # verify results
    base = 0x80000
    for k, data in enumerate(gold.view(np.uint64)):
        addr = base + k * 8
        res = await gb.read(addr)
        # dut._log.info(f"0x{int(data):08x}")
        # dut._log.info(res)
        mask = 0x00FF00FF00FF00FF
        if int(res) & mask != data:
            should_fail = True
            dut._log.error(f"Expected `0x{int(data):08x}` but got `0x{int(res) & mask:08x}`.")

    if should_fail:
        await Timer(CLK_PERIOD * 100, 'ns')
        raise TestFailure()

    # wait a bit
    await Timer(CLK_PERIOD * 100, 'ns')

    # avoid vcs segfault (wtf?)
    await RisingEdge(dut.clk)
