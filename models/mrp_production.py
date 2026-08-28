from typing import ClassVar

from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    _pinout_unavailable_device_states: ClassVar[frozenset[str]] = frozenset(
        {"reserved", "sold", "scrapped"}
    )

    pinout_device_ids = fields.Many2many(
        "pinout.device",
        "mrp_production_pinout_device_rel",
        "production_id",
        "device_id",
        string="Registry Devices",
        copy=False,
        tracking=True,
        help=(
            "Existing physical Devices represented by this Manufacturing Order. "
            "Leave empty to keep the standard Odoo manufacturing workflow."
        ),
    )
    pinout_device_count = fields.Integer(
        compute="_compute_pinout_device_count",
        string="Registry Device Count",
    )
    pinout_allowed_device_type_ids = fields.Many2many(
        "pinout.device.type",
        compute="_compute_pinout_registry_availability",
        string="Allowed Registry Device Types",
    )
    pinout_registry_available = fields.Boolean(
        compute="_compute_pinout_registry_availability",
        string="Registry Available",
    )
    pinout_source_product_id = fields.Many2one(
        "product.product",
        compute="_compute_pinout_source_product",
        string="Registry Source Product Form",
    )
    pinout_target_device_state = fields.Selection(
        related="product_id.product_tmpl_id.pinout_device_state_after_manufacturing",
        string="Device State after Manufacturing",
        readonly=True,
    )

    @api.depends("pinout_device_ids")
    def _compute_pinout_device_count(self):
        for production in self:
            production.pinout_device_count = len(production.pinout_device_ids)

    @api.depends("product_id")
    def _compute_pinout_registry_availability(self):
        for production in self:
            device_types = self.env["pinout.device.type"]
            if production.product_id:
                device_types = device_types.search(
                    [
                        (
                            "allowed_product_template_ids",
                            "in",
                            production.product_id.product_tmpl_id.ids,
                        )
                    ]
                )
            production.pinout_allowed_device_type_ids = device_types
            production.pinout_registry_available = bool(device_types)

    @api.depends(
        "pinout_device_ids.device_type_id",
        "move_raw_ids.product_id",
        "move_raw_ids.state",
    )
    def _compute_pinout_source_product(self):
        for production in self:
            device_types = production.pinout_device_ids.device_type_id
            source_products = self.env["product.product"]
            if len(device_types) == 1:
                source_products = production._get_pinout_source_products(device_types)
            production.pinout_source_product_id = (
                source_products if len(source_products) == 1 else False
            )

    def _get_pinout_source_moves(self, device_type):
        self.ensure_one()
        allowed_templates = device_type.allowed_product_template_ids
        return self.move_raw_ids.filtered(
            lambda move: (
                move.state != "cancel"
                and move.product_id.product_tmpl_id in allowed_templates
            )
        )

    def _get_pinout_source_products(self, device_type):
        self.ensure_one()
        return self._get_pinout_source_moves(device_type).product_id

    def _validate_pinout_registry_devices(self):
        for production in self.filtered("pinout_device_ids"):
            production._validate_pinout_registry_device_configuration()
        return True

    def _get_pinout_untracked_productions(self):
        return self.filtered(
            lambda production: (
                production.pinout_device_ids
                and production.product_id.tracking == "none"
            )
        )

    def _get_pinout_serial_tracked_productions(self):
        return self.filtered(
            lambda production: (
                production.pinout_device_ids
                and production.product_id.tracking == "serial"
                and production.state not in ("done", "cancel")
            )
        )

    def _validate_pinout_registry_device_configuration(self):
        self.ensure_one()
        devices = self.pinout_device_ids
        if not devices:
            return
        if not self.product_id or not self.bom_id:
            raise ValidationError(
                _(
                    "Select a Product and Bill of Materials before linking Registry "
                    "Devices to this Manufacturing Order."
                )
            )

        device_types = devices.device_type_id
        if len(device_types) != 1:
            raise ValidationError(
                _(
                    "A Registry-linked Manufacturing Order must contain Devices of "
                    "exactly one Device Type."
                )
            )
        device_type = device_types

        if self.product_id.product_tmpl_id not in (
            device_type.allowed_product_template_ids
        ):
            raise ValidationError(
                _(
                    "Manufactured Product %(product)s is not an Allowed Product Form "
                    "of Device Type %(device_type)s.",
                    product=self.product_id.display_name,
                    device_type=device_type.display_name,
                )
            )

        bundled_devices = devices.filtered("bundle_id")
        if bundled_devices:
            raise ValidationError(
                _(
                    "Devices %(devices)s belong to a Bundle. Use Cancel and Unpair "
                    "before linking them to a Manufacturing Order.",
                    devices=", ".join(bundled_devices.mapped("device_uid")),
                )
            )

        unavailable_devices = devices.filtered(
            lambda device: device.state in self._pinout_unavailable_device_states
        )
        if unavailable_devices:
            raise ValidationError(
                _(
                    "Reserved, Sold, or Scrapped Devices cannot be linked to a "
                    "Manufacturing Order: %(devices)s.",
                    devices=", ".join(unavailable_devices.mapped("device_uid")),
                )
            )

        missing_product_devices = devices.filtered(
            lambda device: not device.current_product_id
        )
        if missing_product_devices:
            raise ValidationError(
                _(
                    "Every linked Device must have Current Product / Current Form. "
                    "Missing for: %(devices)s.",
                    devices=", ".join(missing_product_devices.mapped("device_uid")),
                )
            )

        produced_quantity = self.product_uom_id._compute_quantity(
            self.product_qty,
            self.product_id.uom_id,
        )
        if (
            float_compare(
                produced_quantity,
                len(devices),
                precision_rounding=self.product_id.uom_id.rounding,
            )
            != 0
        ):
            raise ValidationError(
                _(
                    "Manufacturing Quantity %(quantity)s must match the number of "
                    "linked Registry Devices (%(device_count)s).",
                    quantity=produced_quantity,
                    device_count=len(devices),
                )
            )

        if self.product_id.tracking == "lot":
            raise ValidationError(
                _(
                    "Registry-linked tracked output must use Tracking by Unique "
                    "Serial Number. Tracking by Lots cannot preserve one Device UID "
                    "per physical unit."
                )
            )

        if self.product_id.tracking == "serial" and len(devices) != 1:
            raise ValidationError(
                _(
                    "A serial-tracked output requires a separate Manufacturing "
                    "Order with quantity 1 and exactly one Registry Device."
                )
            )

        source_products = self._get_pinout_source_products(device_type)
        if len(source_products) != 1:
            source_names = ", ".join(source_products.mapped("display_name")) or _(
                "none"
            )
            raise ValidationError(
                _(
                    "Manufacturing Order %(production)s must consume exactly one "
                    "source Product Form allowed for Device Type %(device_type)s. "
                    "Found: %(products)s. Check the BoM and Allowed Product Forms.",
                    production=self.display_name,
                    device_type=device_type.display_name,
                    products=source_names,
                )
            )
        source_product = source_products

        mismatched_devices = devices.filtered(
            lambda device: device.current_product_id != source_product
        )
        if mismatched_devices:
            raise ValidationError(
                _(
                    "Current Product / Current Form of Devices %(devices)s does not "
                    "match the source Product Form %(product)s required by this BoM.",
                    devices=", ".join(mismatched_devices.mapped("device_uid")),
                    product=source_product.display_name,
                )
            )

        source_moves = self._get_pinout_source_moves(device_type).filtered(
            lambda move: move.product_id == source_product
        )
        source_quantity = sum(
            move.product_uom._compute_quantity(
                move.product_uom_qty,
                source_product.uom_id,
            )
            for move in source_moves
        )
        if (
            float_compare(
                source_quantity,
                len(devices),
                precision_rounding=source_product.uom_id.rounding,
            )
            != 0
        ):
            raise ValidationError(
                _(
                    "The BoM must consume exactly one source Product Form per linked "
                    "Device. Required quantity: %(quantity)s; Registry Devices: "
                    "%(device_count)s.",
                    quantity=source_quantity,
                    device_count=len(devices),
                )
            )

        other_production = self.search(
            [
                ("id", "!=", self.id),
                ("state", "not in", ("done", "cancel")),
                ("pinout_device_ids", "in", devices.ids),
            ],
            limit=1,
        )
        if other_production:
            raise ValidationError(
                _(
                    "A linked Device is already assigned to active Manufacturing "
                    "Order %(production)s.",
                    production=other_production.display_name,
                )
            )

    def _prepare_pinout_registry_serials(self):
        lot_model = self.env["stock.lot"]
        for production in self._get_pinout_serial_tracked_productions():
            device = production.pinout_device_ids
            selected_lot = production.lot_producing_id
            if selected_lot and (
                selected_lot.name != device.device_uid
                or selected_lot.product_id != production.product_id
                or selected_lot.company_id != production.company_id
            ):
                raise ValidationError(
                    _(
                        "Lot / Serial Number for Registry Device %(device)s must be "
                        "named %(uid)s and belong to Product %(product)s in Company "
                        "%(company)s.",
                        device=device.display_name,
                        uid=device.device_uid,
                        product=production.product_id.display_name,
                        company=production.company_id.display_name,
                    )
                )

            expected_lot = lot_model.search(
                [
                    ("name", "=", device.device_uid),
                    ("product_id", "=", production.product_id.id),
                    ("company_id", "=", production.company_id.id),
                ],
                limit=1,
            )
            if selected_lot and expected_lot and selected_lot != expected_lot:
                raise ValidationError(
                    _(
                        "Registry serial %(serial)s already exists for Product "
                        "%(product)s. Use that serial instead of %(selected)s.",
                        serial=expected_lot.display_name,
                        product=production.product_id.display_name,
                        selected=selected_lot.display_name,
                    )
                )

            lot = selected_lot or expected_lot
            if lot and lot.pinout_device_id and lot.pinout_device_id != device:
                raise ValidationError(
                    _(
                        "Registry serial %(serial)s is already linked to another "
                        "Device %(device)s.",
                        serial=lot.display_name,
                        device=lot.pinout_device_id.display_name,
                    )
                )
            if lot and (
                lot.product_qty or production._is_finished_sn_already_produced(lot)
            ):
                raise ValidationError(
                    _(
                        "Registry serial %(serial)s for Product %(product)s has "
                        "already been produced and is not available for reuse. "
                        "Complete the corresponding Unbuild or choose the correct "
                        "Device before manufacturing it again.",
                        serial=lot.display_name,
                        product=production.product_id.display_name,
                    )
                )

            created = not lot
            if created:
                lot = lot_model.create(
                    {
                        "name": device.device_uid,
                        "product_id": production.product_id.id,
                        "company_id": production.company_id.id,
                    }
                )
            if production.lot_producing_id != lot:
                production.lot_producing_id = lot
                if created:
                    message = _(
                        "Registry Final Serial %(serial)s was created and prepared "
                        "for Device %(device)s."
                    )
                else:
                    message = _(
                        "Existing Registry Final Serial %(serial)s was prepared for "
                        "Device %(device)s."
                    )
                production.message_post(
                    body=Markup(message)
                    % {
                        "serial": lot._get_html_link(),
                        "device": device._get_html_link(),
                    },
                    subtype_xmlid="mail.mt_note",
                )

    def _validate_pinout_untracked_completion(self):
        for production in self._get_pinout_untracked_productions():
            devices = production.pinout_device_ids
            devices_with_lot = devices.filtered("final_lot_id")
            if devices_with_lot:
                raise ValidationError(
                    _(
                        "Untracked manufacturing cannot synchronize Devices with "
                        "an active Final Lot / Serial: %(devices)s. Clear the active "
                        "Final Lot before completing this Manufacturing Order.",
                        devices=", ".join(devices_with_lot.mapped("device_uid")),
                    )
                )

            quantity_to_produce = production.product_uom_id._compute_quantity(
                production.qty_producing,
                production.product_id.uom_id,
            )
            if (
                float_compare(
                    quantity_to_produce,
                    len(devices),
                    precision_rounding=production.product_id.uom_id.rounding,
                )
                != 0
            ):
                raise ValidationError(
                    _(
                        "Complete all linked Registry Devices together. Quantity to "
                        "Produce %(quantity)s must match the number of Registry "
                        "Devices (%(device_count)s). Partial production and "
                        "backorders are not supported for a linked batch.",
                        quantity=quantity_to_produce,
                        device_count=len(devices),
                    )
                )

            source_product = production.pinout_source_product_id
            source_moves = production._get_pinout_source_moves(
                devices.device_type_id
            ).filtered_domain([("product_id", "=", source_product.id)])
            consumed_quantity = sum(
                move.product_uom._compute_quantity(
                    move.quantity,
                    source_product.uom_id,
                )
                for move in source_moves
            )
            if (
                float_compare(
                    consumed_quantity,
                    len(devices),
                    precision_rounding=source_product.uom_id.rounding,
                )
                != 0
            ):
                raise ValidationError(
                    _(
                        "Actual consumed quantity of source Product Form %(product)s "
                        "must match the number of Registry Devices "
                        "(%(device_count)s). Consumed quantity: %(quantity)s.",
                        product=source_product.display_name,
                        device_count=len(devices),
                        quantity=consumed_quantity,
                    )
                )

    def _validate_pinout_completed_untracked_output(self):
        self.ensure_one()
        completed_moves = self.move_finished_ids.filtered(
            lambda move: move.state == "done" and move.product_id == self.product_id
        )
        completed_quantity = sum(
            move.product_uom._compute_quantity(
                move.quantity,
                self.product_id.uom_id,
            )
            for move in completed_moves
        )
        if (
            float_compare(
                completed_quantity,
                len(self.pinout_device_ids),
                precision_rounding=self.product_id.uom_id.rounding,
            )
            != 0
        ):
            raise ValidationError(
                _(
                    "Completed quantity of Product Form %(product)s must match the "
                    "number of Registry Devices (%(device_count)s). Completed "
                    "quantity: %(quantity)s.",
                    product=self.product_id.display_name,
                    device_count=len(self.pinout_device_ids),
                    quantity=completed_quantity,
                )
            )

    def _synchronize_pinout_devices_after_manufacturing(self):
        state_labels = dict(self.env["pinout.device"]._fields["state"].selection)
        quality_labels = dict(
            self.env["pinout.device"]._fields["quality_status"].selection
        )
        for production in self._get_pinout_untracked_productions():
            production._validate_pinout_completed_untracked_output()
            devices = production.pinout_device_ids
            snapshots = {
                device.id: {
                    "product": device.current_product_id,
                    "state": device.state,
                    "quality": device.quality_status,
                    "location": device.location_id,
                }
                for device in devices
            }
            values = {
                "current_product_id": production.product_id.id,
                "location_id": production.location_dest_id.id,
            }
            target_state = production.pinout_target_device_state
            if target_state != "no_change":
                values["state"] = target_state
            devices.with_context(tracking_disable=True).write(values)

            for device in devices:
                snapshot = snapshots[device.id]
                device_message = _(
                    "Device Registry was synchronized after %(production)s.<br>"
                    "Current Product / Current Form: %(previous_product)s to "
                    "%(next_product)s.<br>State: %(previous_state)s to "
                    "%(next_state)s.<br>Quality Status: %(quality)s "
                    "(unchanged).<br>Odoo Location: %(previous_location)s to "
                    "%(next_location)s."
                )
                device.message_post(
                    body=Markup(device_message)
                    % {
                        "production": production._get_html_link(),
                        "previous_product": escape(snapshot["product"].display_name),
                        "next_product": escape(production.product_id.display_name),
                        "previous_state": escape(
                            state_labels.get(snapshot["state"], snapshot["state"])
                        ),
                        "next_state": escape(
                            state_labels.get(device.state, device.state)
                        ),
                        "quality": escape(
                            quality_labels.get(snapshot["quality"], snapshot["quality"])
                        ),
                        "previous_location": escape(
                            snapshot["location"].display_name or _("none")
                        ),
                        "next_location": escape(
                            production.location_dest_id.display_name
                        ),
                    },
                    subtype_xmlid="mail.mt_note",
                )

            production_message = _(
                "Registry Devices %(devices)s were synchronized to Product Form "
                "%(product)s after this Manufacturing Order."
            )
            production.message_post(
                body=Markup(production_message)
                % {
                    "devices": Markup(", ").join(
                        device._get_html_link() for device in devices
                    ),
                    "product": escape(production.product_id.display_name),
                },
                subtype_xmlid="mail.mt_note",
            )

    @api.constrains(
        "pinout_device_ids",
        "product_id",
        "bom_id",
        "product_qty",
        "product_uom_id",
        "move_raw_ids",
    )
    def _check_pinout_registry_devices(self):
        self._validate_pinout_registry_devices()

    def action_confirm(self):
        self._validate_pinout_registry_devices()
        result = super().action_confirm()
        self._prepare_pinout_registry_serials()
        return result

    def pre_button_mark_done(self):
        self._validate_pinout_registry_devices()
        self._prepare_pinout_registry_serials()
        result = super().pre_button_mark_done()
        self._validate_pinout_untracked_completion()
        return result

    def button_mark_done(self):
        productions_to_sync = self._get_pinout_untracked_productions().filtered(
            lambda production: production.state != "done"
        )
        result = super().button_mark_done()
        productions_to_sync.filtered(
            lambda production: production.state == "done"
        )._synchronize_pinout_devices_after_manufacturing()
        return result
