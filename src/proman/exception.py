class ProManError(Exception):
    """Base class for all ProMan errors."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
        return


class ProManInternalError(ProManError):
    """An internal error occurred in ProMan."""

    def __init__(self, message: str):
        super().__init__(message)
        return
