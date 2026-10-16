import logging
from time import perf_counter
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .test_inventory_audit_calculation import InventoryAuditCase

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class TestInventoryAuditBatch(InventoryAuditCase):
    def test_representative_batch_and_protected_list_read(self):
        """Measure actual work; assert batching, not machine-dependent timings."""
        reader = new_test_user(
            self.env,
            login="audit_batch_reader",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        started = perf_counter()
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=AssertionError("hook calculates"),
        ):
            devices = self.env["pinout.device"].create(
                [
                    {
                        "device_uid": "AUDIT-BATCH-%04d" % i,
                        "device_type_id": self.device_type.id,
                        "current_product_id": self.products[0].id,
                        "location_id": self.locations[i % 2].id,
                    }
                    for i in range(500)
                ]
            )
            for location in self.locations[:2]:
                self.quant(250, location=location)
        create_seconds = perf_counter() - started
        _logger.info("Audit batch: created 500 devices in %.3fs", create_seconds)
        groups = self.env["pinout.inventory.audit.group"].search(
            [("company_id", "=", self.env.company.id)]
        )
        self.assertEqual(len(groups), 2)
        generations = {group.id: group.generation for group in groups}
        started = perf_counter()
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=AssertionError("hook calculates"),
        ):
            devices.write({"state": "reserved"})
        invalidate_seconds = perf_counter() - started
        _logger.info(
            "Audit batch: invalidated 500 devices in %.3fs", invalidate_seconds
        )
        for group in groups:
            self.assertEqual(group.generation, generations[group.id] + 1)
        started = perf_counter()
        self.config.action_refresh()
        refresh_seconds = perf_counter() - started
        _logger.info("Audit batch: full refresh in %.3fs", refresh_seconds)
        self.assertEqual(set(groups.mapped("status")), {"match"})
        self.assertEqual(groups.mapped("expected_quantity"), [250, 250])
        self.env.invalidate_all()
        started = perf_counter()
        with (
            patch.object(
                type(self.config),
                "_calculate",
                side_effect=AssertionError("read calculates"),
            ),
            patch.object(
                type(self.env["stock.quant"]),
                "_read_group",
                side_effect=AssertionError("read aggregates stock"),
            ),
        ):
            rows = (
                devices[:80]
                .with_user(reader)
                .read(
                    ["device_uid", "inventory_check_status", "inventory_check_pending"]
                )
            )
            group_rows = groups.with_user(reader).read(
                ["expected_quantity", "actual_quantity", "status"]
            )
        read_seconds = perf_counter() - started
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(row["inventory_check_status"] == "match" for row in rows))
        self.assertTrue(all(not row["inventory_check_pending"] for row in rows))
        self.assertEqual(len(group_rows), 2)
        _logger.info(
            "Audit batch: 500 devices, 2 exact groups; create %.3fs; "
            "invalidate 500 %.3fs; full refresh %.3fs; "
            "protected cold read (80 devices + 2 groups) %.3fs",
            create_seconds,
            invalidate_seconds,
            refresh_seconds,
            read_seconds,
        )
        self.measure_filters(devices, reader, "500 devices/2 groups")

    def test_mixed_forms_filter_batch(self):
        reader = new_test_user(
            self.env,
            login="audit_mixed_batch_reader",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        devices = self.env["pinout.device"].create(
            [
                {
                    "device_uid": "AUDIT-MIXED-%s-%s-%s" % (product.id, location.id, i),
                    "device_type_id": self.device_type.id,
                    "current_product_id": product.id,
                    "location_id": location.id,
                }
                for product in self.products
                for location in self.locations[:2]
                for i in range(20)
            ]
        )
        for device in devices.filtered(
            lambda d: d.current_product_id.tracking == "serial"
        ):
            lot = self.env["stock.lot"].create(
                {
                    "name": device.device_uid,
                    "product_id": device.current_product_id.id,
                    "company_id": self.env.company.id,
                    "pinout_device_id": device.id,
                }
            )
            device.final_lot_id = lot
            if device.location_id == self.locations[0]:
                self.quant(1, device.current_product_id, device.location_id, lot)
        self.quant(20)
        self.quant(10, location=self.locations[1])
        self.config.action_refresh()
        self.assertEqual(
            set(devices.mapped("inventory_check_status")),
            {
                "match",
                "group_shortage",
                "exact_mismatch",
                "not_checked",
            },
        )
        # Preserve a successful snapshot while marking one group for recheck.
        self.quant(1)
        self.measure_filters(devices, reader, "120 devices/6 groups/3 tracking modes")

    def measure_filters(self, devices, reader, label):
        Device = self.env["pinout.device"].with_user(reader)
        rows = devices.with_user(reader).read(
            ["inventory_check_status", "inventory_check_pending"]
        )
        for field, value in (
            ("inventory_check_status", "match"),
            ("inventory_check_status", "exact_mismatch"),
            ("inventory_check_status", "group_shortage"),
            ("inventory_check_status", "not_checked"),
            ("inventory_check_pending", True),
            ("inventory_check_pending", False),
        ):
            self.env.flush_all()
            self.env.invalidate_all()
            queries = self.cr.sql_log_count
            started = perf_counter()
            with patch.object(
                type(self.config),
                "_calculate",
                side_effect=AssertionError("filter calculates"),
            ):
                found = Device.search([("id", "in", devices.ids), (field, "=", value)])
            elapsed = perf_counter() - started
            queries = self.cr.sql_log_count - queries
            self.assertEqual(
                set(found.ids),
                {row["id"] for row in rows if row[field] == value},
            )
            _logger.info(
                "Audit filter: %s; %s=%s; %.3fs; %d queries",
                label,
                field,
                value,
                elapsed,
                queries,
            )
            # A query per Device is already excessive on these shared sources;
            # keep the check independent of machine-dependent elapsed time.
            self.assertLess(queries, len(devices))
