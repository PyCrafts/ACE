# vim: ts=4:sw=4:et:cc=120

from dataclasses import dataclass, field
import json
from typing import List, Union, Optional

from saq.analysis import Observable, RootAnalysis
from saq.system import ACESystemInterface, get_system
from saq.system.constants import *

class AnalysisModuleTypeVersionError(Exception):
    """Raised when a request for a analysis with an out-of-date version is made."""
    pass

@dataclass
class AnalysisModuleType():
    """Represents a registration of an analysis module type."""
    # the name of the analysis module type
    name: str
    # brief English description of what the module does
    description: str
    # list of supported observable types (empty list supports all observable)
    observable_types: List[str] = field(default_factory=list)
    # list of required directives (empty list means no requirement)
    directives: List[str] = field(default_factory=list)
    # list of other analysis module type names to wait for (empty list means no deps)
    dependencies: List[str] = field(default_factory=list)
    # list of required tags (empty list means no requirement)
    tags: List[str] = field(default_factory=list)
    # list of valid analysis modes
    modes: List[str] = field(default_factory=list)
    # the current version of the analysis module type
    version: str = '1.0.0'
    # how long this analysis module has before it times out (in seconds)
    # by default it takes the global default specified in the configuration file
    # you can set a very high timeout but nothing can never timeout
    timeout: int = 30
    # how long analysis results stay in the cache (in seconds)
    # a value of None means it is not cached
    cache_ttl: Optional[int] = None
    # what additional values should be included to determine the cache key?
    additional_cache_keys: List[str] = field(default_factory=list)

    def version_matches(self, amt) -> bool:
        """Returns True if the given amt is the same version as this amt."""
        return ( 
                self.name == amt.name and
                self.version == amt.version and
                sorted(self.additional_cache_keys) == sorted(amt.additional_cache_keys) )
        # XXX should probably check the other fields as well

    # Trackable implementation
    @property
    def tracking_key(self):
        return self.name

    def to_json(self):
        return json.dumps({
            'name': self.name,
            'description': self.name,
            'observable_types': self.observable_types,
            'directives': self.directives,
            'dependencies': self.dependencies,
            'tags': self.tags,
            'modes': self.modes,
            'version': self.version,
            'timeout': self.timeout,
            'cache_ttl': self.cache_ttl,
            'cache_keys': self.cache_keys,
        })

    @staticmethod
    def from_json(self, json_data: str):
        json_dict = json.loads(json_data)
        return AnalysisModuleType(
            name = json_dict['name'],
            description = json_dict['description'],
            observable_types = json_dict['observable_types'],
            directives = json_dict['directives'],
            dependencies = json_dict['dependencies'],
            tags = json_dict['tags'],
            modes = json_dict['modes'],
            version = json_dict['version'],
            timeout = json_dict['timeout'],
            cache_ttl = json_dict['cache_ttl'],
            cache_keys = json_dict['cache_keys'],
        )

    def accepts(self, observable: Observable) -> bool:

        # TODO conditions are OR vertical AND horizontal?

        # OR
        if self.modes and observable.root.analysis_mode not in self.modes:
            return False

        # OR
        if self.observable_types:
            if observable.type not in self.observable_types:
                return False

        # AND
        for directive in self.directives:
            if not observable.has_directive(directive):
                return False

        # AND
        for tag in self.tags:
            if not observable.has_tag(tag):
                return False

        # AND (this is correct)
        for dep in self.dependencies:
            if not observable.analysis_completed(dep):
                return False

        return True

class AnalysisModuleTrackingInterface(ACESystemInterface):
    def track_analysis_module_type(self, amt: AnalysisModuleType):
        raise NotImplementedError()

    def get_analysis_module_type(self, name: str) -> Union[AnalysisModuleType, None]:
        raise NotImplementedError()

    def get_all_analysis_module_types(self) -> List[AnalysisModuleType]:
        raise NotImplementedError()

def register_analysis_module_type(amt: AnalysisModuleType) -> AnalysisModuleType:
    """Registers the given AnalysisModuleType with the system."""
    current_type = get_analysis_module_type(amt.name)
    if current_type is None:
        get_system().work_queue.add_work_queue(amt.name)

    # regardless we take this to be the new registration for this analysis module
    # any updates to version or cache keys would be saved here
    track_analysis_module_type(amt)
    return amt

def track_analysis_module_type(amt: AnalysisModuleType):
    return get_system().module_tracking.track_analysis_module_type(amt)

def get_analysis_module_type(name: str) -> Union[AnalysisModuleType, None]:
    return get_system().module_tracking.get_analysis_module_type(name)

def get_all_analysis_module_types() -> List[AnalysisModuleType]:
    return get_system().module_tracking.get_all_analysis_module_types()