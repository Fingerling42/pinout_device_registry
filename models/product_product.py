import re

from odoo import api, models
from odoo.osv import expression


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def _name_search(self, name, domain=None, operator="ilike", limit=None, order=None):
        if not (name and self.env.context.get("pinout_device_product_search")):
            return super()._name_search(
                name, domain=domain, operator=operator, limit=limit, order=order
            )

        domain = domain or []
        product_ids = list(
            super()._name_search(
                name, domain=domain, operator=operator, limit=limit, order=order
            )
        )
        if limit and len(product_ids) >= limit:
            return product_ids

        positive_operators = {"=", "ilike", "=ilike", "like", "=like"}
        if operator not in positive_operators:
            return product_ids

        extra_domain = self._pinout_device_product_search_domain(name, operator)
        if product_ids:
            extra_domain = expression.AND(
                [extra_domain, [("id", "not in", product_ids)]]
            )
        extra_limit = limit - len(product_ids) if limit else None
        extra_product_ids = list(
            self._search(
                expression.AND([domain, extra_domain]),
                limit=extra_limit,
                order=order,
            )
        )
        return product_ids + extra_product_ids

    @api.model
    def _pinout_device_product_search_domain(self, name, operator):
        terms = re.findall(r"[\w-]+", name)
        if not terms:
            terms = [name]

        term_domains = []
        for term in terms:
            term_domains.append(
                expression.OR(
                    [
                        [("name", operator, term)],
                        [
                            (
                                "product_template_attribute_value_ids.product_attribute_value_id.name",
                                operator,
                                term,
                            )
                        ],
                        [
                            (
                                "product_template_attribute_value_ids.product_attribute_value_id.variant_code",
                                operator,
                                term,
                            )
                        ],
                    ]
                )
            )
        return expression.AND(term_domains)
