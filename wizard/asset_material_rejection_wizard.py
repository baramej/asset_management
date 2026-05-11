from odoo import models, fields, _


class AssetMaterialRejectionWizard(models.TransientModel):
    _name = "asset.material.rejection.wizard"
    _description = "Reject Material Request"

    material_ids = fields.Many2many(
        "asset.inspection.material",
        string="Materials",
    )
    reason = fields.Text(string="Rejection Reason", required=True)

    def action_confirm(self):
        self.ensure_one()
        material_lines = self.env["asset.inspection.material"].browse(
            self.env.context.get("default_material_ids", [])
        )
        # _do_reject cancels the stock reservation AND sets state = rejected
        for line in material_lines:
            line._do_reject(reason=self.reason or "")
        return {"type": "ir.actions.act_window_close"}
