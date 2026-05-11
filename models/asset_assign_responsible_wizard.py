from odoo import api, fields, models, _

class AssetAssignResponsibleWizard(models.TransientModel):
    _name = "asset.assign.responsible.wizard"
    _description = "Assign Asset Responsible Wizard"

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        required=True,
    )

    asset_code = fields.Char(
        string="Asset Code",
        related="asset_id.asset_code",
        readonly=True,
    )
    name = fields.Char(
        string="Asset Name",
        related="asset_id.name",
        readonly=True,
    )



    current_responsible_id = fields.Many2one(
        "res.users",
        string="Current Responsible",
        related="asset_id.responsible_id",
        readonly=True,
    )

    new_responsible_id = fields.Many2one(
        "res.users",
        string="New Responsible",
        required=True,
    )

    note = fields.Text(string="Notes")

    def action_assign_responsible(self):
        self.ensure_one()

        self.env["asset.assign.history"].create({
            "asset_id": self.asset_id.id,
            "assign_date": fields.Datetime.now(),
            "user_id": self.env.user.id,
            "old_responsible_id": self.current_responsible_id.id,
            "new_responsible_id": self.new_responsible_id.id,
            "note": self.note,
        })

        self.asset_id.write({
            "responsible_id": self.new_responsible_id.id,
        })

        if hasattr(self.asset_id, "message_post"):
            self.asset_id.message_post(
                body=_(
                    "Responsible user changed from %(old)s to %(new)s.<br/>%(note)s"
                ) % {
                         "old": self.current_responsible_id.display_name or "-",
                         "new": self.new_responsible_id.display_name,
                         "note": self.note or "",
                     }
            )

        return {"type": "ir.actions.act_window_close"}


class AssetAssignHistory(models.Model):
    _name = "asset.assign.history"
    _description = "Asset Responsible History"
    _order = "assign_date desc, id desc"

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        required=True,
        ondelete="cascade",
    )

    assign_date = fields.Datetime(
        string="Assign Date",
        default=fields.Datetime.now,
        required=True,
    )

    user_id = fields.Many2one(
        "res.users",
        string="Changed By",
        default=lambda self: self.env.user,
        readonly=True,
    )

    old_responsible_id = fields.Many2one(
        "res.users",
        string="Old Responsible",
        readonly=True,
    )
    new_responsible_id = fields.Many2one(
        "res.users",
        string="New Responsible",
        required=True,
    )

    note = fields.Text(string="Notes")