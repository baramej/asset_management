from odoo import api, fields, models, _
from datetime import timedelta, datetime
import logging

_logger = logging.getLogger(__name__)


class AccountAsset(models.Model):
    _inherit = "account.asset"

    @api.model
    def cron_generate_pm_tasks_multi(self):
        today = fields.Date.today()
        MaintenanceTask = self.env["asset.maintenance.task"]
        advance_days = 5  # create task this many days before due date

        schedules = self.env["asset.pm.schedule"].search([
            ("active", "=", True),
            ("frequency_days", ">", 0),
        ])

        _logger.info(
            "cron_generate_pm_tasks_multi: checking %d schedules", len(schedules)
        )

        for schedule in schedules:
            asset = schedule.asset_id

            base_date = schedule.last_run_date
            if not base_date:
                base_date = (
                        asset.purchase_date
                        or (today - timedelta(days=schedule.frequency_days))
                )

            due_date = base_date + timedelta(days=schedule.frequency_days)

            # Skip if due date is more than advance_days away
            if due_date > today + timedelta(days=advance_days):
                continue

            # Skip if already has an open task for this schedule
            existing = MaintenanceTask.search([
                ("pm_schedule_id", "=", schedule.id),
                ("state", "not in", ["done", "cancel"]),
            ], limit=1)

            if existing:
                _logger.debug(
                    "Schedule %s already has an open task (%s) — skipping",
                    schedule.name, existing.name,
                )
                continue

            checklist_vals = []
            template = schedule.checklist_template_id
            if not template and schedule.service_type:
                template = self.env["asset.checklist.template"].search([
                    ("service_type", "=", schedule.service_type),
                    ("active", "=", True),
                ], limit=1)

            if template:
                for tl in template.line_ids:
                    checklist_vals.append((0, 0, {
                        "sequence": tl.sequence,
                        "description": tl.description,
                        "is_mandatory": tl.is_mandatory,
                        "notes": tl.notes,
                    }))

            maintenance_type_map = {
                "preventive_maintenance": "preventive",
                "housekeeping_routine": "preventive",
                "housekeeping_deep": "preventive",
                "housekeeping_adhoc": "corrective",
                "gardening_daily": "preventive",
                "gardening_monthly": "preventive",
                "gardening_adhoc": "corrective",
                "facility_soft": "preventive",
                "facility_hard": "preventive",
                "emergency": "corrective",
            }

            days_until_due = (due_date - today).days
            if days_until_due > 0:
                task_name = _("%s — %s (Due in %d days)") % (
                    schedule.name,
                    asset.name or asset.asset_code or "",
                    days_until_due,
                )
            else:
                task_name = _("%s — %s (Due today)") % (
                    schedule.name,
                    asset.name or asset.asset_code or "",
                )

            task_vals = {
                "name": task_name,
                "asset_id": asset.id,
                "maintenance_type": maintenance_type_map.get(schedule.service_type, "preventive"),
                "service_type": schedule.service_type,
                "pm_schedule_id": schedule.id,
                "scheduled_date": datetime.combine(due_date, datetime.now().time()),# ← schedule to actual due date, not today
                "checklist_line_ids": checklist_vals,
                "requires_supervisor_signoff": schedule.requires_supervisor_signoff,
                "supervisor_signoff_state": (
                    "pending" if schedule.requires_supervisor_signoff else "not_required"
                ),
            }

            if schedule.maintenance_team_id:
                task_vals["maintenance_team_id"] = schedule.maintenance_team_id.id
            if schedule.assigned_user_id:
                task_vals["assigned_user_id"] = schedule.assigned_user_id.id

            contract_line = self.env["asset.maintenance.contract.line"].search(
                [("asset_id", "=", asset.id)], limit=1
            )
            if contract_line:
                task_vals["contract_line_id"] = contract_line.id
                task_vals["contract_id"] = contract_line.contract_id.id

            task = MaintenanceTask.create(task_vals)

            # Only update last_run_date when the due date has actually arrived
            if due_date <= today:
                schedule.last_run_date = today
                if schedule.service_type == "preventive_maintenance":
                    asset.last_pm_date = today

            _logger.info(
                "Created task '%s' (id=%d) for schedule '%s' on asset '%s' "
                "(due %s, %d days away)",
                task.name, task.id, schedule.name, asset.name,
                due_date, days_until_due,
            )
