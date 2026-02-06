import argparse

from .commands import history, lookup


def main():
    parser = argparse.ArgumentParser(description="Financial Times Markets CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Register commands
    lookup.setup_parser(subparsers)
    history.setup_parser(subparsers)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
