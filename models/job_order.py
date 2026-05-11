from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AssetJobOrder(models.Model):
    _name = "asset.job.order"
    _description = "Asset Job Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, readonly=True, default="New")

    maintenance_task_id = fields.Many2one(
        "asset.maintenance.task", required=True, ondelete="cascade"
    )
    asset_id = fields.Many2one(
        "account.asset", related="maintenance_task_id.asset_id",
        store=True, readonly=True,
    )
    inspection_id = fields.Many2one(
        "asset.inspection", related="maintenance_task_id.inspection_id",
        store=True, readonly=True,
    )
    helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket", related="maintenance_task_id.helpdesk_ticket_id",
        store=True, readonly=True,
    )
    assigned_user_id = fields.Many2one(
        "res.users", related="maintenance_task_id.assigned_user_id",
        store=True,
    )
    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        related="maintenance_task_id.maintenance_team_id",
        store=True,
    )
    scheduled_date = fields.Datetime(
        related="maintenance_task_id.scheduled_date", readonly=True,
    )
    # ── Approval signature ───────────────────────────────────────────────
    approved_by_name = fields.Char(string="Approved By (Signature)", readonly=True)
    approved_by_signature = fields.Binary(string="Approval Signature", readonly=True)

    state = fields.Selection([
        ("draft", "Draft"),
        ("request_material", "Material Requested"),
        ("material_approved", "Material Approved"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("approved", "Approved"),
        ("closed", "Closed"),
        ("rejected", "Rejected"),
    ], default="draft", tracking=True, string="Status")

    description = fields.Text()
    technician_notes = fields.Text(string="Technician Notes")
    supervisor_close_notes = fields.Text(string="Supervisor Close Notes")

    start_datetime = fields.Datetime(string="Work Started")
    end_datetime = fields.Datetime(string="Work Ended")
    total_hours = fields.Float(compute="_compute_total_hours", store=True)

    finding_line_ids = fields.One2many(
        "asset.job.finding.line", "job_order_id", string="Findings"
    )
    material_line_ids = fields.One2many(
        "asset.job.finding.material.line", "job_order_id", string="Materials Used"
    )
    technician_log_ids = fields.One2many(
        "asset.technician.log", "job_order_id", string="Time Logs"
    )

    findings_completion_rate = fields.Float(compute="_compute_completion_rate")

    # ── Approval / Close ─────────────────────────────────────────────────
    approved_by = fields.Many2one("res.users", readonly=True)
    approved_date = fields.Datetime(readonly=True)
    closed_by = fields.Many2one("res.users", readonly=True)
    closed_date = fields.Datetime(readonly=True)

    # ── Signature on close ───────────────────────────────────────────────
    closed_by_name = fields.Char(string="Closed By (Signature)", readonly=True)
    closed_by_signature = fields.Binary(string="Signature", readonly=True)

    # ── Material approval ────────────────────────────────────────────────
    material_approval_state = fields.Selection([
        ("not_requested", "Not Requested"),
        ("pending", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ], default="not_requested", string="Material Approval", tracking=True)
    material_approved_by = fields.Many2one("res.users", readonly=True)
    material_approved_date = fields.Datetime(readonly=True)

    def action_view_maintenance_task(self):
        self.ensure_one()
        return {
            "name": _("Maintenance Task"),
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.task",
            "view_mode": "form",
            "res_id": self.maintenance_task_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    @api.depends("start_datetime", "end_datetime", "technician_log_ids.hours")
    def _compute_total_hours(self):
        for rec in self:
            log_hours = sum(rec.technician_log_ids.mapped("hours"))
            if log_hours:
                rec.total_hours = log_hours
            elif rec.start_datetime and rec.end_datetime:
                rec.total_hours = (
                                          rec.end_datetime - rec.start_datetime
                                  ).total_seconds() / 3600
            else:
                rec.total_hours = 0.0

    @api.depends("finding_line_ids.status")
    def _compute_completion_rate(self):
        for rec in self:
            total = len(rec.finding_line_ids)
            if not total:
                rec.findings_completion_rate = 0.0
            else:
                done = len(rec.finding_line_ids.filtered(
                    lambda f: f.status in ("done", "cannot_fix")
                ))
                rec.findings_completion_rate = (done / total) * 100

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = (
                        self.env["ir.sequence"].next_by_code("asset.job.order")
                        or "JO-0001"
                )
        return super().create(vals_list)

    # ── Stage transitions ────────────────────────────────────────────────

    def action_request_material(self):
        """Open material request wizard."""
        self.ensure_one()
        return {
            "name": _("Request Materials"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.material.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_order_id": self.id},
        }

    def action_approve_materials(self):
        self.ensure_one()
        self.write({
            "state": "material_approved",
            "material_approval_state": "approved",
            "material_approved_by": self.env.user.id,
            "material_approved_date": fields.Datetime.now(),
        })
        for line in self.material_line_ids:
            line.approval_state = "approved"
        self.message_post(
            body=_("Materials approved by %s.") % self.env.user.name
        )

    def action_reject_materials(self):
        self.ensure_one()
        self.write({
            "material_approval_state": "rejected",
        })
        self.message_post(
            body=_("Materials rejected by %s.") % self.env.user.name
        )

    def action_start(self):
        """Start work — blocked if materials requested but not approved."""
        self.ensure_one()
        if (
                self.material_approval_state == "pending"
        ):
            raise UserError(_(
                "Materials are pending approval. "
                "Please wait for the storekeeper to approve before starting work."
            ))
        self.write({
            "state": "in_progress",
            "start_datetime": fields.Datetime.now(),
        })
        self.message_post(
            body=_("Work started by %s.") % self.env.user.name
        )

    def action_end_work(self):
        """Open completion wizard to record material consumption."""
        self.ensure_one()
        return {
            "name": _("End Work — Confirm Materials"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.complete.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_order_id": self.id},
        }

    def action_print_report(self):
        return self.env.ref(
            "asset_management.action_report_asset_job_order"
        ).report_action(self)

    def _do_end_work(self):
        """Called by completion wizard."""
        self.ensure_one()
        self.write({
            "state": "done",
            "end_datetime": fields.Datetime.now(),
        })
        self.message_post(
            body=_("Work completed by %s.") % self.env.user.name
        )

    def action_approve(self):
        """Open approval wizard for signature."""
        self.ensure_one()
        return {
            "name": _("Approve Job Order"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.approve.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_order_id": self.id},
        }

    def _do_approve(self, signed_by_name, signature=False):
        """Called by approval wizard after signature."""
        self.ensure_one()
        self.write({
            "state": "approved",
            "approved_by": self.env.user.id,
            "approved_date": fields.Datetime.now(),
            "approved_by_name": signed_by_name,
            "approved_by_signature": signature,
        })
        self.message_post(
            body=_(
                "Job Order approved and signed by <b>%s</b>."
            ) % signed_by_name
        )

    def action_reject(self):
        self.ensure_one()
        self.write({"state": "rejected"})
        self.message_post(
            body=_("Job Order rejected by %s — returned for revision.") % self.env.user.name
        )

    def action_view_helpdesk_ticket(self):
        self.ensure_one()
        if not self.helpdesk_ticket_id:
            raise UserError(_("No helpdesk ticket linked to this job order."))
        return {
            "name": _("Helpdesk Ticket"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "view_mode": "form",
            "res_id": self.helpdesk_ticket_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_view_inspection(self):
        self.ensure_one()
        if not self.inspection_id:
            raise UserError(_("No inspection linked to this job order."))
        return {
            "name": _("Inspection"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "form",
            "res_id": self.inspection_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_close(self):
        """Open close wizard for signature."""
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_("Job Order must be approved before closing."))
        return {
            "name": _("Close Job Order"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.close.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_order_id": self.id},
        }

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        readonly=True,
        copy=False,
    )
    invoice_state = fields.Selection(
        related="invoice_id.payment_state",
        string="Payment Status",
        readonly=True,
    )
    invoice_count = fields.Integer(
        compute="_compute_invoice_count",
        string="Invoices",
    )


    billing_partner_id = fields.Many2one(
        "res.partner",
        string="Billing Entity",
        help="Internal company or department to bill. Auto-filled from asset customer.",
    )
    labour_rate = fields.Float(
        string="Labour Rate / Hour",
        default=0.0,
        help="Agreed billing rate per hour. Used to compute labour line on invoice.",
    )
    additional_service_ids = fields.One2many(
        "asset.job.additional.service",
        "job_order_id",
        string="Additional Services",
    )
    can_create_invoice = fields.Boolean(
        compute="_compute_can_create_invoice",
        help="True when job is closed and no invoice exists yet.",
    )

    def _do_close(self, signed_by_name, signature=False):
        """Called by close wizard after signature."""
        self.ensure_one()
        self.write({
            "state": "closed",
            "closed_by": self.env.user.id,
            "closed_date": fields.Datetime.now(),
            "closed_by_name": signed_by_name,
            "closed_by_signature": signature,
        })

        # Push actual hours back to task
        task = self.maintenance_task_id
        if task and self.total_hours:
            task.actual_hours = self.total_hours

        # Close the maintenance task
        if task and task.state != "done":
            task.action_done()

        self.message_post(
            body=_(
                "Job Order closed and signed by <b>%s</b>."
            ) % signed_by_name
        )

    @api.depends("state", "invoice_id")
    def _compute_can_create_invoice(self):
        for rec in self:
            rec.can_create_invoice = rec.state in ("approved", "closed") and not rec.invoice_id

    def action_create_invoice(self):
        self.ensure_one()
        _logger.warning(
            "DEBUG invoice: partner=%s, labour_rate=%s, total_hours=%s, materials=%s",
            self.billing_partner_id or self.asset_id.customer_id,
            self.labour_rate,
            sum(self.technician_log_ids.mapped("hours")),
            [(m.product_id.name, m.quantity_used, m.is_consumable)
             for m in self.material_line_ids],
        )
        if self.state not in ("approved", "closed"):
            raise UserError(_("Invoice can only be created once the Job Order is approved or closed."))
        if self.invoice_id:
            raise UserError(_(
                "An invoice already exists for this Job Order: %s"
            ) % self.invoice_id.name)
        self._create_invoice()
        if self.invoice_id:
            return {
                "name": _("Invoice"),
                "type": "ir.actions.act_window",
                "res_model": "account.move",
                "view_mode": "form",
                "res_id": self.invoice_id.id,
                "views": [(False, "form")],
                "target": "current",
            }


    @api.depends("invoice_id")
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = 1 if rec.invoice_id else 0

    def _create_invoice(self):
        self.ensure_one()

        partner = (
                self.billing_partner_id
                or (self.asset_id.customer_id if self.asset_id else False)
                or (self.helpdesk_ticket_id.partner_id if self.helpdesk_ticket_id else False)
        )
        if not partner:
            self.message_post(
                body=_(
                    "⚠ Invoice could not be generated: no billing partner found. "
                    "Set the <b>Billing Entity</b> field and click <b>Create Invoice</b> again."
                )
            )
            _logger.warning(
                "asset.job.order %s: _create_invoice aborted — no billing partner.",
                self.name,
            )
            return

        invoice_lines = []

        job_hours = sum(self.technician_log_ids.mapped("hours"))

        inspection_hours = 0.0
        if self.inspection_id:
            labor_lines = getattr(self.inspection_id, "labor_ids", False)
            if labor_lines:
                inspection_hours = sum(labor_lines.mapped("hours"))

        total_hours = job_hours + inspection_hours

        if total_hours > 0 and self.labour_rate > 0:
            labour_product = self.env["product.product"].search(
                [("name", "ilike", "labour"), ("type", "=", "service")],
                limit=1,
            )
            description_parts = []
            if job_hours:
                description_parts.append("Job Order: %.2f hrs" % job_hours)
            if inspection_hours:
                description_parts.append("Inspection: %.2f hrs" % inspection_hours)

            invoice_lines.append((0, 0, {
                "name": "Labour Hours — " + " | ".join(description_parts),
                "product_id": labour_product.id if labour_product else False,
                "quantity": total_hours,
                "price_unit": self.labour_rate,
                "account_id": self._get_income_account(labour_product),
            }))
        elif total_hours > 0 and self.labour_rate == 0:
            _logger.info(
                "asset.job.order %s: %s total hours logged but labour_rate = 0; "
                "no labour line added to invoice.",
                self.name, total_hours,
            )

        for mat in self.material_line_ids.filtered(
                lambda l: l.is_consumable and l.quantity_used > 0
        ):
            invoice_lines.append((0, 0, {
                "name": mat.description or (mat.product_id.name if mat.product_id else "Material"),
                "product_id": mat.product_id.id if mat.product_id else False,
                "quantity": mat.quantity_used,
                "price_unit": mat.product_id.standard_price if mat.product_id else 0.0,
                "account_id": self._get_income_account(mat.product_id if mat.product_id else False),
            }))

        if self.inspection_id:
            inspection_materials = getattr(self.inspection_id, "material_ids", False)
            if inspection_materials:
                for mat in inspection_materials.filtered(
                        lambda l: getattr(l, "is_consumable", True)
                                  and l.state in ("consumed", "collected")
                                  and getattr(l, "qty_consumed", 0) > 0
                ):
                    invoice_lines.append((0, 0, {
                        "name": "[Inspection] %s" % (mat.product_id.name if mat.product_id else "Material"),
                        "product_id": mat.product_id.id if mat.product_id else False,
                        "quantity": mat.qty_consumed,
                        "price_unit": mat.product_id.standard_price if mat.product_id else 0.0,
                        "account_id": self._get_income_account(mat.product_id if mat.product_id else False),
                    }))

        for svc in self.additional_service_ids:
            invoice_lines.append((0, 0, {
                "name": svc.name,
                "product_id": svc.product_id.id if svc.product_id else False,
                "quantity": svc.quantity,
                "price_unit": svc.unit_price,
                "account_id": self._get_income_account(svc.product_id if svc.product_id else False),
            }))

        if not invoice_lines:
            self.message_post(
                body=_(
                    "⚠ No billable items found. Possible causes:<br/>"
                    "• Labour Rate is 0 (set it in the Billing group)<br/>"
                    "• No materials marked as consumed (Qty Used > 0)<br/>"
                    "• No additional services added<br/>"
                    "Fix the above and click <b>Create Invoice</b> again."
                )
            )
            _logger.warning(
                "asset.job.order %s: _create_invoice — no billable lines, invoice not created.",
                self.name,
            )
            return

        invoice_vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "invoice_date": fields.Date.today(),
            "ref": "Job Order: %s" % self.name,
            "narration": (
                             "Invoice for Job Order %(jo)s\\n"
                             "Asset: %(asset)s\\n"
                             "Closed by: %(closed_by)s\\n"
                             "Closed on: %(closed_date)s"
                         ) % {
                             "jo": self.name,
                             "asset": self.asset_id.name if self.asset_id else "N/A",
                             "closed_by": self.closed_by_name or (self.closed_by.name if self.closed_by else ""),
                             "closed_date": str(self.closed_date or ""),
                         },
            "invoice_line_ids": invoice_lines,
        }

        try:
            invoice = self.env["account.move"].create(invoice_vals)
            self.invoice_id = invoice.id
            self.message_post(
                body=_(
                    "Draft invoice <a href='/odoo/accounting/customer-invoices/%(id)d'>%(name)s</a> "
                    "created successfully. Review and confirm when ready."
                ) % {"id": invoice.id, "name": invoice.name}
            )
            _logger.info(
                "asset.job.order %s: draft invoice %s created.",
                self.name, invoice.name,
            )
        except Exception as e:
            _logger.error(
                "asset.job.order %s: failed to create invoice — %s",
                self.name, str(e),
            )
            raise UserError(
                _("Invoice creation failed: %s") % str(e)
            )

    def _get_income_account(self, product=False):
        if product and product.property_account_income_id:
            return product.property_account_income_id.id
        if product and product.categ_id and product.categ_id.property_account_income_categ_id:
            return product.categ_id.property_account_income_categ_id.id
        account = self.env["account.account"].search([
            ("account_type", "=", "income"),
            ("company_ids", "in", self.env.company.id),
            ("active", "=", True),
        ], limit=1)
        return account.id if account else False

    def action_view_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_("No invoice generated yet."))
        return {
            "name": _("Invoice"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.invoice_id.id,
            "views": [(False, "form")],
            "target": "current",
        }


class AssetJobFindingLine(models.Model):
    _name = "asset.job.finding.line"
    _description = "Job Order Finding Line"
    _order = "sequence, id"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)
    source_finding_id = fields.Many2one(
        "asset.inspection.finding", readonly=True
    )
    description = fields.Text(required=True)
    severity = fields.Selection([
        ("low", "Low"), ("medium", "Medium"),
        ("high", "High"), ("critical", "Critical"),
    ])
    status = fields.Selection([
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("cannot_fix", "Cannot Fix"),
    ], default="pending")
    technician_note = fields.Text()
    is_from_inspection = fields.Boolean(readonly=True, default=False)
    image_1 = fields.Image(max_width=1024, max_height=1024)
    image_2 = fields.Image(max_width=1024, max_height=1024)
    image_3 = fields.Image(max_width=1024, max_height=1024)


class AssetJobMaterialLine(models.Model):
    _name = "asset.job.finding.material.line"
    _description = "Job Order Material Line"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("product.product", required=True)
    description = fields.Char()
    quantity_requested = fields.Float(string="Qty Planned", default=1.0)
    quantity_used = fields.Float(string="Qty Used", default=0.0)
    uom_id = fields.Many2one(
        "uom.uom", related="product_id.uom_id", readonly=True
    )
    on_hand_qty = fields.Float(compute="_compute_on_hand")
    source = fields.Selection([
        ("task", "From Task Plan"),
        ("inspection", "From Inspection"),
        ("manual", "Added on Job"),
    ], default="manual", readonly=True)

    approval_state = fields.Selection([
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ], default="pending")

    is_consumable = fields.Boolean(
        string="Consumable",
        compute="_compute_is_consumable",
        store=True,
        help="Consumable = used up. Not consumable = tool/equipment to be returned.",
    )

    @api.depends("product_id")
    def _compute_is_consumable(self):
        for rec in self:
            if rec.product_id:
                rec.is_consumable = rec.product_id.is_consumable_in_inspection
            else:
                rec.is_consumable = True

    @api.depends("product_id")
    def _compute_on_hand(self):
        for rec in self:
            rec.on_hand_qty = rec.product_id.qty_available if rec.product_id else 0.0


class AssetTechnicianLog(models.Model):
    _name = "asset.technician.log"
    _description = "Technician Time Log"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, ondelete="cascade"
    )
    user_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user
    )
    date = fields.Date(default=fields.Date.today)
    start_time = fields.Float()
    end_time = fields.Float()
    hours = fields.Float(compute="_compute_hours", store=True)
    activity = fields.Char()

    @api.depends("start_time", "end_time")
    def _compute_hours(self):
        for rec in self:
            rec.hours = max(0.0, rec.end_time - rec.start_time)


class AssetTaskMaterialLine(models.Model):
    """Materials planned on the maintenance task — approval happens here."""
    _name = "asset.task.material.line"
    _description = "Task Material Line"

    task_id = fields.Many2one(
        "asset.maintenance.task", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("product.product", required=True)
    description = fields.Char()
    quantity = fields.Float(default=1.0)
    uom_id = fields.Many2one(
        "uom.uom", related="product_id.uom_id", readonly=True
    )
    on_hand_qty = fields.Float(compute="_compute_on_hand")
    approval_state = fields.Selection([
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ], default="pending")
    source = fields.Selection([
        ("task", "From Task Plan"),
        ("inspection", "From Inspection"),
        ("manual", "Added Manually"),
    ], default="manual")

    @api.depends("product_id")
    def _compute_on_hand(self):
        for rec in self:
            rec.on_hand_qty = rec.product_id.qty_available if rec.product_id else 0.0


class AssetTaskLabourLine(models.Model):
    """Planned labour on the maintenance task."""
    _name = "asset.task.labour.line"
    _description = "Task Labour Line"

    task_id = fields.Many2one(
        "asset.maintenance.task", required=True, ondelete="cascade"
    )
    user_id = fields.Many2one("res.users", string="Technician")
    role = fields.Char(string="Role / Skill")
    planned_hours = fields.Float()
    notes = fields.Char()


class AssetTaskFindingLine(models.Model):
    """Findings on the maintenance task — editable by supervisor, copied to job order."""
    _name = "asset.task.finding.line"
    _description = "Task Finding Line"
    _order = "sequence, id"

    task_id = fields.Many2one(
        "asset.maintenance.task", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)
    source_finding_id = fields.Many2one(
        "asset.inspection.finding", readonly=True
    )
    is_from_inspection = fields.Boolean(readonly=True, default=False)
    description = fields.Text(required=True)
    severity = fields.Selection([
        ("low", "Low"), ("medium", "Medium"),
        ("high", "High"), ("critical", "Critical"),
    ])
    image_1 = fields.Image(max_width=1024, max_height=1024)
    image_2 = fields.Image(max_width=1024, max_height=1024)
    image_3 = fields.Image(max_width=1024, max_height=1024)
    notes = fields.Text(string="Supervisor Notes")


class AssetJobAdditionalService(models.Model):
    """Manual billable items added by supervisor on the job order."""
    _name = "asset.job.additional.service"
    _description = "Job Order Additional Service"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, ondelete="cascade"
    )
    name = fields.Char(string="Description", required=True)
    product_id = fields.Many2one(
        "product.product",
        string="Service / Product",
        domain="[('type', 'in', ['service', 'consu'])]",
    )
    quantity = fields.Float(default=1.0)
    unit_price = fields.Float(string="Unit Price")
    subtotal = fields.Float(
        string="Subtotal", compute="_compute_subtotal", store=True
    )

    @api.depends("quantity", "unit_price")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.quantity * rec.unit_price
