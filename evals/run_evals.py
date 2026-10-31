#!/usr/bin/env python3
"""evals/run_evals.py: alias of evals/run.py (the 10 golden AST-grep/tool tasks), kept because the 4.0 hand-off names it this way."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    sys.argv[0] = os.path.join(HERE, "run.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
