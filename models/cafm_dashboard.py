from odoo import api, fields, models
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import json

class FacilityDashboard(models.Model):
    _name = 'asset.dashboard'
    _description = 'Facility Management Dashboard'

    name = fields.Char(default="Facility Dashboard")

    # ASSET STATISTICS
    total_assets = fields.Integer(compute="_compute_asset_stats")
    assets_active = fields.Integer(compute="_compute_asset_stats")
    assets_maintenance = fields.Integer(compute="_compute_asset_stats")
    assets_out_of_service = fields.Integer(compute="_compute_asset_stats")
    assets_retired = fields.Integer(compute="_compute_asset_stats")

    assets_by_company = fields.Text(compute="_compute_assets_by_company")
    assets_by_category = fields.Text(compute="_compute_asset_category_stats")

    # MAINTENANCE STATS
    total_maintenance = fields.Integer(compute="_compute_maintenance_stats")
    maintenance_scheduled = fields.Integer(compute="_compute_maintenance_stats")
    maintenance_in_progress = fields.Integer(compute="_compute_maintenance_stats")
    maintenance_pending_parts = fields.Integer(compute="_compute_maintenance_stats")
    maintenance_completed = fields.Integer(compute="_compute_maintenance_stats")
    maintenance_cancelled = fields.Integer(compute="_compute_maintenance_stats")
    maintenance_overdue = fields.Integer(compute="_compute_maintenance_stats")

    maintenance_preventive = fields.Integer(compute="_compute_maintenance_type")
    maintenance_corrective = fields.Integer(compute="_compute_maintenance_type")
    maintenance_predictive = fields.Integer(compute="_compute_maintenance_type")

    # HELPDESK
    total_helpdesk = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_new = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_open = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_in_progress = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_pending = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_resolved = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_closed = fields.Integer(compute="_compute_helpdesk_stats")
    helpdesk_unassigned = fields.Integer(compute="_compute_helpdesk_stats")

    helpdesk_urgent = fields.Integer(compute="_compute_helpdesk_priority")
    helpdesk_high = fields.Integer(compute="_compute_helpdesk_priority")
    helpdesk_normal = fields.Integer(compute="_compute_helpdesk_priority")
    helpdesk_low = fields.Integer(compute="_compute_helpdesk_priority")

    # TEAMS
    total_teams = fields.Integer(compute="_compute_team_stats")
    total_team_members = fields.Integer(compute="_compute_team_stats")
    teams_available = fields.Integer(compute="_compute_team_stats")
    teams_busy = fields.Integer(compute="_compute_team_stats")
    team_workload = fields.Text(compute="_compute_team_workload")

    # TRENDS
    monthly_maintenance_data = fields.Text(compute="_compute_monthly_maintenance")
    monthly_helpdesk_data = fields.Text(compute="_compute_monthly_helpdesk")


    # ----------------------------
    # ASSET COMPUTATION
    # ----------------------------
    def _compute_asset_stats(self):
        Asset = self.env['account.asset']
        company = self.env.company.id
        domain = [
            ('company_id', '=', company),
        ]

        for rec in self:
            rec.total_assets = Asset.search_count(domain)
            rec.assets_active = Asset.search_count(domain + [('state', '=', 'active')])
            rec.assets_maintenance = Asset.search_count(domain + [('state', '=', 'maintenance')])
            rec.assets_out_of_service = Asset.search_count(domain + [('state', '=', 'out_of_service')])
            rec.assets_retired = Asset.search_count(domain + [('state', '=', 'retired')])

    def _compute_assets_by_company(self):
        for rec in self:
            query = """
                SELECT customer_id, COUNT(id)
                FROM account_asset
                WHERE customer_id IS NOT NULL
                GROUP BY customer_id;
            """

            self.env.cr.execute(query)
            rows = self.env.cr.fetchall()

            result = []

            for customer_id, count in rows:
                customer_name = self.env['res.partner'].browse(customer_id).name
                result.append({
                    'company': customer_name,
                    'count': count,
                })

            rec.assets_by_company = json.dumps(result)

    def _compute_asset_category_stats(self):
        for rec in self:
            field = self.env['account.asset']._fields['asset_category']
            categories = dict(field.selection)
            result = []

            for key, label in categories.items():
                count = self.env['account.asset'].search_count([('asset_category', '=', key)])
                result.append({"name": label, "count": count})

            rec.assets_by_category = json.dumps(result)


    # ----------------------------
    # MAINTENANCE
    # ----------------------------
    def _compute_maintenance_stats(self):
        Task = self.env['asset.maintenance.task']
        for rec in self:
            rec.total_maintenance = Task.search_count([])

            rec.maintenance_scheduled = Task.search_count([('state', '=', 'draft')])
            rec.maintenance_in_progress = Task.search_count([('state', '=', 'in_progress')])
            rec.maintenance_pending_parts = 0  # no such state in your model
            rec.maintenance_completed = Task.search_count([('state', '=', 'done')])
            rec.maintenance_cancelled = Task.search_count([('state', '=', 'cancel')])

            today = fields.Date.today()
            rec.maintenance_overdue = Task.search_count([
                ('scheduled_date', '<', today),
                ('state', 'not in', ['done', 'cancel'])
            ])

    def _compute_maintenance_type(self):
        Task = self.env['asset.maintenance.task']
        for rec in self:
            rec.maintenance_preventive = Task.search_count([('maintenance_type', '=', 'preventive')])
            rec.maintenance_corrective = Task.search_count([('maintenance_type', '=', 'corrective')])
            rec.maintenance_predictive = Task.search_count([('maintenance_type', '=', 'predictive')])


    # ----------------------------
    # HELPDESK
    # ----------------------------
    def _compute_helpdesk_stats(self):
        Ticket = self.env['helpdesk.ticket']
        for rec in self:
            rec.total_helpdesk = Ticket.search_count([])
            rec.helpdesk_new = Ticket.search_count([('stage_id.name', '=', 'New')])
            rec.helpdesk_open = Ticket.search_count([('stage_id.name', '=', 'Open')])
            rec.helpdesk_in_progress = Ticket.search_count([('stage_id.name', '=', 'In Progress')])
            rec.helpdesk_pending = Ticket.search_count([('stage_id.name', '=', 'On Hold')])
            rec.helpdesk_resolved = Ticket.search_count([('stage_id.name', '=', 'Solved')])
            rec.helpdesk_closed = Ticket.search_count([('stage_id.name', '=', 'Closed')])
            rec.helpdesk_unassigned = Ticket.search_count([('user_id', '=', False)])


    def _compute_helpdesk_priority(self):
        Ticket = self.env['helpdesk.ticket']
        for rec in self:
            rec.helpdesk_urgent = Ticket.search_count([('priority', '=', '3')])
            rec.helpdesk_high = Ticket.search_count([('priority', '=', '2')])
            rec.helpdesk_normal = Ticket.search_count([('priority', '=', '1')])
            rec.helpdesk_low = Ticket.search_count([('priority', '=', '0')])


    # ----------------------------
    # TEAM COMPUTATION
    # ----------------------------
    def _compute_team_stats(self):
        Team = self.env['asset.maintenance.team']
        Task = self.env['asset.maintenance.task']

        for rec in self:
            teams = Team.search([])
            rec.total_teams = len(teams)
            rec.total_team_members = sum(len(team.member_ids) for team in teams)

            busy = Task.search([('state', '=', 'in_progress')]).mapped('maintenance_team_id')
            rec.teams_busy = len(busy)
            rec.teams_available = rec.total_teams - rec.teams_busy


    def _compute_team_workload(self):
        Task = self.env['asset.maintenance.task']
        Team = self.env['asset.maintenance.team']

        for rec in self:
            result = []

            for team in Team.search([]):
                task_count = Task.search_count([
                    ('maintenance_team_id', '=', team.id),
                    ('state', 'in', ['draft', 'in_progress'])
                ])
                result.append({
                    "team": team.name,
                    "tasks": task_count,
                    "members": len(team.member_ids)
                })

            rec.team_workload = json.dumps(result)


    # ----------------------------
    # MONTHLY TRENDS
    # ----------------------------
    def _compute_monthly_maintenance(self):
        Task = self.env['asset.maintenance.task']

        for rec in self:
            data = []
            today = datetime.today()

            for i in range(11, -1, -1):
                month = today - relativedelta(months=i)
                start = month.replace(day=1)
                end = (start + relativedelta(months=1)) - timedelta(seconds=1)

                total = Task.search_count([
                    ('create_date', '>=', start),
                    ('create_date', '<=', end),
                ])

                completed = Task.search_count([
                    ('create_date', '>=', start),
                    ('create_date', '<=', end),
                    ('state', '=', 'done'),
                ])

                data.append({
                    "month": month.strftime("%b %Y"),
                    "total": total,
                    "completed": completed,
                })

            rec.monthly_maintenance_data = json.dumps(data)


    def _compute_monthly_helpdesk(self):
        Ticket = self.env['helpdesk.ticket']

        for rec in self:
            data = []
            today = datetime.today()

            for i in range(11, -1, -1):
                month = today - relativedelta(months=i)
                start = month.replace(day=1)
                end = (start + relativedelta(months=1)) - timedelta(seconds=1)

                total = Ticket.search_count([
                    ('create_date', '>=', start),
                    ('create_date', '<=', end),
                ])

                resolved = Ticket.search_count([
                    ('stage_id.name', '=', 'Solved'),
                    ('create_date', '>=', start),
                    ('create_date', '<=', end),
                ])

                data.append({
                    "month": month.strftime("%b %Y"),
                    "total": total,
                    "resolved": resolved,
                })

            rec.monthly_helpdesk_data = json.dumps(data)
