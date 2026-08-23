"""Domain error types with stable machine-readable categories."""


class ContractError(ValueError):
    """Raised when data violates a frozen Memento contract."""

    def __init__(self, message: str, *, kind: str = "schema") -> None:
        super().__init__(message)
        self.kind = kind
