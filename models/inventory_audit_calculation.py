from collections import defaultdict

from odoo import _, models
from odoo.exceptions import AccessError
from odoo.osv import expression
from odoo.tools.float_utils import float_compare


class InventoryAuditConfig(models.Model):
    _inherit = "pinout.inventory.audit.config"

    def _calculate(self, groups=None, devices=None):
        """Build a company snapshot without writing sources or audit results.

        Trusted callers must use sudo: a partial set of sources cannot prove a
        match. Stored results retain the source access protection of stage 1.
        """
        self.ensure_one()
        if not self.env.su:
            raise AccessError(
                _("Inventory audit calculation requires full source access.")
            )
        products = self._get_products()
        locations = self._get_locations()
        Device = self.env["pinout.device"].with_context(active_test=False)
        all_devices = Device.search([])
        quant_domain = []
        keys = None
        if groups is not None:
            keys = {(g.product_id.id, g.location_id.id) for g in groups}
            quant_domain = (
                expression.OR(
                    [
                        [("product_id", "=", p), ("location_id", "=", loc)]
                        for p, loc in sorted(keys)
                    ]
                )
                if keys
                else [("id", "=", False)]
            )
            device_domain = expression.OR(
                [
                    [("current_product_id", "=", p), ("location_id", "=", loc)]
                    for p, loc in sorted(keys)
                ]
                + [[("id", "in", (devices | groups.device_result_ids.device_id).ids)]]
            )
            devices = Device.search(device_domain)
        else:
            devices = all_devices
        grouped_devices = defaultdict(list)
        excluded = []
        for device in devices:
            location = device.location_id
            # Devices have no company. A known foreign location belongs to that
            # company's pass; incomplete records are diagnosed by this pass.
            if location.company_id and location.company_id != self.company_id:
                continue
            reason = False
            if not device.active:
                reason = _("Archived device is excluded from physical stock.")
            elif device.state in ("sold", "scrapped"):
                reason = _("Sold or scrapped device is excluded from physical stock.")
            elif not device.current_product_id:
                reason = _("Current Product is missing.")
            elif not location:
                reason = _("Location is missing; audit company cannot be determined.")
            elif not location.company_id:
                reason = _(
                    "Location has no company; audit company cannot be determined."
                )
            elif location not in locations:
                reason = _("Location is outside the configured inventory audit scope.")
            elif device.current_product_id not in products:
                reason = _("Current Product is outside the configured Product Forms.")
            if reason:
                excluded.append((device, {"status": "not_checked", "details": reason}))
            elif keys is None or (device.current_product_id.id, location.id) in keys:
                grouped_devices[(device.current_product_id.id, location.id)].append(
                    device
                )

        quantities = self.env["stock.quant"]._read_group(
            [
                ("company_id", "=", self.company_id.id),
                ("product_id", "in", products.ids),
                ("location_id", "in", locations.ids),
                *quant_domain,
            ],
            ["product_id", "location_id"],
            ["quantity:sum"],
        )
        actual = {
            (product.id, location.id): qty for product, location, qty in quantities
        }
        # Keep existing zero groups so the last Device/Quant disappearing clears
        # the previous shortage/surplus instead of leaving a stale result.
        existing = (
            groups
            if groups is not None
            else self.env["pinout.inventory.audit.group"].search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("product_id", "in", products.ids),
                    ("location_id", "in", locations.ids),
                ]
            )
        )
        keys = (
            set(actual)
            | set(grouped_devices)
            | {(group.product_id.id, group.location_id.id) for group in existing}
        )
        lots = (
            self.env["pinout.device"]
            .browse(
                [device.id for group in grouped_devices.values() for device in group]
            )
            .final_lot_id
        )
        serial_quants = self.env["stock.quant"].search([("lot_id", "in", lots.ids)])
        by_lot = defaultdict(list)
        for quant in serial_quants:
            by_lot[quant.lot_id.id].append(quant)

        registered_lots = all_devices.final_lot_id | all_devices.final_lot_history_ids
        serial_stock = self.env["stock.quant"]._read_group(
            [
                ("company_id", "=", self.company_id.id),
                (
                    "product_id",
                    "in",
                    products.filtered(lambda p: p.tracking == "serial").ids,
                ),
                ("location_id", "in", locations.ids),
                ("lot_id", "not in", registered_lots.ids),
                *quant_domain,
            ],
            ["product_id", "location_id", "lot_id"],
            ["quantity:sum"],
        )
        unregistered = defaultdict(float)
        uncertain_serial_stock = set()
        for product, location, lot, qty in serial_stock:
            if not float_compare(qty, 0, precision_rounding=product.uom_id.rounding):
                continue
            if (
                not lot
                or lot.product_id != product
                or lot.company_id != self.company_id
                or float_compare(qty, 1, precision_rounding=product.uom_id.rounding)
            ):
                uncertain_serial_stock.add((product.id, location.id))
            else:
                unregistered[(product.id, location.id)] += qty
        groups = []
        for product_id, location_id in sorted(keys):
            product = self.env["product.product"].browse(product_id)
            location = self.env["stock.location"].browse(location_id)
            members = grouped_devices[(product_id, location_id)]
            expected = len(members)
            quantity = actual.get((product_id, location_id), 0.0)
            comparison = float_compare(
                quantity, expected, precision_rounding=product.uom_id.rounding
            )
            shortage = expected - quantity if comparison < 0 else 0.0
            surplus = quantity - expected if comparison > 0 else 0.0
            results = []
            for device in members:
                if product.tracking == "none":
                    values = {
                        "status": "group_shortage" if shortage else "match",
                        "details": _(
                            "Group quantity is insufficient; no individual missing UID can be identified."
                        )
                        if shortage
                        else _(
                            "Group quantity is sufficient; this does not prove the presence of an individual UID."
                        ),
                    }
                elif product.tracking == "lot":
                    values = {
                        "status": "not_checked",
                        "details": _(
                            "Lot tracking does not support an exact device check."
                        ),
                    }
                else:
                    values = self._calculate_serial(
                        device, by_lot[device.final_lot_id.id]
                    )
                results.append((device, values))
            mismatches = sum(
                values["status"] == "exact_mismatch" for _, values in results
            )
            unchecked = any(values["status"] == "not_checked" for _, values in results)
            unchecked = unchecked or (product_id, location_id) in uncertain_serial_stock
            if product.tracking == "lot" or unchecked:
                status = "not_checked"
            else:
                status = "group_shortage" if shortage else "match"
            if mismatches:
                status = "exact_mismatch"
            if product.tracking != "none":
                # Only positive stock of serials absent from the entire current
                # Registry can be called unregistered. A misplaced active or
                # historical serial and an unchecked case are not proof.
                surplus = 0.0
                if product.tracking == "serial" and not unchecked:
                    surplus = min(
                        max(quantity - expected, 0),
                        unregistered[(product.id, location.id)],
                    )
            groups.append(
                {
                    "product": product,
                    "location": location,
                    "devices": results,
                    "values": {
                        "expected_quantity": expected,
                        "actual_quantity": quantity,
                        "shortage_quantity": shortage,
                        "surplus_quantity": surplus,
                        "exact_mismatch_count": mismatches,
                        "status": status,
                        "details": _(
                            "Some serial stock cannot be verified; no unregistered surplus is confirmed."
                        )
                        if (product_id, location_id) in uncertain_serial_stock
                        else (
                            _("Lot tracking does not support an exact device check.")
                            if product.tracking == "lot"
                            else _(
                                "See individual device reasons; quantities refer to this exact location."
                            )
                        ),
                    },
                }
            )
        return {"groups": groups, "excluded": excluded}

    def _calculate_serial(self, device, quants):
        lot = device.final_lot_id
        if not lot:
            return {
                "status": "not_checked",
                "details": _("Active Final Lot is missing."),
            }
        if lot.company_id != self.company_id:
            return {
                "status": "not_checked",
                "details": _("Final Lot company conflicts with Location company."),
            }
        problems = []
        if lot.product_id != device.current_product_id:
            problems.append(_("Final Lot Product differs from Current Product."))
        if lot.name != device.device_uid or lot.pinout_device_id != device:
            problems.append(_("Final Lot identity does not match this device."))
        amounts = defaultdict(float)
        for quant in quants:
            amounts[
                (quant.company_id.id, quant.product_id.id, quant.location_id.id)
            ] += quant.quantity
        key = (self.company_id.id, device.current_product_id.id, device.location_id.id)
        rounding = device.current_product_id.uom_id.rounding
        if float_compare(amounts.get(key, 0), 1, precision_rounding=rounding):
            problems.append(_("Active serial quantity at the exact Location is not 1."))
        elsewhere = []
        for quant_key, qty in amounts.items():
            if quant_key != key and float_compare(qty, 0, precision_rounding=rounding):
                problems.append(
                    _(
                        "Active serial has stock outside the expected Product/Location/company."
                    )
                )
                if qty > 0:
                    elsewhere.append(
                        self.env["stock.location"].browse(quant_key[2]).display_name
                    )
        if elsewhere:
            problems.append(
                _(
                    "Positive serial stock found at: %s",
                    ", ".join(sorted(set(elsewhere))),
                )
            )
        return {
            "status": "exact_mismatch" if problems else "match",
            "details": "\n".join(problems)
            if problems
            else _(
                "Active serial matches Product, company, quantity 1 and exact Location."
            ),
        }

    def _recalculate(self, groups=None, devices=None):
        """Capture generations before reading sources; each group is atomic."""
        self.ensure_one()
        if not self.env.su:
            raise AccessError(
                _("Inventory audit calculation requires full source access.")
            )
        Group = self.env["pinout.inventory.audit.group"]
        Result = self.env["pinout.inventory.audit.device"]
        previous_groups = (
            Group.search([("company_id", "=", self.company_id.id)])
            if groups is None
            else groups
        )
        previous_results = Result.search([])
        group_generations = {g.id: g.generation for g in previous_groups}
        result_generations = {r.id: r.generation for r in previous_results}
        snapshot = (
            self._calculate() if groups is None else self._calculate(groups, devices)
        )

        def check_result(device):
            result = Result.search([("device_id", "=", device.id)])
            if result:
                result._lock()
                if result.generation != result_generations.get(result.id, 1):
                    raise ValueError(
                        "Inventory audit device changed during calculation"
                    )

        for item in snapshot["groups"]:
            with self.env.cr.savepoint():
                group = Group._get_or_create(
                    self.company_id, item["product"], item["location"]
                )
                generation = group_generations.get(group.id, 1)
                group._lock()
                if group.generation != generation:
                    raise ValueError("Inventory audit group changed during calculation")
                current = self.env["pinout.device"].browse(
                    [device.id for device, _ in item["devices"]]
                )
                removed = group.device_result_ids.filtered(
                    lambda result, current=current: result.device_id not in current
                )
                for result in removed:
                    check_result(result.device_id)
                removed._reset(
                    "Device is no longer a member of this inventory audit group."
                )
                result_generations.update({r.id: r.generation for r in removed})
                for device, values in item["devices"]:
                    check_result(device)
                    result = Result._for_device(device, self.company_id, group)
                    if not result._store_success(result.generation, values):
                        raise ValueError(
                            "Inventory audit device changed during calculation"
                        )
                if not group._store_success(generation, item["values"]):
                    raise ValueError("Inventory audit group changed during calculation")
        for device, values in snapshot["excluded"]:
            with self.env.cr.savepoint():
                check_result(device)
                result = Result._for_device(device, self.company_id)
                result._store_success(result.generation, values)
        return snapshot
