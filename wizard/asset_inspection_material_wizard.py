from odoo import models, fields, api, _


class AssetInspectionMaterialWizard(models.TransientModel):
    _name = "asset.inspection.material.wizard"
    _description = "Request Materials for Inspection"

    inspection_id = fields.Many2one(
        "asset.inspection",
        required=True,
        readonly=True,
    )

    line_ids = fields.One2many(
        "asset.inspection.material.wizard.line",
        "wizard_id",
        string="Materials",
    )

    def action_confirm(self):
        self.ensure_one()
        for line in self.line_ids:
            self.env["asset.inspection.material"].create({
                "inspection_id": self.inspection_id.id,
                "product_id": line.product_id.id,
                "qty_requested": line.qty_requested,
                "state": "requested",
                "notes": line.notes,
            })
        self.inspection_id.write({"state": "request_material"})
        self.inspection_id._send_material_request_email()
        return {"type": "ir.actions.act_window_close"}


class AssetInspectionMaterialWizardLine(models.TransientModel):
    _name = "asset.inspection.material.wizard.line"
    _description = "Material Request Line"

    wizard_id = fields.Many2one(
        "asset.inspection.material.wizard",
        required=True,
        ondelete="cascade",
    )

    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain="[('type', 'in', ['consu', 'product'])]",
    )

    qty_requested = fields.Float(string="Qty Needed", default=1.0)

    qty_available = fields.Float(
        string="In Stock",
        compute="_compute_qty_available",
        store=False,
    )

    product_uom_id = fields.Many2one(
        "uom.uom",
        related="product_id.uom_id",
        readonly=True,
    )
    is_consumable = fields.Boolean(
        string="Consumable",
        default=True,
    )

    notes = fields.Char(string="Notes")

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.qty_available = self.product_id.free_qty
            self.is_consumable = self.product_id.is_consumable_in_inspection

    @api.depends("product_id")
    def _compute_qty_available(self):
        for line in self:
            line.qty_available = line.product_id.free_qty if line.product_id else 0.0
