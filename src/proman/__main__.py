import proman
from proman import reporter

# reporter.initialize_logger(title_number=[2])




# x = mdit.element.code_block(yaml_str, language="yaml").source("console")

from rich.syntax import Syntax
from rich.console import Console

yaml_str = """
test: true
examples:
    ex1: 1
    ex2: 2
"""
x = Syntax(yaml_str, lexer="yaml")
console = Console(force_terminal=True)
console.print(x)




# proman.run()
