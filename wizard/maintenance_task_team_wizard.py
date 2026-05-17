from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class AssetTeamScheduleViewerWizard(models.TransientModel):
    _name = "asset.team.schedule.viewer.wizard"
    _description = "Team Schedule Viewer"

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        string="Maintenance Team",
        required=True,
        readonly=True,
    )

    schedule_ids = fields.One2many(
        "asset.team.schedule.viewer.line",
        "wizard_id",
        string="Team Schedule",
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        team_id = self.env.context.get("default_maintenance_team_id")
        if team_id:
            res["maintenance_team_id"] = team_id
            team = self.env["asset.maintenance.team"].browse(team_id)
            lines = self._build_schedule_lines(team)
            res["schedule_ids"] = lines
        return res

    def _build_schedule_lines(self, team):
        lines = []

        inspections = self.env["asset.inspection"].search([
            ("maintenance_team_id", "=", team.id),
            ("state", "in", ["scheduled", "request_material", "material_collected", "in_progress"]),
            ("scheduled_date", "!=", False),
        ], order="scheduled_date asc")

        for rec in inspections:
            lines.append((0, 0, {
                "work_type": "inspection",
                "reference": rec.name,
                "asset_name": rec.asset_id.name if rec.asset_id else "-",
                "scheduled_date": rec.scheduled_date,
                "state": dict(rec._fields["state"].selection).get(rec.state, rec.state),
                "color_state": rec.state,
            }))

        tasks = self.env["asset.maintenance.task"].search([
            ("maintenance_team_id", "=", team.id),
            ("state", "in", ["assigned", "in_progress"]),
            ("scheduled_date", "!=", False),
        ], order="scheduled_date asc")

        for rec in tasks:
            lines.append((0, 0, {
                "work_type": "preventive" if rec.maintenance_type == "preventive" else "corrective",
                "reference": rec.name,
                "asset_name": rec.asset_id.name if rec.asset_id else "-",
                "scheduled_date": rec.scheduled_date,
                "state": dict(rec._fields["state"].selection).get(rec.state, rec.state),
                "color_state": rec.state,
            }))

        job_orders = self.env["asset.job.order"].search([
            ("maintenance_team_id", "=", team.id),
            ("state", "in", ["request_material", "material_approved", "in_progress"]),
            ("scheduled_date", "!=", False),
        ], order="scheduled_date asc")

        for rec in job_orders:
            lines.append((0, 0, {
                "work_type": "job_order",
                "reference": rec.name,
                "asset_name": rec.asset_id.name if rec.asset_id else "-",
                "scheduled_date": rec.scheduled_date,
                "state": dict(rec._fields["state"].selection).get(rec.state, rec.state),
                "color_state": rec.state,
            }))

        lines.sort(key=lambda l: l[2]["scheduled_date"])
        return lines


class AssetTeamScheduleViewerLine(models.TransientModel):
    _name = "asset.team.schedule.viewer.line"
    _description = "Team Schedule Viewer Line"
    _order = "scheduled_date asc"

    wizard_id = fields.Many2one(
        "asset.team.schedule.viewer.wizard",
        required=True,
        ondelete="cascade",
    )
    work_type = fields.Selection([
        ("inspection", "Inspection"),
        ("preventive", "Preventive Maintenance"),
        ("corrective", "Corrective Maintenance"),
        ("job_order", "Job Order"),
    ], string="Type", readonly=True)
    reference = fields.Char(string="Reference", readonly=True)
    asset_name = fields.Char(string="Asset", readonly=True)
    scheduled_date = fields.Datetime(string="Scheduled", readonly=True)
    state = fields.Char(string="Status", readonly=True)
    color_state = fields.Char(readonly=True)