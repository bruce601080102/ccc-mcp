"""Runs one Delivery PM planning request in its own process (started by GoalHub).

The process exists so a planning call that takes minutes does not block the console, and so a run
whose process dies can be told apart from one that is still working (it holds a lock for as long as
it runs). It does one thing, once, and exits: there is no loop and nothing here starts by itself.
"""

import argparse

from .core.context import identifier
from .service import CCCMCP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    CCCMCP(args.home).execute_planning_run(identifier(args.run))


if __name__ == "__main__":
    main()
