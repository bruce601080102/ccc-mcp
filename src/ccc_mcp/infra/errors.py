class BridgeError(Exception):
    """A safe, actionable domain error."""


class VersionConflict(BridgeError):
    pass


class PermissionDenied(BridgeError):
    pass


class ContractDriftBlocked(PermissionDenied):
    """A pinned role was frozen under a fixed contract that has since changed incompatibly."""


class AgentCancelled(BridgeError):
    pass


class AgentTimeout(BridgeError):
    pass


class AgentProcessError(BridgeError):
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        super().__init__(f"Agent exited {returncode}: {stderr[:2000]}")
