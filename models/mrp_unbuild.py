from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class MrpUnbuild(models.Model):
    _inherit = "mrp.unbuild"

    pinout_registry_managed = fields.Boolean(
        compute="_compute_pinout_registry_managed",
        string="Registry Managed",
    )
    pinout_device_id = fields.Many2one(
        "pinout.device",
        string="Registry Device",
        copy=False,
        help=(
            "Physical Device Registry record being unbuilt. For tracked products "
            "it is detected from Lot / Serial Number. For untracked intermediate "
            "forms, select it manually."
        ),
    )

    @api.depends("product_id")
    def _compute_pinout_registry_managed(self):
        templates = self.product_id.product_tmpl_id
        managed_templates = (
            self.env["pinout.device.type"]
            .search([("allowed_product_template_ids", "in", templates.ids)])
            .allowed_product_template_ids
        )
        for unbuild in self:
            unbuild.pinout_registry_managed = (
                unbuild.product_id.product_tmpl_id in managed_templates
            )

    @api.onchange("mo_id")
    def _onchange_mo_id(self):
        result = super()._onchange_mo_id()
        self._onchange_pinout_device_source()
        return result

    @api.onchange("product_id")
    def _onchange_product_id(self):
        result = super()._onchange_product_id()
        self._onchange_pinout_device_source()
        return result

    @api.onchange("lot_id")
    def _onchange_pinout_lot_id(self):
        self._onchange_pinout_device_source()

    def _onchange_pinout_device_source(self):
        for unbuild in self:
            if unbuild.product_id.tracking != "none":
                unbuild.pinout_device_id = (
                    unbuild.lot_id.pinout_device_id
                    if unbuild.lot_id.product_id == unbuild.product_id
                    else False
                )
            elif (
                unbuild.pinout_device_id
                and unbuild.pinout_device_id.current_product_id != unbuild.product_id
            ):
                unbuild.pinout_device_id = False

    def _validate_pinout_registry_unbuild(self):
        self.ensure_one()
        lot_device = (
            self.lot_id.pinout_device_id
            if self.product_id.tracking != "none"
            else self.env["pinout.device"]
        )
        registry_managed = bool(
            self.pinout_registry_managed or self.pinout_device_id or lot_device
        )
        if not registry_managed:
            return self.env["pinout.device"]

        quantity = self.product_uom_id._compute_quantity(
            self.product_qty,
            self.product_id.uom_id,
        )
        if (
            float_compare(
                quantity,
                1.0,
                precision_rounding=self.product_id.uom_id.rounding,
            )
            != 0
        ):
            raise UserError(
                _(
                    "Registry-managed Unbuild Orders must process exactly one "
                    "physical Device."
                )
            )

        if self.product_id.tracking != "none":
            if not self.lot_id:
                raise UserError(
                    _(
                        "Select the current Lot / Serial Number before unbuilding "
                        "this Registry-managed product."
                    )
                )
            if not lot_device:
                raise UserError(
                    _(
                        "Lot / Serial Number %(lot)s is not linked to a Registry "
                        "Device. Link it in Device Registry before unbuilding.",
                        lot=self.lot_id.display_name,
                    )
                )
            if self.pinout_device_id and self.pinout_device_id != lot_device:
                raise UserError(
                    _(
                        "Registry Device does not match the Device linked to Lot / "
                        "Serial Number %(lot)s.",
                        lot=self.lot_id.display_name,
                    )
                )
            device = lot_device
            if device.final_lot_id != self.lot_id:
                raise UserError(
                    _(
                        "Lot / Serial Number %(lot)s is historical and is not the "
                        "current Final Lot / Serial of Device %(device)s.",
                        lot=self.lot_id.display_name,
                        device=device.device_uid,
                    )
                )
        else:
            device = self.pinout_device_id
            if not device:
                raise UserError(
                    _(
                        "Select the Registry Device represented by this untracked "
                        "intermediate product before unbuilding."
                    )
                )

        if device.current_product_id != self.product_id:
            raise UserError(
                _(
                    "Unbuild Product %(product)s does not match Current Product / "
                    "Current Form %(current_product)s of Device %(device)s.",
                    product=self.product_id.display_name,
                    current_product=device.current_product_id.display_name,
                    device=device.device_uid,
                )
            )
        if device.bundle_id:
            raise UserError(
                _(
                    "Device %(device)s belongs to Bundle %(bundle)s. Use Cancel "
                    "and Unpair on the Bundle before unbuilding the Device.",
                    device=device.device_uid,
                    bundle=device.bundle_id.display_name,
                )
            )
        return device

    def action_unbuild(self):
        self.ensure_one()
        device = self._validate_pinout_registry_unbuild()
        if device and self.pinout_device_id != device:
            self.pinout_device_id = device
        return super().action_unbuild()
