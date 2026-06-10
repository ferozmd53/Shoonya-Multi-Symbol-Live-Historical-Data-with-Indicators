from setuptools import setup, find_packages

setup(
    name="Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators",
    version="2.0.0",
    author="ferozmd53",
    description="Shoonya TICK REAL TIME with MULTI SYMBOLS HISTORICAL DATA - StochRSI + Bollinger Bands",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="MIT",
    packages=find_packages(),
    install_requires=[
        "NorenRestApiPy>=0.0.22",  # FIXED: changed from 1.0.0 to 0.0.22
        "xlwings>=0.30.0",
        "numpy>=1.24.0",
        "pandas>=2.0.0",
        "openpyxl>=3.1.0",
    ],
    python_requires=">=3.8",
    include_package_data=True,
    package_data={
        '': ['*.xlsx'],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Financial and Insurance Industry",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
