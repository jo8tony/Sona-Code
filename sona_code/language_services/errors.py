"""Bounded errors shared by local RPC and the editor protocol."""


class NavigationError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail[:2048]
        super().__init__(self.detail)
