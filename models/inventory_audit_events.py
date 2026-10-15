"""Transactional dirty markers; source hooks never calculate quantities."""

from odoo import api, models
from odoo.osv import expression


class InventoryAuditConfig(models.Model):
    _inherit = "pinout.inventory.audit.config"

    @api.model
    def _invalidate_inputs(self, keys=(), lot_ids=(), device_ids=(), product_ids=()):
        configs = self.sudo().search([])
        if not configs:
            return
        keys = set(keys)
        Device = self.env["pinout.device"].sudo().with_context(active_test=False)
        Quant = self.env["stock.quant"].sudo()
        devices = Device.browse(device_ids).exists()
        if lot_ids or product_ids:
            devices |= Device.search(
                expression.OR(
                    [
                        [("final_lot_id", "in", list(lot_ids))],
                        [("current_product_id", "in", list(product_ids))],
                    ]
                )
            )
            quants = Quant.search(
                expression.OR(
                    [
                        [("lot_id", "in", list(lot_ids))],
                        [("product_id", "in", list(product_ids))],
                    ]
                )
            )
            keys.update(quants._inventory_audit_keys())
        keys.update(devices._inventory_audit_keys())
        Group = self.env["pinout.inventory.audit.group"].sudo()
        Result = self.env["pinout.inventory.audit.device"].sudo()
        results = Result.search([("device_id", "in", devices.ids)])
        # Snapshot groups still need refreshing after a source moves away.
        groups = results.group_id
        if lot_ids or product_ids:
            groups |= Group.search(
                expression.OR(
                    [
                        [("source_lot_ids", "in", list(lot_ids))],
                        [("product_id", "in", list(product_ids))],
                    ]
                )
            )
        for config in configs:
            locations = config._get_locations()
            products = config._get_products()
            for company_id, product_id, location_id in sorted(keys):
                if (
                    company_id == config.company_id.id
                    and location_id in locations.ids
                    and product_id in products.ids
                ):
                    groups |= Group._get_or_create(
                        config.company_id,
                        self.env["product.product"].browse(product_id),
                        self.env["stock.location"].browse(location_id),
                    )
            for device in devices - results.device_id:
                company = device.location_id.company_id
                if company == config.company_id or (
                    not company and config == configs[:1]
                ):
                    results |= Result._for_device(device, config.company_id)
        groups._mark_pending()
        (results | groups.device_result_ids)._mark_pending()


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def _inventory_audit_keys(self):
        return {(q.company_id.id, q.product_id.id, q.location_id.id) for q in self}

    @api.model_create_multi
    def create(self, vals_list):
        quants = super().create(vals_list)
        self.env["pinout.inventory.audit.config"]._invalidate_inputs(
            keys=quants.sudo()._inventory_audit_keys(), lot_ids=quants.sudo().lot_id.ids
        )
        return quants

    def write(self, values):
        relevant = {"quantity", "product_id", "location_id", "lot_id"} & values.keys()
        keys = self.sudo()._inventory_audit_keys() if relevant else set()
        lots = self.sudo().lot_id if relevant else self.env["stock.lot"]
        result = super().write(values)
        if relevant:
            self.env["pinout.inventory.audit.config"]._invalidate_inputs(
                keys=keys | self.sudo()._inventory_audit_keys(),
                lot_ids=(lots | self.sudo().lot_id).ids,
            )
        return result

    def unlink(self):
        keys = self.sudo()._inventory_audit_keys()
        lots = self.sudo().lot_id.ids
        result = super().unlink()
        self.env["pinout.inventory.audit.config"]._invalidate_inputs(
            keys=keys, lot_ids=lots
        )
        return result


class Device(models.Model):
    _inherit = "pinout.device"

    def _inventory_audit_keys(self):
        return {
            (d.location_id.company_id.id, d.current_product_id.id, d.location_id.id)
            for d in self
        }

    @api.model_create_multi
    def create(self, vals_list):
        devices = super().create(vals_list)
        self.env["pinout.inventory.audit.config"]._invalidate_inputs(
            device_ids=devices.ids, lot_ids=devices.sudo().final_lot_history_ids.ids
        )
        return devices

    def write(self, values):
        relevant = {
            "current_product_id",
            "location_id",
            "final_lot_id",
            "state",
            "active",
            "device_uid",
            "device_type_id",
        } & values.keys()
        keys = self.sudo()._inventory_audit_keys() if relevant else set()
        lots = self.sudo().final_lot_history_ids if relevant else self.env["stock.lot"]
        result = super().write(values)
        if relevant:
            self.env["pinout.inventory.audit.config"]._invalidate_inputs(
                keys=keys,
                device_ids=self.ids,
                lot_ids=(
                    lots | self.sudo().final_lot_history_ids | self.sudo().final_lot_id
                ).ids,
            )
        return result

    def unlink(self):
        keys = self.sudo()._inventory_audit_keys()
        lots = (self.sudo().final_lot_history_ids | self.sudo().final_lot_id).ids
        # Capture the old group even for a previously incomplete Device.
        groups = (
            self.env["pinout.inventory.audit.device"]
            .sudo()
            .search([("device_id", "in", self.ids)])
            .group_id
        )
        result = super().unlink()
        groups._mark_pending()
        groups.device_result_ids._mark_pending()
        self.env["pinout.inventory.audit.config"]._invalidate_inputs(
            keys=keys, lot_ids=lots
        )
        return result


class StockLot(models.Model):
    _inherit = "stock.lot"

    def write(self, values):
        relevant = {
            "name",
            "product_id",
            "company_id",
            "pinout_device_id",
        } & values.keys()
        products = self.sudo().product_id if relevant else self.env["product.product"]
        result = super().write(values)
        if relevant:
            # Historical links also change whether serial stock is unregistered.
            self.env["pinout.inventory.audit.config"]._invalidate_inputs(
                lot_ids=self.ids, product_ids=(products | self.sudo().product_id).ids
            )
        return result

    def unlink(self):
        products = self.sudo().product_id.ids
        self.env["pinout.inventory.audit.config"]._invalidate_inputs(
            lot_ids=self.ids, product_ids=products
        )
        return super().unlink()


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def write(self, values):
        result = super().write(values)
        if {"tracking", "uom_id"} & values.keys():
            self.env["pinout.inventory.audit.config"]._invalidate_inputs(
                product_ids=self.with_context(active_test=False).product_variant_ids.ids
            )
        return result


class Uom(models.Model):
    _inherit = "uom.uom"

    def write(self, values):
        result = super().write(values)
        if {"rounding", "factor", "factor_inv", "category_id"} & values.keys():
            products = (
                self.env["product.product"]
                .sudo()
                .with_context(active_test=False)
                .search([("uom_id", "in", self.ids)])
            )
            self.env["pinout.inventory.audit.config"]._invalidate_inputs(
                product_ids=products.ids
            )
        return result
