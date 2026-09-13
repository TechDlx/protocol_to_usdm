class StorageError(Exception):
    """Base class for study/run filesystem errors."""


class StudyNotFoundError(StorageError):
    pass


class RunNotFoundError(StorageError):
    pass


class SourceNotFoundError(StorageError):
    pass


class InvalidUploadError(StorageError):
    pass
