# asset_move_wizard.py

from odoo import api, fields, models, _

class AssetMoveWizard(models.TransientModel):
    _name = "asset.move.wizard"
    _description = "Asset Move Wizard"

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

    current_location_id = fields.Many2one(
        "asset.location",
        string="Current Location",
        related="asset_id.location_id",
        readonly=True,
    )
    current_department_id = fields.Many2one(
        "hr.department",
        string="Current Department",
        related="asset_id.department_id",
        readonly=True,
    )

    new_location_id = fields.Many2one(
        "asset.location",
        string="New Location",
        required=True,
    )
    new_department_id = fields.Many2one(
        "hr.department",
        string="New Department",
    )

    move_date = fields.Datetime(
        string="Move Date",
        default=fields.Datetime.now,
        required=True,
    )

    note = fields.Text(string="Move Notes")

    def action_confirm_move(self):
        self.ensure_one()

        self.env["asset.move.history"].create({
            "asset_id": self.asset_id.id,
            "move_date": self.move_date,
            "user_id": self.env.user.id,
            "old_location_id": self.current_location_id.id,
            "old_department_id": self.current_department_id.id,
            "new_location_id": self.new_location_id.id,
            "new_department_id": self.new_department_id.id,
            "note": self.note,
        })

        vals = {
            "location_id": self.new_location_id.id,
        }
        if self.new_department_id:
            vals["department_id"] = self.new_department_id.id

        self.asset_id.write(vals)

        if hasattr(self.asset_id, "message_post"):
            self.asset_id.message_post(
                body=_(
                    "Asset moved on %(date)s from %(old_loc)s / %(old_dept)s "
                    "to %(new_loc)s / %(new_dept)s.<br/>%(note)s"
                ) % {
                         "date": self.move_date,
                         "old_loc": self.current_location_id.display_name or "-",
                         "old_dept": self.current_department_id.display_name or "-",
                         "new_loc": self.new_location_id.display_name or "-",
                         "new_dept": self.new_department_id.display_name or "-",
                         "note": self.note or "",
                     }
            )

        return {"type": "ir.actions.act_window_close"}


class AssetMoveHistory(models.Model):
    _name = "asset.move.history"
    _description = "Asset Move History"
    _order = "move_date desc, id desc"

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        required=True,
        ondelete="cascade",
    )

    move_date = fields.Datetime(
        string="Move Date",
        default=fields.Datetime.now,
        required=True,
    )

    user_id = fields.Many2one(
        "res.users",
        string="Moved By",
        default=lambda self: self.env.user,
        readonly=True,
    )

    old_location_id = fields.Many2one(
        "asset.location",
        string="Old Location",
        readonly=True,
    )
    old_department_id = fields.Many2one(
        "hr.department",
        string="Old Department",
        readonly=True,
    )

    new_location_id = fields.Many2one(
        "asset.location",
        string="New Location",
        required=True,
    )
    new_department_id = fields.Many2one(
        "hr.department",
        string="New Department",
    )

    note = fields.Text(string="Notes")