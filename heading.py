#!/usr/bin/env python3
import sys


def heading(level, title):
    # Determine line length based on the option
    spec = {
        "1": {"len": 80, "style": "\033[1;48;2;0;162;255m"},
        "2": {"len": 65, "style": "\033[1;48;2;200;120;255m"},
        "3": {"len": 50, "style": "\033[1;48;2;252;189;0m"},
        "4": {"len": 35, "style": "\033[48;2;79;255;15m"},
    }
    if level not in spec:
        print("Invalid option. Choose between 1 and 4.")
        sys.exit(1)
    return f"{spec[level]['style']}{title.center(spec[level]['len'])}"


if __name__ == "__main__":
    # Check if the necessary arguments are provided
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <option 1-4> 'Your string here'")
        sys.exit(1)
    print(heading(sys.argv[1], sys.argv[2]))
