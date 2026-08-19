from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    bundle_types = env["pinout.device.bundle.type"].search([])
    previously_unrestricted_types = bundle_types.filtered(
        lambda bundle_type: not bundle_type.allowed_product_template_ids
    )
    previously_unrestricted_types.write({"allow_any_product_form": True})
