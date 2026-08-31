from odoo import api, fields, models, _
from datetime import date, timedelta


class AssetChecklistTemplate(models.Model):
    _name = "asset.checklist.template"
    _description = "Checklist Template"

    name = fields.Char(required=True)
    service_type = fields.Selection([
        ("preventive_maintenance",   "Preventive Maintenance"),
        ("housekeeping_routine",     "Housekeeping — Routine"),
        ("housekeeping_deep",        "Housekeeping — Deep Clean"),
        ("housekeeping_adhoc",       "Housekeeping — Ad-hoc"),
        ("gardening_daily",          "Gardening — Daily/Weekly"),
        ("gardening_monthly",        "Gardening — Monthly/Seasonal"),
        ("gardening_adhoc",          "Gardening — Ad-hoc"),
        ("facility_soft",            "Facility — Soft Services"),
        ("facility_hard",            "Facility — Hard Services"),
        ("emergency",                "Emergency / Breakdown"),
    ], required=True, string="Service Type")

    line_ids = fields.One2many(
        "asset.checklist.template.line",
        "template_id",
        string="Checklist Items",
    )
    active = fields.Boolean(default=True)


class AssetChecklistTemplateLine(models.Model):
    _name = "asset.checklist.template.line"
    _description = "Checklist Template Line"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "asset.checklist.template", required=True, ondelete="cascade"
    )
    sequence   = fields.Integer(default=10)
    description = fields.Char(string="Task / Check Item", required=True)
    is_mandatory = fields.Boolean(default=True)
    notes        = fields.Char(string="Guidance Notes")

class AssetPmSchedule(models.Model):
    _name = "asset.pm.schedule"
    _description = "Asset PM / Service Schedule"
    _order = "next_due_date"

    asset_id = fields.Many2one(
        "account.asset", required=True, ondelete="cascade", index=True
    )
    name = fields.Char(
        string="Schedule Name",
        required=True,
        help="e.g. 'Daily Cleaning', 'Monthly Gardening', 'Quarterly HVAC PM'",
    )
    service_type = fields.Selection([
        ("preventive_maintenance",   "Preventive Maintenance"),
        ("housekeeping_routine",     "Housekeeping — Routine"),
        ("housekeeping_deep",        "Housekeeping — Deep Clean"),
        ("housekeeping_adhoc",       "Housekeeping — Ad-hoc"),
        ("gardening_daily",          "Gardening — Daily/Weekly"),
        ("gardening_monthly",        "Gardening — Monthly/Seasonal"),
        ("gardening_adhoc",          "Gardening — Ad-hoc"),
        ("facility_soft",            "Facility — Soft Services"),
        ("facility_hard",            "Facility — Hard Services"),
        ("emergency",                "Emergency / Breakdown"),
    ], required=True, string="Service Type")

    frequency_days = fields.Integer(
        string="Frequency (Days)", required=True,
        help="How many days between each execution of this schedule."
    )
    last_run_date  = fields.Date(string="Last Run Date")
    next_due_date  = fields.Date(
        string="Next Due Date", compute="_compute_next_due", store=True
    )

    checklist_template_id = fields.Many2one(
        "asset.checklist.template",
        string="Checklist Template",
        domain="[('service_type', '=', service_type)]",
    )

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team", string="Default Team"
    )
    assigned_user_id = fields.Many2one(
        "res.users", string="Default Assignee"
    )

    active = fields.Boolean(default=True)

    requires_supervisor_signoff = fields.Boolean(
        string="Supervisor Sign-off Required",
        default=False,
        help="If enabled, the task will wait for supervisor approval before closing.",
    )

    @api.depends("last_run_date", "frequency_days")
    def _compute_next_due(self):
        today = fields.Date.today()
        for rec in self:
            if rec.frequency_days <= 0:
                rec.next_due_date = False
                continue
            base = rec.last_run_date or today
            rec.next_due_date = base + timedelta(days=rec.frequency_days)


class AssetTaskChecklistLine(models.Model):
    _name = "asset.task.checklist.line"
    _description = "Task Checklist Line"
    _order = "sequence, id"

    task_id = fields.Many2one(
        "asset.maintenance.task",
        required=False,  # ← was True
        ondelete="cascade",
        index=True,
    )
    sequence     = fields.Integer(default=10)
    description  = fields.Char(string="Task / Check Item", required=True)
    is_mandatory = fields.Boolean(default=True)
    is_done      = fields.Boolean(string="Done", default=False)
    notes        = fields.Char(string="Notes / Remarks")
    done_by_id   = fields.Many2one("res.users", string="Done By", readonly=True)
    done_date    = fields.Datetime(string="Done At", readonly=True)
    job_order_id = fields.Many2one(
        "asset.job.order",
        ondelete="cascade",
        index=True,
    )

    def action_mark_done(self):
        for rec in self:
            rec.write({
                "is_done":    True,
                "done_by_id": self.env.user.id,
                "done_date":  fields.Datetime.now(),
            })

    def action_mark_undone(self):
        for rec in self:
            rec.write({
                "is_done":    False,
                "done_by_id": False,
                "done_date":  False,
            })