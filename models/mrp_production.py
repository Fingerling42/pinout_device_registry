from typing import ClassVar

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
        return super().action_confirm()

    def pre_button_mark_done(self):
        self._validate_pinout_registry_devices()
        return super().pre_button_mark_done()
