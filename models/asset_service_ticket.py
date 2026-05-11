from odoo import api, fields, models, _

class AssetServiceTicket(models.Model):
    _name = "asset.service.ticket"
    _description = "Asset Service Ticket"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, default=lambda self: _("New Ticket"), tracking=True)
    asset_id = fields.Many2one("account.asset", string="Asset", required=True, index=True)
    department_id = fields.Many2one(related="asset_id.department_id", store=True, readonly=True)
    location_id = fields.Many2one(related="asset_id.location_id", store=True, readonly=True)

    ticket_type = fields.Selection([
        ("incident", "Incident"),
        ("request", "Service Request"),
        ("complaint", "Complaint"),
        ("pm", "Preventive Follow-up"),
    ], default="incident", required=True)

    reason = fields.Char(string="Reason")
    description = fields.Text()
    priority = fields.Selection([
        ("0", "Low"),
        ("1", "Normal"),
        ("2", "High"),
        ("3", "Critical"),
    ], default="1")

    requester_id = fields.Many2one("res.partner", string="Requested By")
    assigned_user_id = fields.Many2one("res.users", string="Assigned To")
    assigned_team_id = fields.Many2one("res.users", string="Responsible Team")

    tat_hours = fields.Float(string="TAT (Hours)")  # to be computed later from config
    deadline = fields.Datetime(string="TAT Deadline")
    escalation_level = fields.Integer(default=0)

    state = fields.Selection([
        ("new", "New"),
        ("in_progress", "In Progress"),
        ("on_hold", "On Hold"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ], default="new", tracking=True)

    is_under_warranty = fields.Boolean(
        string="Under Warranty?",
        compute="_compute_is_under_warranty",
        store=False,
    )

    @api.depends("asset_id", "asset_id.warranty_start_date", "asset_id.warranty_end_date")
    def _compute_is_under_warranty(self):
        today = fields.Date.today()
        for rec in self:
            a = rec.asset_id
            rec.is_under_warranty = bool(
                a and a.warranty_start_date and a.warranty_end_date
                and a.warranty_start_date <= today <= a.warranty_end_date
            )

    def action_start(self):
        self.write({"state": "in_progress"})

    def action_resolve(self):
        self.write({"state": "resolved"})

    def action_close(self):
        self.write({"state": "closed"})

    def action_cancel(self):
        self.write({"state": "cancelled"})
