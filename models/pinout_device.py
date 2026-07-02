from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .pinout_device_selection import DEVICE_STATE_SELECTION, QUALITY_STATUS_SELECTION


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
    current_attribute_value_ids = fields.Many2many(
        "product.attribute.value",
        compute="_compute_current_attribute_values",
        string="Current Attribute Values",
        readonly=True,
        store=True,
    )
    variant_summary = fields.Char(
        compute="_compute_variant_summary",
        string="Variant Summary",
        readonly=True,
        store=True,
        help="Short readable variant summary generated from current product attributes and device type configuration, e.g. ORNG / ENJ.",
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

    last_customer_id = fields.Many2one(
        "res.partner",
        compute="_compute_last_sales_data",
        readonly=True,
        store=False,
    )
    last_sale_order_id = fields.Many2one(
        "sale.order",
        compute="_compute_last_sales_data",
        readonly=True,
        store=False,
    )
    last_delivery_id = fields.Many2one(
        "stock.picking",
        compute="_compute_last_sales_data",
        readonly=True,
        store=False,
    )
    last_order_reference = fields.Char(
        compute="_compute_last_sales_data",
        readonly=True,
        store=False,
    )

    bundle_id = fields.Many2one(
        "pinout.device.bundle",
        tracking=True,
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

    @api.constrains("bundle_id")
    def _check_dual_bundle_device_count(self):
        for device in self.filtered("bundle_id"):
            bundle = device.bundle_id
            if bundle.bundle_type == "dual" and len(bundle.device_ids) > 2:
                raise ValidationError(_("Dual bundles can contain at most 2 devices."))

    @api.depends("final_lot_id.name")
    def _compute_final_serial_name(self):
        for device in self:
            device.final_serial_name = device.final_lot_id.name

    @api.depends(
        "current_product_id.product_template_attribute_value_ids",
        "current_product_id.product_template_attribute_value_ids.product_attribute_value_id",
    )
    def _compute_current_attribute_values(self):
        for device in self:
            product_attribute_values = device.current_product_id.product_template_attribute_value_ids.product_attribute_value_id
            device.current_attribute_value_ids = product_attribute_values

    @api.depends(
        "current_attribute_value_ids",
        "current_attribute_value_ids.name",
        "current_attribute_value_ids.variant_code",
        "device_type_id.attribute_line_ids",
        "device_type_id.attribute_line_ids.attribute_id",
        "device_type_id.attribute_line_ids.sequence",
        "device_type_id.attribute_line_ids.use_variant_code",
    )
    def _compute_variant_summary(self):
        for device in self:
            values_by_attribute = {
                value.attribute_id.id: value
                for value in device.current_attribute_value_ids
                if value.attribute_id
            }
            summary_parts = []
            for line in device.device_type_id.attribute_line_ids.sorted(
                key=lambda item: (item.sequence, item.id)
            ):
                product_attribute_value = values_by_attribute.get(line.attribute_id.id)
                if not product_attribute_value:
                    continue
                if line.use_variant_code and product_attribute_value.variant_code:
                    summary_parts.append(product_attribute_value.variant_code)
                else:
                    summary_parts.append(product_attribute_value.name)
            device.variant_summary = " / ".join(summary_parts)

    @api.depends("final_lot_id")
    def _compute_last_sales_data(self):
        for device in self:
            device.last_customer_id = False
            device.last_sale_order_id = False
            device.last_delivery_id = False
            device.last_order_reference = False

        devices_by_lot = defaultdict(lambda: self.env["pinout.device"])
        for device in self.filtered("final_lot_id"):
            devices_by_lot[device.final_lot_id.id] |= device
        if not devices_by_lot:
            return

        move_lines = self.env["stock.move.line"].search(
            [
                ("lot_id", "in", list(devices_by_lot)),
                ("state", "=", "done"),
                ("location_dest_id.usage", "=", "customer"),
                ("picking_id", "!=", False),
            ],
            order="date desc, id desc",
        )

        latest_move_line_by_lot = {}
        sorted_move_lines = move_lines.sorted(
            key=lambda line: (
                line.picking_id.date_done or line.picking_id.date or line.date,
                line.id,
            ),
            reverse=True,
        )
        for move_line in sorted_move_lines:
            lot_id = move_line.lot_id.id
            if lot_id not in latest_move_line_by_lot:
                latest_move_line_by_lot[lot_id] = move_line

        for lot_id, devices in devices_by_lot.items():
            move_line = latest_move_line_by_lot.get(lot_id)
            if not move_line:
                continue
            picking = move_line.picking_id
            sale_order = picking.sale_id
            last_order_reference = (
                sale_order.client_order_ref
                or sale_order.name
                or picking.origin
                or False
            )
            for device in devices:
                device.last_delivery_id = picking
                device.last_customer_id = picking.partner_id
                device.last_sale_order_id = sale_order
                device.last_order_reference = last_order_reference

    @api.depends(
        "device_uid",
        "device_type_id.name",
        "current_product_id.display_name",
        "variant_summary",
    )
    def _compute_display_name(self):
        for device in self:
            name_parts = [
                device.device_uid,
                device.device_type_id.name,
                device.current_product_id.display_name,
                device.variant_summary,
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

    def action_open_last_delivery(self):
        self.ensure_one()
        if not self.last_delivery_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Last Delivery"),
            "res_model": "stock.picking",
            "res_id": self.last_delivery_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_last_sale_order(self):
        self.ensure_one()
        if not self.last_sale_order_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Last Sale Order"),
            "res_model": "sale.order",
            "res_id": self.last_sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_last_customer(self):
        self.ensure_one()
        if not self.last_customer_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Last Customer"),
            "res_model": "res.partner",
            "res_id": self.last_customer_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_batch_update_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Batch Update Devices"),
            "res_model": "pinout.device.batch.update.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_device_ids": [(6, 0, self.ids)],
                "active_model": self._name,
                "active_ids": self.ids,
            },
        }
