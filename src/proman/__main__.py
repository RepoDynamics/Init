import loggerman
from repodynamics.exception import RepoDynamicsError

import proman
from proman.exception import ProManError


if __name__ == "__main__":
    loggerman.create(
        global_=True,
        realtime=True,
        github=True,
        init_section_number=2,
        exit_code_critical=1,
        sectioner_exception_catch=(ProManError, RepoDynamicsError),
        sectioner_exception_log_level=loggerman.LogLevel.CRITICAL,
        output_html_filepath="proman_action_log.html",
        root_heading="Execute Action",
        html_title="ProMan Action Log",
    )
    proman.run()
