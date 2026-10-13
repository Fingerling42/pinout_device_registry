from odoo import _, api, fields, models
from odoo.exceptions import UserError
from psycopg2.errors import UniqueViolation

from ..models.pinout_device_selection import (
    DEVICE_STATE_SELECTION,
    QUALITY_STATUS_SELECTION,
)


class PinoutDeviceBatchCreateWizard(models.TransientModel):
    _name = "pinout.device.batch.create.wizard"
    _description = "Batch Create Devices"

    device_type_id = fields.Many2one("pinout.device.type", required=True)
    allowed_product_template_ids = fields.Many2many(
        related="device_type_id.allowed_product_template_ids"
    )
    current_product_id = fields.Many2one(
        "product.product", string="Current Product / Current Form"
    )
    state = fields.Selection(DEVICE_STATE_SELECTION, required=True, default="wip")
    quality_status = fields.Selection(
        QUALITY_STATUS_SELECTION, required=True, default="unknown"
    )
    location_id = fields.Many2one("stock.location", string="Odoo Location")
    physical_location_note = fields.Char()
    pasted_text = fields.Text(string="Paste UID / MAC / Address")
    line_ids = fields.One2many("pinout.device.batch.create.line", "wizard_id")
    device_count = fields.Integer(compute="_compute_device_count", string="Devices")

    @api.depends("line_ids")
    def _compute_device_count(self):
        for wizard in self:
            wizard.device_count = len(wizard.line_ids)

    def action_parse(self):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        if not self.pasted_text or not self.pasted_text.strip():
            raise UserError(_("Paste at least one Device UID."))
        values = []
        for number, raw_line in enumerate(self.pasted_text.splitlines(), start=1):
            # Empty spreadsheet cells are meaningful; only truly empty lines are skipped.
            if not raw_line:
                continue
            cells = [cell.strip() for cell in raw_line.split("\t")]
            if (
                not values
                and number == 1
                and [cell.lower() for cell in cells]
                in (
                    ["uid"],
                    ["device uid"],
                    ["uid", "mac", "address"],
                    ["device uid", "mac", "robonomics device address"],
                )
            ):
                continue
            if len(cells) > 3:
                raise UserError(
                    _(
                        "Line %(line)s: expected at most three tab-separated columns.",
                        line=number,
                    )
                )
            cells += [""] * (3 - len(cells))
            values.append(
                fields.Command.create(
                    {
                        "device_uid": cells[0],
                        "mac": cells[1],
                        "robonomics_device_address": cells[2],
                    }
                )
            )
        if not values:
            raise UserError(_("Paste at least one Device UID after the header."))
        # Parse everything before replacing the editable table, so malformed input loses no rows.
        self.line_ids = [fields.Command.clear(), *values]
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Batch Create Devices"),
            "res_model": self._name,
            "res_id": self.id,
            "views": [(False, "form")],
            "view_mode": "form",
            "target": "new",
        }

    def _validate_lines(self):
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        self.env["pinout.device"].check_access_rights("create")
        if not self.device_type_id or not self.state or not self.quality_status:
            raise UserError(_("Device Type, State and Quality Status are required."))
        allowed = self.device_type_id.allowed_product_template_ids
        if (
            self.current_product_id
            and allowed
            and self.current_product_id.product_tmpl_id not in allowed
        ):
            raise UserError(
                _("Current Product / Current Form is not allowed for this Device Type.")
            )
        if not self.line_ids:
            raise UserError(_("Add at least one device row."))
        uids = [line.device_uid or "" for line in self.line_ids]
        # Only test supplied identifiers for global availability. Never expose other
        # fields or links from inaccessible/archived Devices, and never create with sudo.
        existing = set(
            self.env["pinout.device"]
            .sudo()
            .with_context(active_test=False)
            .search([("device_uid", "in", uids)])
            .mapped("device_uid")
        )
        errors = []
        seen = {}
        for number, device_uid in enumerate(uids, start=1):
            if not device_uid.strip():
                errors.append(_("Row %(row)s: Device UID is required.", row=number))
            elif device_uid in seen:
                errors.append(
                    _(
                        "Row %(row)s: Device UID repeats row %(first)s.",
                        row=number,
                        first=seen[device_uid],
                    )
                )
            elif device_uid in existing:
                errors.append(
                    _("Row %(row)s: Device UID is already in use.", row=number)
                )
            seen[device_uid] = number
        if errors:
            raise UserError("\n".join(errors))

    def action_validate(self):
        self._validate_lines()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Batch checked"),
                "message": _(
                    "%(count)s devices are ready to create.", count=self.device_count
                ),
                "type": "success",
                "next": self._reopen(),
            },
        }

    def action_create(self):
        self._validate_lines()
        common = {
            "device_type_id": self.device_type_id.id,
            "current_product_id": self.current_product_id.id,
            "state": self.state,
            "quality_status": self.quality_status,
            "location_id": self.location_id.id,
            "physical_location_note": self.physical_location_note,
            # Context defaults must not attach unrelated identity/history links.
            "final_lot_id": False,
            "final_lot_history_ids": [fields.Command.clear()],
            "bundle_id": False,
            "manufacturing_order_ids": [fields.Command.clear()],
        }
        values = [
            dict(
                common,
                device_uid=line.device_uid,
                mac=line.mac,
                robonomics_device_address=line.robonomics_device_address,
            )
            for line in self.line_ids
        ]
        try:
            with self.env.cr.savepoint():
                context = {
                    key: value
                    for key, value in self.env.context.items()
                    if not key.startswith("default_")
                }
                devices = self.env["pinout.device"].with_context(context).create(values)
        except UniqueViolation as error:
            if error.diag.constraint_name != "pinout_device_device_uid_unique":
                raise
            raise UserError(
                _(
                    "A Device UID is already in use. Check the rows again; no devices were created."
                )
            ) from error
        action = self.env["ir.actions.actions"]._for_xml_id(
            "pinout_device_registry.pinout_device_action"
        )
        action.update(
            domain=[("id", "in", devices.ids)],
            context={},
            name=_("Created Devices (%(count)s)", count=len(devices)),
        )
        return action


class PinoutDeviceBatchCreateLine(models.TransientModel):
    _name = "pinout.device.batch.create.line"
    _description = "New Device Row"
    _order = "id"

    wizard_id = fields.Many2one(
        "pinout.device.batch.create.wizard", required=True, ondelete="cascade"
    )
    # Missing UIDs must be saveable so the check can report all invalid rows.
    device_uid = fields.Char(string="Device UID")
    mac = fields.Char(string="MAC")
    robonomics_device_address = fields.Char(string="Robonomics Device Address")
