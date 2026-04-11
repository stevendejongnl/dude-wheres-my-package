from functools import lru_cache

from dwmp.carriers.postnl import PostNL
from dwmp.carriers.dhl import DHL
from dwmp.carriers.dpd import DPD
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
            "postnl": PostNL(),
            "dhl": DHL(),
            "dpd": DPD(),
        },
    )
