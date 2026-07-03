from pathlib import Path
from setuptools import setup, find_packages

from version import VERSION

ROOT = Path(__file__).parent.resolve()

# Read requirements
with open(ROOT / "requirements.txt", "r", encoding="utf-8") as f:
    requirements = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#")
    ]

# Read README (support both README.md and readme.md)
readme_path = ROOT / "README.md"
if not readme_path.exists():
    readme_path = ROOT / "readme.md"
with open(readme_path, "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="penzer-cli",
    version=VERSION,
    description="Penzer CLI - Autonomous Pentesting Agent with User-Driven ReAct Loop",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Eric Penzer",
    author_email="penzer@example.com",
    url="https://github.com/HuzaifaAshraf13/PENZER-CLI",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "penzer=cli:main_entrypoint",
        ],
    },
    include_package_data=True,
    zip_safe=False,
    keywords=[
        "pentesting",
        "agent",
        "llm",
        "ai",
        "security",
        "autonomous",
        "react",
        "gguf",
    ],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Security Researchers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)