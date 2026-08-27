"""Internal event bus.

A deliberately small transport layer shared by every service. Broadcasting
lives here rather than inside ingest because the analysis service publishes
too -- putting it in ingest would force analysis to depend on ingest for no
reason other than where the file happened to land.
"""

from .fanout import Fanout
from .subscriber import subscribe

__all__ = ["Fanout", "subscribe"]
