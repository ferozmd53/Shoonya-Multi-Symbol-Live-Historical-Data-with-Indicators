from setuptools import setup, find_packages

setup(
    name="shoonya-multi-symbol-live-historical-data",
    version="2.0.2",
    author="ferozmd53",
    description="Shoonya multi-symbol live and historical data with Excel indicators",
    license="MIT",
    packages=find_packages(),
    py_modules=["Extreme_Reversal_Signal", "get_auth", "instrument_master", "run"],
    install_requires=[
        "NorenRestApiPy>=0.0.22",
        "xlwings>=0.30.0",
        "numpy>=1.24.0",
        "pandas>=2.0.0",
        "openpyxl>=3.1.0",
        "requests>=2.31.0",
        "pyotp>=2.9.0",
        "websocket-client>=1.8.0",
    ],
    entry_points={
        "console_scripts": [
            "shoonya-trader=run:main",
        ],
    },
    python_requires=">=3.8",
    include_package_data=True,
)
