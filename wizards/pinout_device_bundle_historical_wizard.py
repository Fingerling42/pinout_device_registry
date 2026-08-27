from odoo import api, fields, models


class PinoutDeviceBundleHistoricalWizard(models.TransientModel):
    _name = "pinout.device.bundle.historical.wizard"
    _description = "Reconstruct Historical Device Bundle"

    bundle_id = fields.Many2one(
        "pinout.device.bundle",
        required=True,
        readonly=True,
        default=lambda self: self._default_bundle_id(),
    )
    bundle_product_id = fields.Many2one(
        related="bundle_id.bundle_product_id",
        readonly=True,
    )
    expected_component_checklist = fields.Html(
        related="bundle_id.expected_component_checklist",
        readonly=True,
    )
    expected_product_ids = fields.Many2many(
        "product.product",
        compute="_compute_expected_product_ids",
    )
    eligible_device_ids = fields.Many2many(
        "pinout.device",
        compute="_compute_eligible_device_ids",
    )
    device_ids = fields.Many2many(
        "pinout.device",
        required=True,
    )

    @api.model
    def _default_bundle_id(self):
        if self.env.context.get("active_model") == "pinout.device.bundle":
            return self.env.context.get("active_id")
        return self.env.context.get("default_bundle_id")

    @api.depends("bundle_id", "bundle_id.bundle_product_id")
    def _compute_expected_product_ids(self):
        for wizard in self:
            wizard.expected_product_ids = self.env["product.product"].browse(
                [
                    product.id
                    for product in wizard.bundle_id._get_expected_component_quantities()
                ]
            )

    @api.depends("expected_product_ids")
    def _compute_eligible_device_ids(self):
        for wizard in self:
            domain = [
                ("bundle_id", "=", False),
                ("state", "in", ["sold", "returned"]),
            ]
            if wizard.expected_product_ids:
                domain.append(
                    ("current_product_id", "in", wizard.expected_product_ids.ids)
                )
            wizard.eligible_device_ids = self.env["pinout.device"].search(domain)

    def action_reconstruct(self):
        self.ensure_one()
        self.bundle_id._reconstruct_historical_bundle(self.device_ids)
        return {"type": "ir.actions.act_window_close"}
