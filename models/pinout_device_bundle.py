from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PinoutDeviceBundle(models.Model):
    _name = "pinout.device.bundle"
    _description = "Pinout Device Bundle"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"

    name = fields.Char(required=True, index=True)
    bundle_type = fields.Selection(
        [
            ("dual", "Dual"),
            ("other", "Other"),
        ],
        default="dual",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("sold", "Sold"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    device_ids = fields.One2many(
        "pinout.device",
        "bundle_id",
    )
    device_count = fields.Integer(
        compute="_compute_device_count",
        readonly=True,
    )
    customer_id = fields.Many2one(
        "res.partner",
        compute="_compute_bundle_sales_data",
        readonly=True,
        store=False,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        compute="_compute_bundle_sales_data",
        readonly=True,
        store=False,
    )
    delivery_id = fields.Many2one(
        "stock.picking",
        compute="_compute_bundle_sales_data",
        readonly=True,
        store=False,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("device_ids")
    def _compute_device_count(self):
        for bundle in self:
            bundle.device_count = len(bundle.device_ids)

    @api.depends("device_ids", "device_ids.final_lot_id")
    def _compute_bundle_sales_data(self):
        for bundle in self:
            devices = bundle.device_ids
            bundle.customer_id = bundle._get_shared_device_value(
                devices, "last_customer_id"
            )
            bundle.sale_order_id = bundle._get_shared_device_value(
                devices, "last_sale_order_id"
            )
            bundle.delivery_id = bundle._get_shared_device_value(
                devices, "last_delivery_id"
            )

    def _get_shared_device_value(self, devices, field_name):
        if not devices:
            return False
        values = [device[field_name] for device in devices]
        if not all(values):
            return False
        first_value = values[0]
        if all(value == first_value for value in values):
            return first_value
        return False

    @api.constrains("bundle_type", "device_ids")
    def _check_dual_device_count(self):
        for bundle in self:
            if bundle.bundle_type == "dual" and len(bundle.device_ids) > 2:
                raise ValidationError(_("Dual bundles can contain at most 2 devices."))

    def action_open_devices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Devices"),
            "res_model": "pinout.device",
            "view_mode": "tree,form",
            "domain": [("bundle_id", "=", self.id)],
            "context": {"default_bundle_id": self.id},
            "target": "current",
        }
