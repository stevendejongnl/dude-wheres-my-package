from dwmp.carriers.base import TrackingStatus
from dwmp.carriers.dragonfly import Dragonfly

dragonfly = Dragonfly()

# status=300 + isDelivered=false → OUT_FOR_DELIVERY (loaded on truck)
def test_status_300_not_delivered():
    result = dragonfly._parse_result("AMZNL000005063964", {
        "last_status": {"status": 300, "isDelivered": False},
        "status_list": [],
    })
    assert result.status == TrackingStatus.OUT_FOR_DELIVERY

# isDelivered=true overrides status code → DELIVERED
def test_is_delivered_flag():
    result = dragonfly._parse_result("X", {
        "last_status": {"status": 300, "isDelivered": True},
        "status_list": [],
    })
    assert result.status == TrackingStatus.DELIVERED
