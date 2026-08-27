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
        tracking=True,
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
        result = super().action_unbuild()
        if device:
            self._synchronize_pinout_device_after_unbuild(device)
        return result

    def _get_pinout_main_result_product(self, device):
        self.ensure_one()
        allowed_templates = device.device_type_id.allowed_product_template_ids
        produced_moves = self.produce_line_ids.filtered(
            lambda move: move.state == "done" and move.quantity > 0
        )
        candidate_moves = produced_moves.filtered(
            lambda move: (
                move.product_id != self.product_id
                and move.product_id.product_tmpl_id in allowed_templates
            )
        )
        candidates = candidate_moves.product_id
        if len(candidates) != 1:
            candidate_names = ", ".join(candidates.mapped("display_name")) or _("none")
            raise UserError(
                _(
                    "Unbuild Order %(unbuild)s must produce exactly one next "
                    "Product Form allowed for Device Type %(device_type)s. "
                    "Found: %(products)s. Check the BoM and Allowed Product Forms.",
                    unbuild=self.display_name,
                    device_type=device.device_type_id.display_name,
                    products=candidate_names,
                )
            )

        product = candidates
        produced_quantity = sum(
            move.product_uom._compute_quantity(move.quantity, product.uom_id)
            for move in candidate_moves.filtered(
                lambda move: move.product_id == product
            )
        )
        if (
            float_compare(
                produced_quantity,
                1.0,
                precision_rounding=product.uom_id.rounding,
            )
            != 0
        ):
            raise UserError(
                _(
                    "The next Product Form %(product)s must be produced in "
                    "quantity 1 to preserve one physical Device identity. "
                    "Produced quantity: %(quantity)s.",
                    product=product.display_name,
                    quantity=produced_quantity,
                )
            )
        return product

    def _synchronize_pinout_device_after_unbuild(self, device):
        self.ensure_one()
        previous_product = device.current_product_id
        previous_lot = device.final_lot_id
        next_product = self._get_pinout_main_result_product(device)
        device.with_context(tracking_disable=True).write(
            {
                "current_product_id": next_product.id,
                "state": "rework",
                "quality_status": "needs_test",
                "location_id": self.location_dest_id.id,
                "final_lot_id": False,
            }
        )

        if previous_lot:
            lot_note = _(
                "Current Final Lot / Serial %(lot)s was cleared and retained "
                "in Final Lot History.",
                lot=previous_lot.display_name,
            )
        else:
            lot_note = _("No active Final Lot / Serial needed to be cleared.")
        device.message_post(
            body=_(
                "Device Registry was synchronized after %(unbuild)s.<br>"
                "Current Product / Current Form: %(previous_product)s to "
                "%(next_product)s.<br>State: Rework.<br>Quality Status: "
                "Needs Test.<br>Odoo Location: %(location)s.<br>%(lot_note)s",
                unbuild=self._get_html_link(),
                previous_product=previous_product.display_name,
                next_product=next_product.display_name,
                location=self.location_dest_id.display_name,
                lot_note=lot_note,
            ),
            subtype_xmlid="mail.mt_note",
        )
        self.message_post(
            body=_(
                "Registry Device %(device)s was synchronized to Product Form "
                "%(product)s after this Unbuild Order.",
                device=device._get_html_link(),
                product=next_product.display_name,
            ),
            subtype_xmlid="mail.mt_note",
        )
