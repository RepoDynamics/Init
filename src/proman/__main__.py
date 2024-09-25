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
x = Syntax(yaml_str, lexer="yaml", code_width=80, dedent=False)
console = Console(no_color=False, width=88)
console.print(x)




# proman.run()
