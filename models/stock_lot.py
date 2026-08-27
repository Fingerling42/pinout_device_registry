from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockLot(models.Model):
    _inherit = "stock.lot"

    pinout_device_id = fields.Many2one(
        "pinout.device",
        string="Registry Device",
        copy=False,
        index=True,
        ondelete="restrict",
        help=(
            "Physical Device Registry record that has used this final lot/serial. "
            "The link remains as history after the device is unbuilt."
        ),
    )
    pinout_is_current_device_lot = fields.Boolean(
        string="Current",
        compute="_compute_pinout_is_current_device_lot",
        help="This lot/serial is the device's currently active final serial.",
    )

    @api.depends("pinout_device_id.final_lot_id")
    def _compute_pinout_is_current_device_lot(self):
        for lot in self:
            lot.pinout_is_current_device_lot = lot.pinout_device_id.final_lot_id == lot

    @api.constrains("name", "pinout_device_id")
    def _check_pinout_device_uid(self):
        for lot in self.filtered("pinout_device_id"):
            if lot.name != lot.pinout_device_id.device_uid:
                raise ValidationError(
                    _(
                        "Lot / Serial %(lot)s cannot be linked to Device %(device)s "
                        "because their identifiers do not match.",
                        lot=lot.name,
                        device=lot.pinout_device_id.device_uid,
                    )
                )
