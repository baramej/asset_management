from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_consumable_in_inspection = fields.Boolean(
        string="Consumable (used up)",
        default=False,
        help="If checked, this product is consumed during inspection (materials, chemicals, etc). "
             "If unchecked, it is a tool/equipment that must be returned to the store.",
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    is_consumable_in_inspection = fields.Boolean(
        related="product_tmpl_id.is_consumable_in_inspection",
        store=True,
        readonly=False,
    )