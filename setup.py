from setuptools import setup, find_packages

setup(
    name="Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators",
    version="2.1.0",
    author="ferozmd53",
    description="Shoonya TICK REAL TIME - StochRSI + Bollinger Bands",
    license="MIT",
    packages=find_packages(),
    py_modules=['Extreme_Reversal_Signal', 'get_auth', 'instrument_master'],
    install_requires=[
        "NorenRestApiPy>=0.0.22",
        "xlwings>=0.30.0",
        "numpy>=1.24.0",
        "pandas>=2.0.0",
        "openpyxl>=3.1.0",
        "requests>=2.31.0",
        "selenium>=4.10.0",
        "pyotp>=2.9.0",
        "webdriver-manager>=4.0.0",
    ],
    python_requires=">=3.8",
    include_package_data=True,
)