import re
import unicodedata
from typing import ClassVar

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PinoutDeviceBundleType(models.Model):
    _name = "pinout.device.bundle.type"
    _description = "Pinout Device Bundle Type"
    _order = "name, id"

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        index=True,
        help="Unique prefix used for automatically generated Bundle IDs.",
    )
    allowed_product_template_ids = fields.Many2many(
        "product.template",
        string="Allowed Bundle Product Forms",
        help="Product templates that can represent bundles of this type.",
    )
    allow_any_product_form = fields.Boolean(
        string="Allow Any Product Form",
        help="Allow any product template to represent this bundle type. When disabled, only Product Forms listed below are allowed.",
    )
    requires_kit_bom = fields.Boolean(
        string="Requires Kit BoM",
        default=True,
        help="Require a Bundle Product / Kit Variant with an active Kit BoM and validate attached devices against its components.",
    )
    active = fields.Boolean(default=True)
    bundle_ids = fields.One2many(
        "pinout.device.bundle",
        "bundle_type_id",
        readonly=True,
    )
    bundle_count = fields.Integer(
        compute="_compute_bundle_count",
        readonly=True,
    )

    _sql_constraints: ClassVar[list[tuple[str, str, str]]] = [
        (
            "code_unique",
            "unique(code)",
            "The bundle type code must be unique.",
        ),
    ]

    @api.depends("bundle_ids")
    def _compute_bundle_count(self):
        bundle_model = self.env["pinout.device.bundle"].with_context(active_test=False)
        for bundle_type in self:
            origin_id = bundle_type._origin.id
            bundle_type.bundle_count = (
                bundle_model.search_count([("bundle_type_id", "=", origin_id)])
                if origin_id
                else 0
            )

    @api.model
    def _normalize_code(self, value):
        ascii_value = (
            unicodedata.normalize("NFKD", value or "")
            .encode("ascii", "ignore")
            .decode()
        )
        return re.sub(r"[^A-Z0-9]+", "-", ascii_value.upper()).strip("-")

    @api.model
    def _prepare_code(self, value):
        code = self._normalize_code(value)
        if not code:
            raise ValidationError(
                _(
                    "Bundle Type Code could not be generated. Enter a code using "
                    "Latin letters or numbers."
                )
            )
        return code

    @api.onchange("name")
    def _onchange_name_set_code(self):
        for bundle_type in self:
            if not bundle_type.code:
                bundle_type.code = bundle_type._normalize_code(bundle_type.name)

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for vals in vals_list:
            prepared_vals = dict(vals)
            prepared_vals["code"] = self._prepare_code(
                prepared_vals.get("code") or prepared_vals.get("name")
            )
            prepared_vals_list.append(prepared_vals)
        return super().create(prepared_vals_list)

    def write(self, vals):
        if "code" not in vals:
            return super().write(vals)

        prepared_vals = dict(vals)
        new_code = self._prepare_code(prepared_vals["code"])
        bundle_model = self.env["pinout.device.bundle"].with_context(active_test=False)
        for bundle_type in self:
            if new_code == bundle_type.code:
                continue
            if bundle_model.search([("bundle_type_id", "=", bundle_type.id)], limit=1):
                raise ValidationError(
                    _(
                        "Bundle Type Code cannot be changed because bundles of "
                        "this type already exist."
                    )
                )
        prepared_vals["code"] = new_code
        return super().write(prepared_vals)

    @api.constrains(
        "requires_kit_bom",
        "allow_any_product_form",
        "allowed_product_template_ids",
    )
    def _check_existing_bundles(self):
        bundles = self.env["pinout.device.bundle"].search(
            [("bundle_type_id", "in", self.ids)]
        )
        bundles._check_bundle_configuration()
