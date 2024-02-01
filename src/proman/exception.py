class ProManError(Exception):
    """Base class for all ProMan errors."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
        return
