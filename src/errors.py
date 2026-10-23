class ContextLimitError(ValueError):
    """Current input cannot fit the configured model context."""


class FileLimitError(ValueError):
    pass


class KnowledgeUnavailable(RuntimeError):
    pass


class RepairRequired(RuntimeError):
    pass


class IndexRebuildError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result
