"""
Migration script: convert legacy single pm_frequency_days on account.asset
into proper asset.pm.schedule records.

Run this ONCE after deploying the new models by calling:
    env['account.asset'].migrate_legacy_pm_to_schedules()

Or run it from a one-time post_init_hook in __init__.py.
"""

from odoo import api, fields, models, _
import logging

_logger = logging.getLogger(__name__)


class AccountAsset(models.Model):
    _inherit = "account.asset"

    @api.model
    def migrate_legacy_pm_to_schedules(self):
        """
        For every asset that has pm_frequency_days set but no pm_schedule_ids yet,
        create one 'Preventive Maintenance' schedule entry.
        Safe to run multiple times — skips assets that already have schedules.
        """
        assets = self.search([
            ("pm_frequency_days", ">", 0),
        ])

        created = 0
        for asset in assets:
            # Skip if already migrated
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