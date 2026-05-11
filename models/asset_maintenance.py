from odoo import api, fields, models, _
from datetime import date, timedelta
import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
# -------------------------------------------------------------
# 1) Maintenance Contract
# -------------------------------------------------------------
class AssetMaintenanceContract(models.Model):
    _name = "asset.maintenance.contract"
    _description = "Maintenance Contract"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    customer_id = fields.Many2one("res.partner", required=True)
    start_date = fields.Date(required=True)
    end_date = fields.Date(required=True)

    contract_type = fields.Selection([
        ("pm_only", "PM Only"),
        ("pm_plus_corrective", "PM + Corrective"),
        ("all_inclusive", "All Inclusive"),
        ("pay_per_task", "Pay per Task"),
    ], default="pm_only", required=True)

    free_period_months = fields.Integer(default=0)
    free_parts = fields.Selection([
        ("none", "No Free Parts"),
        ("consumables", "Consumables Only"),
        ("all", "All Parts Included"),
    ], default="none")

    visits_per_year = fields.Integer(default=4)
    pm_frequency_days = fields.Integer(default=90)

    price_list_id = fields.Many2one("product.pricelist")
    is_active = fields.Boolean(default=True)

    contract_line_ids = fields.One2many(
        "asset.maintenance.contract.line", "contract_id"
    )


# -------------------------------------------------------------
# 2) Contract Line
# -------------------------------------------------------------
class AssetMaintenanceContractLine(models.Model):
    _name = "asset.maintenance.contract.line"
    _description = "Contract Asset Line"

    contract_id = fields.Many2one("asset.maintenance.contract", required=True)
    asset_id = fields.Many2one("account.asset", required=True)

    next_pm_date = fields.Date()
    remaining_visits = fields.Integer()
    remaining_free_parts_value = fields.Float()
    due_status = fields.Char(compute="_compute_due_status")
    spare_part_ids = fields.One2many(
        "asset.contract.spare.part",
        "contract_line_id",
        string="Spare Parts"
    )


    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)

        for line in lines:
            contract = line.contract_id
            asset = line.asset_id
            line._sync_spare_parts_to_asset()

            if contract.pm_frequency_days:
                asset.pm_frequency_days = contract.pm_frequency_days

            if not line.next_pm_date:
                line.next_pm_date = fields.Date.today() + timedelta(
                    days=contract.pm_frequency_days
                )

            if not line.remaining_visits:
                line.remaining_visits = contract.visits_per_year

        return lines

    @api.depends("next_pm_date")
    def _compute_due_status(self):
        today = fields.Date.today()
        for line in self:
            if not line.next_pm_date:
                line.due_status = "Not Scheduled"
                continue

            diff = (line.next_pm_date - today).days

            if diff > 5:
                line.due_status = f"Due in {diff} days"
            elif 0 < diff <= 5:
                line.due_status = f"⚠ Due in {diff} days"
            elif diff == 0:
                line.due_status = "⚠ Due Today"
            else:
                line.due_status = f"❌ {abs(diff)} days overdue"

    def write(self, vals):
        res = super().write(vals)
        self._sync_spare_parts_to_asset()
        for line in self:
            if "contract_id" in vals or "asset_id" in vals:
                contract = line.contract_id
                asset = line.asset_id

                if contract.pm_frequency_days:
                    asset.pm_frequency_days = contract.pm_frequency_days

        return res

    def _sync_spare_parts_to_asset(self):
        for line in self:
            asset = line.asset_id

            for part in line.spare_part_ids:

                asset_line = self.env["asset.spare.part.line"].search([
                    ("asset_id", "=", asset.id),
                    ("product_id", "=", part.product_id.id)
                ], limit=1)

                vals = {
                    "product_id": part.product_id.id,
                    "asset_id": asset.id,
                    "quantity": part.qty_per_maintenance,
                    "min_qty": part.min_qty,
                    "usage_type": part.usage_type,
                }

                if not asset_line:
                    self.env["asset.spare.part.line"].create(vals)
                else:
                    asset_line.write(vals)

    def action_open_spare_part_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Edit Spare Parts",
            "res_model": "asset.contract.spare.part.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_contract_line_id": self.id,
            },
        }

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
# -------------------------------------------------------------
# 3) Maintenance Task
# -------------------------------------------------------------
class AssetMaintenanceTask(models.Model):
    _name = "asset.maintenance.task"
    _description = "Asset Maintenance Task"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    asset_id = fields.Many2one("account.asset", required=True)
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

    # ── ADD THESE TWO ──
    inspection_id = fields.Many2one(
        "asset.inspection",
        string="Inspection",
        readonly=True,
        store=True,
    )
    
    inspection_count = fields.Integer(
        string="Inspections",
        compute="_compute_inspection_count",
    )

    def action_view_inspection(self):
        self.ensure_one()
        if not self.inspection_id:
            raise UserError(_("No inspection linked to this maintenance task."))
        return {
            "name": _("Inspection"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "form",
            "res_id": self.inspection_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    @api.depends("inspection_id")
    def _compute_inspection_count(self):
        for rec in self:
            rec.inspection_count = 1 if rec.inspection_id else 0

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

    assigned_user_id = fields.Many2one("res.users")

    state = fields.Selection([
        ("draft", "New"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("cancel", "Cancelled"),
    ], default="draft", tracking=True)

    # ── New fields to add to AssetMaintenanceTask ──────────────────────

    job_order_id = fields.Many2one(
        "asset.job.order", string="Job Order", readonly=True
    )
    job_order_count = fields.Integer(compute="_compute_job_order_count")

    # Planned material lines — supervisor fills these, approval happens here
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

    # Planned labour
    planned_labour_ids = fields.One2many(
        "asset.task.labour.line", "task_id", string="Labour Plan"
    )
    planned_hours = fields.Float(string="Planned Hours")
    actual_hours = fields.Float(string="Actual Hours", readonly=True)

    # Findings — copied from inspection, supervisor can add more
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
        """Supervisor creates job order after approvals — pushes everything across."""
        self.ensure_one()
        if self.job_order_id:
            return self._open_job_order()

        # ── Copy findings (from inspection + any added on task) ──────────
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

        # ── Copy approved materials only ─────────────────────────────────
        material_vals = []
        for m in self.planned_material_ids:
            material_vals.append((0, 0, {
                "product_id": m.product_id.id,
                "description": m.description,
                "quantity_requested": m.quantity,
                "source": m.source,
            }))

        # ── Copy labour plan → technician time log stubs ─────────────────
        technician_log_vals = []
        for l in self.planned_labour_ids:
            if not l.user_id:
                continue
            technician_log_vals.append((0, 0, {
                "user_id": l.user_id.id,
                "date": self.scheduled_date.date() if self.scheduled_date else fields.Date.today(),
                "start_time": 0.0,
                "end_time": 0.0,
                "activity": l.role or "",
            }))

        job = self.env["asset.job.order"].create({
            "maintenance_task_id": self.id,
            "description": self.description or "",
            "finding_line_ids": finding_vals,
            "material_line_ids": material_vals,
            "technician_log_ids": technician_log_vals,
            "start_datetime": self.scheduled_date,
        })
        self.job_order_id = job.id
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

            # If maintenance is completed → override everything
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


    def action_start(self):
        for rec in self:
            rec.state = "in_progress"

            contract_line = rec.contract_line_id or rec.env[
                "asset.maintenance.contract.line"
            ].search([("asset_id", "=", rec.asset_id.id)], limit=1)

            asset = rec.asset_id
            today = fields.Date.today()
            is_under_warranty = (
                asset.warranty_start_date
                and asset.warranty_end_date
                and asset.warranty_start_date <= today <= asset.warranty_end_date
            )

            if is_under_warranty:
                rec.is_free = True
                continue

            if contract_line:
                contract = contract_line.contract_id

                if contract.contract_type in ["all_inclusive", "pm_plus_corrective"]:
                    rec.is_free = True
                    continue

                order = rec.env["sale.order"].create({
                    "partner_id": contract.customer_id.id,
                })
                rec.sale_order_id = order.id


    def action_done(self):
        self.write({"state": "done", "done_date": fields.Datetime.now()})

        for task in self:

            _logger.info("=== MAINT TASK DONE DEBUG ===")
            _logger.info("Task ID: %s", task.id)
            _logger.info("Task Name: %s", task.name)
            _logger.info("Helpdesk Ticket Exists: %s", bool(task.helpdesk_ticket_id))

            if task.helpdesk_ticket_id:
                _logger.info("Helpdesk Ticket ID: %s", task.helpdesk_ticket_id.id)
                _logger.info("Current Ticket Stage: %s", task.helpdesk_ticket_id.stage_id.name)
            else:
                _logger.warning("No helpdesk ticket linked to this maintenance task.")

            if task.helpdesk_ticket_id:
                _logger.info("Searching for 'Solved' stage...")

                solved_stage = self.env['helpdesk.stage'].search([
                    ('name', 'ilike', 'solved')
                ], limit=1)

                if solved_stage:
                    _logger.info("Solved stage found: %s", solved_stage.name)
                    task.helpdesk_ticket_id.stage_id = solved_stage.id
                else:
                    _logger.warning("Solved stage NOT found. Using team close stage instead.")

                    close_stage = task.helpdesk_ticket_id.team_id.close_stage_id
                    if close_stage:
                        _logger.info("Close stage found: %s", close_stage.name)
                        task.helpdesk_ticket_id.stage_id = close_stage.id
                    else:
                        _logger.error("NO close stage configured for team!")

            if task.maintenance_type == "preventive":

                asset = task.asset_id
                freq = asset.pm_frequency_days or 0

                # 1) Last PM should be DONE DATE, not scheduled_date
                done_date = task.done_date.date() if task.done_date else fields.Date.today()
                asset.last_pm_date = done_date

                # 2) Next PM = done date + frequency
                if freq > 0:
                    asset.next_pm_date = done_date + timedelta(days=freq)

                # Update contract line
                line = task.contract_line_id
                if line:
                    if freq > 0:
                        line.next_pm_date = done_date + timedelta(days=freq)

                    if line.remaining_visits > 0:
                        line.remaining_visits -= 1



            if task.consumed_part_line_ids:
                picking_type = self.env.ref('stock.picking_type_out')

                picking = self.env['stock.picking'].create({
                    "partner_id": task.asset_id.customer_id.id,
                    "location_id": task.asset_id.location_id.id,
                    "location_dest_id": self.env.ref('stock.stock_location_customers').id,
                    "picking_type_id": picking_type.id,
                    "origin": f"MAINT-{task.asset_id.name}-{task.name}",
                    "maintenance_task_id": task.id,
                })

                for line in task.consumed_part_line_ids:
                    self.env['stock.move'].create({
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.quantity,
                        "product_uom": line.product_id.uom_id.id,
                        "picking_id": picking.id,
                    })

                picking.action_confirm()
                picking.action_assign()

                for task in self:
                    for line in task.consumed_part_line_ids:
                        contract_part = self.env["asset.contract.spare.part"].search([
                            ("contract_line_id", "=", task.contract_line_id.id),
                            ("product_id", "=", line.product_id.id)
                        ], limit=1)

                        if contract_part:
                            contract_part.consumed_qty += line.quantity

                            # Optional low stock warning
                            if (contract_part.stock_qty - contract_part.consumed_qty) < contract_part.min_qty:
                                task.message_post(
                                    body=f"⚠ Low Stock: {contract_part.product_id.display_name} is below minimum stock level."
                                )

        return True

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

            # Skip inactive or expired contract
            if not (contract.start_date <= today <= contract.end_date):
                continue

            # Determine last PM reference
            last_date = asset.last_pm_date or asset.purchase_date

            if not last_date:
                # fallback: assume PM should start today - frequency
                last_date = today - timedelta(days=contract.pm_frequency_days)

            due_date = last_date + timedelta(days=contract.pm_frequency_days)
            upcoming_date = due_date - timedelta(days=warning_window)

            # -----------------------
            # 1) Create upcoming PM task (5 days before)
            # -----------------------
            if todays_date := today >= upcoming_date and today < due_date:
                # Only create if not already created for this due period
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

            # -----------------------
            # 2) Create PM task when ACTUALLY due or overdue
            # -----------------------
            if today >= due_date:
                # Avoid duplicates
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

                    # Update asset last_pm
                    asset.last_pm_date = due_date

                    # Update next visit
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


class AssetMaintenancePlan(models.Model):
    _name = "asset.maintenance.plan"
    _description = "Asset Maintenance Plan"

    name = fields.Char(required=True)
    asset_id = fields.Many2one("account.asset", required=True)
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
        "res.users",
        string="Team Leader",
        required=True,
        tracking=True,
    )

    member_ids = fields.Many2many(
        "res.users",
        "maintenance_team_user_rel",
        "team_id",
        "user_id",
        string="Team Members",
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
        """Rewrite all spare parts for this contract line."""
        contract_line = self.contract_line_id

        # Remove old lines
        contract_line.spare_part_ids.unlink()

        # Create new ones
        for line in self.spare_part_ids:
            self.env["asset.contract.spare.part"].create({
                "contract_line_id": contract_line.id,
                "product_id": line.product_id.id,
                "min_qty": line.min_qty,
                "qty_per_maintenance": line.qty_per_maintenance,
                "usage_type": line.usage_type,
            })

        contract_line._sync_spare_parts_to_asset()

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

