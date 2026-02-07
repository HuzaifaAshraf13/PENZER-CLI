# setup.py
from setuptools import setup, find_packages

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="penzer-cli",
    version="0.1.0",
    description="Penzer-CLI: Local Cognitive Shell - Advanced AI-powered CLI",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Eric Penzer",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "penzer=cli:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
