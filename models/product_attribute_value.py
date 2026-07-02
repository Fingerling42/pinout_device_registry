from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductAttributeValue(models.Model):
    _inherit = "product.attribute.value"

    variant_code = fields.Char(
        string="Variant Code",
        help="Short code used in product references, labels, and Device Registry summaries. Example: ORNG, LIPU, ENJ.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_variant_code_values(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._normalize_variant_code_values(vals)
        return super().write(vals)

    @api.constrains("attribute_id", "variant_code")
    def _check_variant_code_unique_per_attribute(self):
        for value in self.filtered("variant_code"):
            normalized_code = self._normalize_variant_code(value.variant_code)
            duplicates = self.search(
                [
                    ("attribute_id", "=", value.attribute_id.id),
                    ("id", "!=", value.id),
                    ("variant_code", "!=", False),
                ]
            ).filtered(
                lambda duplicate: (
                    self._normalize_variant_code(duplicate.variant_code)
                    == normalized_code
                )
            )
            if duplicates:
                raise ValidationError(
                    _(
                        "Variant Code %(code)s is already used for attribute %(attribute)s.",
                        code=normalized_code,
                        attribute=value.attribute_id.display_name,
                    )
                )

    @api.model
    def _normalize_variant_code_values(self, vals):
        if "variant_code" in vals:
            vals["variant_code"] = self._normalize_variant_code(vals["variant_code"])

    @api.model
    def _normalize_variant_code(self, variant_code):
        if not variant_code:
            return False
        normalized_code = str(variant_code).strip().upper()
        return normalized_code or False
