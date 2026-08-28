from odoo import fields, models

from .pinout_device_selection import DEVICE_STATE_AFTER_MANUFACTURING_SELECTION


class ProductTemplate(models.Model):
    _inherit = "product.template"

    pinout_device_state_after_manufacturing = fields.Selection(
        DEVICE_STATE_AFTER_MANUFACTURING_SELECTION,
        string="Device State after Manufacturing",
        default="no_change",
        required=True,
        help=(
            "Target lifecycle state for linked Registry Devices after this Product "
            "Form is manufactured. Do Not Change preserves their existing state when "
            "post-manufacturing synchronization is applied."
        ),
    )
