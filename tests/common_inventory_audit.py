from odoo.tests import TransactionCase


class InventoryAuditCompanyCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sources = []
        for model, names in (
            (
                "pinout.device",
                [
                    "device_uid",
                    "active",
                    "device_type_id",
                    "current_product_id",
                    "location_id",
                    "state",
                    "quality_status",
                    "final_lot_id",
                    "final_lot_history_ids",
                    "bundle_id",
                    "write_date",
                ],
            ),
            (
                "pinout.device.type",
                ["active", "allowed_product_template_ids", "write_date"],
            ),
            ("stock.lot", ["name", "product_id", "pinout_device_id", "write_date"]),
            ("pinout.device.bundle", ["state", "device_ids", "write_date"]),
        ):
            records = cls.env[model].with_context(active_test=False).search([])
            sources.append((records, names, records.read(names, load=None)))

        def check_existing_sources():
            # Run before TransactionCase closes its cursor and rolls back setup.
            for records, names, before in sources:
                records.invalidate_recordset(names)
                after = records.exists().read(names, load=None)
                if after != before:
                    raise AssertionError(
                        f"Audit fixture changed existing {records._name} records"
                    )

        cls.addClassCleanup(check_existing_sources)
        company = cls.env["res.company"].create({"name": cls.__name__})
        cls.env = cls.env(
            context={**cls.env.context, "allowed_company_ids": company.ids}
        )
