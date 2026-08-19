from collections import defaultdict
from typing import ClassVar

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .pinout_device_selection import DEVICE_STATE_SELECTION, QUALITY_STATUS_SELECTION


class PinoutDevice(models.Model):
    _name = "pinout.device"
    _description = "Device Registry"
    _inherit: ClassVar[list[str]] = ["mail.thread", "mail.activity.mixin"]
    _order = "device_uid, id"
    _pairing_update_context: ClassVar[str] = "pinout_bundle_pairing_update"
    _bundle_identity_fields: ClassVar[frozenset[str]] = frozenset(
        {"device_uid", "device_type_id", "current_product_id", "final_lot_id"}
    )

    device_uid = fields.Char(
        required=True,
        index=True,
        tracking=True,
        help=(
            "Globally identifies one physical device across all Device Types. "
            "The same Device UID cannot be reused for another registry record. "
            "Current batch: MAC without separators. Future batches: PCB serial."
        ),
    )
    device_type_id = fields.Many2one(
        "pinout.device.type",
        required=True,
        tracking=True,
    )
    allowed_product_template_ids = fields.Many2many(
        "product.template",
        related="device_type_id.allowed_product_template_ids",
        readonly=True,
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

    _sql_constraints: ClassVar[list[tuple[str, str, str]]] = [
        (
            "device_uid_unique",
            "unique(device_uid)",
            "Device UID must be globally unique across all Device Types.",
        ),
    ]

    @api.constrains("bundle_id", "current_product_id")
    def _check_bundle_device_allowed(self):
        for device in self.filtered("bundle_id"):
            if device.bundle_id.state != "draft" and not self.env.context.get(
                self._pairing_update_context
            ):
                raise ValidationError(
                    _("Devices can only be attached to a bundle while it is in Draft.")
                )
            device.bundle_id._validate_bundle_devices()

    def write(self, vals):
        if not self.env.context.get(self._pairing_update_context):
            self._check_bundle_write_allowed(vals)
        return super().write(vals)

    def _check_bundle_write_allowed(self, vals):
        if "bundle_id" in vals:
            value = vals["bundle_id"]
            new_bundle_id = value.id if isinstance(value, models.BaseModel) else value
            new_bundle = self.env["pinout.device.bundle"].browse(new_bundle_id)
            for device in self:
                if device.bundle_id.id == new_bundle.id:
                    continue
                if device.bundle_id and device.bundle_id.state != "draft":
                    raise ValidationError(
                        _(
                            "Pairing can only be changed while the current bundle "
                            "is in Draft."
                        )
                    )
                if new_bundle and new_bundle.state != "draft":
                    raise ValidationError(
                        _("Devices can only be attached to a Draft bundle.")
                    )

        changed_identity_fields = self._bundle_identity_fields.intersection(vals)
        for device in self.filtered(
            lambda item: item.bundle_id and item.bundle_id.state != "draft"
        ):
            for field_name in changed_identity_fields:
                current_value = device[field_name]
                new_value = vals[field_name]
                if isinstance(current_value, models.BaseModel):
                    new_value = (
                        new_value.id
                        if isinstance(new_value, models.BaseModel)
                        else new_value
                    )
                    changed = current_value.id != new_value
                else:
                    changed = current_value != new_value
                if changed:
                    raise ValidationError(
                        _(
                            "Device identity, Current Product and Final Lot cannot "
                            "be changed while its bundle pairing is fixed."
                        )
                    )

    @api.constrains("device_uid", "current_product_id", "final_lot_id")
    def _check_final_lot_matches_device(self):
        for device in self.filtered("final_lot_id"):
            if device.final_lot_id.name != device.device_uid:
                raise ValidationError(
                    _(
                        "Final Lot / Serial %(lot)s does not match Device UID %(device)s. "
                        "Restore the matching Device UID or manually clear Final Lot / "
                        "Serial before saving.",
                        lot=device.final_lot_id.name,
                        device=device.device_uid,
                    )
                )
            if (
                device.current_product_id
                and device.final_lot_id.product_id != device.current_product_id
            ):
                raise ValidationError(
                    _(
                        "Final Lot / Serial %(lot)s belongs to %(lot_product)s, but "
                        "Current Product / Current Form is %(current_product)s. "
                        "Restore the product linked to the lot or manually clear "
                        "Final Lot / Serial before saving.",
                        lot=device.final_lot_id.name,
                        lot_product=device.final_lot_id.product_id.display_name,
                        current_product=device.current_product_id.display_name,
                    )
                )

    @api.constrains("device_type_id", "current_product_id")
    def _check_current_product_allowed_for_device_type(self):
        for device in self.filtered("current_product_id"):
            allowed_templates = device.device_type_id.allowed_product_template_ids
            if (
                allowed_templates
                and device.current_product_id.product_tmpl_id not in allowed_templates
            ):
                raise ValidationError(
                    _(
                        "Current Product / Current Form is not allowed for this Device Type."
                    )
                )

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

    def _sync_state_from_stock_moves(self):
        devices = self.filtered("final_lot_id")
        if not devices:
            return

        devices.invalidate_recordset(
            [
                "last_customer_id",
                "last_sale_order_id",
                "last_delivery_id",
                "last_order_reference",
            ]
        )

        devices_by_lot = defaultdict(lambda: self.env["pinout.device"])
        for device in devices:
            devices_by_lot[device.final_lot_id.id] |= device

        move_lines = self.env["stock.move.line"].search(
            [
                ("lot_id", "in", list(devices_by_lot)),
                ("state", "=", "done"),
                ("quantity", ">", 0),
            ],
            order="date desc, id desc",
        )
        latest_event_by_lot = {}
        for move_line in move_lines:
            if move_line.lot_id.id in latest_event_by_lot:
                continue
            new_state = self._get_state_from_stock_move_line(move_line)
            if new_state:
                latest_event_by_lot[move_line.lot_id.id] = (new_state, move_line)

        for lot_id, lot_devices in devices_by_lot.items():
            event = latest_event_by_lot.get(lot_id)
            if not event:
                continue
            new_state, move_line = event
            for device in lot_devices:
                if device.state == new_state:
                    continue
                device.with_context(tracking_disable=True).state = new_state
                device._post_automatic_state_message(new_state, move_line.move_id)

    @api.model
    def _get_state_from_stock_move_line(self, move_line):
        if move_line.location_dest_id.scrap_location:
            return "scrapped"
        if move_line.location_dest_id.usage == "customer":
            return "sold"
        if (
            move_line.location_id.usage == "customer"
            and move_line.location_dest_id.usage == "internal"
        ):
            return "returned"
        return False

    def _post_automatic_state_message(self, new_state, move):
        self.ensure_one()
        source = move.scrap_id or move.picking_id or move
        source_link = Markup(
            '<a href="#" data-oe-model="{}" data-oe-id="{}">{}</a>'
        ).format(source._name, source.id, source.display_name)
        state_label = dict(self._fields["state"].selection).get(new_state, new_state)
        self.message_post(
            body=_(
                "Device state was automatically changed to %(state)s after "
                "%(document)s was completed.",
                state=state_label,
                document=source_link,
            ),
            subtype_xmlid="mail.mt_note",
        )

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
        warning = self._get_final_lot_mismatch_warning()
        return {"warning": warning} if warning else None

    @api.onchange("device_uid", "current_product_id")
    def _onchange_final_lot_domain(self):
        result = {"domain": {"final_lot_id": self._get_final_lot_domain()}}
        warning = self._get_final_lot_mismatch_warning()
        if warning:
            result["warning"] = warning
        return result

    @api.onchange("device_type_id")
    def _onchange_device_type_id(self):
        result = {"domain": {"current_product_id": self._get_current_product_domain()}}
        allowed_templates = self.device_type_id.allowed_product_template_ids
        if (
            self.current_product_id
            and allowed_templates
            and self.current_product_id.product_tmpl_id not in allowed_templates
        ):
            result["warning"] = {
                "title": _("Product Not Allowed"),
                "message": _(
                    "Current Product / Current Form remains selected because dependent "
                    "identity fields must not be cleared automatically. Choose a compatible "
                    "Device Type or Product before saving. Clear Final Lot / Serial manually "
                    "if the physical form really needs to change."
                ),
            }
        return result

    @api.onchange("current_product_id")
    def _onchange_current_product_id(self):
        result = {
            "domain": {
                "current_product_id": self._get_current_product_domain(),
                "final_lot_id": self._get_final_lot_domain(),
            }
        }
        allowed_templates = self.device_type_id.allowed_product_template_ids
        if (
            self.current_product_id
            and allowed_templates
            and self.current_product_id.product_tmpl_id not in allowed_templates
        ):
            return {
                "warning": {
                    "title": _("Product Not Allowed"),
                    "message": _(
                        "Current Product / Current Form remains selected because dependent "
                        "identity fields must not be cleared automatically. Choose a product "
                        "allowed for this Device Type before saving. Clear Final Lot / Serial "
                        "manually if the physical form really needs to change."
                    ),
                },
                **result,
            }
        return result

    def _get_final_lot_mismatch_warning(self):
        self.ensure_one()
        if not self.final_lot_id:
            return False
        if self.device_uid and self.final_lot_id.name != self.device_uid:
            return {
                "title": _("Final Lot Mismatch"),
                "message": _(
                    "Final Lot / Serial %(lot)s remains linked, but it does not match "
                    "Device UID %(device)s. Restore the matching UID or clear Final Lot / "
                    "Serial manually before saving.",
                    lot=self.final_lot_id.name,
                    device=self.device_uid,
                ),
            }
        if (
            self.current_product_id
            and self.final_lot_id.product_id != self.current_product_id
        ):
            return {
                "title": _("Final Lot Product Mismatch"),
                "message": _(
                    "Final Lot / Serial %(lot)s remains linked to %(lot_product)s, but "
                    "Current Product / Current Form is %(current_product)s. Restore the "
                    "product linked to the lot or clear Final Lot / Serial manually before "
                    "saving.",
                    lot=self.final_lot_id.name,
                    lot_product=self.final_lot_id.product_id.display_name,
                    current_product=self.current_product_id.display_name,
                ),
            }
        return False

    def _get_current_product_domain(self):
        domain = []
        allowed_templates = self.device_type_id.allowed_product_template_ids
        if allowed_templates:
            domain.append(("product_tmpl_id", "in", allowed_templates.ids))
        return domain

    def _get_final_lot_domain(self):
        domain = []
        if self.device_uid:
            domain.append(("name", "=", self.device_uid))
        if self.current_product_id:
            domain.append(("product_id", "=", self.current_product_id.id))
        return domain

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
