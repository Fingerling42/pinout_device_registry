DEVICE_STATE_SELECTION = [
    ("wip", "WIP"),
    ("rework", "Rework"),
    ("ready_for_packaging", "Ready for Packaging"),
    ("ready_for_sale", "Ready for Sale"),
    ("reserved", "Reserved"),
    ("sold", "Sold"),
    ("returned", "Returned"),
    ("scrapped", "Scrapped"),
]

DEVICE_STATE_AFTER_MANUFACTURING_SELECTION = [
    ("no_change", "Do Not Change"),
    *DEVICE_STATE_SELECTION,
]

QUALITY_STATUS_SELECTION = [
    ("unknown", "Unknown"),
    ("ok", "OK"),
    ("needs_test", "Needs Test"),
    ("failed", "Failed"),
]
