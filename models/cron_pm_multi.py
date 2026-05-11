from odoo import api, fields, models, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class AccountAsset(models.Model):
    _inherit = "account.asset"

    # ── New multi-schedule cron ─────────────────────────────────────────────
    @api.model
    def cron_generate_pm_tasks_multi(self):
        """
        Replaces cron_generate_pm_tasks.
        Loops through every active asset.pm.schedule and creates a
        asset.maintenance.task when the schedule is due.
        """
        today = fields.Date.today()
        MaintenanceTask = self.env["asset.maintenance.task"]

        schedules = self.env["asset.pm.schedule"].search([
            ("active", "=", True),
            ("frequency_days", ">", 0),
        ])

        _logger.info(
            "cron_generate_pm_tasks_multi: checking %d schedules", len(schedules)
        )

        for schedule in schedules:
            asset = schedule.asset_id

            # Determine reference date
            base_date = schedule.last_run_date
            if not base_date:
                # No last run: use asset purchase date or today - frequency
                base_date = (
                    asset.purchase_date
                    or (today - timedelta(days=schedule.frequency_days))
                )

            due_date = base_date + timedelta(days=schedule.frequency_days)

            if due_date > today:
                continue  # Not due yet

            # Avoid duplicate: check no open task exists for this schedule
            # in the current due window
            existing = MaintenanceTask.search([
                ("pm_schedule_id", "=", schedule.id),
                ("state",          "not in", ["done", "cancel"]),
            ], limit=1)

            if existing:
                _logger.debug(
                    "Schedule %s already has an open task (%s) — skipping",
                    schedule.name, existing.name,
                )
                continue

            # Build checklist lines from template if one is set
            checklist_vals = []
            template = schedule.checklist_template_id
            if not template and schedule.service_type:
                template = self.env["asset.checklist.template"].search([
                    ("service_type", "=", schedule.service_type),
                    ("active",       "=", True),
                ], limit=1)

            if template:
                for tl in template.line_ids:
                    checklist_vals.append((0, 0, {
                        "sequence":    tl.sequence,
                        "description": tl.description,
                        "is_mandatory": tl.is_mandatory,
                        "notes":       tl.notes,
                    }))

            # Map service_type → maintenance_type field (existing field)
            maintenance_type_map = {
                "preventive_maintenance": "preventive",
                "housekeeping_routine":   "preventive",
                "housekeeping_deep":      "preventive",
                "housekeeping_adhoc":     "corrective",
                "gardening_daily":        "preventive",
                "gardening_monthly":      "preventive",
                "gardening_adhoc":        "corrective",
                "facility_soft":          "preventive",
                "facility_hard":          "preventive",
                "emergency":              "corrective",
            }

            task_vals = {
                "name": _("%s — %s") % (schedule.name, asset.name or asset.asset_code or ""),
                "asset_id":           asset.id,
                "maintenance_type":   maintenance_type_map.get(schedule.service_type, "preventive"),
                "service_type":       schedule.service_type,
                "pm_schedule_id":     schedule.id,
                "scheduled_date":     fields.Datetime.now(),
                "checklist_line_ids": checklist_vals,
                "requires_supervisor_signoff": schedule.requires_supervisor_signoff,
                "supervisor_signoff_state": (
                    "pending" if schedule.requires_supervisor_signoff else "not_required"
                ),
            }

            # Inherit team / assignee from schedule if set
            if schedule.maintenance_team_id:
                task_vals["maintenance_team_id"] = schedule.maintenance_team_id.id
            if schedule.assigned_user_id:
                task_vals["assigned_user_id"] = schedule.assigned_user_id.id

            # Inherit contract from asset if present (for preventive)
            contract_line = self.env["asset.maintenance.contract.line"].search(
                [("asset_id", "=", asset.id)], limit=1
            )
            if contract_line:
                task_vals["contract_line_id"] = contract_line.id
                task_vals["contract_id"]      = contract_line.contract_id.id

            task = MaintenanceTask.create(task_vals)

            # Update last run date on schedule
            schedule.last_run_date = today

            # Keep legacy asset.last_pm_date in sync (backward compatibility)
            if schedule.service_type == "preventive_maintenance":
                asset.last_pm_date = today

            _logger.info(
                "Created task '%s' (id=%d) for schedule '%s' on asset '%s'",
                task.name, task.id, schedule.name, asset.name,
            )