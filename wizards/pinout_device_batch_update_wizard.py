from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.pinout_device_selection import (
    DEVICE_STATE_SELECTION,
    QUALITY_STATUS_SELECTION,
)


class PinoutDeviceBatchUpdateWizard(models.TransientModel):
    _name = "pinout.device.batch.update.wizard"
    _description = "Batch Update Devices"

    device_ids = fields.Many2many(
        "pinout.device",
        required=True,
        readonly=True,
        default=lambda self: self._default_device_ids(),
    )

    update_current_product = fields.Boolean()
    current_product_id = fields.Many2one("product.product")

    update_state = fields.Boolean()
    state = fields.Selection(DEVICE_STATE_SELECTION)

    update_quality_status = fields.Boolean()
    quality_status = fields.Selection(QUALITY_STATUS_SELECTION)

    update_location = fields.Boolean()
    location_id = fields.Many2one("stock.location")

    update_physical_location_note = fields.Boolean()
    physical_location_note = fields.Char()

    update_bundle = fields.Boolean()
    bundle_id = fields.Many2one("pinout.device.bundle")

    @api.model
    def _default_device_ids(self):
        if self.env.context.get("active_model") != "pinout.device":
            return False
        return [(6, 0, self.env.context.get("active_ids", []))]

    def action_apply(self):
        self.ensure_one()
        update_values, update_labels = self._prepare_update_values()
        if not update_values:
            raise UserError(_("Select at least one field to update."))
        if not self.device_ids:
            raise UserError(_("Select at least one device to update."))

        self.device_ids.write(update_values)
        message_body = self._build_chatter_message(update_labels)
        for device in self.device_ids:
            device.message_post(body=message_body)
        return {"type": "ir.actions.act_window_close"}

    def _prepare_update_values(self):
        self.ensure_one()
        update_values = {}
        update_labels = []

        update_fields = [
            (
                "update_current_product",
                "current_product_id",
                _("Current Product / Current Form"),
            ),
            ("update_state", "state", _("State")),
            ("update_quality_status", "quality_status", _("Quality Status")),
            ("update_location", "location_id", _("Odoo Location")),
            (
                "update_physical_location_note",
                "physical_location_note",
                _("Physical Location Note"),
            ),
            ("update_bundle", "bundle_id", _("Bundle")),
        ]

        for checkbox_field, value_field, label in update_fields:
            if not self[checkbox_field]:
                continue
            value = self[value_field]
            update_values[value_field] = value.id if hasattr(value, "id") else value
            update_labels.append((label, self._format_value(value_field, value)))

        return update_values, update_labels

    def _format_value(self, field_name, value):
        field = self._fields[field_name]
        if field.type == "many2one":
            return value.display_name if value else _("Empty")
        if field.type == "selection":
            return dict(field.selection).get(value, value) if value else _("Empty")
        return value or _("Empty")

    def _build_chatter_message(self, update_labels):
        list_items = Markup("").join(
            Markup("<li><strong>%s:</strong> %s</li>") % (escape(label), escape(value))
            for label, value in update_labels
        )
        return Markup("<p>%s</p><ul>%s</ul>") % (
            escape(_("Batch update applied:")),
            list_items,
        )
