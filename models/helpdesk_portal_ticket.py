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
        "asset.location", string="Main Location",
        help="First level picked on the portal, e.g. WATTAYAH.",
    )
    sublocation_id = fields.Many2one(
        "asset.location", string="Exact Location",
        help="Deepest level picked on the portal, e.g. BOILER ROOM under FORD SHOWROOM.",
    )
    location_display = fields.Char(
        string="Location Path",
        compute="_compute_location_display",
        help="e.g. WATTAYAH / FORD SHOWROOM / BOILER ROOM",
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

    @api.depends("location_id", "sublocation_id", "sublocation_id.parent_path")
    def _compute_location_display(self):
        for rec in self:
            leaf = rec.sublocation_id or rec.location_id
            if not leaf:
                rec.location_display = False
                continue
            ids = [int(x) for x in (leaf.parent_path or "").split("/") if x] or [leaf.id]
            chain = leaf.browse(ids)
            if rec.location_id and rec.location_id in chain:
                chain = chain[list(chain).index(rec.location_id):]
            rec.location_display = " / ".join(chain.mapped("name"))
