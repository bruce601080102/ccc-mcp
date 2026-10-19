from .adapters.git import revision
from .adapters.storage import Store
from .core.access import ProjectAccessHub
from .core.configuration import ConfigurationHub
from .core.context import ContextHub
from .core.directedit import DirectEditHub
from .core.tasks import TaskHub
from .core.workspace import WorkspaceHub
from .infra.config import bridge_home


class CCCMCP(ConfigurationHub, WorkspaceHub, DirectEditHub, ProjectAccessHub, ContextHub, TaskHub):
    """The same application service is used by CLI, MCP and independent workers."""

    def __init__(self, home=None):
        self.home = bridge_home(home)
        self.store = Store(self.home)

    git_revision = staticmethod(revision)
