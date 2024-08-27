import loggerman
import controlman

import proman
from proman.exception import ProManException


if __name__ == "__main__":
    loggerman.logger.initialize(
        realtime=True,
        github=True,
        exit_code_critical=1,
        # sectioner_exception_catch=(ProManException, controlman.exception.ControlManException),
        sectioner_exception_log_level=loggerman.LogLevel.CRITICAL,
        output_html_filepath="workflow_log.html",
        root_heading="Execute Action",
        html_title="Workflow Log",
    )
    proman.run()
