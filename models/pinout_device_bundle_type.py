from typing import ClassVar

from odoo import api, fields, models


class PinoutDeviceBundleType(models.Model):
    _name = "pinout.device.bundle.type"
    _description = "Pinout Device Bundle Type"
    _order = "name, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    allowed_product_template_ids = fields.Many2many(
        "product.template",
        string="Allowed Bundle Product Forms",
        help="Product templates that can represent bundles of this type.",
    )
    allow_any_product_form = fields.Boolean(
        string="Allow Any Product Form",
        help="Allow any product template to represent this bundle type. When disabled, only Product Forms listed below are allowed.",
    )
    requires_kit_bom = fields.Boolean(
        string="Requires Kit BoM",
        default=True,
        help="Require a Bundle Product / Kit Variant with an active Kit BoM and validate attached devices against its components.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints: ClassVar[list[tuple[str, str, str]]] = [
        (
            "code_unique",
            "unique(code)",
            "The bundle type code must be unique.",
        ),
    ]

    @api.constrains(
        "requires_kit_bom",
        "allow_any_product_form",
        "allowed_product_template_ids",
    )
    def _check_existing_bundles(self):
        bundles = self.env["pinout.device.bundle"].search(
            [("bundle_type_id", "in", self.ids)]
        )
        bundles._check_bundle_configuration()
