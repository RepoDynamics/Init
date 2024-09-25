import proman
from proman import reporter

reporter.initialize_logger(title_number=[2])


import pyserials as ps
from loggerman import logger
import mdit
data = {"fail": False,
        "run": {
            "website": True
        },
        "website": []
       }
yaml_str = ps.write.to_yaml_string(data)
x = mdit.element.code_block(yaml_str, language="yaml")
x.display("console")
logger.info("Action Output", x)

proman.run()
