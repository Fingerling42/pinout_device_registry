"""Explicit manual refresh of derived audit results."""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from psycopg2 import Error as DatabaseError

_logger = logging.getLogger(__name__)


class InventoryAuditConfig(models.Model):
    _inherit = "pinout.inventory.audit.config"

    full_check_started_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)

    def _try_refresh_lock(self):
        self.ensure_one()
        if not self.env.su:
            raise AccessError(
                _("Inventory audit calculation requires full source access.")
            )
        # Full and selected manual checks share a transaction-scoped lock.
        # Source hooks must still be able to invalidate the snapshot.
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(734921, %s)", [self.company_id.id]
        )
        return self.env.cr.fetchone()[0]

    def _queue_full_check(self):
        """Discover keys without aggregating stock, including forms without Devices."""
        self.ensure_one()
        if not self.env.su:
            raise AccessError(
                _("Inventory audit calculation requires full source access.")
            )
        locations, products = self._get_locations(), self._get_products()
        Group = self.env["pinout.inventory.audit.group"]
        Result = self.env["pinout.inventory.audit.device"]
        devices = (
            self.env["pinout.device"]
            .with_context(active_test=False)
            .search(
                expression.OR(
                    [
                        [("location_id.company_id", "=", self.company_id.id)],
                        [("location_id.company_id", "=", False)],
                    ]
                )
            )
        )
        quants = self.env["stock.quant"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("product_id", "in", products.ids),
                ("location_id", "in", locations.ids),
            ]
        )
        keys = {(q.product_id.id, q.location_id.id) for q in quants}
        keys.update(
            (d.current_product_id.id, d.location_id.id)
            for d in devices
            if d.current_product_id in products and d.location_id in locations
        )
        groups = Group.search([("company_id", "=", self.company_id.id)])
        for product_id, location_id in sorted(keys):
            groups |= Group._get_or_create(
                self.company_id,
                self.env["product.product"].browse(product_id),
                self.env["stock.location"].browse(location_id),
            )
        outside = groups.filtered(
            lambda g: g.product_id not in products or g.location_id not in locations
        )
        outside._discard()
        groups -= outside
        groups._mark_pending()
        results = Result.search([("company_id", "=", self.company_id.id)])
        for device in devices - results.device_id:
            # An uncertain Device may already belong to another company's audit.
            if not Result.search_count([("device_id", "=", device.id)]):
                results |= Result._for_device(device, self.company_id)
        results._mark_pending()
        self.write(
            {
                "full_check_pending": False,
                "full_check_started_at": fields.Datetime.now(),
                "last_error": False,
            }
        )

    def _process_refresh(self, limit=50):
        """Process at most limit group/orphan units, isolating recoverable errors."""
        self.ensure_one()
        if not self.env.su:
            raise AccessError(
                _("Inventory audit calculation requires full source access.")
            )
        if limit < 1:
            raise ValueError("Inventory audit batch limit must be positive")
        Group = self.env["pinout.inventory.audit.group"]
        Result = self.env["pinout.inventory.audit.device"]
        domain = [("company_id", "=", self.company_id.id), ("pending", "=", True)]
        groups = Group.search(domain, order="write_date, id", limit=limit)
        # Group membership may be stale. Included Devices are found again from
        # current keys; moved and incomplete Devices are processed independently.
        results = (
            Result.search(
                [*domain, ("group_id", "=", False)],
                order="write_date, id",
                limit=max(limit - len(groups), 0),
            )
            if len(groups) < limit
            else Result.browse()
        )
        units = [(group, Result.browse()) for group in groups]
        units += [(Group.browse(), result) for result in results]
        for group, result in units:
            # An earlier group may have checked this initially ungrouped Device.
            if result and not result.pending:
                continue
            devices = result.device_id
            targets = group | Group.browse()
            affected = group.device_result_ids | Result.search(
                [("device_id", "in", devices.ids)]
            )
            generations = {r.id: r.generation for r in affected}
            generation = group.generation if group else None
            try:
                with self.env.cr.savepoint():
                    # A valid ungrouped Device needs its entire current group.
                    if (
                        devices
                        and devices.current_product_id in self._get_products()
                        and devices.location_id in self._get_locations()
                    ):
                        targets = Group._get_or_create(
                            self.company_id,
                            devices.current_product_id,
                            devices.location_id,
                        )
                    self._recalculate(targets, devices)
            except DatabaseError:
                # REPEATABLE READ conflicts/deadlocks need a transaction retry,
                # never an error stored against an obsolete PostgreSQL snapshot.
                raise
            except Exception as exc:
                _logger.exception(
                    "Inventory audit refresh failed for company %s", self.company_id.id
                )
                message = _(
                    "Inventory audit calculation failed (%s). Retry Refresh; see server log for details.",
                    type(exc).__name__,
                )
                if group:
                    group._store_error(generation, message)
                for result in affected:
                    result._store_error(generations[result.id], message)
                self.last_error = message
        if not Group.search_count(domain) and not Result.search_count(domain):
            values = {"last_error": False}
            if self.full_check_started_at:
                values.update(
                    {
                        "last_full_check_at": fields.Datetime.now(),
                        "full_check_started_at": False,
                    }
                )
            self.write(values)
        return len(units)

    @api.model
    def _cron_refresh(self, limit=50):
        """Compatibility entry point for legacy scheduled actions; never calculate.

        Upgrade disables the noupdate cron. Keeping its old entry point inert
        also prevents a re-enabled legacy action from resuming background work.
        """
        return None

    def action_refresh(self):
        self.check_access_rights("read")
        self.check_access_rule("read")
        for config in self:
            trusted = config.sudo()
            if not trusted._try_refresh_lock():
                raise UserError(
                    _(
                        "Inventory audit refresh is already running. Please retry shortly."
                    )
                )
            try:
                with self.env.cr.savepoint():
                    trusted._queue_full_check()
            except DatabaseError:
                raise
            except Exception as exc:
                _logger.exception(
                    "Inventory audit discovery failed for company %s",
                    config.company_id.id,
                )
                trusted.write(
                    {
                        "full_check_pending": True,
                        "last_error": _(
                            "Inventory audit discovery failed (%s). Retry Refresh; see server log for details.",
                            type(exc).__name__,
                        ),
                    }
                )
                continue
            # Check the full scope in this request, including orphan Devices.
            count = sum(
                self.env[model]
                .sudo()
                .search_count(
                    [("company_id", "=", config.company_id.id), ("pending", "=", True)]
                )
                for model in (
                    "pinout.inventory.audit.group",
                    "pinout.inventory.audit.device",
                )
            )
            trusted._process_refresh(max(count, 1))
        return True


class PinoutDevice(models.Model):
    _inherit = "pinout.device"

    def action_refresh_inventory_audit(self):
        self.check_access_rights("read")
        self.check_access_rule("read")
        Config = self.env["pinout.inventory.audit.config"]
        Config.check_access_rights("read")
        configs = Config.search([])
        refreshed = False
        for config in configs:
            devices = self.sudo().filtered(
                lambda d, config=config: (
                    not d.location_id.company_id
                    or d.location_id.company_id == config.company_id
                )
            )
            if not devices:
                continue
            trusted = config.sudo()
            if not trusted._try_refresh_lock():
                raise UserError(
                    _(
                        "Inventory audit refresh is already running. Please retry shortly."
                    )
                )
            refreshed = True
            # Refresh selected current keys and previous snapshot groups.
            Result = self.env["pinout.inventory.audit.device"].sudo()
            groups = Result.search(
                [
                    ("device_id", "in", devices.ids),
                    ("company_id", "=", config.company_id.id),
                ]
            ).group_id
            Group = self.env["pinout.inventory.audit.group"].sudo()
            for device in devices:
                if (
                    device.current_product_id in trusted._get_products()
                    and device.location_id in trusted._get_locations()
                ):
                    groups |= Group._get_or_create(
                        config.company_id, device.current_product_id, device.location_id
                    )
            # Failure rolls back the selected check. Full-scope checks instead
            # isolate errors per group. Neither path returns privileged sources.
            trusted._recalculate(groups, devices)
        if self and not refreshed:
            raise UserError(
                _(
                    "Configure Inventory Audit for the device company and select that company first."
                )
            )
        return True
