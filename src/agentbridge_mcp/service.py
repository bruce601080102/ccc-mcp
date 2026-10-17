from .config import bridge_home
from .context import ContextHub
from .git import revision
from .storage import Store
from .tasks import TaskHub


class AgentBridge(ContextHub, TaskHub):
    """The same application service is used by CLI, MCP and independent workers."""

    def __init__(self, home=None):
        self.home = bridge_home(home)
        self.store = Store(self.home)

    git_revision = staticmethod(revision)
