# vim: ts=4:sw=4:et:cc=120
import hashlib
from typing import Union, Optional

from saq.analysis import Analysis, Observable
from saq.system import ACESystemInterface, get_system
from saq.system.analysis_module import AnalysisModuleType

def generate_cache_key(observable: Observable, amt: AnalysisModuleType) -> str:
    """Returns the key that should be used for caching the result of the
    analysis generated by this analysis module type against this observable."""
    if observable is None:
        return None

    if amt is None:
        return None

    # if the cache_ttl is None then caching is disabled (this is the default behavior)
    if amt.cache_ttl is None:
        return None

    h = hashlib.sha256()
    h.update(observable.type.encode('utf8', errors='ignore'))
    h.update(observable.value.encode('utf8', errors='ignore'))
    if observable.time:
        h.update(str(observable.time.timestamp()).encode('utf8', errors='ignore'))

    h.update(amt.name.encode('utf8', errors='ignore'))
    h.update(amt.version.encode('utf8', errors='ignore'))

    for key in sorted(amt.additional_cache_keys):
        h.update(key.encode('utf8', errors='ignore'))

    return h.hexdigest()

class CachingInterface(ACESystemInterface):
    def get_cached_analysis(self, cache_key: str) -> Union[dict, None]:
        raise NotImplementedError()

    def cache_analysis(self, cache_key: str, analysis: Analysis, expiration: Optional[int]) -> str:
        raise NotImplementedError()

def get_cached_analysis(observable: Observable, amt: AnalysisModuleType) -> Union[Analysis, None]:
    cache_key = generate_cache_key(observable, amt)
    if cache_key is None:
        return None

    return get_system().caching.get_cached_analysis(cache_key)

def cache_analysis(observable: Observable, amt: AnalysisModuleType, analysis: Analysis) -> Union[str, None]:
    cache_key = generate_cache_key(observable, amt)
    if cache_key is None:
        return None

    return get_system().caching.cache_analysis(cache_key, analysis, amt.cache_ttl)