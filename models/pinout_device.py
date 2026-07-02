from odoo import _, api, fields, models

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

QUALITY_STATUS_SELECTION = [
    ("unknown", "Unknown"),
    ("ok", "OK"),
    ("needs_test", "Needs Test"),
    ("failed", "Failed"),
]


class PinoutDevice(models.Model):
    _name = "pinout.device"
    _description = "Device Registry"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "device_uid, id"

    device_uid = fields.Char(
        required=True,
        index=True,
        tracking=True,
        help="Main physical device UID. Current batch: MAC without separators. Future batches: PCB serial.",
    )
    device_type_id = fields.Many2one(
        "pinout.device.type",
        required=True,
        tracking=True,
    )
    mac = fields.Char(
        tracking=True,
        help="Human-readable MAC address if known. Not unique.",
    )
    temp_mark = fields.Char(
        tracking=True,
        help="Temporary physical mark written on device, bag, tray, or carrier, e.g. URB F470.",
    )

    current_product_id = fields.Many2one(
        "product.product",
        string="Current Product / Current Form",
        tracking=True,
        help="What this device physically is right now: PCB Base, PCB+SDS Base, Core, Assembled Device, Retail Unit, etc.",
    )
    state = fields.Selection(
        DEVICE_STATE_SELECTION,
        default="wip",
        required=True,
        tracking=True,
    )
    quality_status = fields.Selection(
        QUALITY_STATUS_SELECTION,
        default="unknown",
        required=True,
        tracking=True,
    )
    location_id = fields.Many2one(
        "stock.location",
        string="Odoo Location",
        tracking=True,
        help="Approximate Odoo stock location if known. This is not meant to mirror every stock move automatically.",
    )
    physical_location_note = fields.Char(
        tracking=True,
        help="Human-readable physical place: ESD Box 3, Shelf A2, Ready Shelf, With Pavel, Returned Box, etc.",
    )

    final_lot_id = fields.Many2one(
        "stock.lot",
        string="Final Lot / Serial",
        tracking=True,
        help="Final Odoo stock lot/serial when the device becomes a retail unit. Empty for WIP stages.",
    )
    final_serial_name = fields.Char(
        compute="_compute_final_serial_name",
        string="Final Serial",
        readonly=True,
        store=True,
    )

    robonomics_device_address = fields.Char(tracking=True)
    subscription_owner_address = fields.Char(tracking=True)
    robonomics_notes = fields.Text()

    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "device_uid_unique",
            "unique(device_uid)",
            "The Device UID must be unique.",
        ),
    ]

    @api.depends("final_lot_id.name")
    def _compute_final_serial_name(self):
        for device in self:
            device.final_serial_name = device.final_lot_id.name

    @api.depends(
        "device_uid",
        "device_type_id.name",
        "current_product_id.display_name",
    )
    def _compute_display_name(self):
        for device in self:
            name_parts = [
                device.device_uid,
                device.device_type_id.name,
                device.current_product_id.display_name,
            ]
            device.display_name = " — ".join(part for part in name_parts if part)

    @api.onchange("final_lot_id")
    def _onchange_final_lot_id(self):
        if (
            self.final_lot_id
            and self.device_uid
            and self.final_lot_id.name != self.device_uid
        ):
            return {
                "warning": {
                    "title": _("Final Lot Mismatch"),
                    "message": _(
                        "Final lot serial does not match Device UID. This may be intentional, but please verify."
                    ),
                }
            }
        return None

    def action_open_final_lot(self):
        self.ensure_one()
        if not self.final_lot_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Final Lot / Serial"),
            "res_model": "stock.lot",
            "res_id": self.final_lot_id.id,
            "view_mode": "form",
            "target": "current",
        }
