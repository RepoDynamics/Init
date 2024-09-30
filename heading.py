#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
from typing import TYPE_CHECKING
import re
import json

if TYPE_CHECKING:
    from typing import Sequence


PATH_PYPROJECT = Path(__file__).parent / "pyproject.toml"
LEN_CONSOLE_LINE = 88

VERSION_PREFIX = "Version "
VERSION_COLOR_PREFIX = (75, 100, 160)
VERSION_COLOR_VERSION = (0, 100, 200)
HEADING_COLORS = [
    (255, 200, 255),
    (235, 160, 255),
    (215, 120, 255),
    (195, 80, 255),
    (175, 40, 255),
    (155, 0, 255),
]
LINE_COLORS = [
    (250, 250, 230),
    (220, 220, 200),
    (190, 190, 170),
    (160, 160, 140),
    (130, 130, 110),
    (100, 100, 80),
]
BOX_COLOR = (100, 100, 100)
BOX_TOP_LEFT = "╭"
BOX_TOP = "─"
BOX_TOP_RIGHT = "╮"
BOX_RIGHT = "│"
BOX_BOTTOM_RIGHT = "╯"
BOX_BOTTOM = "─"
BOX_BOTTOM_LEFT = "╰"
BOX_LEFT = "│"
MARGIN_HOR = 2
MARGIN_VER = 1
LOGO_COLOR_REPO = (245, 140, 0)
LOGO_COLOR_DYNAMICS = (0, 100, 200)
LOGO_REPO = """
╭━━━╮         
┃╭━╮┃         
┃╰━╯┣━━┳━━┳━━╮
┃╭╮╭┫┃━┫╭╮┃╭╮┃
┃┃┃╰┫┃━┫╰╯┃╰╯┃
╰╯╰━┻━━┫╭━┻━━╯
       ┃┃     
       ╰╯     
"""
LOGO_DYNAMICS = """
╭━━━╮                    
╰╮╭╮┃                    
 ┃┃┃┣╮ ╭┳━╮╭━━┳╮╭┳┳━━┳━━╮
 ┃┃┃┃┃ ┃┃╭╮┫╭╮┃╰╯┣┫╭━┫━━┫
╭╯╰╯┃╰━╯┃┃┃┃╭╮┃┃┃┃┃╰━╋━ ┃
╰━━━┻━╮╭┻╯╰┻╯╰┻┻┻┻┻━━┻━━╯
    ╭━╯┃
    ╰━━╯                 
"""

def heading(content: str):
    number, title = content.split(" ", 1)
    level = min(len(number.removesuffix(".").split(".")), len(HEADING_COLORS))
    len_heading = len(content)
    len_margin = 2
    num_dashes = LEN_CONSOLE_LINE - len_heading - len_margin
    num_dashes_left = num_dashes // 2
    num_dashes_right = num_dashes - num_dashes_left
    line_char = "–"
    heading_text = _apply_style(content.strip(), HEADING_COLORS[level - 1], bold=True)
    line_left = _apply_style(line_char * num_dashes_left, LINE_COLORS[level - 1], bold=True)
    line_right = _apply_style(line_char * num_dashes_right, LINE_COLORS[level - 1], bold=True)
    return f"{line_left} {heading_text} {line_right}"


def logo(
    brand_parts: Sequence[tuple[str, tuple[int, int, int]]] = (
        (LOGO_REPO, LOGO_COLOR_REPO),
        (LOGO_DYNAMICS, LOGO_COLOR_DYNAMICS),
    ),
    product_parts: Sequence[tuple[str, tuple[int, int, int]]] = (
        (LOGO_PRO, LOGO_COLOR_PRO),
        (LOGO_MAN, LOGO_COLOR_MAN),
    ),
    brand_product_separator: str = "  ",
):
    brand_parts = [
        _logo_prepare_part(part, color)
        for part, color in brand_parts
    ]
    product_parts = [
        _logo_prepare_part(part, color)
        for part, color in product_parts
    ]
    brand_logo = assemble_logo_parts(brand_parts, separator="")
    product_logo = assemble_logo_parts(product_parts, separator="")
    brand_logo_len = sum(part[1] for part in brand_parts)
    product_logo_len = sum(part[1] for part in product_parts)
    full_logo = assemble_logo_parts(
        [
            (brand_logo, brand_logo_len),
            (product_logo, product_logo_len),
        ],
        separator=brand_product_separator,
    )
    logo_len = brand_logo_len + product_logo_len + len(brand_product_separator)
    box_len = max(LEN_CONSOLE_LINE, logo_len + 2 * MARGIN_HOR)
    total_hor_spaces = box_len - logo_len - 2
    spaces_left = (total_hor_spaces // 2)
    spaces_right = total_hor_spaces - spaces_left
    ver_margin_lines = [_apply_style(f'{BOX_LEFT}{" " * (box_len - 2)}{BOX_RIGHT}', BOX_COLOR)] * MARGIN_VER
    boxed_logo = [
        "",
        _apply_style(f"{BOX_TOP_LEFT}{BOX_TOP * (box_len - 2)}{BOX_TOP_RIGHT}", BOX_COLOR),
        *ver_margin_lines,
    ]
    for line in full_logo:
        boxed_logo.append(
            f'{_apply_style(BOX_LEFT, BOX_COLOR)}{spaces_left * " "}{line}{spaces_right * " "}{_apply_style(BOX_RIGHT, BOX_COLOR, bold=True)}'
        )

    pyproject = PATH_PYPROJECT.read_text()
    version = re.findall(r'^version\s+=\s*"([^"]*)"', pyproject, re.MULTILINE)[0]
    version_str = f" {_apply_style(VERSION_PREFIX, VERSION_COLOR_PREFIX)}{_apply_style(version, VERSION_COLOR_VERSION)} "
    version_str_len = len(VERSION_PREFIX) + len(version) + 2
    num_chars = box_len - version_str_len - 2
    chars_left = num_chars // 2
    chars_right = num_chars - chars_left
    line_left = _apply_style(f"{BOX_BOTTOM_LEFT}{BOX_BOTTOM * chars_left}", BOX_COLOR)
    line_right = _apply_style(f"{BOX_BOTTOM * chars_right}{BOX_BOTTOM_RIGHT}", BOX_COLOR)
    boxed_logo.extend(
        [*ver_margin_lines, f"{line_left}{version_str}{line_right}", ""]
    )
    return "\n".join(boxed_logo)


def assemble_logo_parts(parts: Sequence[tuple[Sequence[str], int]], separator: str):
    max_lines = max(len(part[0]) for part in parts)
    out_lines = []
    for i in range(max_lines):
        line = separator.join(part[0][i] if i < len(part[0]) else " " * part[1] for part in parts)
        out_lines.append(line)
    return out_lines


def _logo_prepare_part(part: str, color: tuple[int, int, int]):
    lines = [line.rstrip() for line in part.strip().splitlines()]
    max_len = max(len(line) for line in lines)
    out_lines = [
        _apply_style(line.ljust(max_len), color)
        for line in lines
    ]
    return out_lines, max_len


def _apply_style(text: str, color: tuple[int, int, int], bold: bool = False):
    return f"\033[{'1;' if bold else ''}38;2;{color[0]};{color[1]};{color[2]}m{text}\033[0m"


if __name__ == "__main__":
    # Check if the necessary arguments are provided
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} 'X.Y.Z Title'")
        sys.exit(1)
    arg = sys.argv[1]
    if arg == "logo":
        print(logo())
    else:
        print(heading(sys.argv[1]))
