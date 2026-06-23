from odoo import models, fields, api

class AssetSparePartLine(models.Model):
    _name = "asset.spare.part.line"
    _description = "Spare Parts for Asset"

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        required=False,
        ondelete="cascade"
    )

    product_id = fields.Many2one(
        "product.product",
        string="Spare Part",
        required=True,
        domain=[("type", "in", ["product", "consu"])],
    )

    quantity = fields.Float(
        string="Required Quantity",
        default=1.0
    )

    consumed_qty = fields.Float(
        string="Consumed",
        compute="_compute_consumed_qty",
        store=False
    )
    usage_type = fields.Selection([
        ('preventive', 'Preventive'),
        ('corrective', 'Corrective'),
    ], string="Usage Type", default='preventive')

    on_hand_qty = fields.Float(
        string="Available in Stock",
        compute="_compute_on_hand_qty",
        store=False
    )

    min_qty = fields.Float(string="Min Qty")
    usage_type = fields.Selection([
        ('preventive', 'Preventive'),
        ('corrective', 'Corrective'),
        ('both', 'Both'),
    ], string="Usage Type")

    @api.depends("product_id")
    def _compute_on_hand_qty(self):
        for line in self:
            line.on_hand_qty = line.product_id.qty_available if line.product_id else 0

    @api.depends("asset_id", "product_id")
    def _compute_consumed_qty(self):
        for line in self:
            if not line.asset_id or not line.product_id:
                line.consumed_qty = 0
                continue

            consumed = self.env["asset.consumed.part.line"].search([
                ("asset_id", "=", line.asset_id.id),
                ("product_id", "=", line.product_id.id),
                ("task_id.state", "=", "done"),
            ])

            line.consumed_qty = sum(consumed.mapped("quantity"))


class AssetConsumedPartLine(models.Model):
    _name = "asset.consumed.part.line"
    _description = "Consumed Spare Parts in Maintenance"

    task_id = fields.Many2one("asset.maintenance.task", required=True)
    asset_id = fields.Many2one("account.asset", related="task_id.asset_id", store=True)

    product_id = fields.Many2one("product.product", required=True)
    quantity = fields.Float(default=1.0)

    usage_type = fields.Selection(
        [('preventive', 'Preventive'),
         ('corrective', 'Corrective')],
        default="preventive"
    )

    on_hand_qty = fields.Float(
        compute="_compute_on_hand_qty",
        store=False
    )

    def _compute_on_hand_qty(self):
        for rec in self:
            rec.on_hand_qty = rec.product_id.qty_available if rec.product_id else 0


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    maintenance_task_id = fields.Many2one(
        'asset.maintenance.task',
        string="Maintenance Task",
        ondelete="set null"
    )
