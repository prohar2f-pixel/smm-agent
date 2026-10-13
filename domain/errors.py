class DomainError(RuntimeError):
    """Base error for expected domain failures."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class ValidationError(DomainError):
    pass
