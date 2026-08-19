from typing import ClassVar

from odoo import fields, models


class PinoutDeviceType(models.Model):
    _name = "pinout.device.type"
    _description = "Pinout Device Type"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    attribute_line_ids = fields.One2many(
        "pinout.device.type.attribute.line",
        "device_type_id",
    )
    allowed_product_template_ids = fields.Many2many(
        "product.template",
        string="Allowed Product Forms",
        help="Product templates/forms that can represent this device type across its lifecycle.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints: ClassVar[list[tuple[str, str, str]]] = [
        (
            "code_unique",
            "unique(code)",
            "The device type code must be unique.",
        ),
    ]


class PinoutDeviceTypeAttributeLine(models.Model):
    _name = "pinout.device.type.attribute.line"
    _description = "Pinout Device Type Attribute Line"
    _order = "sequence, id"

    device_type_id = fields.Many2one(
        "pinout.device.type",
        required=True,
        ondelete="cascade",
    )
    attribute_id = fields.Many2one(
        "product.attribute",
        required=True,
    )
    sequence = fields.Integer(default=10)
    use_variant_code = fields.Boolean(
        default=True,
        help="Use product.attribute.value.variant_code when available. Fallback to attribute value name.",
    )

    _sql_constraints: ClassVar[list[tuple[str, str, str]]] = [
        (
            "device_type_attribute_unique",
            "unique(device_type_id, attribute_id)",
            "Each attribute can only be configured once per device type.",
        ),
    ]
