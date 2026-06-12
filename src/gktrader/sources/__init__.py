"""Source adapters for MVP data feeds.

Each adapter implements the locked SourceAdapter contract
and handles one source family with its specific acquisition,
normalization, and versioning semantics.
"""

from gktrader.sources.base import SourceAdapter
from gktrader.sources.whitehouse import WhiteHouseAdapter
from gktrader.sources.nist import NISTAdapter
from gktrader.sources.truthsocial import TruthSocialAdapter
from gktrader.sources.commerce import CommerceAdapter
from gktrader.sources.sec import SECAdapter

__all__: list[str] = [
    "SourceAdapter",
    "WhiteHouseAdapter",
    "NISTAdapter",
    "TruthSocialAdapter",
    "CommerceAdapter",
    "SECAdapter",
]