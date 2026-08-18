from odoo import SUPERUSER_ID, api

LEGACY_XML_IDS = (
    "pinout_device_registry.product_attribute_value_action",
    "pinout_device_registry.product_attribute_value_list_inherit_variant_code",
    "pinout_device_registry.product_attribute_form_inherit_variant_code",
    "pinout_device_registry.product_attribute_value_view_form",
    "pinout_device_registry.product_attribute_value_view_search",
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xml_id in LEGACY_XML_IDS:
        record = env.ref(xml_id, raise_if_not_found=False)
        if record:
            record.unlink()
