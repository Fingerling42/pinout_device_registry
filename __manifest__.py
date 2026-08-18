{
    "name": "Pinout Device Registry",
    "version": "17.0.1.1.0",
    "category": "Inventory/Manufacturing",
    "summary": "Generic hardware device registry for physical device identity, lifecycle, bundles, and final serial linkage",
    "author": "Pinout LTD",
    "license": "Other OSI approved licence", # Apache-2.0
    "depends": [
        "base",
        "mail",
        "product",
        "pinout_product_variant_search",
        "stock",
        "mrp",
        "sale_stock",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/pinout_device_type_views.xml",
        "views/pinout_device_bundle_views.xml",
        "views/pinout_device_views.xml",
        "views/pinout_device_batch_update_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
