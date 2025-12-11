#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Setup script for mod package
"""

from setuptools import setup, find_packages
import os
import sys

# Get the long description from README
this_dir = os.path.abspath(os.path.dirname(__file__))
readme_path = os.path.join(this_dir, 'README.md')
with open(readme_path, 'r', encoding='utf-8') as fh:
    long_description = fh.read()

# Get version from mod package if possible
version = '0.1.0'  # default
mod_path = os.path.join(this_dir, 'mod', '__init__.py')
if os.path.exists(mod_path):
    import re
    with open(mod_path, 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if match:
        version = match.group(1)

# Core dependencies
install_requires = [
    # network
    'fastapi>=0.115.13',
    'sse-starlette>=2.1,<2.3.7',
    'paramiko>=3.5.1',
    'nest_asyncio>=1.6.0',
    'uvicorn>=0.22.0',  # updated to avoid Python 3.12 loop_factory issues
    'hypercorn>=0.14.0',
    'aiohttp>=3.12.13',
    'msgpack_numpy>=0.4.8',
    'netaddr>=1.3.0',
    'pyyaml>=6.0.2',
    'websocket-client>=0.57.0',
    'certifi>=2019.3.9',
    'idna>=2.1.0',
    'requests>=2.21.0',

    # misc
    'aiofiles>=24.1.0',
    'loguru>=0.7.3',
    'xxhash>=1.3.0',
    'python-dotenv',
    'pandas>=2.3.0',
    'rich>=13.6.0',
    'munch>=4.0.0',

    # ai
    'safetensors>=0.5.3',
    'openai>=1.91.0',
    'torch>=2.7.1',

    # crypot
    'scalecodec>=1.2.10,<1.3',
    'base58>=1.0.3',
    'ecdsa>=0.17.0',
    'eth-keys>=0.2.1',
    'eth_utils>=1.3.0',
    'pycryptodome>=3.11.0',
    'PyNaCl>=1.0.1',
    'py-sr25519-bindings>=0.2.0',
    'py-ed25519-zebra-bindings>=1.0',
    'py-bip39-bindings>=0.1.9',
    'psutil>=7.0.0',
]

# Optional dependencies
extras_require = {
    'quality': [
        'black==22.3',
        'click==8.0.4',
        'isort>=5.5.4',
        'flake8>=3.8.3',
    ],
    'testing': [
        'pytest>=7.2.0',
    ],
}
extras_require['all'] = extras_require['quality'] + extras_require['testing']

setup(
    name='mod',
    version=version,
    description='Global toolbox that allows you to connect and share any tool (module)',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='developers',
    author_email='bloc@proton.me',
    url='https://modchain.org/',
    project_urls={
        'Homepage': 'https://modchain.ai/',
        'Repository': 'https://github.com/mod-chain/modsdk',
        'Issues': 'https://github.com/mod-chain/modsdk/issues',
    },
    packages=find_packages(exclude=['tests*', 'docs*']),
    include_package_data=True,
    python_requires='>=3.8, <3.13',  # restrict to Python <3.13 to avoid asyncio issues
    install_requires=install_requires,
    extras_require=extras_require,
    entry_points={
        'console_scripts': ['m=mod:main','c=mod:main' ],
    },
    keywords=['modular', 'sdk', 'ai', 'crypto'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: Implementation :: CPython',
    ],
    license='MIT',
    zip_safe=False,
)
