#!/usr/bin/env python3
import sys


def heading(level, title):
    spec = {
        "1": {"top": 2, "bottom": 1, "len": 110, "style": "\033[1;38;2;255;255;255;48;2;150;0;170m"},
        "2": {"top": 1, "bottom": 1, "len": 95, "style": "\033[1;38;2;255;255;255;48;2;25;100;175m"},
        "3": {"top": 1, "bottom": 1, "len": 80, "style": "\033[1;38;2;255;255;255;48;2;100;160;0m"},
        "4": {"top": 1, "bottom": 0, "len": 65, "style": "\033[1;38;2;255;255;255;48;2;200;150;0m"},
        "5": {"top": 1, "bottom": 0, "len": 50, "style": "\033[1;38;2;255;255;255;48;2;240;100;0m"},
        "6": {"top": 1, "bottom": 0, "len": 35, "style": "\033[1;38;2;255;255;255;48;2;220;0;35m"},
    }
    if level not in spec:
        print("Invalid option. Choose between 1 and 4.")
        sys.exit(1)
    margin_top = "\n" * spec[level]['top']
    margin_bottom = "\n" * spec[level]['bottom']
    return f"{margin_top}{spec[level]['style']}{title.center(spec[level]['len'])}{margin_bottom}"


if __name__ == "__main__":
    # Check if the necessary arguments are provided
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <option 1-4> 'Your string here'")
        sys.exit(1)
    print(heading(sys.argv[1], sys.argv[2]))
