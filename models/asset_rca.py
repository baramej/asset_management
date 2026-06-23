from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import timedelta


class AssetRcaConfig(models.Model):
    _name = "asset.rca.config"
    _description = "RCA Trigger Configuration"

    name = fields.Char(string="Rule Name", required=True)
    active = fields.Boolean(default=True)

    trigger_type = fields.Selection([
        ("inspection", "Inspections"),
        ("job_order", "Job Orders"),
        ("helpdesk", "Helpdesk Tickets"),
        ("combined", "Any (Inspections + Job Orders + Tickets combined)"),
    ], string="Trigger On", required=True, default="inspection")

    threshold_count = fields.Integer(
        string="Minimum Count",
        default=3,
        help="Minimum number of events to trigger RCA flag.",
    )
    threshold_days = fields.Integer(
        string="Within (days)",
        default=60,
        help="Time window in days to count events.",
    )

    notes = fields.Text(string="Notes / Description")

    def name_get(self):
        result = []
        for rec in self:
            label = dict(self._fields["trigger_type"].selection).get(
                rec.trigger_type, rec.trigger_type
            )
            result.append((
                rec.id,
                f"{rec.name} — {label} ≥ {rec.threshold_count} in {rec.threshold_days}d"
            ))
        return result


class AssetRcaCandidate(models.Model):
    """Read-only view-like model: assets that match any active RCA rule."""
    _name = "asset.rca.candidate"
    _description = "Asset Flagged for RCA"
    _order = "flag_date desc"

    asset_id = fields.Many2one("account.asset", required=True, ondelete="cascade")
    config_id = fields.Many2one("asset.rca.config", string="Triggered By Rule", readonly=True)
    flag_date = fields.Date(string="Flagged On", default=fields.Date.today, readonly=True)

    inspection_count = fields.Integer(string="Inspections", readonly=True)
    job_order_count = fields.Integer(string="Job Orders", readonly=True)
    helpdesk_count = fields.Integer(string="Helpdesk Tickets", readonly=True)
    combined_count = fields.Integer(
        string="Total Events",
        compute="_compute_combined",
        store=True,
    )

    rca_id = fields.Many2one(
        "asset.rca.report", string="RCA Report", readonly=True
    )
    has_rca = fields.Boolean(
        string="RCA Created", compute="_compute_has_rca", store=True
    )

    asset_location = fields.Char(
        string="Location",
        compute="_compute_asset_fields",
        store=True,
    )
    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        compute="_compute_asset_fields",
        store=True,
    )

    @api.depends("asset_id", "asset_id.location_id", "asset_id.customer_id")
    def _compute_asset_fields(self):
        for rec in self:
            rec.asset_location = rec.asset_id.location_id.name if rec.asset_id else False
            rec.customer_id = rec.asset_id.customer_id if rec.asset_id else False

    @api.depends("inspection_count", "job_order_count", "helpdesk_count")
    def _compute_combined(self):
        for rec in self:
            rec.combined_count = (
                rec.inspection_count + rec.job_order_count + rec.helpdesk_count
            )

    @api.depends("rca_id")
    def _compute_has_rca(self):
        for rec in self:
            rec.has_rca = bool(rec.rca_id)

    def action_create_rca(self):
        self.ensure_one()
        if self.rca_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "asset.rca.report",
                "view_mode": "form",
                "res_id": self.rca_id.id,
                "views": [(False, "form")],
                "target": "current",
            }
        rca = self.env["asset.rca.report"].create({
            "asset_id": self.asset_id.id,
            "candidate_id": self.id,
            "inspection_count": self.inspection_count,
            "job_order_count": self.job_order_count,
            "helpdesk_count": self.helpdesk_count,
            "trigger_rule_id": self.config_id.id,
        })
        self.rca_id = rca.id
        return {
            "type": "ir.actions.act_window",
            "res_model": "asset.rca.report",
            "view_mode": "form",
            "res_id": rca.id,
            "views": [(False, "form")],
            "target": "current",
        }

    @api.model
    def cron_refresh_candidates(self):
        """Run daily — re-evaluates all active RCA rules and flags matching assets."""
        configs = self.env["asset.rca.config"].search([("active", "=", True)])
        today = fields.Date.today()

        for config in configs:
            since = today - timedelta(days=config.threshold_days)
            assets = self.env["account.asset"].search([])

            for asset in assets:
                count_inspections = count_jobs = count_tickets = 0

                if config.trigger_type in ("inspection", "combined"):
                    count_inspections = self.env["asset.inspection"].search_count([
                        ("asset_id", "=", asset.id),
                        ("scheduled_date", ">=", str(since)),
                    ])

                if config.trigger_type in ("job_order", "combined"):
                    count_jobs = self.env["asset.job.order"].search_count([
                        ("asset_id", "=", asset.id),
                        ("scheduled_date", ">=", str(since)),
                    ])

                if config.trigger_type in ("helpdesk", "combined"):
                    count_tickets = self.env["helpdesk.ticket"].search_count([
                        ("asset_id", "=", asset.id),
                        ("create_date", ">=", str(since)),
                    ])

                total = {
                    "inspection": count_inspections,
                    "job_order": count_jobs,
                    "helpdesk": count_tickets,
                    "combined": count_inspections + count_jobs + count_tickets,
                }.get(config.trigger_type, 0)

                if total < config.threshold_count:
                    continue

                # Check if candidate already exists for this asset + rule
                existing = self.search([
                    ("asset_id", "=", asset.id),
                    ("config_id", "=", config.id),
                ], limit=1)

                if existing:
                    existing.write({
                        "inspection_count": count_inspections,
                        "job_order_count": count_jobs,
                        "helpdesk_count": count_tickets,
                        "flag_date": today,
                    })
                else:
                    self.create({
                        "asset_id": asset.id,
                        "config_id": config.id,
                        "flag_date": today,
                        "inspection_count": count_inspections,
                        "job_order_count": count_jobs,
                        "helpdesk_count": count_tickets,
                    })


class AssetRcaReport(models.Model):
    _name = "asset.rca.report"
    _description = "Root Cause Analysis Report"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc"

    name = fields.Char(
        string="RCA Reference",
        readonly=True,
        default=lambda self: _("New"),
        copy=False,
    )
    asset_id = fields.Many2one(
        "account.asset", required=True, tracking=True
    )
    candidate_id = fields.Many2one(
        "asset.rca.candidate", string="Flagged Candidate", readonly=True
    )
    trigger_rule_id = fields.Many2one(
        "asset.rca.config", string="Trigger Rule", readonly=True
    )

    date = fields.Date(
        string="RCA Date", default=fields.Date.today, required=True, tracking=True
    )
    conducted_by_id = fields.Many2one(
        "res.users",
        string="Conducted By",
        default=lambda self: self.env.user,
        tracking=True,
    )
    team_ids = fields.Many2many(
        "hr.employee",
        "rca_report_employee_rel",
        "rca_id",
        "employee_id",
        string="Analysis Team",
    )

    state = fields.Selection([
        ("draft", "Draft"),
        ("in_review", "In Review"),
        ("approved", "Approved"),
        ("closed", "Closed"),
    ], default="draft", tracking=True)

    # ── Event Summary (auto-filled from candidate) ───────────────────────────
    inspection_count = fields.Integer(string="Inspections in Period", readonly=True)
    job_order_count = fields.Integer(string="Job Orders in Period", readonly=True)
    helpdesk_count = fields.Integer(string="Tickets in Period", readonly=True)

    # ── Asset context (computed) ──────────────────────────────────────────────
    asset_location = fields.Char(
        string="Location",
        compute="_compute_asset_fields",
        store=True,
    )
    asset_category = fields.Selection([
        ("it", "IT Equipment"), ("electrical", "Electrical"),
        ("mechanical", "Mechanical"), ("hvac", "HVAC"),
        ("furniture", "Furniture"), ("other", "Other"),
    ], string="Category", compute="_compute_asset_fields", store=True)

    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        compute="_compute_asset_fields",
        store=True,
    )

    @api.depends("asset_id", "asset_id.location_id", "asset_id.customer_id",
                 "asset_id.asset_category")
    def _compute_asset_fields(self):
        for rec in self:
            rec.asset_location = rec.asset_id.location_id.name if rec.asset_id else False
            rec.customer_id = rec.asset_id.customer_id if rec.asset_id else False
            rec.asset_category = rec.asset_id.asset_category if rec.asset_id else False


    # ── Problem Statement ─────────────────────────────────────────────────────
    problem_statement = fields.Text(
        string="Problem Statement",
        help="Describe the recurring issue observed.",
    )
    timeline_description = fields.Text(
        string="Timeline of Events",
        help="Summarise the sequence of incidents/inspections/job orders.",
    )

    # ── 5-Why Analysis ───────────────────────────────────────────────────────
    why_1 = fields.Text(string="Why #1")
    why_2 = fields.Text(string="Why #2")
    why_3 = fields.Text(string="Why #3")
    why_4 = fields.Text(string="Why #4")
    why_5 = fields.Text(string="Why #5")
    root_cause = fields.Text(string="Root Cause (Summary)", tracking=True)

    # ── Fishbone / Contributing Factors ──────────────────────────────────────
    factor_line_ids = fields.One2many(
        "asset.rca.factor.line", "rca_id", string="Contributing Factors"
    )

    # ── Corrective Actions ───────────────────────────────────────────────────
    action_line_ids = fields.One2many(
        "asset.rca.action.line", "rca_id", string="Corrective Actions"
    )

    # ── Preventive Measures ──────────────────────────────────────────────────
    preventive_measures = fields.Text(string="Preventive Measures")
    pm_schedule_change = fields.Boolean(
        string="PM Schedule Change Required?", default=False
    )
    pm_schedule_notes = fields.Text(
        string="PM Schedule Change Notes",
        invisible="not pm_schedule_change",
    )

    # ── Outcome ───────────────────────────────────────────────────────────────
    outcome = fields.Text(string="Outcome / Resolution")
    recurrence_risk = fields.Selection([
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ], string="Recurrence Risk", tracking=True)

    notes = fields.Text(string="Internal Notes")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("asset.rca.report")
                    or _("New")
                )
        return super().create(vals_list)

    def action_submit_review(self):
        self.write({"state": "in_review"})
        self.message_post(body=_("RCA submitted for review by %s.") % self.env.user.name)

    def action_approve(self):
        self.write({"state": "approved"})
        self.message_post(body=_("RCA approved by %s.") % self.env.user.name)

    def action_close(self):
        open_actions = self.action_line_ids.filtered(
            lambda a: a.status != "completed"
        )
        if open_actions:
            raise UserError(_(
                "Cannot close RCA — the following corrective actions are not completed:\n• %s"
            ) % "\n• ".join(open_actions.mapped("description")))
        self.write({"state": "closed"})
        self.message_post(body=_("RCA closed by %s.") % self.env.user.name)

    def action_reset_draft(self):
        self.write({"state": "draft"})

    def action_view_asset_inspections(self):
        self.ensure_one()
        return {
            "name": _("Inspections — %s") % self.asset_id.name,
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.asset_id.id)],
        }

    def action_view_asset_job_orders(self):
        self.ensure_one()
        return {
            "name": _("Job Orders — %s") % self.asset_id.name,
            "type": "ir.actions.act_window",
            "res_model": "asset.job.order",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.asset_id.id)],
        }

    def action_view_asset_tickets(self):
        self.ensure_one()
        return {
            "name": _("Helpdesk Tickets — %s") % self.asset_id.name,
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.asset_id.id)],
        }


class AssetRcaFactorLine(models.Model):
    _name = "asset.rca.factor.line"
    _description = "RCA Contributing Factor"
    _order = "sequence, id"

    rca_id = fields.Many2one(
        "asset.rca.report", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)

    category = fields.Selection([
        ("man", "Man / People"),
        ("machine", "Machine / Equipment"),
        ("method", "Method / Process"),
        ("material", "Material"),
        ("environment", "Environment"),
        ("management", "Management"),
    ], string="Category", required=True)

    description = fields.Text(string="Factor Description", required=True)
    is_root_cause = fields.Boolean(string="Root Cause?", default=False)


class AssetRcaActionLine(models.Model):
    _name = "asset.rca.action.line"
    _description = "RCA Corrective Action"
    _order = "sequence, id"

    rca_id = fields.Many2one(
        "asset.rca.report", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)

    description = fields.Text(string="Action", required=True)
    assigned_to_id = fields.Many2one("hr.employee", string="Assigned To")
    due_date = fields.Date(string="Due Date")
    status = fields.Selection([
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ], default="pending", string="Status")
    notes = fields.Text(string="Notes")