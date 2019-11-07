from setuptools import setup

setup(
    name="shale",
    author='Teguh Hofstee',
    url='https://github.com/thofstee/shale',

    python_requires='>=3.7',
    install_requires = [
        "astor",
        "cocotb @ git+http://github.com/thofstee/cocotb.git@timescale#egg=cocotb",
        "genesis2",
        "numpy",
#         "pycoreir",
    ],
)
