import loggerman

import proman

loggerman.logger.initialize(
    realtime_levels=list(range(1, 7)),
    github=True,
    github_debug=True,
    title_number=[2],
)
proman.run()
