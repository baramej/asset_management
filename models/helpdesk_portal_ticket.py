from odoo import api, fields, models, _


class HelpdeskJobCategory(models.Model):
    _name = "helpdesk.job.category"
    _description = "Helpdesk Job Category"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Char()


class HelpdeskPortalTicketLine(models.Model):
    _name = "helpdesk.portal.ticket.line"
    _description = "Portal Ticket Extra Data"

    ticket_id = fields.Many2one(
        "helpdesk.ticket", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one("res.company", string="Company")
    department_id = fields.Many2one("hr.department", string="Department")
    location_id = fields.Many2one(
        "asset.location", string="Parent Location",
        domain=[("parent_id", "=", False)],
    )
    sublocation_id = fields.Many2one(
        "asset.location", string="Sub-location",
    )
    building = fields.Char(string="Building / Unit")
    job_category_id = fields.Many2one(
        "helpdesk.job.category", string="Job Category"
    )
    asset_id = fields.Many2one("account.asset", string="Equipment")
    remarks = fields.Text(string="Remarks")
    approver_id = fields.Many2one(
        "res.users", string="Approver (Manager)", readonly=True
    )