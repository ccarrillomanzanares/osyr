"""Entry point of `osyr`: dispatches to the spaces (net, ui...)."""
import argparse
import sys

from . import ai, audio, backup, bt, disk, doc, files, memory, net, remind, sys as osyr_sys, ui, update, web
from .result import fail


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(f"{self.prog}: {message}")


def build_parser():
    p = Parser(prog="osyr", description="OSYR system command line")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="space", required=True)
    for space in (ai, audio, backup, bt, disk, doc, files, memory, net, remind, osyr_sys, ui, update, web):
        space.register(sub)
    return p


def validate(argv):
    """None if the command exists and its arguments are valid; otherwise, the error.
    The agent uses it before asking for confirmation: nobody is asked about
    commands that do not exist, and the model gets the error to correct itself."""
    try:
        build_parser().parse_args([a for a in argv if a != "--json"])
        return None
    except UsageError as e:
        return str(e)
    except SystemExit:  # --help and the like
        return "help requested"


def main(argv=None):
    p = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv  # valid in any position: osyr net status --json
    try:
        args = p.parse_args([a for a in argv if a != "--json"])
    except UsageError as e:
        return fail("USAGE", "Run: osyr --help", {"error": str(e)}, text=str(e)).emit(as_json)
    args.json = as_json
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as e:  # the JSON contract holds even if something fails
        result = fail("INTERNAL_ERROR", "Run: osyr-diag", {"error": f"{type(e).__name__}: {e}"})
    return result.emit(args.json)


if __name__ == "__main__":
    sys.exit(main())
