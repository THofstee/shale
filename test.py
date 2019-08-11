# TODO: flatten nested dimensions that still have linear access
# TODO: handle multidimensional outputs

import csv
import json
from commands import *

import ast
import astor
import textwrap

import argparse

def parse_ast(s):
    return ast.parse(textwrap.dedent(s))

def print_ast(tree):
    print(astor.to_source(tree))

def process_inst(inst):
    args = inst['genargs']

    num_bits = args['width'][1]
    num_elems = args['depth'][1]
    if num_bits != 16:
        raise NotImplementedError("Non 16-bit inputs are not supported.")

    for k,v in args.items():
        print(k, v)

    dims = []
    for k in range(args['dimensionality'][1]):
        stride = args[f"stride_{k}"][1]
        length = args[f"range_{k}"][1]
        dims.append((length, stride))

    return {
        "addr": None,
        "location": 0,
        "dims": dims,
        "size": num_elems,
        "nbytes": (num_bits // 8) * num_elems,
    }


parser = argparse.ArgumentParser(description="""
A simple SoC stub to test application flow of the CGRA.
""")

parser.add_argument('--verify-trace', action='store_true')
args = parser.parse_args()

# app = "handcrafted_ub_conv_3_3"
# app = "handcrafted_ub_layer_gb"
app = "conv_3_3"

with open(app + ".json", "r") as f:
    js = json.load(f)
    with open(app + ".map.json", "r") as f2:
        mapping = json.load(f2)

    print(mapping['inputs'])
    print(mapping['outputs'])

    instances = js['namespaces']['global']['modules']['DesignTop']['instances']

    inputs = []
    for i in mapping['inputs']:
        name = i[0]
        i = i[1]
        inst = instances[i['instance']]
        _in = process_inst(inst)
        _in['location'] = i['location']

        if i.get('num_active'):
            num_active = i['num_active']
        else:
            num_active = _in['dims'][0][0]

        if i.get('num_inactive'):
            num_inactive = i['num_inactive']
        else:
            num_inactive = instances['ub_' + name]['genargs']['iter_cnt'][1] - num_active

        _in['num_active'] = num_active
        _in['num_inactive'] = num_inactive
        _in['double_buffered'] = False
        _in['kind'] = 'input'

        # TODO this is metadata for testing... not sure where to put it...
        _in['file'] = i['file']
        _in['trace'] = i['trace']
        _in['name'] = name

        inputs.append(_in)

    outputs = []
    for o in mapping['outputs']:
        name = o[0]
        o = o[1]
        inst = instances[o['instance']]
        _out = process_inst(inst)
        _out['location'] = o['location']
        _out['kind'] = 'output'

        # TODO this is metadata for testing... not sure where to put it...
        _out['file'] = o['file']
        _out['trace'] = o['trace']
        _out['name'] = name

        outputs.append(_out)

    def allocate_gb(inputs, outputs):
        bank = 0
        for _in in inputs:
            _in['addr'] = BANK_ADDR(bank)
            bank += 4

        for _out in outputs:
            _out['addr'] = BANK_ADDR(bank)
            bank += 4

    allocate_gb(inputs, outputs)

    def create_function(name,
                        args=[],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[]):

        return ast.FunctionDef(
            name=name,
            args=ast.arguments(
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=[],
            decorator_list=[]
        )

    def process_input(_in):
        assert _in['dims'][0][1] == 1, "ERROR: Innermost loop accesses must be linear."

        temp = create_function(f"stream_{_in['location']}")
        temp.decorator_list.append(ast.Attribute(
            value=ast.Name(id='cocotb'),
            attr='coroutine',
        ))

        context = []
        curr_body = temp.body
        context.append(curr_body)

        idxs = [hex(_in['addr'])]
        for k, dim in enumerate(_in['dims'][1:][::-1]):
            curr_body.append(parse_ast(f"""
            for x{k} in range({dim[0]}):
                idx{k} = x{k} * {dim[1]} * 2
            """).body[0])
            idxs.append(f"idx{k}")
            context.append(curr_body[-1].body)
            curr_body = context[-1]

        # Initial config
        if len(idxs) > 1:
            conds = [f"x{k} == 0" for k in range(len(idxs)-1)]
            cond = " and ".join(conds)
            curr_body.append(parse_ast(f"""
            if {cond}:
                pass
            """).body[0])
            context.append(curr_body[-1].body)
            curr_body = context[-1]
            del curr_body[0]

        if _in['double_buffered']:
            curr_body.append(parse_ast(f"""
            for command in configure_io(mode=IO_INPUT_STREAM,
                         addr={" + ".join(idxs)},
                         size={_in['dims'][0][0]},
                         io_ctrl={_in['location']},
                         num_active={_in['num_active']},
                         num_inactive=0,
                         width=32):
                yield gc.write(command.addr, command.data)
            """).body[0])
        else:
            curr_body.append(parse_ast(f"""
            for command in configure_io(mode=IO_INPUT_STREAM,
                         addr={" + ".join(idxs)},
                         size={_in['dims'][0][0]},
                         io_ctrl={_in['location']},
                         num_active={_in['num_active']},
                         num_inactive={_in['num_inactive']},
                         width=32):
                yield gc.write(command.addr, command.data)
            """).body[0])

        curr_body.append(parse_ast(f"""
        init_done[{_in['location']}].set()
        """).body[0])

        curr_body.append(parse_ast(f"""
        yield start_done.wait()
        """).body[0])


        context.pop()
        curr_body = context[-1][-1].orelse

        # Iteration config
        if len(idxs) > 1:
            curr_body.append(parse_ast(f"""
            for command in configure_io(mode=IO_INPUT_STREAM,
                         addr={" + ".join(idxs)},
                         size={_in['dims'][0][0]},
                         io_ctrl={_in['location']},
                         num_active={_in['num_active']},
                         num_inactive={_in['num_inactive']},
                         width=32):
                yield gc.write(command.addr, command.data)
            """).body[0])

            curr_body += parse_ast(f"""
            yield gc.write(IO_AUTO_RESTART_REG({_in['location']}), 1)
            # dut._log.info("Waiting for input auto_restart...")
            yield FallingEdge(auto_restart_instream[{_in['location']}])
            """).body


        # for k, dim in enumerate(_in['dims'][1:]):
        #     old_body = temp.body
        #     temp.body = parse_ast(f"""
        #     for x{k} in range({dim[0]}):
        #         idx{k} = x{k} * {dim[1]}
        #     """).body
        #     temp.body[0].body += old_body
        #     print(dim)

        # print(astor.dump_tree(temp))
        print_ast(temp)

    def process_output(_out):
        assert _out['dims'][0][1] == 1, "ERROR: Innermost loop accesses must be linear."

        temp = create_function(f"stream_{_out['location']}")
        temp.decorator_list.append(ast.Attribute(
            value=ast.Name(id='cocotb'),
            attr='coroutine',
        ))

        temp.body.append(parse_ast(f"""
        for command in configure_io(mode=IO_OUTPUT_STREAM,
                     addr={_out['addr']},
                     size={_out['dims'][0][0]},
                     io_ctrl={_out['location']},
                     width=32):
            yield gc.write(command.addr, command.data)
        """).body[0])

        temp.body.append(parse_ast(f"""
        init_done[{_out['location']}].set()
        """).body[0])

        print_ast(temp)

    print("start_done = Event()")
    print(f"init_done = [{', '.join(['Event()' for _ in range(len(inputs) + len(outputs))])}]")
    for _in in inputs:
        process_input(_in)

    for _out in outputs:
        process_output(_out)

    def index(dims):
        xs = [ 0 for _ in range(len(dims)) ]

        def increment(idx):
            xs[idx] += 1
            if xs[idx] == dims[idx][0]:
                xs[idx] = 0
                return True
            return False

        k = 0
        while k < len(dims):
            idx = 0
            for i,x in enumerate(xs):
                idx += x * dims[i][1]
            yield idx

            k = 0
            while increment(k):
                k += 1
                if k == len(dims):
                    break

    def read_csv(fname):
        with open(fname) as f:
            reader = csv.reader(f, skipinitialspace=True, quoting=csv.QUOTE_NONNUMERIC)
            for row in reader:
                return list(map(int, row[:-1]))

    def validate(i):
        data = np.fromfile(i['file'], dtype=np.uint8)
        trace = np.array(read_csv(i['trace']), dtype=np.uint8)
        gold = [data[k] for k in index(i['dims'])]

        print(f"Validating {i['name']}...")
        if len(gold) != len(trace):
            print(f"ERROR: Expected {len(gold)} values but got {len(trace)} instead.")

        for k in range(len(trace)):
            if gold[k] != trace[k]:
                print(f"ERROR ({k}): Expected {hex(gold[k])} but got {hex(trace[k])}.")
        print("Done.")

    if args.verify_trace:
        for i in inputs:
            validate(i)

        for o in outputs:
            validate(o)

    # def index(dims, idx):
    #     if len(dims) == 0:
    #         yield idx
    #     else:
    #         for x in range(dims[0][0]):
    #             for k in index(dims[1:], idx + x * dims[0][1] ):
    #                 yield k

    # # print([ x for x in index([(256,1), (2,0)][::-1], 0)])
    # # print([ x for x in index(inputs[0]['dims'], 0) ])
    # # print(inputs[0]['dims'])

    # TODO: go through everything and figure out sizes to place in the global buffer
    # TODO: need to get location from gb_args
    # TODO: need to calculate inputs and outputs reserved space
    # TODO: support for double buffering
    # TODO: addressing for double buffering
    # TODO: allocate into global buffer regions
    # TODO: which input ports are these going to?
    # TODO: is the input expected to be every single cycle?

    # inputs = []
    # inputs.append({
    #     "addr": BANK_ADDR(0),
    #     "location": 0,
    #     "size": num_inputs,
    #     "nbytes": (num_bits // 8) * num_inputs,
    # })

    # outputs = []
    # outputs.append({
    #     "addr": BANK_ADDR(16),
    #     "location": 1,
    #     "size": prod([ x[0] for x in dims ]),
    #     "nbytes": (num_bits // 8) * prod([ x[0] for x in dims ]),
    # })

    # print(inputs)
    # print(outputs)

    # print([
    #     # WRITE_REG(GLOBAL_RESET_REG, 1), # TODO: delete?
    #     # Stall the CGRA
    #     WRITE_REG(STALL_REG, 0b1111),

    #     # Enable interrupts
    #     WRITE_REG(INTERRUPT_ENABLE_REG, 0b11),

    #     # Configure the CGRA
    #     PRINT("Configuring CGRA..."),
    #     # *gc_config_bitstream(self.bitstream),
    #     *gb_config_bitstream(self.bitstream, width=self.args.width),
    #     PRINT("Done."),

    #     # Set up global buffer for pointwise
    #     *configure_io(
    #         IO_INPUT_STREAM,
    #         inputs[0]["addr"],
    #         inputs[0]["size"],
    #         io_ctrl=inputs[0]["location"],
    #         # num_active=inputs[0]["num_active"],
    #         # num_inactive=inputs[0]["num_inactive"],
    #         width=self.args.width
    #     ),
    #     *configure_io(
    #         IO_INPUT_STREAM,
    #         inputs[1]["addr"],
    #         inputs[1]["size"],
    #         io_ctrl=inputs[1]["location"],
    #         num_active=inputs[1]["num_active"],
    #         num_inactive=inputs[1]["num_inactive"],
    #         width=self.args.width
    #     ),
    #     *configure_io(
    #         IO_OUTPUT_STREAM,
    #         outputs[0]["addr"],
    #         outputs[0]["size"],
    #         io_ctrl=outputs[0]["location"],
    #         width=self.args.width
    #     ),

    #     # Put image into global buffer
    #     PRINT("Transferring input data..."),
    #     WRITE_DATA(BANK_ADDR(0), 0xc0ffee, im.nbytes, im),
    #     PRINT("Done."),

    #     # Start the application
    #     PRINT("Starting application..."),
    #     WRITE_REG(STALL_REG, 0),
    #     PEND(0b01, "start"),
    #     WRITE_REG(CGRA_START_REG, 1),
    #     PRINT("Waiting for completion..."),
    #     WAIT(0b01, "start"),
    #     PRINT("Done."),

    #     PRINT("Reading output data..."),
    #     READ_DATA(
    #         BANK_ADDR(16),
    #         gold.nbytes,
    #         gold,
    #         _file=self.outfile,
    #     ),
    #     PRINT("All tasks complete!"),
    # ])
