
class ProManException(Exception):
    """Base class for all exceptions raised by ProMan."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
        return


class ProManInternalError(Exception):
    """An internal error occurred in ProMan."""

    def __init__(self):
        message = "An internal error occurred in ProMan."
        super().__init__(message)
        return


class ProManInputError(ProManException):
    """Error in the input arguments provided to ProMan action."""

    def __init__(self, message: str):
        super().__init__(message)
        return
