from typing import ClassVar

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.osv import expression
from odoo.tools import SQL


class InventoryAuditAccess(models.AbstractModel):
    _name = "pinout.inventory.audit.access"
    _description = "Inventory Audit Source Access"

    _source_fields: ClassVar = {
        "product.product": "source_product_ids",
        "stock.location": "source_location_ids",
        "stock.quant": "source_quant_ids",
        "stock.lot": "source_lot_ids",
        "pinout.device": "source_device_ids",
    }

    def _source_domains(self, include_snapshot=True):
        self.ensure_one()
        product_domain = [
            ("company_id", "=", self.company_id.id),
            ("product_id", "=", self.product_id.id),
        ]
        device_domain = [("current_product_id", "=", self.product_id.id)]
        if self._name == "pinout.inventory.audit.device":
            device_domain = expression.OR(
                [device_domain, [("id", "=", self.device_id.id)]]
            )
        devices = (
            self.env["pinout.device"]
            .sudo()
            .with_context(active_test=False)
            .search(device_domain)
        )
        lots = devices.final_lot_id
        if self._name == "pinout.inventory.audit.device":
            lots |= self.final_lot_id
        quant_domain = expression.OR([product_domain, [("lot_id", "in", lots.ids)]])
        quants = self.env["stock.quant"].sudo().search(quant_domain)
        sources = {
            "product.product": [
                (
                    "id",
                    "in",
                    (quants.product_id | lots.product_id | self.product_id).ids,
                )
            ],
            "stock.location": [
                ("id", "in", (quants.location_id | self.location_id).ids)
            ],
            "stock.quant": quant_domain,
            "stock.lot": expression.OR(
                [product_domain, [("id", "in", (lots | quants.lot_id).ids)]]
            ),
            "pinout.device": device_domain,
        }
        if include_snapshot:
            for model_name, field_name in self._source_fields.items():
                sources[model_name] = expression.OR(
                    [sources[model_name], [("id", "in", self[field_name].ids)]]
                )
        return sources

    def _visible_ids(self):
        """Check current sources and the sources of the last successful snapshot.

        Check the entire product in the company, including other locations:
        exact serial diagnostics may refer to stock outside the audit scope.
        sudo only detects missing access; it never returns source data.
        Evaluate per request, outside ir.rule's cache.
        """
        candidates = self.sudo().search([("company_id", "in", self.env.companies.ids)])
        visible = []
        source_access = {}
        for record in candidates:
            for model_name, domain in record._source_domains().items():
                model = self.env[model_name].with_context(active_test=False)
                key = (model_name, repr(domain))
                if key not in source_access:
                    source_access[key] = model.check_access_rights(
                        "read", raise_exception=False
                    ) and set(model.search(domain).ids) == set(
                        model.sudo().search(domain).ids
                    )
                if not source_access[key]:
                    break
            else:
                visible.append(record.id)
        return visible

    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        if not self.env.su:
            domain = expression.AND([domain, [("id", "in", self._visible_ids())]])
        return super()._search(domain, offset, limit, order, access_rights_uid)

    def check_access_rule(self, operation):
        super().check_access_rule(operation)
        if operation == "read" and not self.env.su:
            if set(self.ids) - set(self._visible_ids()):
                raise AccessError(
                    _("You cannot read the sources of this inventory audit.")
                )


class InventoryAuditResult(models.AbstractModel):
    _name = "pinout.inventory.audit.result"
    _description = "Latest Inventory Audit Result"
    _inherit = "pinout.inventory.audit.access"

    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    freshness = fields.Selection(
        [
            ("no_result", "No result"),
            ("recheck", "Recheck required"),
            ("unmarked", "No marked changes"),
        ],
        compute="_compute_freshness",
        string="Result freshness",
    )

    @api.depends("checked_at", "pending")
    def _compute_freshness(self):
        for result in self:
            result.freshness = (
                "no_result"
                if not result.checked_at
                else "recheck"
                if result.pending
                else "unmarked"
            )

    checked_at = fields.Datetime(readonly=True)
    pending = fields.Boolean(default=True, readonly=True, index=True)
    generation = fields.Integer(default=1, readonly=True)
    last_error = fields.Text(readonly=True)
    # Retain source identities until the next successful snapshot: sources can
    # move out of the current group while its stale quantities remain stored.
    # Archived sources still protect a stale snapshot after leaving its group.
    source_product_ids = fields.Many2many(
        "product.product", readonly=True, context={"active_test": False}
    )
    source_location_ids = fields.Many2many(
        "stock.location", readonly=True, context={"active_test": False}
    )
    source_quant_ids = fields.Many2many(
        "stock.quant", readonly=True, context={"active_test": False}
    )
    source_lot_ids = fields.Many2many(
        "stock.lot", readonly=True, context={"active_test": False}
    )
    source_device_ids = fields.Many2many(
        "pinout.device", readonly=True, context={"active_test": False}
    )

    def _lock(self):
        # Storage writes belong to trusted audit code, not RPC clients.
        if not self.env.su:
            raise AccessError(_("Inventory audit results are read-only."))
        self.flush_recordset()
        self.env.cr.execute(
            SQL(
                "SELECT id FROM %s WHERE id IN %s ORDER BY id FOR UPDATE",
                SQL.identifier(self._table),
                tuple(self.ids),
            )
        )
        self.invalidate_recordset()

    def _mark_pending(self):
        if not self:
            return
        self._lock()
        for record in self:
            record.write({"pending": True, "generation": record.generation + 1})

    def _store_success(self, generation, values):
        self.ensure_one()
        self._lock()
        if self.generation != generation:
            return False
        allowed = self._result_fields()
        if set(values) - allowed:
            raise ValueError("Unsupported inventory audit result fields")
        source_values = {
            self._source_fields[model_name]: [
                fields.Command.set(
                    self.env[model_name]
                    .with_context(active_test=False)
                    .search(domain)
                    .ids
                )
            ]
            for model_name, domain in self._source_domains(
                include_snapshot=False
            ).items()
        }
        self.write(
            {
                **values,
                **source_values,
                "checked_at": fields.Datetime.now(),
                "pending": False,
                "last_error": False,
            }
        )
        return True

    def _store_error(self, generation, message):
        self.ensure_one()
        self._lock()
        if self.generation == generation:
            self.write({"pending": True, "last_error": message})


class InventoryAuditConfig(models.Model):
    _name = "pinout.inventory.audit.config"
    _description = "Inventory Audit Configuration"
    _check_company_auto = True
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        ondelete="cascade",
    )
    scope_ids = fields.One2many("pinout.inventory.audit.scope", "config_id")
    full_check_pending = fields.Boolean(default=True, readonly=True)
    last_full_check_at = fields.Datetime(readonly=True)

    _sql_constraints: ClassVar = [
        (
            "company_unique",
            "unique(company_id)",
            "Only one inventory audit configuration is allowed per company.",
        ),
    ]


class InventoryAuditScope(models.Model):
    _name = "pinout.inventory.audit.scope"
    _description = "Inventory Audit Location Root"
    _check_company_auto = True
    _rec_name = "location_id"

    config_id = fields.Many2one(
        "pinout.inventory.audit.config", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="config_id.company_id", store=True, index=True)
    location_id = fields.Many2one(
        "stock.location", required=True, check_company=True, ondelete="restrict"
    )
    include_children = fields.Boolean(default=True)

    _sql_constraints: ClassVar = [
        (
            "root_unique",
            "unique(config_id, location_id)",
            "This location is already configured.",
        ),
    ]


class InventoryAuditGroup(models.Model):
    _name = "pinout.inventory.audit.group"
    _description = "Inventory Audit Product and Location"
    _inherit = "pinout.inventory.audit.result"
    _check_company_auto = True
    _rec_name = "product_id"

    product_id = fields.Many2one(
        "product.product",
        required=True,
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    location_id = fields.Many2one(
        "stock.location",
        required=True,
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    expected_quantity = fields.Float(readonly=True)
    actual_quantity = fields.Float(readonly=True)
    shortage_quantity = fields.Float(readonly=True)
    surplus_quantity = fields.Float(readonly=True)
    exact_mismatch_count = fields.Integer(readonly=True)
    status = fields.Selection(
        [
            ("not_checked", "Not Checked"),
            ("match", "Match"),
            ("exact_mismatch", "Exact Mismatch"),
            ("group_shortage", "Group Shortage"),
        ],
        default="not_checked",
        required=True,
        readonly=True,
    )
    details = fields.Text(readonly=True)
    device_result_ids = fields.One2many(
        "pinout.inventory.audit.device", "group_id", readonly=True
    )

    _sql_constraints: ClassVar = [
        (
            "group_unique",
            "unique(company_id, product_id, location_id)",
            "An inventory audit group already exists for this company, product and location.",
        ),
    ]

    @api.model
    def _get_or_create(self, company, product, location):
        if not self.env.su:
            raise AccessError(_("Inventory audit results are read-only."))
        # Serialize creation of a missing key; also covers groups with no Devices.
        self.env.cr.execute(
            "SELECT id FROM res_company WHERE id = %s FOR UPDATE", [company.id]
        )
        domain = [
            ("company_id", "=", company.id),
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
        ]
        return self.search(domain) or self.create(
            {
                "company_id": company.id,
                "product_id": product.id,
                "location_id": location.id,
            }
        )

    def _result_fields(self):
        return {
            "expected_quantity",
            "actual_quantity",
            "shortage_quantity",
            "surplus_quantity",
            "exact_mismatch_count",
            "status",
            "details",
        }

    def _discard(self):
        """Retire out-of-scope groups without leaving valid Device matches."""
        if not self:
            return
        self._lock()
        self.mapped("device_result_ids")._reset(
            "The inventory audit group is no longer in scope."
        )
        self.unlink()


class InventoryAuditDevice(models.Model):
    _name = "pinout.inventory.audit.device"
    _description = "Inventory Audit Device Result"
    _inherit = "pinout.inventory.audit.result"
    _check_company_auto = True
    _rec_name = "device_id"

    device_id = fields.Many2one(
        "pinout.device", required=True, ondelete="cascade", index=True
    )
    group_id = fields.Many2one(
        "pinout.inventory.audit.group",
        check_company=True,
        ondelete="set null",
        index=True,
    )
    # Snapshot keys: related fields would silently relabel an old result.
    product_id = fields.Many2one("product.product", ondelete="cascade")
    location_id = fields.Many2one("stock.location", ondelete="cascade")
    final_lot_id = fields.Many2one("stock.lot", ondelete="cascade")
    status = fields.Selection(
        [
            ("not_checked", "Not Checked"),
            ("match", "Match"),
            ("exact_mismatch", "Exact Mismatch"),
            ("group_shortage", "Group Shortage"),
        ],
        default="not_checked",
        required=True,
        readonly=True,
    )
    details = fields.Text(readonly=True)

    _sql_constraints: ClassVar = [
        (
            "device_unique",
            "unique(device_id)",
            "Only one current inventory audit result is allowed per Device.",
        ),
    ]

    @api.constrains("group_id", "product_id", "location_id", "company_id")
    def _check_group_key(self):
        for record in self.filtered("group_id"):
            if (
                record.company_id != record.group_id.company_id
                or record.product_id != record.group_id.product_id
                or record.location_id != record.group_id.location_id
            ):
                raise ValidationError(
                    _("Device audit keys must match the inventory audit group.")
                )

    def _result_fields(self):
        return {"status", "details"}

    @api.model
    def _for_device(self, device, company, group=None):
        if not self.env.su:
            raise AccessError(_("Inventory audit results are read-only."))
        self.env.cr.execute(
            "SELECT id FROM pinout_device WHERE id = %s FOR UPDATE", [device.id]
        )
        result = self.search([("device_id", "=", device.id)])
        values = {
            "company_id": company.id,
            "product_id": device.current_product_id.id,
            "location_id": device.location_id.id,
            "final_lot_id": device.final_lot_id.id,
            "group_id": group.id if group else False,
        }
        if not result:
            return self.create({"device_id": device.id, **values})
        if any(result[field].id != value for field, value in values.items()):
            result._reset("Device audit inputs changed.")
            result.write(values)
        return result

    def _reset(self, reason):
        if not self:
            return
        self._mark_pending()
        self.write(
            {
                "group_id": False,
                "status": "not_checked",
                "details": reason,
                "checked_at": False,
                "last_error": False,
            }
        )
