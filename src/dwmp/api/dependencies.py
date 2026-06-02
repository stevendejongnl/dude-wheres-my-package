from functools import lru_cache

from dwmp.carriers.amazon import Amazon
from dwmp.carriers.dhl import DHL
from dwmp.carriers.dpd import DPD
from dwmp.carriers.gls import GLS
from dwmp.carriers.postnl import PostNL
from dwmp.carriers.trunkrs import Trunkrs
from dwmp.services.tracking import TrackingService
from dwmp.storage.repository import PackageRepository


@lru_cache
def get_repository() -> PackageRepository:
    return PackageRepository()


@lru_cache
def get_tracking_service() -> TrackingService:
    return TrackingService(
        repository=get_repository(),
        carriers={
            "amazon": Amazon(),
            "postnl": PostNL(),
            "dhl": DHL(),
            "dpd": DPD(),
            "gls": GLS(),
            "trunkrs": Trunkrs(),
        },
    )
