# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hr_sync_employee_company_id = fields.Many2one(
        "res.company",
        string="Create Employees Under",
        config_parameter="rental_management_hr_sync.employee_company_id",
        help="Company every employee created by the legacy HR sync (Occupy "
        "wizard Sync, portal sign-up) is created under, e.g. SBG. Leave empty "
        "to use the company that carries the code.",
    )