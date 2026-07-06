"""
Setup script for LocalFS
"""

from setuptools import setup, find_packages

setup(
    name="localfs",
    version="0.1.0",
    description="Content-addressable local filesystem storage with Python-to-Rust bindings",
    author="Mod Framework",
    python_requires=">=3.8",
    packages=find_packages(),
    install_requires=[
        "base58>=2.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "black>=23.0",
        ],
        "rust": [
            "maturin>=1.0,<2.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "localfs-test=localfs.mod:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Rust",
    ],
)
