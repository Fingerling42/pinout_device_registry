from collections import defaultdict
from typing import ClassVar

from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools import float_compare


class PinoutDeviceBundle(models.Model):
    _name = "pinout.device.bundle"
    _description = "Pinout Device Bundle"
    _inherit: ClassVar[list[str]] = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"
    _state_transition_context: ClassVar[str] = "pinout_bundle_state_transition"
    _pairing_update_context: ClassVar[str] = "pinout_bundle_pairing_update"
    _locked_pairing_fields: ClassVar[frozenset[str]] = frozenset(
        {"bundle_type_id", "bundle_product_id", "device_ids"}
    )

    name = fields.Char(
        string="Bundle ID",
        default=lambda self: _("New"),
        required=True,
        copy=False,
        index=True,
        tracking=True,
        help="Leave New to generate an ID from the Bundle Type Code, or enter a custom unique ID.",
    )
    bundle_type_id = fields.Many2one(
        "pinout.device.bundle.type",
        string="Bundle Type",
        default=lambda self: self.env.ref(
            "pinout_device_registry.bundle_type_dual",
            raise_if_not_found=False,
        ),
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    allowed_product_template_ids = fields.Many2many(
        "product.template",
        related="bundle_type_id.allowed_product_template_ids",
        readonly=True,
    )
    bundle_type_requires_kit_bom = fields.Boolean(
        related="bundle_type_id.requires_kit_bom",
        readonly=True,
    )
    bundle_type_allows_any_product_form = fields.Boolean(
        related="bundle_type_id.allow_any_product_form",
        readonly=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready_for_sale", "Ready for Sale"),
            ("reserved", "Reserved"),
            ("sold", "Sold"),
            ("partially_returned", "Partially Returned"),
            ("returned", "Returned"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    bundle_product_id = fields.Many2one(
        "product.product",
        string="Bundle Product / Kit Variant",
        tracking=True,
        help="Odoo product variant used as the physical kit/bundle. Its Kit BoM defines which device products can be attached.",
    )
    expected_component_checklist = fields.Html(
        compute="_compute_expected_component_checklist",
        string="Expected Components",
        readonly=True,
        sanitize=True,
    )
    device_ids = fields.One2many(
        "pinout.device",
        "bundle_id",
    )
    device_count = fields.Integer(
        compute="_compute_device_count",
        readonly=True,
    )
    customer_id = fields.Many2one(
        "res.partner",
        compute="_compute_bundle_sales_data",
        readonly=True,
        store=False,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        compute="_compute_bundle_sales_data",
        readonly=True,
        store=False,
    )
    delivery_id = fields.Many2one(
        "stock.picking",
        compute="_compute_bundle_sales_data",
        readonly=True,
        store=False,
    )
    reservation_delivery_id = fields.Many2one(
        "stock.picking",
        string="Reserved Delivery",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints: ClassVar[list[tuple[str, str, str]]] = [
        (
            "name_unique",
            "unique(name)",
            "The Bundle ID must be globally unique.",
        ),
    ]

    @api.model
    def _check_names_available(self, names, exclude_ids=None):
        if len(names) != len(set(names)):
            raise ValidationError(_("The Bundle ID must be globally unique."))

        domain = [("name", "in", names)]
        if exclude_ids:
            domain.append(("id", "not in", exclude_ids))
        if self.with_context(active_test=False).search(domain, limit=1):
            raise ValidationError(_("The Bundle ID must be globally unique."))

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        default_bundle_type_id = self.default_get(["bundle_type_id"]).get(
            "bundle_type_id"
        )
        for vals in vals_list:
            prepared_vals = dict(vals)
            if prepared_vals.get("state", "draft") != "draft":
                raise ValidationError(_("A new bundle must start in Draft state."))
            name = (prepared_vals.get("name") or "").strip()
            if not name or name in {"/", _("New")}:
                bundle_type_id = (
                    prepared_vals.get("bundle_type_id") or default_bundle_type_id
                )
                bundle_type = self.env["pinout.device.bundle.type"].browse(
                    bundle_type_id
                )
                if not bundle_type.exists():
                    raise ValidationError(
                        _("Select a Bundle Type before generating the Bundle ID.")
                    )
                sequence = self.env["ir.sequence"].next_by_code("pinout.device.bundle")
                if not sequence:
                    raise ValidationError(
                        _("The sequence for automatic Bundle IDs is not configured.")
                    )
                name = f"{bundle_type.code}-{sequence}"
            prepared_vals["name"] = name
            prepared_vals_list.append(prepared_vals)
        self._check_names_available([vals["name"] for vals in prepared_vals_list])
        return super().create(prepared_vals_list)

    def write(self, vals):
        self._check_write_allowed(vals)
        if "name" not in vals:
            return super().write(vals)

        prepared_vals = dict(vals)
        new_name = (prepared_vals["name"] or "").strip()
        if not new_name or new_name in {"/", _("New")}:
            raise ValidationError(_("Bundle ID cannot be empty after creation."))
        for bundle in self:
            if new_name != bundle.name and bundle.state != "draft":
                raise ValidationError(
                    _("Bundle ID can only be changed while the bundle is in Draft.")
                )
        if any(new_name != bundle.name for bundle in self):
            if len(self) > 1:
                raise ValidationError(_("The Bundle ID must be globally unique."))
            self._check_names_available([new_name], exclude_ids=self.ids)
        prepared_vals["name"] = new_name
        return super().write(prepared_vals)

    def _check_write_allowed(self, vals):
        if "state" in vals and not self.env.context.get(self._state_transition_context):
            changed_bundles = self.filtered(
                lambda bundle: bundle.state != vals["state"]
            )
            if changed_bundles:
                raise ValidationError(
                    _("Use the bundle lifecycle actions to change its state.")
                )

        if self.env.context.get(self._pairing_update_context):
            return
        changed_pairing_fields = self._locked_pairing_fields.intersection(vals)
        if not changed_pairing_fields:
            return
        for bundle in self.filtered(lambda item: item.state != "draft"):
            changed = "device_ids" in changed_pairing_fields
            for field_name in changed_pairing_fields - {"device_ids"}:
                value = vals[field_name]
                value_id = value.id if isinstance(value, models.BaseModel) else value
                if bundle[field_name].id != value_id:
                    changed = True
                    break
            if changed:
                raise ValidationError(
                    _(
                        "Bundle Type, Bundle Product and pairing can only be changed "
                        "while the bundle is in Draft."
                    )
                )

    @api.depends("device_ids")
    def _compute_device_count(self):
        for bundle in self:
            bundle.device_count = len(bundle.device_ids)

    @api.depends(
        "bundle_product_id",
        "device_ids",
        "device_ids.current_product_id",
        "device_ids.device_uid",
    )
    def _compute_expected_component_checklist(self):
        for bundle in self:
            bundle.expected_component_checklist = (
                bundle._format_expected_component_checklist()
            )

    @api.depends(
        "device_ids",
        "device_ids.final_lot_id",
        "reservation_delivery_id",
        "reservation_delivery_id.partner_id",
        "reservation_delivery_id.sale_id",
    )
    def _compute_bundle_sales_data(self):
        for bundle in self:
            if bundle.state == "reserved" and bundle.reservation_delivery_id:
                picking = bundle.reservation_delivery_id
                bundle.customer_id = picking.partner_id
                bundle.sale_order_id = picking.sale_id
                bundle.delivery_id = picking
                continue
            devices = bundle.device_ids
            bundle.customer_id = bundle._get_shared_device_value(
                devices, "last_customer_id"
            )
            bundle.sale_order_id = bundle._get_shared_device_value(
                devices, "last_sale_order_id"
            )
            bundle.delivery_id = bundle._get_shared_device_value(
                devices, "last_delivery_id"
            )

    def _get_shared_device_value(self, devices, field_name):
        if not devices:
            return False
        values = [device[field_name] for device in devices]
        if not all(values):
            return False
        first_value = values[0]
        if all(value == first_value for value in values):
            return first_value
        return False

    def _sync_sold_state_from_move_lines(self, delivered_move_lines):
        pickings = delivered_move_lines.picking_id
        for bundle in self:
            devices = bundle.device_ids
            if (
                bundle.state in {"sold", "partially_returned", "returned", "cancelled"}
                or not devices
                or any(device.state != "sold" for device in devices)
                or any(not device.final_lot_id for device in devices)
                or not bundle._is_composition_complete()
            ):
                continue

            device_lots = devices.final_lot_id
            delivery = False
            for picking in pickings:
                delivered_lots = delivered_move_lines.filtered_domain(
                    [("picking_id", "=", picking.id)]
                ).lot_id
                if not (device_lots - delivered_lots):
                    delivery = picking
                    break
            if not delivery:
                continue

            bundle._set_lifecycle_state(
                "sold",
                _(
                    "Bundle state was automatically changed to Sold after all "
                    "attached devices were delivered in %(document)s."
                ),
                document=delivery,
                extra_values={"reservation_delivery_id": False},
            )

    def _sync_return_state_from_devices(self, return_picking):
        for bundle in self.filtered(
            lambda item: item.state in {"sold", "partially_returned", "returned"}
        ):
            devices = bundle.device_ids
            if not devices:
                continue
            returned_count = len(
                devices.filtered(lambda device: device.state == "returned")
            )
            if returned_count == len(devices):
                new_state = "returned"
            elif returned_count:
                new_state = "partially_returned"
            else:
                continue
            if bundle.state == new_state:
                continue
            bundle._set_lifecycle_state(
                new_state,
                _(
                    "Bundle state was automatically changed to %(state)s after "
                    "the customer return in %(document)s."
                ),
                document=return_picking,
            )

    def _sync_reservation_state(self):
        for bundle in self.filtered(
            lambda item: item.state in {"ready_for_sale", "reserved"}
        ):
            delivery = bundle._get_active_reservation_delivery()
            if delivery and bundle.state == "ready_for_sale":
                if any(
                    device.state != "ready_for_sale" for device in bundle.device_ids
                ):
                    continue
                bundle.device_ids.with_context(tracking_disable=True).write(
                    {"state": "reserved"}
                )
                bundle._set_lifecycle_state(
                    "reserved",
                    _(
                        "Bundle was automatically reserved after all Final Lots "
                        "were assigned to %(document)s."
                    ),
                    document=delivery,
                    extra_values={"reservation_delivery_id": delivery.id},
                )
            elif delivery and bundle.state == "reserved":
                if bundle.reservation_delivery_id != delivery:
                    bundle.with_context(
                        **{self._state_transition_context: True},
                        tracking_disable=True,
                    ).write({"reservation_delivery_id": delivery.id})
            elif not delivery and bundle.state == "reserved":
                bundle.device_ids.filtered(
                    lambda device: device.state == "reserved"
                ).with_context(tracking_disable=True).write({"state": "ready_for_sale"})
                bundle._set_lifecycle_state(
                    "ready_for_sale",
                    _(
                        "Bundle was automatically returned to Ready for Sale "
                        "because its serial assignment was removed or the delivery "
                        "was cancelled."
                    ),
                    extra_values={"reservation_delivery_id": False},
                )

    def _get_active_reservation_delivery(self):
        self.ensure_one()
        devices = self.device_ids
        if not devices or any(not device.final_lot_id for device in devices):
            return self.env["stock.picking"]

        lot_ids = set(devices.final_lot_id.ids)
        move_lines = self.env["stock.move.line"].search(
            [
                ("lot_id", "in", list(lot_ids)),
                ("quantity", ">", 0),
                ("state", "not in", ["done", "cancel"]),
                ("picking_id", "!=", False),
                ("picking_id.picking_type_code", "=", "outgoing"),
                ("picking_id.location_dest_id.usage", "=", "customer"),
                ("picking_id.sale_id", "!=", False),
                ("picking_id.partner_id", "!=", False),
            ],
            order="picking_id, id",
        )
        for picking in move_lines.picking_id:
            picking_lot_ids = set(
                move_lines.filtered_domain([("picking_id", "=", picking.id)]).lot_id.ids
            )
            if lot_ids.issubset(picking_lot_ids):
                return picking
        return self.env["stock.picking"]

    def _set_lifecycle_state(
        self,
        new_state,
        message,
        *,
        document=None,
        extra_values=None,
        message_values=None,
    ):
        state_labels = dict(self._fields["state"].selection)
        for bundle in self:
            values = dict(extra_values or {})
            values["state"] = new_state
            bundle.with_context(
                **{self._state_transition_context: True},
                tracking_disable=True,
            ).write(values)
            format_values = dict(message_values or {})
            format_values.update(
                {
                    "state": state_labels.get(new_state, new_state),
                }
            )
            if document:
                format_values["document"] = bundle._format_document_link(document)
            bundle.message_post(
                body=message % format_values,
                subtype_xmlid="mail.mt_note",
            )

    def _format_document_link(self, document):
        return Markup('<a href="#" data-oe-model="{}" data-oe-id="{}">{}</a>').format(
            document._name, document.id, document.display_name
        )

    def action_mark_ready_for_sale(self):
        for bundle in self:
            if bundle.state != "draft":
                raise ValidationError(
                    _("Only a Draft bundle can be marked Ready for Sale.")
                )
            bundle._check_ready_for_sale_requirements()
            bundle._set_lifecycle_state(
                "ready_for_sale",
                _(
                    "Bundle was marked Ready for Sale. Its physical pairing is "
                    "now fixed until Reset to Draft is used."
                ),
            )
        self._sync_reservation_state()
        return True

    def action_reset_to_draft(self):
        for bundle in self:
            if bundle.state != "ready_for_sale":
                raise ValidationError(
                    _("Only a Ready for Sale bundle can be reset to Draft.")
                )
            bundle._set_lifecycle_state(
                "draft",
                _(
                    "Bundle was reset to Draft. Remove its existing physical "
                    "labels before changing the pairing."
                ),
                extra_values={"reservation_delivery_id": False},
            )
        return True

    def action_cancel_and_unpair(self):
        allowed_states = {"draft", "ready_for_sale", "returned"}
        for bundle in self:
            if bundle.state not in allowed_states:
                raise ValidationError(
                    _(
                        "Cancel and Unpair is only available for Draft, Ready for "
                        "Sale or fully Returned bundles. Remove any active serial "
                        "assignment in the delivery first."
                    )
                )
            device_uids = bundle.device_ids.sorted("device_uid").mapped("device_uid")
            previous_devices = Markup("<br>").join(escape(uid) for uid in device_uids)
            if not previous_devices:
                previous_devices = Markup(
                    '<span class="text-muted">%s</span>'
                ) % escape(_("None"))
            bundle.device_ids.with_context(
                **{self._pairing_update_context: True}
            ).write({"bundle_id": False})
            bundle._set_lifecycle_state(
                "cancelled",
                _(
                    "Bundle was cancelled and unpaired. Previously attached "
                    "Device UIDs:<br>%(devices)s"
                ),
                extra_values={"reservation_delivery_id": False},
                message_values={"devices": previous_devices},
            )
        return True

    def action_prepare_for_resale(self):
        for bundle in self:
            if bundle.state != "returned":
                raise ValidationError(
                    _("Only a fully Returned bundle can be prepared for resale.")
                )
            bundle._check_ready_for_sale_requirements()
            bundle._set_lifecycle_state(
                "ready_for_sale",
                _(
                    "Returned bundle was prepared for resale with the same "
                    "physical pairing."
                ),
            )
        self._sync_reservation_state()
        return True

    def _check_ready_for_sale_requirements(self):
        for bundle in self:
            bundle._validate_bundle_devices()
            if not bundle.device_ids:
                raise ValidationError(
                    _(
                        "Attach at least one Device before marking the bundle Ready for Sale."
                    )
                )
            if not bundle._is_composition_complete():
                raise ValidationError(
                    _(
                        "Expected Components must be complete before the bundle "
                        "can be marked Ready for Sale."
                    )
                )
            missing_lot = bundle.device_ids.filtered(
                lambda device: not device.final_lot_id
            )
            if missing_lot:
                raise ValidationError(
                    _(
                        "Every Device must have a Final Lot / Serial before the "
                        "bundle can be marked Ready for Sale. Missing: %(devices)s",
                        devices=", ".join(missing_lot.mapped("device_uid")),
                    )
                )
            invalid_quality = bundle.device_ids.filtered(
                lambda device: device.quality_status != "ok"
            )
            if invalid_quality:
                raise ValidationError(
                    _(
                        "Every Device must have Quality Status OK before the bundle "
                        "can be marked Ready for Sale. Check: %(devices)s",
                        devices=", ".join(invalid_quality.mapped("device_uid")),
                    )
                )
            invalid_state = bundle.device_ids.filtered(
                lambda device: device.state != "ready_for_sale"
            )
            if invalid_state:
                raise ValidationError(
                    _(
                        "Every Device must be Ready for Sale before the bundle can "
                        "be marked Ready for Sale. Check: %(devices)s",
                        devices=", ".join(invalid_state.mapped("device_uid")),
                    )
                )

    def _is_composition_complete(self):
        self.ensure_one()
        if not self.device_ids:
            return False
        expected_quantities = self._get_expected_component_quantities()
        if not expected_quantities:
            return not self.bundle_type_id.requires_kit_bom
        rows = self._get_component_checklist_rows()
        return bool(rows) and all(row["status"] == "complete" for row in rows)

    @api.constrains("bundle_type_id", "bundle_product_id")
    def _check_bundle_configuration(self):
        for bundle in self:
            if bundle.bundle_type_id.requires_kit_bom and not bundle.bundle_product_id:
                raise ValidationError(
                    _(
                        "Bundle type %(bundle_type)s requires a Bundle Product / Kit Variant.",
                        bundle_type=bundle.bundle_type_id.display_name,
                    )
                )
            allowed_templates = bundle.allowed_product_template_ids
            if (
                bundle.bundle_product_id
                and not bundle.bundle_type_id.allow_any_product_form
                and bundle.bundle_product_id.product_tmpl_id not in allowed_templates
            ):
                raise ValidationError(
                    _(
                        "Bundle Product / Kit Variant is not allowed for Bundle Type %(bundle_type)s.",
                        bundle_type=bundle.bundle_type_id.display_name,
                    )
                )
            if (
                bundle.bundle_type_id.requires_kit_bom
                and bundle.bundle_product_id
                and not bundle._get_expected_component_quantities()
            ):
                raise ValidationError(
                    _(
                        "Bundle product %(product)s must have an active Kit BoM with component lines.",
                        product=bundle.bundle_product_id.display_name,
                    )
                )

    @api.constrains("bundle_product_id", "device_ids")
    def _check_devices_match_expected_components(self):
        self._validate_bundle_devices()

    def _validate_bundle_devices(self):
        self._check_bundle_configuration()
        blocked_states = self._get_blocked_device_states()
        for bundle in self:
            if not bundle.bundle_product_id:
                continue

            expected_quantities = bundle._get_expected_component_quantities()
            if not expected_quantities:
                if bundle.bundle_type_id.requires_kit_bom:
                    raise ValidationError(
                        _(
                            "Bundle product %(product)s must have an active Kit BoM with component lines.",
                            product=bundle.bundle_product_id.display_name,
                        )
                    )
                continue

            actual_quantities = defaultdict(float)
            for device in bundle.device_ids:
                if bundle.state == "draft" and device.state in blocked_states:
                    raise ValidationError(
                        _(
                            "Device %(device)s cannot be added to a bundle because it is %(state)s.",
                            device=device.device_uid,
                            state=dict(device._fields["state"].selection).get(
                                device.state, device.state
                            ),
                        )
                    )
                if bundle.state == "draft" and device.last_delivery_id:
                    raise ValidationError(
                        _(
                            "Device %(device)s cannot be added to a bundle because it was already delivered in %(delivery)s.",
                            device=device.device_uid,
                            delivery=device.last_delivery_id.display_name,
                        )
                    )
                if not device.current_product_id:
                    raise ValidationError(
                        _(
                            "Device %(device)s must have Current Product / Current Form before it can be added to a bundle.",
                            device=device.device_uid,
                        )
                    )
                if device.current_product_id not in expected_quantities:
                    raise ValidationError(
                        _(
                            "Device %(device)s has product %(product)s, which is not expected by the Kit BoM of %(bundle)s.",
                            device=device.device_uid,
                            product=device.current_product_id.display_name,
                            bundle=bundle.bundle_product_id.display_name,
                        )
                    )
                actual_quantities[device.current_product_id] += 1.0

            for product, actual_quantity in actual_quantities.items():
                expected_quantity = expected_quantities[product]
                if (
                    float_compare(
                        actual_quantity,
                        expected_quantity,
                        precision_rounding=product.uom_id.rounding,
                    )
                    > 0
                ):
                    raise ValidationError(
                        _(
                            "Bundle %(bundle)s can contain at most %(qty)s x %(product)s according to its Kit BoM.",
                            bundle=bundle.name,
                            qty=self._format_quantity(expected_quantity),
                            product=product.display_name,
                        )
                    )

    def _get_blocked_device_states(self):
        return {"sold", "returned", "scrapped"}

    @api.onchange("bundle_type_id")
    def _onchange_bundle_type_id(self):
        result = {"domain": {"bundle_product_id": self._get_bundle_product_domain()}}
        allowed_templates = self.allowed_product_template_ids
        if (
            self.bundle_product_id
            and not self.bundle_type_id.allow_any_product_form
            and self.bundle_product_id.product_tmpl_id not in allowed_templates
        ):
            result["warning"] = {
                "title": _("Bundle Product Not Allowed"),
                "message": _(
                    "Bundle Product / Kit Variant remains selected. Choose a product "
                    "allowed for this Bundle Type or clear it manually before saving."
                ),
            }
        return result

    def _get_bundle_product_domain(self):
        if self.bundle_type_id.allow_any_product_form:
            return []
        return [("product_tmpl_id", "in", self.allowed_product_template_ids.ids)]

    def _get_kit_bom(self):
        self.ensure_one()
        if not self.bundle_product_id:
            return self.env["mrp.bom"]
        bom_by_product = self.env["mrp.bom"]._bom_find(
            self.bundle_product_id,
            bom_type="phantom",
        )
        return bom_by_product.get(self.bundle_product_id, self.env["mrp.bom"])

    def _get_expected_component_quantities(self):
        self.ensure_one()
        bom = self._get_kit_bom()
        if not bom:
            return {}

        quantities = defaultdict(float)
        for line in bom.bom_line_ids:
            if line._skip_bom_line(self.bundle_product_id):
                continue
            line_quantity = line.product_uom_id._compute_quantity(
                line.product_qty / bom.product_qty,
                line.product_id.uom_id,
                round=False,
            )
            quantities[line.product_id] += line_quantity
        return quantities

    def _get_component_checklist_rows(self):
        self.ensure_one()
        expected_quantities = self._get_expected_component_quantities()
        attached_devices_by_product = defaultdict(lambda: self.env["pinout.device"])
        for device in self.device_ids.filtered("current_product_id"):
            attached_devices_by_product[device.current_product_id] |= device

        products = sorted(
            set(expected_quantities) | set(attached_devices_by_product),
            key=lambda product: product.display_name,
        )
        rows = []
        for product in products:
            expected_quantity = expected_quantities.get(product, 0.0)
            attached_devices = attached_devices_by_product[product]
            attached_quantity = float(len(attached_devices))
            quantity_comparison = float_compare(
                attached_quantity,
                expected_quantity,
                precision_rounding=product.uom_id.rounding,
            )
            if quantity_comparison < 0:
                status = "missing"
            elif quantity_comparison > 0:
                status = "excess"
            else:
                status = "complete"
            rows.append(
                {
                    "product": product,
                    "expected_quantity": expected_quantity,
                    "attached_quantity": attached_quantity,
                    "attached_devices": attached_devices,
                    "status": status,
                }
            )
        return rows

    def _format_expected_component_checklist(self):
        self.ensure_one()
        if not self.bundle_product_id:
            return False

        rows = self._get_component_checklist_rows()
        if not rows:
            warning = Markup(
                '<div class="alert alert-warning mb-0" role="alert">%s</div>'
            )
            return warning % escape(
                _("No active Kit BoM found for this product variant.")
            )

        table_rows = Markup("").join(
            self._format_component_checklist_row(row) for row in rows
        )
        return Markup(
            '<div class="table-responsive">'
            '<table class="table table-sm table-hover align-middle mb-0">'
            "<thead><tr>"
            '<th scope="col">%s</th>'
            '<th scope="col" class="text-end">%s</th>'
            '<th scope="col" class="text-end">%s</th>'
            '<th scope="col">%s</th>'
            '<th scope="col">%s</th>'
            "</tr></thead>"
            "<tbody>%s</tbody>"
            "</table>"
            "</div>"
        ) % (
            escape(_("Expected Product")),
            escape(_("Expected Quantity")),
            escape(_("Attached Quantity")),
            escape(_("Status")),
            escape(_("Attached Device UID")),
            table_rows,
        )

    def _format_component_checklist_row(self, row):
        status_labels = {
            "missing": (_("Missing"), "text-bg-warning"),
            "complete": (_("Complete"), "text-bg-success"),
            "excess": (_("Excess"), "text-bg-danger"),
        }
        status_label, status_class = status_labels[row["status"]]
        status_badge = Markup('<span class="badge %s">%s</span>') % (
            status_class,
            escape(status_label),
        )
        device_uids = Markup("<br>").join(
            escape(device.device_uid)
            for device in row["attached_devices"].sorted("device_uid")
        )
        if not device_uids:
            device_uids = Markup('<span class="text-muted">%s</span>') % escape(
                _("None")
            )

        return Markup(
            "<tr>"
            "<td>%s</td>"
            '<td class="text-end">%s</td>'
            '<td class="text-end">%s</td>'
            "<td>%s</td>"
            "<td>%s</td>"
            "</tr>"
        ) % (
            escape(row["product"].display_name),
            escape(self._format_quantity(row["expected_quantity"])),
            escape(self._format_quantity(row["attached_quantity"])),
            status_badge,
            device_uids,
        )

    def _format_quantity(self, quantity):
        return f"{quantity}".rstrip("0").rstrip(".")

    def action_open_devices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Devices"),
            "res_model": "pinout.device",
            "view_mode": "tree,form",
            "domain": [("bundle_id", "=", self.id)],
            "context": {"default_bundle_id": self.id},
            "target": "current",
        }

    def action_manage_devices(self):
        self.ensure_one()
        if self.state != "draft":
            raise ValidationError(
                _("Device pairing can only be managed while the bundle is in Draft.")
            )

        candidate_domain = [
            ("bundle_id", "=", False),
            ("state", "not in", list(self._get_blocked_device_states())),
        ]
        expected_products = self.env["product.product"].browse(
            [product.id for product in self._get_expected_component_quantities()]
        )
        if expected_products:
            candidate_domain.append(("current_product_id", "in", expected_products.ids))
        domain = expression.OR(
            [
                [("bundle_id", "=", self.id)],
                candidate_domain,
            ]
        )
        action = self.env["ir.actions.actions"]._for_xml_id(
            "pinout_device_registry.pinout_device_action"
        )
        action.update(
            {
                "name": _("Manage Devices for %(bundle)s", bundle=self.display_name),
                "domain": domain,
                "context": {
                    "active_model": "pinout.device",
                    "default_bundle_id": self.id,
                    "create": False,
                },
                "target": "current",
            }
        )
        return action
