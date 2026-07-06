"""
Firecracker microVM management for the mod framework.

Thin wrapper — implementation lives in src/mod.py.
"""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DIR, 'src'))

from mod import Mod
