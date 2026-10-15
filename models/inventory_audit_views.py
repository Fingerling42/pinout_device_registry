from odoo import _, api, fields, models
from odoo.osv import expression


class InventoryAuditConfig(models.Model):
    _inherit = "pinout.inventory.audit.config"

    surplus_group_count = fields.Integer(compute="_compute_audit_counts")
    mismatch_group_count = fields.Integer(compute="_compute_audit_counts")
    not_checked_device_count = fields.Integer(compute="_compute_audit_counts")
    mismatch_group_ids = fields.Many2many(
        "pinout.inventory.audit.group", compute="_compute_audit_counts"
    )

    @api.depends_context("uid", "company", "allowed_company_ids")
    @api.depends("company_id")
    def _compute_audit_counts(self):
        for config in self:
            groups = self.env["pinout.inventory.audit.group"].search(
                [("company_id", "=", config.company_id.id)]
            )
            mismatches = groups.filtered(
                lambda g: g.status in ("exact_mismatch", "group_shortage")
            )
            config.mismatch_group_ids = mismatches
            config.mismatch_group_count = len(mismatches)
            config.surplus_group_count = len(
                groups.filtered(lambda g: g.surplus_quantity > 0)
            )
            config.not_checked_device_count = self.env[
                "pinout.inventory.audit.device"
            ].search_count(
                [
                    ("company_id", "=", config.company_id.id),
                    ("status", "=", "not_checked"),
                ]
            )

    @api.model
    def action_open_dashboard(self):
        config = self.search([("company_id", "=", self.env.company.id)], limit=1)
        view = (
            "inventory_audit_dashboard_form"
            if config
            else "inventory_audit_config_form"
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Inventory Audit"),
            "res_model": self._name,
            "res_id": config.id,
            "views": [(self.env.ref("pinout_device_registry." + view).id, "form")],
            "target": "current",
        }

    def action_open_groups(self):
        self.check_access_rights("read")
        self.ensure_one()
        self.check_access_rule("read")
        action = self.env["ir.actions.actions"]._for_xml_id(
            "pinout_device_registry.inventory_audit_group_action"
        )
        action["domain"] = [("company_id", "=", self.company_id.id)]
        return action

    def action_open_surplus(self):
        action = self.action_open_groups()
        action["context"] = {"search_default_surplus": 1}
        return action

    def action_open_not_checked(self):
        self.check_access_rights("read")
        self.ensure_one()
        self.check_access_rule("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Not Checked Devices"),
            "res_model": "pinout.inventory.audit.device",
            "views": [(False, "tree"), (False, "form")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("status", "=", "not_checked"),
            ],
        }

    def action_open_configuration(self):
        self.check_access_rights("read")
        self.ensure_one()
        self.check_access_rule("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Inventory Audit Configuration"),
            "res_model": self._name,
            "res_id": self.id,
            "views": [
                (
                    self.env.ref(
                        "pinout_device_registry.inventory_audit_config_form"
                    ).id,
                    "form",
                )
            ],
        }


class InventoryAuditGroup(models.Model):
    _inherit = "pinout.inventory.audit.group"

    def action_open_devices(self):
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Registry Devices (group membership, not missing UIDs)"),
            "res_model": "pinout.device",
            "views": [(False, "tree"), (False, "form")],
            "domain": [("id", "in", self.device_result_ids.device_id.ids)],
            "context": {"active_test": False},
        }

    def action_open_quants(self):
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        self.env["stock.quant"].check_access_rights("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Quants"),
            "res_model": "stock.quant",
            "views": [(self.env.ref("stock.view_stock_quant_tree").id, "tree")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("product_id", "=", self.product_id.id),
                ("location_id", "=", self.location_id.id),
            ],
            "context": {"create": False, "edit": False, "delete": False},
        }


class PinoutDevice(models.Model):
    _inherit = "pinout.device"

    inventory_audit_result_ids = fields.One2many(
        "pinout.inventory.audit.device", "device_id", groups="stock.group_stock_user"
    )
    inventory_check_status = fields.Selection(
        selection=[
            ("not_checked", "Not Checked"),
            ("match", "Match"),
            ("exact_mismatch", "Exact Mismatch"),
            ("group_shortage", "Group Shortage"),
        ],
        compute="_compute_inventory_check",
        search="_search_inventory_check_status",
        groups="stock.group_stock_user",
        string="Inventory Check Status",
    )
    inventory_check_details = fields.Text(
        compute="_compute_inventory_check", groups="stock.group_stock_user"
    )
    last_inventory_check = fields.Datetime(
        compute="_compute_inventory_check", groups="stock.group_stock_user"
    )
    inventory_check_pending = fields.Boolean(
        compute="_compute_inventory_check",
        search="_search_inventory_check_pending",
        groups="stock.group_stock_user",
        string="Inventory Check Pending / Stale",
    )
    inventory_check_error = fields.Text(
        compute="_compute_inventory_check", groups="stock.group_stock_user"
    )

    @api.depends_context("uid", "company", "allowed_company_ids")
    @api.depends(
        "inventory_audit_result_ids.status",
        "inventory_audit_result_ids.details",
        "inventory_audit_result_ids.checked_at",
        "inventory_audit_result_ids.pending",
        "inventory_audit_result_ids.last_error",
    )
    def _compute_inventory_check(self):
        # Search with the reader's rights: even a related field with compute_sudo
        # could otherwise expose a snapshot whose stock sources are hidden.
        results = self.env["pinout.inventory.audit.device"].search(
            [("device_id", "in", self.ids)]
        )
        by_device = {result.device_id.id: result for result in results}
        for device in self:
            result = by_device.get(device.id)
            device.inventory_check_status = result.status if result else "not_checked"
            device.inventory_check_details = (
                result.details
                if result
                else _(
                    "No visible inventory audit result. Configure the scope and Refresh, or check your source access."
                )
            )
            device.last_inventory_check = result.checked_at if result else False
            device.inventory_check_pending = result.pending if result else True
            device.inventory_check_error = result.last_error if result else False

    def _inventory_check_domain(self, field, operator, value, default):
        Result = self.env["pinout.inventory.audit.device"]
        results = Result.search([])
        matching = Result.search([(field, operator, value)])
        domain = [("id", "in", matching.device_id.ids)]
        # Include absent/inaccessible results exactly when their displayed default
        # satisfies the filter, using Odoo's own domain evaluator for operators.
        default_matches = bool(
            Result.new({field: default}).filtered_domain([(field, operator, value)])
        )
        if default_matches:
            domain = expression.OR([domain, [("id", "not in", results.device_id.ids)]])
        return domain

    def _search_inventory_check_status(self, operator, value):
        return self._inventory_check_domain("status", operator, value, "not_checked")

    def _search_inventory_check_pending(self, operator, value):
        return self._inventory_check_domain("pending", operator, value, True)
