#!/usr/bin/env python3
import sys


def heading(heading: str):
    spec = {
        1: {"top": 1, "bottom": 0, "len": 110, "style": "\033[1;38;2;150;0;170m"},
        2: {"top": 1, "bottom": 0, "len": 95, "style": "\033[1;38;2;25;100;175m"},
        3: {"top": 1, "bottom": 0, "len": 80, "style": "\033[1;38;2;100;160;0m"},
        4: {"top": 1, "bottom": 0, "len": 65, "style": "\033[1;38;2;200;150;0m"},
        5: {"top": 1, "bottom": 0, "len": 50, "style": "\033[1;38;2;240;100;0m"},
        6: {"top": 1, "bottom": 0, "len": 35, "style": "\033[1;38;2;220;0;35m"},
    }
    colors = [
        (255, 120, 255),
        (0, 255, 255),
        (127, 255, 0),
        (255, 255, 0),
        (255, 200, 55),
        (255, 255, 255),
    ]
    line_color = (255, 100, 100)
    ansi_reset = f"\033[0m"
    number, title = heading.split(" ", 1)
    level = min(len(number.split(".")), len(colors))
    len_heading = len(heading)
    len_margin = 2
    len_console_line = 88
    num_dashes = len_console_line - len_heading - len_margin
    num_dashes_left = num_dashes // 2
    num_dashes_right = num_dashes - num_dashes_left
    dash = "–"
    ansi_heading = make_ansi(colors[level - 1])
    ansi_line = make_ansi(line_color)
    return f"{ansi_line}{dash * num_dashes_left}{ansi_reset} {ansi_heading}{heading.strip()}{ansi_reset} {ansi_line}{dash * num_dashes_right}"


def make_ansi(color: tuple[int, int, int]):
    return f"\033[1;38;2;{color[0]};{color[1]};{color[2]}m"


if __name__ == "__main__":
    # Check if the necessary arguments are provided
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <option 1-6> 'Your string here'")
        sys.exit(1)
    print(heading(sys.argv[1]))

# spec = {
#         "1": {"top": 2, "bottom": 1, "len": 110, "style": "\033[1;38;2;255;255;255;48;2;150;0;170m"},
#         "2": {"top": 1, "bottom": 1, "len": 95, "style": "\033[1;38;2;255;255;255;48;2;25;100;175m"},
#         "3": {"top": 1, "bottom": 1, "len": 80, "style": "\033[1;38;2;255;255;255;48;2;100;160;0m"},
#         "4": {"top": 1, "bottom": 0, "len": 65, "style": "\033[1;38;2;255;255;255;48;2;200;150;0m"},
#         "5": {"top": 1, "bottom": 0, "len": 50, "style": "\033[1;38;2;255;255;255;48;2;240;100;0m"},
#         "6": {"top": 1, "bottom": 0, "len": 35, "style": "\033[1;38;2;255;255;255;48;2;220;0;35m"},
#     }

