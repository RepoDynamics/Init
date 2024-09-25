import proman
from proman import reporter

# reporter.initialize_logger(title_number=[2])


import pyserials as ps
import mdit
data = {"fail": False,
        "run": {
            "website": True
        },
        "website": []
       }
yaml_str = ps.write.to_yaml_string(data)
# x = mdit.element.code_block(yaml_str, language="yaml").source("console")
from rich import syntax
x = syntax.Syntax(
    yaml_str,
    lexer="yaml",
)

import rich
rich.print(x)

from rich.console import Console
console = Console(
    color_system="truecolor",
    force_terminal=True,
    width=88,
)
console.print(x)




# proman.run()
