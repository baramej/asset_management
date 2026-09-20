from odoo import api, fields, models, _
from datetime import date, timedelta
import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AssetMaintenanceContract(models.Model):
    _name = "asset.maintenance.contract"
    _description = "AMC Contract"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Contract Name", required=True, tracking=True)
    reference = fields.Char(string="Contract Reference / Number", tracking=True)
    customer_id = fields.Many2one(
        "res.partner", string="Client / Company", required=True, tracking=True
    )
    vendor_id = fields.Many2one(
        "res.partner", string="Service Provider",
        help="The maintenance company providing the service under this contract.",
        tracking=True,
    )

    start_date = fields.Date(required=True, tracking=True)
    end_date = fields.Date(required=True, tracking=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("active", "Active"),
        ("expiring", "Expiring Soon"),
        ("expired", "Expired"),
        ("cancelled", "Cancelled"),
    ], compute="_compute_state", store=True, tracking=True)

    days_to_expiry = fields.Integer(compute="_compute_state", store=True)
    renewal_reminder_days = fields.Integer(
        string="Renewal Reminder (days before expiry)",
        default=30,
    )

    location_id = fields.Many2one(
        "asset.location",
        string="Location",
        tracking=True,
        help="Site/building/location this contract covers.",
    )

    contract_type = fields.Selection([
        ("pm_only", "PM Only — Scheduled visits only"),
        ("pm_plus_corrective", "PM + Corrective — Visits, no parts"),
        ("all_inclusive", "All Inclusive — Visits + all parts"),
        ("parts_only", "Parts Only — No visit charge"),
        ("pay_per_task", "Pay per Task — Nothing included"),
    ], default="pm_only", required=True, tracking=True)

    labour_covered = fields.Boolean(
        string="Labour / Visits Covered",
        compute="_compute_coverage_flags",
        store=True,
    )
    parts_covered = fields.Selection([
        ("none", "Not covered — billed separately"),
        ("consumables", "Consumables only"),
        ("all", "All parts included"),
    ], compute="_compute_coverage_flags", store=True)

    inclusions = fields.Text(
        string="What's Included",
        help="Describe what is covered under this contract.",
    )
    exclusions = fields.Text(
        string="What's Excluded",
        help="Describe what is NOT covered and will be billed separately.",
    )

    visits_per_year = fields.Integer(default=4, string="Scheduled Visits / Year")
    pm_frequency_days = fields.Integer(default=90, string="PM Frequency (days)")
    next_service_date = fields.Date(string="Next Scheduled Service")

    contract_value = fields.Monetary(
        string="Annual Contract Value", currency_field="currency_id"
    )
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id
    )
    payment_terms = fields.Selection([
        ("annual", "Annual upfront"),
        ("semi", "Semi-annual"),
        ("quarterly", "Quarterly"),
        ("monthly", "Monthly"),
    ], string="Payment Terms")

    contract_line_ids = fields.One2many(
        "asset.maintenance.contract.line", "contract_id",
        string="Assets Under Contract"
    )
    service_visit_ids = fields.One2many(
        "asset.contract.service.visit", "contract_id",
        string="Service Visit Log"
    )

    asset_count = fields.Integer(compute="_compute_counts")
    visit_count = fields.Integer(compute="_compute_counts")
    visits_completed = fields.Integer(compute="_compute_counts")

    notes = fields.Text(string="Internal Notes")
    terms_and_conditions = fields.Text(string="Terms & Conditions Summary")

    is_active = fields.Boolean(default=True)

    contractor_portal_token = fields.Char(
        string="Contractor Portal Token",
        copy=False,
        readonly=True,
    )

    def _get_or_create_contractor_token(self):
        self.ensure_one()
        if not self.contractor_portal_token:
            import uuid
            self.contractor_portal_token = str(uuid.uuid4())
        return self.contractor_portal_token

    def action_copy_contractor_link(self):
        self.ensure_one()
        token = self._get_or_create_contractor_token()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        url = f"{base_url}/contract/{self.id}/submit-visit?token={token}"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Contractor Portal Link",
                "message": url,
                "type": "info",
                "sticky": True,
            },
        }

    @api.depends("start_date", "end_date", "is_active", "renewal_reminder_days")
    def _compute_state(self):
        today = fields.Date.today()
        for rec in self:
            if not rec.is_active:
                rec.state = "cancelled"
                rec.days_to_expiry = 0
                continue
            if not rec.end_date:
                rec.state = "draft"
                rec.days_to_expiry = 0
                continue
            diff = (rec.end_date - today).days
            rec.days_to_expiry = diff
            if not rec.start_date or today < rec.start_date:
                rec.state = "draft"
            elif diff < 0:
                rec.state = "expired"
            elif diff <= (rec.renewal_reminder_days or 30):
                rec.state = "expiring"
            else:
                rec.state = "active"

    def action_view_assets(self):
        self.ensure_one()
        return {
            "name": _("Assets — %s") % self.name,
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.contract.line",
            "view_mode": "list,form",
            "domain": [("contract_id", "=", self.id)],
            "context": {"default_contract_id": self.id},
        }

    @api.depends("contract_type")
    def _compute_coverage_flags(self):
        for rec in self:
            ct = rec.contract_type
            rec.labour_covered = ct in (
                "pm_only", "pm_plus_corrective", "all_inclusive"
            )
            if ct == "all_inclusive":
                rec.parts_covered = "all"
            elif ct in ("pm_only", "pm_plus_corrective", "pay_per_task"):
                rec.parts_covered = "none"
            elif ct == "parts_only":
                rec.parts_covered = "all"
            else:
                rec.parts_covered = "none"

    @api.depends("contract_line_ids", "service_visit_ids",
                 "service_visit_ids.status")
    def _compute_counts(self):
        for rec in self:
            rec.asset_count = len(rec.contract_line_ids)
            rec.visit_count = len(rec.service_visit_ids)
            rec.visits_completed = len(
                rec.service_visit_ids.filtered(
                    lambda v: v.status == "completed"
                )
            )

    def get_coverage_summary(self):
        self.ensure_one()
        parts_label = {
            "none": "Parts billed separately",
            "consumables": "Consumables included",
            "all": "All parts included",
        }.get(self.parts_covered, "—")
        labour = "Labour included" if self.labour_covered else "Labour billed separately"
        return f"{labour} | {parts_label}"

    def action_activate(self):
        for rec in self:
            rec.is_active = True
        self.message_post(body=_("Contract activated."))

    def action_cancel(self):
        for rec in self:
            rec.is_active = False
            rec.state = "cancelled"
        self.message_post(body=_(
            "Contract cancelled by %s.") % self.env.user.name
                          )

    def action_log_service_visit(self):
        self.ensure_one()
        return {
            "name": _("Log Service Visit"),
            "type": "ir.actions.act_window",
            "res_model": "asset.contract.service.visit",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_contract_id": self.id,
                "default_visit_date": fields.Date.today(),
            },
        }

    def action_view_visits(self):
        self.ensure_one()
        return {
            "name": _("Service Visits — %s") % self.name,
            "type": "ir.actions.act_window",
            "res_model": "asset.contract.service.visit",
            "view_mode": "list,form",
            "domain": [("contract_id", "=", self.id)],
            "context": {"default_contract_id": self.id},
        }

    @api.model
    def cron_flag_expiring_contracts(self):
        """Run daily — posts a chatter note on contracts nearing expiry."""
        contracts = self.search([("state", "=", "expiring"), ("is_active", "=", True)])
        for c in contracts:
            c.message_post(
                body=_(
                    "⚠ Contract <b>%s</b> expires on <b>%s</b> (%d days remaining). "
                    "Please arrange renewal."
                ) % (c.name, c.end_date, c.days_to_expiry),
                subtype_xmlid="mail.mt_note",
            )


class AssetMaintenanceContractLine(models.Model):
    _name = "asset.maintenance.contract.line"
    _description = "Asset Under AMC Contract"

    contract_id = fields.Many2one(
        "asset.maintenance.contract", required=True, ondelete="cascade"
    )
    asset_id = fields.Many2one("account.asset", required=False, string="Asset")
    asset_location = fields.Char(
        related="asset_id.location_id.name", string="Location", readonly=True
    )

    next_pm_date = fields.Date(string="Next PM Date")
    last_pm_date = fields.Date(string="Last PM Date")
    remaining_visits = fields.Integer(string="Remaining Visits")
    due_status = fields.Char(compute="_compute_due_status", store=False)

    special_terms = fields.Text(
        string="Special Terms for this Asset",
        help="Any coverage exceptions or extra terms that apply to this specific asset only.",
    )

    @api.depends("next_pm_date")
    def _compute_due_status(self):
        today = fields.Date.today()
        for line in self:
            if not line.next_pm_date:
                line.due_status = "Not scheduled"
                continue
            diff = (line.next_pm_date - today).days
            if diff > 5:
                line.due_status = f"Due in {diff} days"
            elif 0 < diff <= 5:
                line.due_status = f"⚠ Due in {diff} days"
            elif diff == 0:
                line.due_status = "⚠ Due today"
            else:
                line.due_status = f"{abs(diff)} days overdue"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            contract = line.contract_id
            if not line.next_pm_date and contract.pm_frequency_days:
                from datetime import timedelta
                line.next_pm_date = fields.Date.today() + timedelta(
                    days=contract.pm_frequency_days
                )
            if not line.remaining_visits:
                line.remaining_visits = contract.visits_per_year
        return lines


class AssetContractServiceVisit(models.Model):
    _name = "asset.contract.service.visit"
    _description = "AMC Service Visit Log"
    _order = "visit_date desc, id desc"
    _inherit = ["mail.thread"]

    contract_id = fields.Many2one(
        "asset.maintenance.contract", required=True, ondelete="cascade"
    )
    asset_id = fields.Many2one(
        "account.asset",
        string="Asset Visited",
        domain="[('id', 'in', contract_asset_ids)]",
    )
    contract_asset_ids = fields.Many2many(
        "account.asset",
        compute="_compute_contract_asset_ids",
        string="Contract Assets (domain helper)",
    )

    access_token = fields.Char(string="Portal Token", copy=False, readonly=True)
    portal_state = fields.Selection([
        ("pending", "Awaiting Contractor Report"),
        ("submitted", "Report Submitted"),
        ("reviewed", "Reviewed"),
    ], default="pending", tracking=True, string="Portal Status")

    labour_line_ids = fields.One2many(
        "asset.contract.visit.labour.line", "visit_id", string="Labour / Technicians"
    )
    material_line_ids = fields.One2many(
        "asset.contract.visit.material.line", "visit_id", string="Materials Used"
    )
    portal_attachment_ids = fields.Many2many(
        "ir.attachment",
        "contract_visit_portal_attachment_rel",
        "visit_id",
        "attachment_id",
        string="Contractor Documents",
    )
    total_labour_cost = fields.Float(compute="_compute_totals", store=True)
    total_material_cost = fields.Float(compute="_compute_totals", store=True)
    total_cost = fields.Float(compute="_compute_totals", store=True)

    contractor_notes = fields.Text(string="Contractor Notes / Remarks")
    submitted_by_name = fields.Char(string="Submitted By", readonly=True)
    submitted_on = fields.Datetime(string="Submitted On", readonly=True)

    @api.depends("labour_line_ids.subtotal", "material_line_ids.subtotal")
    def _compute_totals(self):
        for rec in self:
            rec.total_labour_cost = sum(rec.labour_line_ids.mapped("subtotal"))
            rec.total_material_cost = sum(rec.material_line_ids.mapped("subtotal"))
            rec.total_cost = rec.total_labour_cost + rec.total_material_cost

    def _get_or_create_portal_token(self):
        self.ensure_one()
        if not self.access_token:
            import uuid
            self.access_token = str(uuid.uuid4())
        return self.access_token

    def action_send_contractor_portal_link(self):
        self.ensure_one()
        token = self._get_or_create_portal_token()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        portal_url = f"{base_url}/contract/visit/{self.id}/report?token={token}"

        vendor = self.contract_id.vendor_id
        if not vendor or not vendor.email:
            raise UserError(_("Please set a Service Provider with an email on the contract before sending."))

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:#1a1a2e;padding:24px 32px;">
                <h2 style="color:#ffffff;margin:0;">Service Visit Report — Submission Required</h2>
            </div>
            <div style="padding:24px 32px;background-color:#ffffff;">
                <p style="color:#333;font-size:15px;">Dear {vendor.name},</p>
                <p style="color:#333;font-size:15px;">
                    Please submit your service visit report for the following job:
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:35%;border:1px solid #ddd;">Contract</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.contract_id.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Visit Date</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.visit_date}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.asset_id.name if self.asset_id else 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Visit Type</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{dict(self._fields['visit_type'].selection).get(self.visit_type, self.visit_type)}</td>
                    </tr>
                </table>
                <div style="text-align:center;margin:28px 0;">
                    <a href="{portal_url}"
                       style="background-color:#1a1a2e;color:white;padding:12px 28px;
                              border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                        Submit Visit Report
                    </a>
                </div>
                <p style="color:#888;font-size:13px;text-align:center;">
                    This link is unique to this visit. Please do not share it.
                </p>
            </div>
            <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                <p style="color:#aaa;font-size:12px;margin:0;">
                    Automated notification — Asset Management System.
                </p>
            </div>
        </div>"""

        self.env["mail.mail"].sudo().create({
            "subject": f"Submit Service Report — {self.contract_id.name} / {self.visit_date}",
            "body_html": body_html,
            "email_to": vendor.email,
            "author_id": self.env.user.partner_id.id,
            "auto_delete": False,
            "state": "outgoing",
        }).send(raise_exception=False)

        self.message_post(
            body=_(
                "Contractor portal link sent to <b>%(name)s</b> (%(email)s).<br/>"
                "URL: <a href='%(url)s'>%(url)s</a>",
                name=vendor.name, email=vendor.email, url=portal_url,
            ),
            subtype_xmlid="mail.mt_note",
        )
        self.write({"portal_state": "pending"})

    @api.depends("contract_id", "contract_id.contract_line_ids.asset_id")
    def _compute_contract_asset_ids(self):
        for rec in self:
            if rec.contract_id:
                rec.contract_asset_ids = rec.contract_id.contract_line_ids.mapped("asset_id")
            else:
                rec.contract_asset_ids = self.env["account.asset"]

    visit_date = fields.Date(
        required=True, default=fields.Date.today, tracking=True
    )
    visit_type = fields.Selection([
        ("preventive", "Preventive Maintenance"),
        ("corrective", "Corrective / Repair"),
        ("inspection", "Inspection"),
        ("emergency", "Emergency"),
        ("warranty", "Warranty Claim"),
    ], required=True, default="preventive", tracking=True)

    technician_name = fields.Char(string="Technician / Engineer")
    technician_company = fields.Char(
        string="From Company",
        help="Name of the service provider's engineer or subcontractor company.",
    )

    status = fields.Selection([
        ("scheduled", "Scheduled"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ], default="scheduled", tracking=True)

    work_done = fields.Text(
        string="Work Carried Out",
        help="Describe the maintenance work performed during this visit.",
    )
    findings = fields.Text(
        string="Findings / Observations",
        help="Any issues observed, recommendations, or follow-up actions.",
    )
    parts_used = fields.Text(
        string="Parts / Materials Used",
        help="List any spare parts or consumables used during this visit.",
    )
    is_covered_under_contract = fields.Boolean(
        string="Covered Under Contract",
        default=True,
        help="Uncheck if this visit falls outside contract scope and will be billed.",
    )
    billing_note = fields.Char(
        string="Billing Note",
        help="Reason for billing if not covered, or reference to invoice.",
        invisible="is_covered_under_contract",
    )

    next_visit_date = fields.Date(string="Next Visit Recommended")
    report_ref = fields.Char(string="Service Report Reference")

    attachment_count = fields.Integer(compute="_compute_attachment_count")

    @api.depends()
    def _compute_attachment_count(self):
        Attach = self.env["ir.attachment"]
        for rec in self:
            rec.attachment_count = Attach.search_count([
                ("res_model", "=", self._name),
                ("res_id", "=", rec.id),
            ])

    def action_mark_reviewed(self):
        self.ensure_one()
        self.write({"portal_state": "reviewed"})
        self.message_post(
            body=_("Contractor report reviewed by <b>%s</b>.") % self.env.user.name,
            subtype_xmlid="mail.mt_note",
        )

    def action_mark_completed(self):
        self.write({"status": "completed"})
        self.message_post(
            body=_("Visit marked as completed by %s.") % self.env.user.name
        )
        if self.asset_id and self.contract_id:
            line = self.env["asset.maintenance.contract.line"].search([
                ("contract_id", "=", self.contract_id.id),
                ("asset_id", "=", self.asset_id.id),
            ], limit=1)
            if line:
                from datetime import timedelta
                line.last_pm_date = self.visit_date
                freq = self.contract_id.pm_frequency_days
                if freq:
                    line.next_pm_date = self.visit_date + timedelta(days=freq)
                if line.remaining_visits > 0:
                    line.remaining_visits -= 1

    def action_view_attachments(self):
        self.ensure_one()
        return {
            "name": _("Attachments"),
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "view_mode": "list,form",
            "domain": [("res_model", "=", self._name), ("res_id", "=", self.id)],
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
            },
        }


class AssetContractVisitLabourLine(models.Model):
    _name = "asset.contract.visit.labour.line"
    _description = "Service Visit — Labour Line"

    visit_id = fields.Many2one("asset.contract.service.visit", required=True, ondelete="cascade")
    technician = fields.Char(string="Technician Name", required=True)
    role = fields.Char(string="Role / Skill")
    hours = fields.Float(string="Hours", default=1.0)
    hourly_rate = fields.Float(string="Rate / Hour")
    subtotal = fields.Float(compute="_compute_subtotal", store=True)

    @api.depends("hours", "hourly_rate")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.hours * rec.hourly_rate


class AssetContractVisitMaterialLine(models.Model):
    _name = "asset.contract.visit.material.line"
    _description = "Service Visit — Material Line"

    visit_id = fields.Many2one("asset.contract.service.visit", required=True, ondelete="cascade")
    description = fields.Char(string="Item / Material", required=True)
    quantity = fields.Float(default=1.0)
    unit = fields.Char(string="Unit", default="pcs")
    unit_price = fields.Float(string="Unit Price")
    subtotal = fields.Float(compute="_compute_subtotal", store=True)

    @api.depends("quantity", "unit_price")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.quantity * rec.unit_price


class AssetContractSparePartLine(models.Model):
    _name = "asset.contract.spare.part"
    _description = "Contract Spare Parts"

    contract_line_id = fields.Many2one(
        "asset.maintenance.contract.line", required=True, ondelete="cascade"
    )

    product_id = fields.Many2one("product.product", required=True)
    min_qty = fields.Float(string="Min Qty", default=0)
    qty_per_maintenance = fields.Float(string="Qty / Visit", default=1)
    consumed_qty = fields.Float(string="Consumed Qty", default=0, readonly=True)
    stock_qty = fields.Float(string="In Stock", compute="_compute_stock_qty")
    usage_type = fields.Selection([
        ('preventive', 'Preventive'),
        ('corrective', 'Corrective'),
        ('both', 'Both'),
    ], default='preventive', string="Usage Type")

    @api.depends('product_id')
    def _compute_stock_qty(self):
        for rec in self:
            rec.stock_qty = rec.product_id.qty_available


class AssetMaintenanceTask(models.Model):
    _name = "asset.maintenance.task"
    _description = "Asset Maintenance Task"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    asset_id = fields.Many2one("account.asset", required=False)
    plan_id = fields.Many2one("asset.maintenance.plan")
    sale_order_id = fields.Many2one("sale.order")

    request_date = fields.Datetime(default=fields.Datetime.now)
    scheduled_date = fields.Datetime()
    done_date = fields.Datetime()
    description = fields.Text()

    contract_id = fields.Many2one("asset.maintenance.contract")
    contract_line_id = fields.Many2one("asset.maintenance.contract.line")
    is_upcoming = fields.Boolean(compute="_compute_due_colors")
    is_overdue = fields.Boolean(compute="_compute_due_colors")
    due_badge = fields.Char(compute="_compute_due_badge")

    is_free = fields.Boolean(default=False)

    picking_ids = fields.One2many(
        'stock.picking',
        'maintenance_task_id',
        string="Delivery Orders"
    )

    def action_open_delivery_orders(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Delivery Orders",
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("maintenance_task_id", "=", self.id)],
            "context": {"default_maintenance_task_id": self.id},
        }

    @api.depends("scheduled_date")
    def _compute_due_badge(self):
        today = date.today()
        for rec in self:
            if not rec.scheduled_date:
                rec.due_badge = "—"
                continue

            diff = (rec.scheduled_date.date() - today).days

            if diff > 5:
                rec.due_badge = f"Scheduled ({diff} days)"
            elif 0 < diff <= 5:
                rec.due_badge = f"Due in {diff} days"
            elif diff == 0:
                rec.due_badge = "Due Today"
            else:
                rec.due_badge = f"Overdue {abs(diff)} days"

    helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Helpdesk Ticket",
        readonly=True
    )

    consumed_part_line_ids = fields.One2many(
        "asset.consumed.part.line",
        "task_id",
        string="Consumed Parts"
    )

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        string="Maintenance Team"
    )

    maintenance_type = fields.Selection([
        ("preventive", "Preventive"),
        ("corrective", "Corrective / Ad-hoc"),
    ], default="preventive")

    assigned_user_id = fields.Many2one("hr.employee")

    state = fields.Selection([
        ("draft", "New"),
        ("assigned", "Job Order Assigned"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("cancel", "Cancelled"),
    ], default="draft", tracking=True)

    job_order_id = fields.Many2one(
        "asset.job.order", string="Job Order", readonly=True
    )
    job_order_count = fields.Integer(compute="_compute_job_order_count")

    planned_material_ids = fields.One2many(
        "asset.task.material.line", "task_id", string="Materials Required"
    )
    material_approval_state = fields.Selection([
        ("not_requested", "Not Required"),
        ("pending", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ], default="not_requested", string="Material Approval", tracking=True)
    material_approved_by = fields.Many2one("res.users", readonly=True)
    material_approved_date = fields.Datetime(readonly=True)

    planned_labour_ids = fields.One2many(
        "asset.task.labour.line", "task_id", string="Labour Plan"
    )
    planned_hours = fields.Float(string="Planned Hours")
    actual_hours = fields.Float(string="Actual Hours", readonly=True)

    task_finding_ids = fields.One2many(
        "asset.task.finding.line", "task_id", string="Findings"
    )

    supervisor_notes = fields.Text(string="Supervisor Notes")

    @api.depends("job_order_id")
    def _compute_job_order_count(self):
        for rec in self:
            rec.job_order_count = 1 if rec.job_order_id else 0

    def action_request_material_approval(self):
        self.ensure_one()
        if not self.planned_material_ids:
            raise UserError(_("No materials listed to request approval for."))
        self.material_approval_state = "pending"
        self.message_post(
            body=_("Material approval requested by %s") % self.env.user.name
        )

    def action_view_team_schedule(self):
        self.ensure_one()
        if not self.maintenance_team_id:
            raise UserError(_("Please assign a Maintenance Team first."))
        return {
            "name": _("Team Schedule — %s") % self.maintenance_team_id.name,
            "type": "ir.actions.act_window",
            "res_model": "asset.team.schedule.viewer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_maintenance_team_id": self.maintenance_team_id.id,
            },
        }

    def action_approve_materials(self):
        self.ensure_one()
        self.write({
            "material_approval_state": "approved",
            "material_approved_by": self.env.user.id,
            "material_approved_date": fields.Datetime.now(),
        })
        for line in self.planned_material_ids:
            line.approval_state = "approved"
        self.message_post(
            body=_("Materials approved by %s") % self.env.user.name
        )

    def action_reject_materials(self):
        self.ensure_one()
        self.material_approval_state = "rejected"
        self.message_post(
            body=_("Materials rejected by %s") % self.env.user.name
        )

    def action_create_job_order(self):
        self.ensure_one()
        if self.job_order_id:
            return self._open_job_order()

        finding_vals = []
        for f in self.task_finding_ids:
            finding_vals.append((0, 0, {
                "description": f.description,
                "severity": f.severity,
                "status": "pending",
                "source_finding_id": f.source_finding_id.id if f.source_finding_id else False,
                "is_from_inspection": f.is_from_inspection,
                "image_1": f.image_1,
                "image_2": f.image_2,
                "image_3": f.image_3,
            }))

        material_vals = []
        for m in self.planned_material_ids:
            material_vals.append((0, 0, {
                "product_id": m.product_id.id,
                "description": m.description,
                "quantity_requested": m.quantity,
                "source": m.source,
            }))

        job = self.env["asset.job.order"].create({
            "maintenance_task_id": self.id,
            "description": self.description or "",
            "finding_line_ids": finding_vals,
            "material_line_ids": material_vals,
            "start_datetime": self.scheduled_date,
        })
        self.job_order_id = job.id
        self.state = "assigned"

        self.action_notify_job_order_issued()

        # Migrate flat checklist lines from the task into the job order
        if self.job_type == "flat":
            job.action_load_checklist_to_job_order()

        # Migrate PM checklist (for preventive tasks with a service checklist)
        if self.checklist_line_ids:
            job.action_copy_pm_checklist_from_task()

        return self._open_job_order()

    def _open_job_order(self):
        return {
            "name": _("Job Order"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.order",
            "view_mode": "form",
            "res_id": self.job_order_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_view_job_order(self):
        self.ensure_one()
        return self._open_job_order()

    @api.model_create_multi
    def create(self, vals_list):

        if self._name != "asset.maintenance.task":
            return super().create(vals_list)

        tasks = super().create(vals_list)

        for rec in tasks:

            if not rec.responsible_employee_id:
                rule = self.env["asset.task.assignment.rule"].find_matching_rule(rec)
                if rule:
                    rec.responsible_employee_id = rule.responsible_employee_id.id
                    rule.sudo().write({"match_count": rule.match_count + 1})
                    rec.message_post(
                        body=_(
                            "Responsible auto-assigned to <b>%(name)s</b> "
                            "via rule <i>%(rule)s</i>.",
                            name=rule.responsible_employee_id.name,
                            rule=rule.name,
                        ),
                        subtype_xmlid="mail.mt_note",
                    )
                    # Send assignment notification email
                    rec.action_notify_responsible_assigned()

            if rec.name.startswith("Maintenance from Helpdesk -"):
                ticket_name = rec.name.replace("Maintenance from Helpdesk -", "").strip()
                ticket = self.env['helpdesk.ticket'].search([('name', '=', ticket_name)], limit=1)

                if ticket:
                    _logger.info("Auto-linking maintenance task to helpdesk ticket: %s", ticket.id)
                    rec.helpdesk_ticket_id = ticket.id
                    ticket.maintenance_task_id = rec.id

            if rec.maintenance_type == "preventive":

                line = rec.contract_line_id or rec.env[
                    "asset.maintenance.contract.line"
                ].search([("asset_id", "=", rec.asset_id.id)], limit=1)

                if line:
                    rec.contract_line_id = line.id
                    rec.contract_id = line.contract_id.id

                    if line.remaining_visits > 0:
                        line.remaining_visits -= 1

                    line.next_pm_date = fields.Date.today() + timedelta(
                        days=line.contract_id.pm_frequency_days
                    )

            ticket_id = self.env.context.get("helpdesk_ticket_id")
            if ticket_id:
                ticket = rec.env["helpdesk.ticket"].browse(ticket_id)
                ticket.maintenance_task_id = rec.id
                rec.helpdesk_ticket_id = ticket.id

                stage = rec.env["helpdesk.stage"].search(
                    [("name", "ilike", "in progress")],
                    limit=1
                )
                if stage:
                    ticket.stage_id = stage.id

            if rec.asset_id and rec.maintenance_type:
                matching_parts = rec.asset_id.spare_part_line_ids.filtered(
                    lambda p: p.usage_type in [rec.maintenance_type, "both"]
                )

                lines = []
                for part in matching_parts:
                    lines.append((0, 0, {
                        "product_id": part.product_id.id,
                        "quantity": part.quantity,
                        "usage_type": part.usage_type,
                    }))

                rec.consumed_part_line_ids = lines

        return tasks

    @api.depends("scheduled_date", "state")
    def _compute_due_badge(self):
        today = date.today()
        for rec in self:

            if rec.state == "done":
                rec.due_badge = "Completed"
                continue

            if not rec.scheduled_date:
                rec.due_badge = "—"
                continue

            diff = (rec.scheduled_date.date() - today).days

            if diff > 5:
                rec.due_badge = f"Scheduled ({diff} days)"
            elif 0 < diff <= 5:
                rec.due_badge = f"Due in {diff} days"
            elif diff == 0:
                rec.due_badge = "Due Today"
            else:
                rec.due_badge = f"Overdue {abs(diff)} days"

    @api.depends("scheduled_date")
    def _compute_due_colors(self):
        today = date.today()
        for rec in self:
            if not rec.scheduled_date:
                rec.is_upcoming = False
                rec.is_overdue = False
                continue

            diff = (rec.scheduled_date.date() - today).days

            rec.is_upcoming = 0 < diff <= 5
            rec.is_overdue = diff < 0

    def action_set_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_done(self):
        self.write({"state": "done", "done_date": fields.Datetime.now()})
        for task in self:
            task._handle_done_side_effects()
        return True

    def _handle_done_side_effects(self):
        task = self

        if task.helpdesk_ticket_id:
            solved_stage = task.env['helpdesk.stage'].search(
                [('name', 'ilike', 'solved')], limit=1
            )
            if solved_stage:
                task.helpdesk_ticket_id.stage_id = solved_stage.id
            else:
                close_stage = task.helpdesk_ticket_id.team_id.close_stage_id
                if close_stage:
                    task.helpdesk_ticket_id.stage_id = close_stage.id

        if task.maintenance_type == "preventive":
            from datetime import timedelta
            asset = task.asset_id
            freq = asset.pm_frequency_days or 0
            done_date = task.done_date.date() if task.done_date else fields.Date.today()
            asset.last_pm_date = done_date
            if freq > 0:
                asset.next_pm_date = done_date + timedelta(days=freq)
            line = task.contract_line_id
            if line:
                if freq > 0:
                    line.next_pm_date = done_date + timedelta(days=freq)
                if line.remaining_visits > 0:
                    line.remaining_visits -= 1

        if task.consumed_part_line_ids:
            picking_type = task.env.ref('stock.picking_type_out')
            picking = task.env['stock.picking'].create({
                "partner_id": task.asset_id.customer_id.id,
                "location_id": task.asset_id.location_id.id,
                "location_dest_id": task.env.ref('stock.stock_location_customers').id,
                "picking_type_id": picking_type.id,
                "origin": f"MAINT-{task.asset_id.name}-{task.name}",
                "maintenance_task_id": task.id,
            })
            for line in task.consumed_part_line_ids:
                task.env['stock.move'].create({
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.quantity,
                    "product_uom": line.product_id.uom_id.id,
                    "picking_id": picking.id,
                })
            picking.action_confirm()
            picking.action_assign()

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_set_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    @api.model
    def cron_generate_contract_pm(self):
        today = fields.Date.today()
        warning_window = 5  # days before due date

        lines = self.env["asset.maintenance.contract.line"].search([])

        for line in lines:
            contract = line.contract_id
            asset = line.asset_id

            if not (contract.start_date <= today <= contract.end_date):
                continue

            last_date = asset.last_pm_date or asset.purchase_date

            if not last_date:
                last_date = today - timedelta(days=contract.pm_frequency_days)

            due_date = last_date + timedelta(days=contract.pm_frequency_days)
            upcoming_date = due_date - timedelta(days=warning_window)

            if todays_date := today >= upcoming_date and today < due_date:
                existing = self.search([
                    ("asset_id", "=", asset.id),
                    ("maintenance_type", "=", "preventive"),
                    ("scheduled_date", "=", upcoming_date)
                ])
                if not existing:
                    self.create({
                        "name": _("Upcoming PM (Due Soon) - %s") % asset.name,
                        "asset_id": asset.id,
                        "maintenance_type": "preventive",
                        "scheduled_date": upcoming_date,
                        "description": "PM due in 5 days.",
                        "is_free": True,
                        "contract_id": contract.id,
                        "contract_line_id": line.id,
                    })

            if today >= due_date:
                existing_due = self.search([
                    ("asset_id", "=", asset.id),
                    ("maintenance_type", "=", "preventive"),
                    ("scheduled_date", "=", due_date)
                ])
                if not existing_due:
                    self.create({
                        "name": _("PM Visit - %s") % asset.name,
                        "asset_id": asset.id,
                        "maintenance_type": "preventive",
                        "scheduled_date": due_date,
                        "description": "PM is now due or overdue.",
                        "is_free": True,
                        "contract_id": contract.id,
                        "contract_line_id": line.id,
                    })

                    asset.last_pm_date = due_date

                    line.next_pm_date = due_date + timedelta(days=contract.pm_frequency_days)

                    if line.remaining_visits > 0:
                        line.remaining_visits -= 1

    @api.onchange('asset_id', 'maintenance_type')
    def _onchange_load_spare_parts(self):

        if not self.asset_id or not self.maintenance_type:
            return

        self.consumed_part_line_ids = [(5, 0, 0)]

        matching_parts = self.asset_id.spare_part_line_ids.filtered(
            lambda p: p.usage_type == self.maintenance_type
        )

        lines = []
        for part in matching_parts:
            lines.append((
                0, 0,
                {
                    "product_id": part.product_id.id,
                    "quantity": part.quantity,
                    "usage_type": part.usage_type,
                }
            ))

        self.consumed_part_line_ids = lines

    responsible_employee_id = fields.Many2one(
        "hr.employee",
        string="Responsible",
        tracking=True,
        help="Employee responsible for following up and creating the job order.",
    )
    responsible_email = fields.Char(
        string="Responsible Email",
        compute="_compute_responsible_email",
        store=True,
    )

    @api.depends("responsible_employee_id")
    def _compute_responsible_email(self):
        for rec in self:
            emp = rec.responsible_employee_id
            rec.responsible_email = emp.work_email or "" if emp else ""

    def _send_responsible_email(self, subject, body_html):
        """Send email to the responsible employee. Silently skips if no email."""
        self.ensure_one()
        email = self.responsible_email
        if not email:
            return
        mail = self.env["mail.mail"].sudo().create({
            "subject": subject,
            "body_html": body_html,
            "email_to": email,
            "author_id": self.env.user.partner_id.id,
            "auto_delete": False,
            "state": "outgoing",
        })
        mail.send(raise_exception=False)

    def _build_task_email_body(self, heading, intro_line, color="#1a1a2e", extra_rows=""):
        """Shared HTML template for all task notification emails."""
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref(
            "asset_management.action_asset_maintenance_task", raise_if_not_found=False
        )
        action_id = action.id if action else "asset_maintenance_task"
        task_url = f"{base}/odoo/action-{action_id}/{self.id}"

        responsible_name = (
            self.responsible_employee_id.name if self.responsible_employee_id else "—"
        )
        scheduled = str(self.scheduled_date)[:16] if self.scheduled_date else "Not set"
        request = str(self.request_date)[:16] if self.request_date else "—"

        return f"""
        <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:{color};padding:22px 30px;">
                <h2 style="color:#fff;margin:0;">{heading}</h2>
            </div>
            <div style="padding:24px 30px;background:#fff;">
                <p style="color:#333;font-size:15px;">{intro_line}</p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:38%;border:1px solid #ddd;">Task</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.asset_id.name if self.asset_id else '—'}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Type</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{dict(self._fields['maintenance_type'].selection).get(self.maintenance_type, '—')}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Requested</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{request}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Scheduled Date</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{scheduled}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Responsible</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{responsible_name}</td>
                    </tr>
                    {extra_rows}
                </table>
                <div style="text-align:center;margin:24px 0;">
                    <a href="{task_url}"
                       style="background-color:{color};color:white;padding:12px 28px;
                              border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                        Open Maintenance Task
                    </a>
                </div>
            </div>
            <div style="background:#f5f5f5;padding:12px 30px;text-align:center;">
                <p style="color:#aaa;font-size:12px;margin:0;">
                    Asset Management System — Automated Notification
                </p>
            </div>
        </div>"""

    def action_notify_responsible_assigned(self):
        """Call after writing responsible_employee_id to notify them."""
        self.ensure_one()
        if not self.responsible_email:
            return
        emp_name = self.responsible_employee_id.name
        body = self._build_task_email_body(
            heading="Maintenance Task Assigned to You",
            intro_line=f"Hello <b>{emp_name}</b>, you have been assigned as responsible for the following maintenance task.",
            color="#00695C",
        )
        self._send_responsible_email(
            subject=f"[Assigned] Maintenance Task — {self.name}",
            body_html=body,
        )
        self.message_post(
            body=_(
                "Assignment notification sent to responsible employee "
                "<b>%(name)s</b> (%(email)s).",
                name=emp_name,
                email=self.responsible_email,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def write(self, vals):
        old_responsible = {rec.id: rec.responsible_employee_id.id for rec in self}
        result = super().write(vals)

        # Re-run auto-assignment if team changed and no responsible set yet
        if "maintenance_team_id" in vals:
            for rec in self:
                if not rec.responsible_employee_id:
                    rule = self.env["asset.task.assignment.rule"].find_matching_rule(rec)
                    if rule:
                        rec.responsible_employee_id = rule.responsible_employee_id.id
                        rule.sudo().write({"match_count": rule.match_count + 1})
                        rec.message_post(
                            body=_(
                                "Responsible auto-assigned to <b>%(name)s</b> "
                                "via rule <i>%(rule)s</i> (triggered by team change).",
                                name=rule.responsible_employee_id.name,
                                rule=rule.name,
                            ),
                            subtype_xmlid="mail.mt_note",
                        )
                        rec.action_notify_responsible_assigned()

        # Existing responsible-change notification logic
        if "responsible_employee_id" in vals:
            for rec in self:
                new_id = rec.responsible_employee_id.id
                if new_id and new_id != old_responsible.get(rec.id):
                    rec.action_notify_responsible_assigned()

        return result

    def action_notify_job_order_issued(self):
        """Call after job order is created to notify responsible."""
        self.ensure_one()
        if not self.responsible_email or not self.job_order_id:
            return
        emp_name = self.responsible_employee_id.name if self.responsible_employee_id else "Team"
        extra = f"""
            <tr>
                <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Job Order</td>
                <td style="padding:8px 12px;border:1px solid #ddd;">{self.job_order_id.name}</td>
            </tr>"""
        body = self._build_task_email_body(
            heading="Job Order Created — Task In Progress",
            intro_line=f"Hello <b>{emp_name}</b>, a job order has been issued for the maintenance task you are responsible for.",
            color="#1565C0",
            extra_rows=extra,
        )
        self._send_responsible_email(
            subject=f"[Job Order Issued] {self.name}",
            body_html=body,
        )

    @api.model
    def cron_send_task_reminders(self):
        """
        Daily cron — sends 4 types of notifications to responsible employees:

        1. No-action reminder  — task still draft 3+ days after request_date, no job order
        2. Pre-schedule reminder — scheduled_date is in 3 days, still no job order
        3. Overdue alert       — scheduled_date has passed, still draft/no job order
        4. (Assignment & job-order-issued are event-driven, not cron)
        """
        from datetime import datetime, timedelta
        today = fields.Datetime.now().date()
        three_days_ago = fields.Datetime.now() - timedelta(days=3)
        three_days_from_now_start = fields.Datetime.now() + timedelta(days=3)
        three_days_from_now_end = fields.Datetime.now() + timedelta(days=4)

        # ── Reminder 1: No action 3 days after request ──────────────────────────
        no_action_tasks = self.search([
            ("state", "=", "draft"),
            ("job_order_id", "=", False),
            ("request_date", "<=", three_days_ago),
            ("responsible_employee_id", "!=", False),
        ])
        for task in no_action_tasks:
            if not task.responsible_email:
                continue
            emp_name = task.responsible_employee_id.name
            days_waiting = (fields.Datetime.now() - task.request_date).days
            body = task._build_task_email_body(
                heading="⏰ Reminder: Maintenance Task Awaiting Action",
                intro_line=(
                    f"Hello <b>{emp_name}</b>, this task has been open for "
                    f"<b>{days_waiting} days</b> with no action taken. "
                    f"Please review and create a job order."
                ),
                color="#E65100",
            )
            task._send_responsible_email(
                subject=f"[Reminder] No Action — {task.name} ({days_waiting} days)",
                body_html=body,
            )
            task.message_post(
                body=_(
                    "No-action reminder sent to <b>%(name)s</b> — "
                    "%(days)d days since request with no job order.",
                    name=emp_name,
                    days=days_waiting,
                ),
                subtype_xmlid="mail.mt_note",
            )

        # ── Reminder 2: 3 days before scheduled date ─────────────────────────────
        pre_schedule_tasks = self.search([
            ("state", "in", ["draft", "assigned"]),
            ("job_order_id", "=", False),
            ("scheduled_date", ">=", three_days_from_now_start),
            ("scheduled_date", "<", three_days_from_now_end),
            ("responsible_employee_id", "!=", False),
        ])
        for task in pre_schedule_tasks:
            if not task.responsible_email:
                continue
            emp_name = task.responsible_employee_id.name
            body = task._build_task_email_body(
                heading="📅 Reminder: Job Order Due in 3 Days",
                intro_line=(
                    f"Hello <b>{emp_name}</b>, the scheduled date for this task is "
                    f"in <b>3 days</b> and no job order has been created yet. "
                    f"Please create the job order as soon as possible."
                ),
                color="#1565C0",
            )
            task._send_responsible_email(
                subject=f"[Reminder] Job Order Needed in 3 Days — {task.name}",
                body_html=body,
            )
            task.message_post(
                body=_(
                    "Pre-schedule reminder sent to <b>%(name)s</b> — "
                    "scheduled date is in 3 days, no job order yet.",
                    name=emp_name,
                ),
                subtype_xmlid="mail.mt_note",
            )

        # ── Reminder 3: Overdue — scheduled date passed, no job order ───────────
        overdue_tasks = self.search([
            ("state", "in", ["draft", "assigned"]),
            ("job_order_id", "=", False),
            ("scheduled_date", "<", fields.Datetime.now()),
            ("scheduled_date", "!=", False),
            ("responsible_employee_id", "!=", False),
        ])
        for task in overdue_tasks:
            if not task.responsible_email:
                continue
            emp_name = task.responsible_employee_id.name
            days_overdue = (fields.Datetime.now() - task.scheduled_date).days
            body = task._build_task_email_body(
                heading="🚨 Overdue: Maintenance Task Past Scheduled Date",
                intro_line=(
                    f"Hello <b>{emp_name}</b>, this task is <b>{days_overdue} day(s) overdue</b>. "
                    f"The scheduled date has passed and no job order has been created. "
                    f"Immediate action is required."
                ),
                color="#B71C1C",
            )
            task._send_responsible_email(
                subject=f"[OVERDUE] {days_overdue}d — {task.name}",
                body_html=body,
            )
            task.message_post(
                body=_(
                    "Overdue alert sent to <b>%(name)s</b> — "
                    "%(days)d day(s) past scheduled date, no job order.",
                    name=emp_name,
                    days=days_overdue,
                ),
                subtype_xmlid="mail.mt_note",
            )


class AssetTaskAssignmentRule(models.Model):
    _name = "asset.task.assignment.rule"
    _description = "Automatic Task Assignment Rule"
    _order = "sequence, id"

    name = fields.Char(string="Rule Name", required=True)
    sequence = fields.Integer(default=10, help="Lower sequence = higher priority.")
    active = fields.Boolean(default=True)

    # ── Matching Criteria ────────────────────────────────────────────────────
    maintenance_type = fields.Selection([
        ("preventive", "Preventive"),
        ("corrective", "Corrective / Ad-hoc"),
        ("any", "Any"),
    ], string="Maintenance Type", default="any", required=True)

    asset_category = fields.Selection([
        ("it", "IT Equipment"),
        ("electrical", "Electrical"),
        ("mechanical", "Mechanical"),
        ("hvac", "HVAC"),
        ("furniture", "Furniture"),
        ("other", "Other"),
    ], string="Asset Category", help="Leave empty to match any category.")

    asset_location_id = fields.Many2one(
        "asset.location",
        string="Asset Location",
        help="Leave empty to match any location.",
    )

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        string="Maintenance Team",
        help="Leave empty to match any team.",
    )

    job_type = fields.Selection([
        ("general", "General Job"),
        ("flat", "Flat Inspection"),
        ("any", "Any"),
    ], string="Job Type", default="any", required=True)

    # ── Assignment Output ────────────────────────────────────────────────────
    responsible_employee_id = fields.Many2one(
        "hr.employee",
        string="Assign Responsible",
        required=True,
        help="Employee to set as Responsible when this rule matches.",
    )

    # ── Description ─────────────────────────────────────────────────────────
    description = fields.Text(string="Notes / Description")

    match_count = fields.Integer(
        string="Times Matched",
        readonly=True,
        default=0,
        help="How many tasks have been auto-assigned using this rule.",
    )

    def _matches_task(self, task):
        """
        Return True if this rule matches the given task record.
        Criteria are ANDed — empty criteria fields are wildcards.
        """
        self.ensure_one()

        # Maintenance type
        if self.maintenance_type != "any":
            if task.maintenance_type != self.maintenance_type:
                return False

        # Job type
        if self.job_type != "any":
            job_type = getattr(task, "job_type", "general") or "general"
            if job_type != self.job_type:
                return False

        # Asset category
        if self.asset_category:
            asset_category = task.asset_id.asset_category if task.asset_id else False
            if asset_category != self.asset_category:
                return False

        # Asset location
        if self.asset_location_id:
            asset_location = (
                task.asset_id.location_id if task.asset_id else False
            )
            if asset_location != self.asset_location_id:
                return False

        # Maintenance team
        if self.maintenance_team_id:
            if task.maintenance_team_id != self.maintenance_team_id:
                return False

        return True

    @api.model
    def find_matching_rule(self, task):
        """
        Return the first active rule (by sequence) that matches the task,
        or an empty recordset if none match.
        """
        rules = self.search([("active", "=", True)], order="sequence asc, id asc")
        for rule in rules:
            if rule._matches_task(task):
                return rule
        return self.browse()

    def action_test_rule(self):
        """Preview how many existing tasks this rule would match."""
        self.ensure_one()
        tasks = self.env["asset.maintenance.task"].search([
            ("state", "not in", ["done", "cancel"]),
        ])
        matched = tasks.filtered(lambda t: self._matches_task(t))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": f"Rule: {self.name}",
                "message": (
                    f"This rule would match {len(matched)} active task(s)."
                    if matched else
                    "No active tasks currently match this rule."
                ),
                "type": "info" if matched else "warning",
                "sticky": False,
            },
        }


class AssetMaintenancePlan(models.Model):
    _name = "asset.maintenance.plan"
    _description = "Asset Maintenance Plan"

    name = fields.Char(required=True)
    asset_id = fields.Many2one("account.asset", required=False)
    frequency_value = fields.Integer(required=True)
    frequency_unit = fields.Selection([
        ("day", "Days"),
        ("week", "Weeks"),
        ("month", "Months"),
    ], default="month")
    active = fields.Boolean(default=True)


class AssetMaintenanceTeam(models.Model):
    _name = "asset.maintenance.team"
    _description = "Maintenance Team"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)

    team_leader_id = fields.Many2one(
        "hr.employee",
        string="Team Leader",
        required=True,
        tracking=True,
    )

    member_ids = fields.Many2many(
        "hr.employee",
        "maintenance_team_user_rel",
        "team_id",
        "employee_id",
        string="Team Members",
    )

    team_leader_email = fields.Char(
        string="Team Leader Email",
        help="Email address to notify when a task is assigned to this team. "
             "Does not require an Odoo user account.",
    )

    skill_set = fields.Text(string="Skills / Specializations")

    max_capacity = fields.Integer(
        string="Max Concurrent Tasks",
        default=5,
        help="Maximum tasks this team can work on at the same time"
    )

    active_tasks = fields.Integer(
        string="Active Tasks",
        compute="_compute_active_tasks",
        store=False,
    )

    availability_rate = fields.Float(
        string="Availability %",
        compute="_compute_availability",
        store=False
    )

    color = fields.Integer(string="Color Index")

    task_ids = fields.One2many(
        "asset.maintenance.task",
        "maintenance_team_id",
        string="Assigned Tasks"
    )

    @api.depends("task_ids.state")
    def _compute_active_tasks(self):
        for team in self:
            team.active_tasks = len(
                team.task_ids.filtered(lambda t: t.state in ["draft", "in_progress"])
            )

    @api.depends("active_tasks", "max_capacity")
    def _compute_availability(self):
        for team in self:
            if team.max_capacity:
                team.availability_rate = 100 - ((team.active_tasks / team.max_capacity) * 100)
            else:
                team.availability_rate = 0


class AssetContractSparePartWizard(models.TransientModel):
    _name = "asset.contract.spare.part.wizard"
    _description = "Wizard: Add/Edit Spare Parts for Contract Line"

    contract_line_id = fields.Many2one(
        "asset.maintenance.contract.line",
        required=True,
    )

    spare_part_ids = fields.One2many(
        "asset.contract.spare.part.wizard.line",
        "wizard_id",
        string="Spare Parts"
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = self.env.context.get("default_contract_line_id")

        if line_id:
            contract_line = self.env["asset.maintenance.contract.line"].browse(line_id)

            res["spare_part_ids"] = [
                (0, 0, {
                    "product_id": sp.product_id.id,
                    "min_qty": sp.min_qty,
                    "qty_per_maintenance": sp.qty_per_maintenance,
                }) for sp in contract_line.spare_part_ids
            ]

        return res

    def action_save(self):
        contract_line = self.contract_line_id

        contract_line.spare_part_ids.unlink()

        for line in self.spare_part_ids:
            self.env["asset.contract.spare.part"].create({
                "contract_line_id": contract_line.id,
                "product_id": line.product_id.id,
                "min_qty": line.min_qty,
                "qty_per_maintenance": line.qty_per_maintenance,
                "usage_type": line.usage_type,
            })

        return {"type": "ir.actions.act_window_close"}


class AssetContractSparePartWizardLine(models.TransientModel):
    _name = "asset.contract.spare.part.wizard.line"
    _description = "Wizard Line: Spare Part Entry"

    wizard_id = fields.Many2one("asset.contract.spare.part.wizard")

    product_id = fields.Many2one("product.product", required=True)
    min_qty = fields.Float(default=0)
    qty_per_maintenance = fields.Float(default=1)
    usage_type = fields.Selection([
        ('preventive', 'Preventive'),
        ('corrective', 'Corrective'),
    ], string="Usage Type", default='preventive')


class AssetMaintenanceTaskInspection(models.Model):
    _inherit = "asset.maintenance.task"

    property_id = fields.Many2one(
        related="helpdesk_ticket_id.property_id", store=True, string="Property"
    )
    inspection_type = fields.Selection(
        related="helpdesk_ticket_id.inspection_type", store=True,
        string="Inspection Type",
    )
