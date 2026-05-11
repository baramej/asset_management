
from odoo import api, fields, models, _
from datetime import timedelta

from odoo.exceptions import UserError


class AccountAsset(models.Model):
    _inherit = "account.asset"

    asset_code = fields.Char(string="Asset Code", index=True)
    qr_code = fields.Char(string="QR Code", help="QR/Barcode encoded ID")
    serial_no = fields.Char(string="Serial Number")
    manufacturer_id = fields.Many2one("res.partner", string="Manufacturer")
    service_provider_id = fields.Many2one("res.partner", string="Service Provider")
    customer_id = fields.Many2one(
        "res.partner",
        string="Customer / Company",
        help="Customer that owns or will be charged for this asset’s spare parts."
    )

    asset_type = fields.Selection([
        ("physical", "Physical Asset"),
        ("location", "Location / Space"),
        ("service", "Service / Activity"),
    ], string="Asset Type", default="physical", required=True, tracking=True)

    facility_code = fields.Char(
        string="Facility Code",
        help="Unique identifier for locations or service areas (e.g. ROOM-101, GARDEN-ZONE-A)",
        index=True,
    )

    department_id = fields.Many2one(
        "hr.department",
        string="Department",
        help="Owning or responsible department",
    )
    location_id = fields.Many2one(
        "asset.location",
        string="Location",
        help="Physical location of the asset",
    )
    responsible_id = fields.Many2one(
        "res.users",
        string="Responsible",
        tracking=True,
        help="User responsible/owner for this asset.",
    )
    customer_id = fields.Many2one(
        "res.partner",
        string="Customer / Company",
        help="Customer that owns or will be charged for this asset’s spare parts."
    )

    warranty_start_date = fields.Date(string="Warranty Start Date")
    warranty_end_date = fields.Date(string="Warranty End Date")
    purchase_date = fields.Date(string="Purchase Date")
    purchase_price = fields.Monetary(string="Purchase Price")
    warranty_in_days = fields.Integer(
        string="Warranty Days",
        compute="_compute_warranty_days",
        store=False,
    )

    location_latitude = fields.Float(
        string="Location Latitude",
        digits=(16, 7),
        compute="_compute_location_geo",
        store=False,
    )
    location_longitude = fields.Float(
        string="Location Longitude",
        digits=(16, 7),
        compute="_compute_location_geo",
        store=False,
    )
    location_map_iframe = fields.Html(
        string="Location Map",
        compute="_compute_location_geo",
        sanitize=False,
        store=False,
    )

    service_ticket_ids = fields.One2many(
        "asset.service.ticket",
        "asset_id",
        string="Service Tickets",
    )
    maintenance_task_ids = fields.One2many(
        "asset.maintenance.task",
        "asset_id",
        string="Maintenance Tasks",
    )

    helpdesk_ticket_ids = fields.One2many(
        "helpdesk.ticket",
        "asset_id",
        string="Helpdesk Tickets",
    )

    pm_frequency_days = fields.Integer(
        string="PM Frequency (Days)",
        help="Number of days between preventive maintenance tasks."
    )
    last_pm_date = fields.Date(
        string="Last PM Date",
        help="Last date a preventive maintenance task was generated."
    )

    spare_part_line_ids = fields.One2many(
        "asset.spare.part.line",
        "asset_id",
        string="Spare Parts Lines"
    )

    asset_category = fields.Selection([
        ("it", "IT Equipment"),
        ("electrical", "Electrical"),
        ("mechanical", "Mechanical"),
        ("hvac", "HVAC"),
        ("furniture", "Furniture"),
        ("other", "Other"),
    ], string="Asset Category")

    tagged_by_id = fields.Many2one(
        "res.users",
        string="Tagged By",
        readonly=True,
    )

    tagged_date = fields.Datetime(
        string="Tagged Date",
        readonly=True,
    )

    lifecycle_state = fields.Selection([
        ("tagging", "Tagging"),
        ("operation", "Operation"),
        ("maintenance", "Under Maintenance"),
        ("monitoring", "Monitoring"),
        ("renewal", "For Renewal"),
        ("disposal", "For Disposal"),
    ], string="Lifecycle Status", default="tagging", tracking=True)

    auto_name = fields.Char(
        string="Auto Name",
        help="Final generated asset name",
        readonly=True,
        copy=False,
    )

    auto_name_preview = fields.Char(
        string="Name Preview",
        compute="_compute_auto_name_preview",
        store=False,
        help="Live preview of the generated asset name",
    )
    contract_line_ids = fields.One2many(
        "asset.maintenance.contract.line",
        "asset_id",
        string="Contract Lines",
        readonly=True,
    )

    contract_id = fields.Many2one(
        "asset.maintenance.contract",
        string="Contract",
        compute="_compute_contract_id",
        store=False,
        readonly=True,
    )

    move_history_ids = fields.One2many(
        "asset.move.history",
        "asset_id",
        string="Move History",
    )
    assign_history_ids = fields.One2many(
        "asset.assign.history",
        "asset_id",
        string="Responsible History",
    )

    move_history_count = fields.Integer(
        string="Moves",
        compute="_compute_history_counts",
    )
    assign_history_count = fields.Integer(
        string="Assignments",
        compute="_compute_history_counts",
    )

    contract_count = fields.Integer(
        string="Contracts",
        compute="_compute_contract_count"
    )

    # ── New: multiple PM schedules ──────────────────────────────────────────
    pm_schedule_ids = fields.One2many(
        "asset.pm.schedule",
        "asset_id",
        string="PM / Service Schedules",
    )

    # Convenience computed fields replacing the old single next_pm_date
    next_pm_date = fields.Date(
        string="Next PM Due",
        compute="_compute_next_pm_date_multi",
        store=False,
        help="Earliest upcoming due date across all active PM schedules.",
    )

    @api.depends("pm_schedule_ids.next_due_date", "pm_schedule_ids.active")
    def _compute_next_pm_date_multi(self):
        for asset in self:
            dates = asset.pm_schedule_ids.filtered(
                lambda s: s.active and s.next_due_date
            ).mapped("next_due_date")
            asset.next_pm_date = min(dates) if dates else False

    def _compute_contract_count(self):
        ContractLine = self.env["asset.maintenance.contract.line"]
        for asset in self:
            asset.contract_count = ContractLine.search_count([
                ("asset_id", "=", asset.id)
            ])


    def _short_code(self, text, length=4):
        if not text:
            return ""
        clean = "".join(text.upper().split())
        return clean[:length]

    def _compute_contract_id(self):
        ContractLine = self.env["asset.maintenance.contract.line"]
        for asset in self:
            line = ContractLine.search([("asset_id", "=", asset.id)], limit=1)
            asset.contract_id = line.contract_id.id if line else False

    def _build_auto_name_preview(self):
        self.ensure_one()

        # For location/service types, use facility_code as the primary identifier
        if self.asset_type in ("location", "service"):
            loc_part = self.location_id.full_code or self.location_id.code or ""
            code_part = self.facility_code or ""
            parts = [p for p in [loc_part, code_part] if p]
            return "/".join(parts) if parts else False

        # Original physical asset logic
        loc_part = self.location_id.full_code or self.location_id.code or ""
        dept_part = self._short_code(self.department_id.name, 3)
        cat_map = {
            "it": "IT", "electrical": "ELEC", "mechanical": "MECH",
            "hvac": "HVAC", "furniture": "FURN", "other": "OTH",
        }
        cat_part = cat_map.get(self.asset_category or "", "")
        code_part = self.asset_code or self.qr_code or ""
        parts = [p for p in [loc_part, dept_part, cat_part, code_part] if p]
        return "/".join(parts) if parts else False

    def _generate_auto_name(self):
        for rec in self:
            final = rec._build_auto_name_preview()
            if final:
                rec.auto_name = final
                rec.name = final

    @api.depends("last_pm_date", "pm_frequency_days")
    def _compute_next_pm_date(self):
        today = fields.Date.today()
        for asset in self:
            freq = asset.pm_frequency_days or 0

            if freq <= 0:
                asset.next_pm_date = False
                continue

            if asset.last_pm_date:
                asset.next_pm_date = asset.last_pm_date + timedelta(days=freq)
            else:
                asset.next_pm_date = today + timedelta(days=freq)

    def _apply_asset_model_template(self):
        for rec in self:
            if not getattr(rec, "model_id", False) or not rec.model_id:
                continue

            model = rec.model_id
            vals = {}

            # Accounting M2O fields (existing)
            m2o_fields = [
                "account_asset_id",
                "account_depreciation_id",
                "account_depreciation_expense_id",
                "journal_id",
            ]
            for fname in m2o_fields:
                if fname in rec._fields and fname in model._fields:
                    if not rec[fname] and model[fname]:
                        vals[fname] = model[fname].id

            # Simple/scalar fields (existing)
            simple_fields = [
                "method",
                "method_number",
                "method_period",
                "method_progress_factor",
                "prorata_computation_type",
                "prorata_date",
                "salvage_value",
            ]
            for fname in simple_fields:
                if fname in rec._fields and fname in model._fields:
                    if not rec[fname] and model[fname]:
                        vals[fname] = model[fname]

            # NEW: Asset Details M2O fields
            asset_detail_m2o_fields = [
                "customer_id",
                "responsible_id",
                "location_id",
                "department_id",
                "manufacturer_id",
                "service_provider_id",
            ]
            for fname in asset_detail_m2o_fields:
                if fname in rec._fields and fname in model._fields:
                    if not rec[fname] and model[fname]:
                        vals[fname] = model[fname].id

            # NEW: Asset Details char/simple fields
            asset_detail_simple_fields = [
                "qr_code",
                "serial_no",
                "asset_category",
                "pm_frequency_days",
            ]
            for fname in asset_detail_simple_fields:
                if fname in rec._fields and fname in model._fields:
                    if not rec[fname] and model[fname]:
                        vals[fname] = model[fname]

            if vals:
                rec.write(vals)


    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name"):
                temp_name = vals.get("asset_code") or vals.get("qr_code") or _("New Asset")
                vals["name"] = temp_name

        assets = super(AccountAsset, self).create(vals_list)

        for asset in assets:
            asset._apply_asset_model_template()
            asset._generate_auto_name()

        return assets


    def write(self, vals):
        res = super(AccountAsset, self).write(vals)

        if "model_id" in vals:
            for rec in self:
                rec._apply_asset_model_template()

        watched = {"location_id", "department_id", "asset_category", "asset_code", "qr_code", "facility_code"}
        if any(f in vals for f in watched):
            for rec in self:
                rec._generate_auto_name()

        return res

    def action_open_helpdesk_tickets(self):
        self.ensure_one()
        return {
            "name": _("Helpdesk Tickets"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

    def action_complete_tagging(self):
        self.ensure_one()
        if self.asset_type != "physical":
            raise UserError(_("Tagging is only applicable to physical assets."))

        self._generate_auto_name()
        self.write({
            "lifecycle_state": "operation",
            "tagged_by_id": self.env.user.id,
            "tagged_date": fields.Datetime.now(),
        })

        return {
            "name": _("Tagging Completed"),
            "type": "ir.actions.act_window",
            "res_model": "asset.tagging.complete.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_asset_id": self.id,
            },
        }

    @api.depends("location_id", "location_id.full_code",
                 "department_id", "asset_category",
                 "asset_code", "qr_code", "facility_code", "asset_type")
    def _compute_auto_name_preview(self):
        for rec in self:
            rec.auto_name_preview = rec._build_auto_name_preview()

    @api.depends("warranty_start_date", "warranty_end_date")
    def _compute_warranty_days(self):
        for asset in self:
            if asset.warranty_start_date and asset.warranty_end_date:
                asset.warranty_in_days = (asset.warranty_end_date - asset.warranty_start_date).days
            else:
                asset.warranty_in_days = 0

    def action_open_service_tickets(self):
        self.ensure_one()
        return {
            "name": _("Service Tickets"),
            "type": "ir.actions.act_window",
            "res_model": "asset.service.ticket",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

    def action_open_maintenance_tasks(self):
        self.ensure_one()
        return {
            "name": _("Maintenance Tasks"),
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.task",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

    def action_open_contracts(self):
        self.ensure_one()
        return {
            "name": "Contracts",
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.contract.line",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id}
        }

    @api.depends("location_id", "location_id.gps_latitude", "location_id.gps_longitude")
    def _compute_location_geo(self):
        for rec in self:
            lat = rec.location_id.gps_latitude if rec.location_id else False
            lon = rec.location_id.gps_longitude if rec.location_id else False

            rec.location_latitude = lat or False
            rec.location_longitude = lon or False

            if lat and lon:
                rec.location_map_iframe = f"""
                    <iframe
                        width="100%"
                        height="250"
                        frameborder="0"
                        scrolling="no"
                        marginheight="0"
                        marginwidth="0"
                        src="https://maps.google.com/maps?q={lat:.7f},{lon:.7f}&z=18&output=embed">
                    </iframe>
                """
            else:
                rec.location_map_iframe = False

    @api.model
    def cron_generate_pm_tasks(self):
        today = fields.Date.today()
        MaintenanceTask = self.env["asset.maintenance.task"]

        assets = self.search([
            ("pm_frequency_days", ">", 0),
        ])


        for asset in assets:
            if asset.last_pm_date:
                last_date = asset.last_pm_date
            elif asset.purchase_date:
                last_date = asset.purchase_date
            else:
                last_date = today - timedelta(days=asset.pm_frequency_days)

            due_date = last_date + timedelta(days=asset.pm_frequency_days)



            if due_date <= today:
                task_vals = {
                    "name": _("Preventive Maintenance - %s") % (asset.name or asset.asset_code or ""),
                    "asset_id": asset.id,
                    "maintenance_type": "preventive",
                    "scheduled_date": today,
                }
                MaintenanceTask.create(task_vals)

                asset.last_pm_date = today

    def _compute_history_counts(self):
        MoveHistory = self.env["asset.move.history"]
        AssignHistory = self.env["asset.assign.history"]
        for asset in self:
            asset.move_history_count = MoveHistory.search_count([
                ("asset_id", "=", asset.id)
            ])
            asset.assign_history_count = AssignHistory.search_count([
                ("asset_id", "=", asset.id)
            ])

    def action_open_move_history(self):
        self.ensure_one()
        return {
            "name": _("Asset Move History"),
            "type": "ir.actions.act_window",
            "res_model": "asset.move.history",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

    def action_open_assign_history(self):
        self.ensure_one()
        return {
            "name": _("Asset Responsible History"),
            "type": "ir.actions.act_window",
            "res_model": "asset.assign.history",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

