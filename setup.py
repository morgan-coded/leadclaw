from setuptools import setup, find_packages

setup(
    name="leadclaw",
    version="0.1.0",
    description="Lightweight lead tracking CLI for local service businesses",
    author="morgan-coded",
    python_requires=">=3.9",
    install_requires=[
        "anthropic>=0.25.0",
    ],
    py_modules=["db", "seed", "queries", "drafting", "commands", "scheduler"],
    entry_points={
        "console_scripts": [
            "leadclaw=commands:main",
        ],
    },
)
