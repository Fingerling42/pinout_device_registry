from collections import defaultdict
from typing import ClassVar

from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class PinoutDeviceBundle(models.Model):
    _name = "pinout.device.bundle"
    _description = "Pinout Device Bundle"
    _inherit: ClassVar[list[str]] = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"

    name = fields.Char(required=True, index=True)
    bundle_type = fields.Selection(
        [
            ("dual", "Dual"),
            ("other", "Other"),
        ],
        default="dual",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("sold", "Sold"),
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
    notes = fields.Text()
    active = fields.Boolean(default=True)

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

    @api.depends("device_ids", "device_ids.final_lot_id")
    def _compute_bundle_sales_data(self):
        for bundle in self:
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

    def _sync_sold_state_from_pickings(self, pickings):
        for bundle in self:
            devices = bundle.device_ids
            if (
                bundle.state in {"sold", "cancelled"}
                or not devices
                or any(device.state != "sold" for device in devices)
                or any(not device.final_lot_id for device in devices)
            ):
                continue

            device_lots = devices.final_lot_id
            delivery = False
            for picking in pickings:
                delivered_lots = picking.move_line_ids.filtered(
                    lambda line: (
                        line.state == "done"
                        and line.quantity > 0
                        and line.location_dest_id.usage == "customer"
                    )
                ).lot_id
                if not (device_lots - delivered_lots):
                    delivery = picking
                    break
            if not delivery:
                continue

            bundle.with_context(tracking_disable=True).state = "sold"
            delivery_link = Markup(
                '<a href="#" data-oe-model="{}" data-oe-id="{}">{}</a>'
            ).format(delivery._name, delivery.id, delivery.display_name)
            bundle.message_post(
                body=_(
                    "Bundle state was automatically changed to Sold after all "
                    "attached devices were delivered in %(delivery)s.",
                    delivery=delivery_link,
                ),
                subtype_xmlid="mail.mt_note",
            )

    @api.constrains("bundle_type", "bundle_product_id")
    def _check_dual_bundle_product(self):
        for bundle in self:
            if bundle.bundle_type == "dual" and not bundle.bundle_product_id:
                raise ValidationError(
                    _("Dual bundles must have a Kit product variant.")
                )

    @api.constrains("bundle_product_id", "device_ids")
    def _check_devices_match_expected_components(self):
        self._validate_bundle_devices()

    def _validate_bundle_devices(self):
        blocked_states = self._get_blocked_device_states()
        for bundle in self:
            if not bundle.bundle_product_id:
                continue

            expected_quantities = bundle._get_expected_component_quantities()
            if not expected_quantities:
                raise ValidationError(
                    _(
                        "Bundle product %(product)s must have an active Kit BoM with component lines.",
                        product=bundle.bundle_product_id.display_name,
                    )
                )

            actual_quantities = defaultdict(float)
            for device in bundle.device_ids:
                if device.state in blocked_states:
                    raise ValidationError(
                        _(
                            "Device %(device)s cannot be added to a bundle because it is %(state)s.",
                            device=device.device_uid,
                            state=dict(device._fields["state"].selection).get(
                                device.state, device.state
                            ),
                        )
                    )
                if device.last_delivery_id:
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
