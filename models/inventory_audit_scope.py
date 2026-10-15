from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression


class InventoryAuditConfig(models.Model):
    _inherit = "pinout.inventory.audit.config"

    scope_message = fields.Char(compute="_compute_scope_message")

    @api.depends("scope_ids.location_id", "scope_ids.include_children")
    def _compute_scope_message(self):
        for config in self:
            config.scope_message = (
                _("No locations configured. Inventory audit scope is not configured.")
                if not config.scope_ids
                else _(
                    "Only active internal, non-scrap locations in this company are checked. Each location is checked separately."
                )
            )

    def _get_locations(self):
        """Return exact locations; overlapping roots never duplicate a location."""
        self.ensure_one()
        domains = []
        for scope in self.scope_ids:
            root = scope.location_id
            if (
                not root.active
                or root.scrap_location
                or root.usage not in ("internal", "view")
                or (root.company_id and root.company_id != self.company_id)
            ):
                continue
            domains.append(
                [("id", "child_of" if scope.include_children else "=", root.id)]
            )
        return self.env["stock.location"].search(
            expression.AND(
                [
                    expression.OR(domains) if domains else [("id", "=", False)],
                    [
                        ("active", "=", True),
                        ("usage", "=", "internal"),
                        ("scrap_location", "=", False),
                        ("company_id", "=", self.company_id.id),
                    ],
                ]
            )
        )

    def _get_products(self):
        self.ensure_one()
        templates = (
            self.env["pinout.device.type"]
            .search([("active", "=", True)])
            .allowed_product_template_ids
        )
        return self.env["product.product"].search(
            [
                ("product_tmpl_id", "in", templates.ids),
                ("active", "=", True),
                ("product_tmpl_id.active", "=", True),
                ("type", "=", "product"),
                ("company_id", "in", [False, self.company_id.id]),
            ]
        )

    def _invalidate_scope(self):
        # Called only by trusted ORM hooks. Never change Inventory or Registry.
        for config in self.sudo():
            config.full_check_pending = True
            groups = (
                self.env["pinout.inventory.audit.group"]
                .sudo()
                .search(
                    [
                        ("company_id", "=", config.company_id.id),
                    ]
                )
            )
            locations = config._get_locations()
            products = config._get_products()
            excluded = groups.filtered(
                lambda group, locations=locations, products=products: (
                    group.location_id not in locations
                    or group.product_id not in products
                )
            )
            excluded._discard()
            (groups - excluded)._mark_pending()
            results = (
                self.env["pinout.inventory.audit.device"]
                .sudo()
                .search(
                    [
                        ("company_id", "=", config.company_id.id),
                    ]
                )
            )
            outside = results.filtered(
                lambda result, locations=locations, products=products: (
                    result.location_id not in locations
                    or result.product_id not in products
                )
            )
            outside._reset(
                "The device is outside the configured inventory audit scope."
            )
            (results - outside)._mark_pending()

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        configs._invalidate_scope()
        return configs

    def write(self, values):
        if "company_id" in values:
            scopes = self.scope_ids
            for config in self:
                if config.company_id.id != values["company_id"]:
                    # Retire the old company's results before moving the setting.
                    self.env["pinout.inventory.audit.group"].sudo().search(
                        [
                            ("company_id", "=", config.company_id.id),
                        ]
                    )._discard()
                    self.env["pinout.inventory.audit.device"].sudo().search(
                        [
                            ("company_id", "=", config.company_id.id),
                        ]
                    )._reset("The inventory audit configuration changed company.")
        result = super().write(values)
        if "company_id" in values:
            scopes._check_location()
            self._invalidate_scope()
        return result

    def unlink(self):
        for config in self:
            self.env["pinout.inventory.audit.group"].sudo().search(
                [
                    ("company_id", "=", config.company_id.id),
                ]
            )._discard()
            self.env["pinout.inventory.audit.device"].sudo().search(
                [
                    ("company_id", "=", config.company_id.id),
                ]
            )._reset("Inventory audit scope is not configured.")
        return super().unlink()


class InventoryAuditScope(models.Model):
    _inherit = "pinout.inventory.audit.scope"

    @api.constrains("location_id", "config_id", "include_children")
    def _check_location(self):
        self._check_company()
        for scope in self:
            location = scope.location_id
            if (
                not location.active
                or location.scrap_location
                or location.usage not in ("internal", "view")
            ):
                raise ValidationError(
                    _(
                        "Choose an active internal or view location that is not a scrap location."
                    )
                )
            if location.usage == "view" and not scope.include_children:
                raise ValidationError(
                    _(
                        "A view location requires Include Child Locations; it holds no stock itself."
                    )
                )
            if location.usage == "internal" and not location.company_id:
                raise ValidationError(
                    _("An internal audit location must have a company.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        scopes = super().create(vals_list)
        scopes.config_id._invalidate_scope()
        return scopes

    def write(self, values):
        configs = self.config_id
        result = super().write(values)
        (configs | self.config_id)._invalidate_scope()
        return result

    def unlink(self):
        configs = self.config_id
        result = super().unlink()
        configs._invalidate_scope()
        return result


class StockLocation(models.Model):
    _inherit = "stock.location"

    @api.model_create_multi
    def create(self, vals_list):
        locations = super().create(vals_list)
        self.env["pinout.inventory.audit.config"].sudo().search([])._invalidate_scope()
        return locations

    def write(self, values):
        result = super().write(values)
        if {
            "location_id",
            "usage",
            "company_id",
            "active",
            "scrap_location",
        } & values.keys():
            self.env["pinout.inventory.audit.config"].sudo().search(
                []
            )._invalidate_scope()
        return result

    def unlink(self):
        result = super().unlink()
        self.env["pinout.inventory.audit.config"].sudo().search([])._invalidate_scope()
        return result


class DeviceType(models.Model):
    _inherit = "pinout.device.type"

    @api.model_create_multi
    def create(self, vals_list):
        types = super().create(vals_list)
        self.env["pinout.inventory.audit.config"].sudo().search([])._invalidate_scope()
        return types

    def write(self, values):
        result = super().write(values)
        if {"allowed_product_template_ids", "active"} & values.keys():
            self.env["pinout.inventory.audit.config"].sudo().search(
                []
            )._invalidate_scope()
        return result

    def unlink(self):
        result = super().unlink()
        self.env["pinout.inventory.audit.config"].sudo().search([])._invalidate_scope()
        return result


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def write(self, values):
        result = super().write(values)
        if {"active", "type", "detailed_type", "company_id"} & values.keys():
            self.env["pinout.inventory.audit.config"].sudo().search(
                []
            )._invalidate_scope()
        return result


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        self.env["pinout.inventory.audit.config"].sudo().search([])._invalidate_scope()
        return products

    def write(self, values):
        result = super().write(values)
        if {"active", "product_tmpl_id"} & values.keys():
            self.env["pinout.inventory.audit.config"].sudo().search(
                []
            )._invalidate_scope()
        return result

    def unlink(self):
        result = super().unlink()
        self.env["pinout.inventory.audit.config"].sudo().search([])._invalidate_scope()
        return result
