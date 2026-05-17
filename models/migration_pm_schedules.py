from odoo import api, fields, models, _
import logging

_logger = logging.getLogger(__name__)


class AccountAsset(models.Model):
    _inherit = "account.asset"

    @api.model
    def migrate_legacy_pm_to_schedules(self):
        assets = self.search([
            ("pm_frequency_days", ">", 0),
        ])

        created = 0
        for asset in assets:
            if asset.pm_schedule_ids:
                continue

            self.env["asset.pm.schedule"].create({
                "asset_id":        asset.id,
                "name":            _("Preventive Maintenance"),
                "service_type":    "preventive_maintenance",
                "frequency_days":  asset.pm_frequency_days,
                "last_run_date":   asset.last_pm_date or False,
                "maintenance_team_id": False,
                "active":          True,
            })
            created += 1

        _logger.info(
            "migrate_legacy_pm_to_schedules: created %d schedule records.", created
        )
        return created