#!/usr/bin/env python3
"""Entry point. Run from the repo root: python solution/ledger_cli.py --help"""
import sys

from ledger.cli import main

if __name__ == "__main__":
    sys.exit(main())
