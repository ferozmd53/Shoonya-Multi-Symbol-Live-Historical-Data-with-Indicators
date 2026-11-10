from setuptools import setup, find_packages

setup(
    name="Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators",
    version="2.1.1",
    author="ferozmd53",
    description="Shoonya multi-symbol live and historical trading system with indicators",
    license="MIT",
    packages=find_packages(),
    py_modules=["Extreme_Reversal_Signal", "get_auth", "instrument_master"],
    install_requires=[
        "NorenRestApiOAuth>=0.0.41",
        "xlwings>=0.30.0",
        "numpy>=1.24.0",
        "pandas>=2.0.0",
        "openpyxl>=3.1.0",
        "requests>=2.31.0",
        "pyotp>=2.9.0",
        "websocket-client>=1.8.0",
    ],
    python_requires=">=3.8",
    include_package_data=True,
)
