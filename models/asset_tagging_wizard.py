from odoo import api, fields, models, _

class AssetTaggingCompleteWizard(models.TransientModel):
    _name = "asset.tagging.complete.wizard"
    _description = "Asset Tagging Complete Wizard"

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        required=True,
        readonly=True,
    )

    def action_edit_asset(self):
        self.ensure_one()
        return {
            "name": _("Asset"),
            "type": "ir.actions.act_window",
            "res_model": "account.asset",
            "view_mode": "form",
            "res_id": self.asset_id.id,
            "view_id": self.env.ref("account_asset.view_account_asset_form").id,
            "target": "current",
        }

    def action_new_tagging(self):
        self.ensure_one()
        action = self.env.ref("asset_management.action_asset_tagging").read()[0]
        action.update({
            "views": [(self.env.ref("asset_management.view_asset_tagging_form").id, "form")],
            "view_mode": "form",
        })
        return action
