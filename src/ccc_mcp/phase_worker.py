"""Looks at one phase run and does what it can do now (started by PhaseHub when something changed).

A short process, not a resident one: it takes the run's lock, makes the next decision from durable
state, acts, looks again if it was woken in the meantime, and exits. Nothing here starts by itself;
a worker exists because an execution finished or a person asked.
"""

import argparse

from .core.context import identifier
from .service import CCCMCP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    CCCMCP(args.home).run_phase_worker(identifier(args.run))


if __name__ == "__main__":
    main()
