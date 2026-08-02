"""HNI Event Radar - event discovery for private wealth managers.

Finds conferences, summits and expos where a wealth manager can meet HNI and
UHNI prospects, scores each one on how likely the room is to contain clients
and how easy it is to get in, and reports what to actually do about it.
"""

from .models import Event
from .scoring import Scorer, score_events

__version__ = "1.0.0"
__all__ = ["Event", "Scorer", "score_events"]
