from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError


class AssetInspection(models.Model):
    _name = "asset.inspection"
    _description = "Asset Inspection"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "scheduled_date desc"

    name = fields.Char(
        string="Reference",
        readonly=True,
        default=lambda self: _("New"),
        copy=False,
    )

    ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Helpdesk Ticket",
        readonly=True,
    )

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        required=False,
        tracking=True,
    )

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        string="Maintenance Team",
        tracking=True,
    )

    employee_ids = fields.Many2many(
        "hr.employee",
        "inspection_employee_rel",
        "inspection_id",
        "employee_id",
        string="Assigned Employees",
        tracking=True,
    )

    scheduled_date = fields.Datetime(
        string="Scheduled Date",
        required=True,
        tracking=True,
    )

    actual_date = fields.Datetime(
        string="Start Date",
        tracking=True,
    )

    completed_date = fields.Datetime(
        string="Completed Date",
        tracking=True,
    )

    escalation_required = fields.Boolean(
        string="Escalation Required",
        default=False,
        tracking=True,
    )
    resolution_notes = fields.Text(
        string="Resolution Notes",
        help="How the issue was resolved during inspection.",
    )
    escalation_notes = fields.Text(
        string="Escalation Notes",
        help="What corrective maintenance is needed.",
    )

    state = fields.Selection(
        [
            ("new", "New"),
            ("scheduled", "Scheduled"),
            ("request_material", "Request Material"),
            ("material_collected", "Material Collected"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("escalated", "Escalated — Action Required"),
            ("cancelled", "Cancelled"),
        ],
        default="new",
        tracking=True,
        string="Status",
    )

    finding_ids = fields.One2many(
        "asset.inspection.finding",
        "inspection_id",
        string="Findings",
    )

    material_ids = fields.One2many(
        "asset.inspection.material",
        "inspection_id",
        string="Parts & Materials",
    )

    labor_ids = fields.One2many(
        "asset.inspection.labor",
        "inspection_id",
        string="Labor Lines",
    )

    total_hours = fields.Float(
        string="Total Hours",
        compute="_compute_totals",
        store=True,
    )

    maintenance_task_id = fields.Many2one(
        "asset.maintenance.task",
        string="Resulting Maintenance Task",
        readonly=True,
    )

    pending_return_count = fields.Integer(
        string="Pending Returns",
        compute="_compute_pending_return_count",
        store=True,
    )

    notes = fields.Text(string="General Notes")

    billing_entity_id = fields.Many2one(
        "res.partner",
        string="Billing Entity",
        compute="_compute_billing_entity",
        store=True,
        readonly=False,
        tracking=True,
        help="Customer/company to be billed for this inspection. Pulled from the asset.",
    )

    @api.depends("asset_id", "asset_id.customer_id")
    def _compute_billing_entity(self):
        for rec in self:
            rec.billing_entity_id = rec.asset_id.customer_id if rec.asset_id else False

    @api.depends("labor_ids.hours")
    def _compute_totals(self):
        for rec in self:
            rec.total_hours = sum(rec.labor_ids.mapped("hours"))

    @api.depends("material_ids.state")
    def _compute_pending_return_count(self):
        for rec in self:
            rec.pending_return_count = len(
                rec.material_ids.filtered(lambda l: l.state == "pending_return")
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                        self.env["ir.sequence"].next_by_code("asset.inspection") or _("New")
                )
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        # If material lines were updated, check for any newly requested lines
        # that haven't been notified yet (no approved_by, no rejection_reason)
        if "material_ids" in vals:
            for rec in self:
                if rec.state in ("request_material", "material_collected", "in_progress", "escalated"):
                    new_requested = rec.material_ids.filtered(
                        lambda l: l.state == "requested"
                                  and not l.approved_by
                                  and not l.rejection_reason
                    )
                    if new_requested:
                        rec._send_additional_material_request_email(new_requested)
        return result

    def action_schedule(self):
        self.write({"state": "scheduled"})

    def action_request_material(self):
        self.ensure_one()
        return {
            "name": _("Request Materials"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection.material.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_inspection_id": self.id,
                "send_material_email": True,
            },
        }

    def action_material_collected(self):
        self.ensure_one()
        pending = self.material_ids.filtered(lambda l: l.state == "requested")
        if pending:
            raise UserError(_(
                "The following materials are still pending Storekeeper approval:\n%s"
            ) % "\n".join(f"- {l.product_id.name}" for l in pending))

        rejected_only = (
            all(l.state == "rejected" for l in self.material_ids)
            if self.material_ids else False
        )
        if rejected_only:
            raise UserError(_(
                "All material requests were rejected. "
                "Please request new materials or proceed without."
            ))

        approved = self.material_ids.filtered(lambda l: l.state == "approved")
        approved.action_mark_collected()

        self.write({"state": "material_collected"})

    def action_start(self):
        self.write({"state": "in_progress", "actual_date": fields.Datetime.now()})

    def action_cancel(self):
        reservable = self.material_ids.filtered(
            lambda l: l.state in ("requested", "approved", "collected")
                      and l.picking_id
                      and l.picking_id.state not in ("done", "cancel")
        )
        reservable._cancel_stock_reservation()
        self.write({"state": "cancelled"})

    def action_create_maintenance(self):
        self.ensure_one()

        finding_vals = []
        for f in self.finding_ids:
            finding_vals.append((0, 0, {
                "description": f.description,
                "severity": f.severity,
                "notes": f.notes,
                "source_finding_id": f.id,
                "is_from_inspection": True,
                "image_1": f.image if hasattr(f, "image") else False,
            }))

        employees = self.env["hr.employee"]

        if self.maintenance_team_id:
            team = self.maintenance_team_id
            if team.team_leader_id:
                leader_emp = self.env["hr.employee"].search(
                    [("user_id", "=", team.team_leader_id.id)], limit=1
                )
                if leader_emp:
                    employees |= leader_emp
            for member in team.member_ids:
                member_emp = self.env["hr.employee"].search(
                    [("user_id", "=", member.id)], limit=1
                )
                if member_emp:
                    employees |= member_emp

        employees |= self.employee_ids

        labour_vals = []
        for emp in employees:
            labour_vals.append((0, 0, {
                "user_id": emp.user_id.id if emp.user_id else False,
                "role": "",
                "planned_hours": 0.0,
                "notes": "",
            }))

        task = self.env["asset.maintenance.task"].create({
            "name": f"Maintenance from Inspection - {self.name}",
            "asset_id": self.asset_id.id,
            "maintenance_type": "corrective",
            "description": self.notes or "",
            "maintenance_team_id": self.maintenance_team_id.id or False,
            "scheduled_date": self.scheduled_date,
            "task_finding_ids": finding_vals,
            "planned_labour_ids": labour_vals,
            "inspection_id": self.id,
            "helpdesk_ticket_id": self.ticket_id.id or False,
            "billing_entity_id": self.billing_entity_id.id or False,
        })

        self.write({"maintenance_task_id": task.id})
        if self.ticket_id:
            self.ticket_id.write({"maintenance_task_id": task.id})

        return {
            "name": _("Maintenance Task"),
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.task",
            "view_mode": "form",
            "res_id": task.id,
            "views": [(False, "form")],
            "target": "current",
        }

    maintenance_task_count = fields.Integer(
        string="Maintenance Tasks",
        compute="_compute_maintenance_task_count",
    )
    job_order_count = fields.Integer(
        string="Job Orders",
        compute="_compute_job_order_count",
    )
    ticket_count = fields.Integer(
        string="Helpdesk Tickets",
        compute="_compute_ticket_count",
    )

    @api.depends("maintenance_task_id")
    def _compute_maintenance_task_count(self):
        for rec in self:
            rec.maintenance_task_count = 1 if rec.maintenance_task_id else 0

    @api.depends("maintenance_task_id.job_order_id")
    def _compute_job_order_count(self):
        for rec in self:
            rec.job_order_count = 1 if (
                    rec.maintenance_task_id and rec.maintenance_task_id.job_order_id
            ) else 0

    @api.depends("ticket_id")
    def _compute_ticket_count(self):
        for rec in self:
            rec.ticket_count = 1 if rec.ticket_id else 0

    def action_view_helpdesk_ticket(self):
        self.ensure_one()
        if not self.ticket_id:
            raise UserError(_("No helpdesk ticket linked to this inspection."))
        return {
            "name": _("Helpdesk Ticket"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "view_mode": "form",
            "res_id": self.ticket_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_view_maintenance_task(self):
        self.ensure_one()
        if not self.maintenance_task_id:
            raise UserError(_("No maintenance task linked to this inspection."))
        return {
            "name": _("Maintenance Task"),
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.task",
            "view_mode": "form",
            "res_id": self.maintenance_task_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_view_job_order(self):
        self.ensure_one()
        job = self.maintenance_task_id.job_order_id if self.maintenance_task_id else False
        if not job:
            raise UserError(_("No job order linked to this inspection."))
        return {
            "name": _("Job Order"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.order",
            "view_mode": "form",
            "res_id": job.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_print_report(self):
        return self.env.ref(
            "asset_management.action_report_asset_inspection"
        ).report_action(self)

    def action_complete(self):
        self.ensure_one()
        return {
            "name": _("Complete Inspection"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection.complete.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_inspection_id": self.id},
        }

    def _get_storekeeper_users(self):
        group = self.env.ref(
            "asset_management.group_asset_storekeeper", raise_if_not_found=False
        )
        if not group:
            return self.env["res.users"]
        return self.env["res.users"].search([
            ("group_ids", "in", group.ids),
            ("email", "!=", False),
            ("active", "=", True),
        ])

    def _get_storekeeper_emails(self):
        return ", ".join(self._get_storekeeper_users().mapped("email"))

    def _get_inspection_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref(
            "asset_management.action_asset_inspection", raise_if_not_found=False
        )
        action_id = action.id if action else "asset_inspection"
        return (
            f"{base}/odoo/action-{action_id}/{self.id}"
            if base
            else f"/odoo/action-{action_id}/{self.id}"
        )

    def _send_material_request_email(self):

        self.ensure_one()

        pending = self.material_ids.filtered(lambda l: l.state == "requested")
        if not pending:
            return

        storekeepers = self._get_storekeeper_users()
        if not storekeepers:
            self.message_post(
                body=_("⚠ Material request confirmed but <b>no Storekeeper users found</b> "
                       "(no users in the Storekeeper group with a valid email address). "
                       "Please assign the Storekeeper group to at least one user."),
                subtype_xmlid="mail.mt_note",
            )
            return

        rows_html = ""
        for line in pending:
            stock_color = (
                "#d9534f" if line.qty_available == 0
                else "#f0ad4e" if line.qty_available < line.qty_requested
                else "#5cb85c"
            )
            rows_html += f"""
                <tr>
                    <td style="padding:8px 12px;border:1px solid #ddd;">{line.product_id.name}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">{line.qty_requested}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">
                        {line.product_uom_id.name if line.product_uom_id else ''}
                    </td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;color:{stock_color};">
                        {line.qty_available}
                    </td>
                </tr>"""

        inspection_url = self._get_inspection_url()

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:#875A7B;padding:24px 32px;">
                <h2 style="color:#ffffff;margin:0;">Material Request Pending Approval</h2>
            </div>
            <div style="padding:24px 32px;background-color:#ffffff;">
                <p style="color:#333;font-size:15px;">
                    A new material request has been submitted for your approval:
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">
                            Inspection Reference
                        </td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">
                            {self.asset_id.name if self.asset_id else 'N/A'}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                            Scheduled Date
                        </td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.scheduled_date}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                            Maintenance Team
                        </td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">
                            {self.maintenance_team_id.name if self.maintenance_team_id else 'N/A'}
                        </td>
                    </tr>
                </table>

                <h3 style="color:#875A7B;margin-bottom:8px;">Requested Materials</h3>
                <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
                    <thead>
                        <tr style="background-color:#875A7B;color:white;">
                            <th style="padding:8px 12px;text-align:left;border:1px solid #ddd;">Product</th>
                            <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Qty</th>
                            <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Unit</th>
                            <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Available Stock</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>

                <div style="text-align:center;margin:28px 0;">
                    <a href="{inspection_url}"
                       style="background-color:#875A7B;color:white;padding:12px 28px;
                              border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                        Review &amp; Approve Materials
                    </a>
                </div>
                <p style="color:#888;font-size:13px;text-align:center;">
                    You can approve or reject each material line directly on the inspection record.
                </p>
            </div>
            <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                <p style="color:#aaa;font-size:12px;margin:0;">
                    This is an automated notification from the Asset Management system.
                </p>
            </div>
        </div>"""

        subject = f"New Material Request – {self.name}"

        MailMail = self.env["mail.mail"].sudo()
        mails_created = []

        for user in storekeepers:
            mail = MailMail.create({
                "subject": subject,
                "body_html": body_html,
                "email_to": user.email,
                "author_id": self.env.user.partner_id.id,
                "auto_delete": False,
                "state": "outgoing",
            })
            mails_created.append(mail)

        for mail in mails_created:
            try:
                mail.send(raise_exception=False)
            except Exception as e:
                self.message_post(
                    body=_(
                        "⚠ Failed to send email to <b>%(email)s</b>: %(error)s",
                        email=mail.email_to,
                        error=str(e),
                    ),
                    subtype_xmlid="mail.mt_note",
                )

        names = ", ".join(storekeepers.mapped("name"))
        emails = ", ".join(storekeepers.mapped("email"))
        self.message_post(
            body=_(
                "Material request notification queued for Storekeeper(s): "
                "<b>%(names)s</b> (%(emails)s).",
                names=names,
                emails=emails,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def _send_additional_material_request_email(self, new_lines):
        self.ensure_one()

        storekeepers = self._get_storekeeper_users()

        rows_html = ""
        for line in new_lines:
            stock_color = (
                "#d9534f" if line.qty_available == 0
                else "#f0ad4e" if line.qty_available < line.qty_requested
                else "#5cb85c"
            )
            rows_html += f"""
                <tr>
                    <td style="padding:8px 12px;border:1px solid #ddd;">{line.product_id.name}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">{line.qty_requested}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">
                        {line.product_uom_id.name if line.product_uom_id else ''}
                    </td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;color:{stock_color};">
                        {line.qty_available}
                    </td>
                </tr>"""

        inspection_url = self._get_inspection_url()

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:#875A7B;padding:24px 32px;">
                <h2 style="color:#ffffff;margin:0;">Additional Materials Requested</h2>
            </div>
            <div style="padding:24px 32px;background-color:#ffffff;">
                <p style="color:#333;font-size:15px;">
                    Additional materials have been requested for an ongoing inspection:
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">
                            Inspection Reference
                        </td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">
                            {self.asset_id.name if self.asset_id else 'N/A'}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                            Current Status
                        </td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">
                            {dict(self._fields['state'].selection).get(self.state, self.state)}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                            Maintenance Team
                        </td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">
                            {self.maintenance_team_id.name if self.maintenance_team_id else 'N/A'}
                        </td>
                    </tr>
                </table>

                <h3 style="color:#875A7B;margin-bottom:8px;">New Materials Requested</h3>
                <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
                    <thead>
                        <tr style="background-color:#875A7B;color:white;">
                            <th style="padding:8px 12px;text-align:left;border:1px solid #ddd;">Product</th>
                            <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Qty</th>
                            <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Unit</th>
                            <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Available Stock</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>

                <div style="text-align:center;margin:28px 0;">
                    <a href="{inspection_url}"
                       style="background-color:#875A7B;color:white;padding:12px 28px;
                              border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                        Review &amp; Approve Materials
                    </a>
                </div>
            </div>
            <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                <p style="color:#aaa;font-size:12px;margin:0;">
                    Automated notification — Asset Management system.
                </p>
            </div>
        </div>"""

        subject = f"Additional Materials Requested — {self.name}"
        product_names = ", ".join(new_lines.mapped("product_id.name"))

        if not storekeepers:
            self.message_post(
                body=_(
                    "⚠ Additional materials added (<b>%(products)s</b>) but "
                    "no Storekeeper users found to notify.",
                    products=product_names,
                ),
                subtype_xmlid="mail.mt_note",
            )
            return

        mail_sudo = self.env["mail.mail"].sudo()
        for user in storekeepers:
            mail = mail_sudo.create({
                "subject": subject,
                "body_html": body_html,
                "email_to": user.email,
                "author_id": self.env.user.partner_id.id,
                "auto_delete": False,
                "state": "outgoing",
            })
            try:
                mail.send(raise_exception=False)
            except Exception:
                pass

        names = ", ".join(storekeepers.mapped("name"))
        self.message_post(
            body=_(
                "Additional material request for <b>%(products)s</b> "
                "sent to Storekeeper(s): <b>%(names)s</b>.",
                products=product_names,
                names=names,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def _do_complete(self):
        self.ensure_one()

        final_state = "escalated" if self.escalation_required else "completed"
        self.write({
            "state": final_state,
            "completed_date": fields.Datetime.now(),
        })

        # Non-consumables + partially consumed consumables → pending return
        non_consumables = self.material_ids.filtered(
            lambda l: l.state in ("collected", "pending_return") and (
                    not l.is_consumable
                    or (l.is_consumable and l.qty_consumed < l.qty_requested)
            )
        )
        if non_consumables:
            non_consumables.write({"state": "pending_return"})
            self._send_pending_return_email(non_consumables)
            items = ", ".join(non_consumables.mapped("product_id.name"))
            self.message_post(
                body=_(
                    "The following items must be returned to the store: "
                    "<b>%(items)s</b>. The Storekeeper has been notified.",
                    items=items,
                ),
                subtype_xmlid="mail.mt_note",
            )

        # Only post consumption moves for fully consumed consumables
        # (partial consumables are in pending_return state — skip them here)
        consumed_lines = self.material_ids.filtered(
            lambda l: l.qty_consumed and l.qty_consumed > 0
                      and l.state == "consumed"  # ← was checking 'collected'/'pending_return' too
                      and l.is_consumable
        )
        for line in consumed_lines:
            try:
                line._post_consumption_move()
            except Exception as e:
                self.message_post(
                    body=_(
                        "⚠ Could not post consumption move for <b>%(product)s</b>: %(error)s",
                        product=line.product_id.name,
                        error=str(e),
                    ),
                    subtype_xmlid="mail.mt_note",
                )

        if self.ticket_id:
            ticket_vals = {}

            if self.asset_id and self.ticket_id.asset_id != self.asset_id:
                ticket_vals["asset_id"] = self.asset_id.id

            complete_stage = self.env["helpdesk.stage"].search(
                [("name", "=", "Inspection Complete")], limit=1
            )
            if not complete_stage:
                under_inspection = self.env["helpdesk.stage"].search(
                    [("name", "=", "Under Inspection")], limit=1
                )
                complete_stage = self.env["helpdesk.stage"].create({
                    "name": "Inspection Complete",
                    "sequence": (under_inspection.sequence + 1) if under_inspection else 6,
                })
            ticket_vals["stage_id"] = complete_stage.id
            self.ticket_id.write(ticket_vals)

            self.ticket_id.message_post(
                body=_(
                    "Inspection <b>%(insp)s</b> completed.%(asset)s%(escalation)s",
                    insp=self.name,
                    asset=(
                        f"<br/>Asset: <b>{self.asset_id.name}</b>"
                        if self.asset_id else ""
                    ),
                    escalation=(
                        "<br/>⚠ <b>Escalated to corrective maintenance.</b>"
                        if self.escalation_required
                        else "<br/>Issue resolved — no maintenance required."
                    ),
                ),
                subtype_xmlid="mail.mt_note",
            )

    @api.onchange("maintenance_team_id", "employee_ids")
    def _onchange_populate_labor_lines(self):

        employees = self.env["hr.employee"]

        if self.maintenance_team_id:
            team = self.maintenance_team_id
            if team.team_leader_id:
                leader_employee = self.env["hr.employee"].search(
                    [("user_id", "=", team.team_leader_id.id)], limit=1
                )
                if leader_employee:
                    employees |= leader_employee
            for member in team.member_ids:
                member_employee = self.env["hr.employee"].search(
                    [("user_id", "=", member.id)], limit=1
                )
                if member_employee:
                    employees |= member_employee

        if self.employee_ids:
            employees |= self.employee_ids

        if not employees:
            return

        existing_employee_ids = self.labor_ids.mapped("employee_id").ids

        new_lines = []
        for emp in employees:
            if emp.id not in existing_employee_ids:
                new_lines.append((0, 0, {
                    "employee_id": emp.id,
                    "date": fields.Date.today(),
                    "clock_in": 0.0,
                    "clock_out": 0.0,
                    "description": "",
                }))

        if new_lines:
            self.labor_ids = list(self.labor_ids) + new_lines

    @api.depends("material_ids.state")
    def _compute_pending_return_count(self):
        for rec in self:
            rec.pending_return_count = len(
                rec.material_ids.filtered(lambda l: l.state == "pending_return")
            )

    def _send_pending_return_email(self, non_consumable_lines):
        self.ensure_one()

        recipients = {}
        for u in self._get_storekeeper_users():
            recipients[u.email] = u.name
        if self.maintenance_team_id and self.maintenance_team_id.team_leader_id:
            leader = self.maintenance_team_id.team_leader_id
            if leader.email:
                recipients[leader.email] = leader.name

        if not recipients:
            return

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref("asset_management.action_asset_inspection", raise_if_not_found=False)
        action_id = action.id if action else "asset_inspection"
        inspection_url = f"{base}/odoo/action-{action_id}/{self.id}"

        rows_html = ""
        for line in non_consumable_lines:
            rows_html += f"""
                    <tr>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{line.product_id.name}</td>
                        <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">{line.qty_requested}</td>
                        <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">
                            {line.product_uom_id.name if line.product_uom_id else ''}
                        </td>
                    </tr>"""

        subject = f"Equipment Return Required – {self.name}"
        body_html = f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                        border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
                <div style="background-color:#E65100;padding:24px 32px;">
                    <h2 style="color:#ffffff;margin:0;">Equipment Return Required</h2>
                </div>
                <div style="padding:24px 32px;background-color:#ffffff;">
                    <p style="color:#333;font-size:15px;">
                        Inspection <b>{self.name}</b> has been completed.
                        The following non-consumable equipment must be returned to the store:
                    </p>
                    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">Asset</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{self.asset_id.name if self.asset_id else 'N/A'}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Team</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{self.maintenance_team_id.name if self.maintenance_team_id else 'N/A'}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Completed On</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{self.completed_date}</td>
                        </tr>
                    </table>
                    <h3 style="color:#E65100;margin-bottom:8px;">Items to Return</h3>
                    <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
                        <thead>
                            <tr style="background-color:#E65100;color:white;">
                                <th style="padding:8px 12px;text-align:left;border:1px solid #ddd;">Equipment</th>
                                <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Qty</th>
                                <th style="padding:8px 12px;text-align:center;border:1px solid #ddd;">Unit</th>
                            </tr>
                        </thead>
                        <tbody>{rows_html}</tbody>
                    </table>
                    <div style="text-align:center;margin:28px 0;">
                        <a href="{inspection_url}"
                           style="background-color:#E65100;color:white;padding:12px 28px;
                                  border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                            View Inspection &amp; Confirm Return
                        </a>
                    </div>
                    <p style="color:#888;font-size:13px;text-align:center;">
                        Once the equipment is back in the store, click <b>Confirm Return</b>
                        on each item in the Parts &amp; Materials tab.
                    </p>
                </div>
                <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                    <p style="color:#aaa;font-size:12px;margin:0;">
                        Automated notification — Asset Management system.
                    </p>
                </div>
            </div>"""

        mail_sudo = self.env["mail.mail"].sudo()
        for email, name in recipients.items():
            mail = mail_sudo.create({
                "subject": subject,
                "body_html": body_html,
                "email_to": email,
                "author_id": self.env.user.partner_id.id,
                "auto_delete": False,
                "state": "outgoing",
            })
            try:
                mail.send(raise_exception=False)
            except Exception:
                pass

    def action_view_pending_returns(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "form",
            "res_id": self.id,
            "views": [(False, "form")],
            "target": "current",
        }


class AssetInspectionFinding(models.Model):
    _name = "asset.inspection.finding"
    _description = "Inspection Finding Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)

    inspection_id = fields.Many2one(
        "asset.inspection",
        string="Inspection",
        required=True,
        ondelete="cascade",
    )

    description = fields.Char(string="Finding", required=True)

    severity = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        string="Severity",
        default="medium",
    )

    image = fields.Image(
        string="Photo",
        max_width=1920,
        max_height=1920,
    )

    image_filename = fields.Char(string="Image Filename")

    notes = fields.Text(string="Additional Notes")

    job_status = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('cannot_fix', 'Cannot Fix'),
    ], default='pending', string="Job Status")

    technician_note = fields.Text(string="Technician Note")


class AssetInspectionLabor(models.Model):
    _name = "asset.inspection.labor"
    _description = "Inspection Labor / Time Log"
    _order = "date, employee_id"

    inspection_id = fields.Many2one(
        "asset.inspection",
        required=True,
        ondelete="cascade",
    )

    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
    )

    date = fields.Date(
        string="Date",
        default=fields.Date.today,
        required=True,
    )

    clock_in = fields.Float(
        string="Clock In",
        digits=(2, 2),
        help="Enter as decimal hours, e.g. 8.5 = 08:30",
    )

    clock_out = fields.Float(
        string="Clock Out",
        digits=(2, 2),
        help="Enter as decimal hours, e.g. 17.0 = 17:00",
    )

    hours = fields.Float(
        string="Hours",
        compute="_compute_hours",
        store=True,
        readonly=True,
    )

    description = fields.Char(string="Work Description")

    @api.depends("clock_in", "clock_out")
    def _compute_hours(self):
        for rec in self:
            if rec.clock_out and rec.clock_in and rec.clock_out > rec.clock_in:
                rec.hours = rec.clock_out - rec.clock_in
            else:
                rec.hours = 0.0

    @api.constrains("clock_in", "clock_out")
    def _check_times(self):
        for rec in self:
            if rec.clock_in and rec.clock_out and rec.clock_out <= rec.clock_in:
                raise UserError(_(
                    "Clock Out must be after Clock In for employee %s on %s."
                ) % (rec.employee_id.name, rec.date))
            if rec.clock_in and not (0.0 <= rec.clock_in < 24.0):
                raise UserError(_("Clock In must be between 00:00 and 23:59."))
            if rec.clock_out and not (0.0 < rec.clock_out <= 24.0):
                raise UserError(_("Clock Out must be between 00:01 and 24:00."))


class AssetInspectionMaterial(models.Model):
    _name = "asset.inspection.material"
    _description = "Inspection Material / Parts"
    _inherit = ["mail.thread"]
    _order = "id"

    inspection_id = fields.Many2one(
        "asset.inspection",
        string="Inspection",
        required=True,
        ondelete="cascade",
    )

    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain="[('type', 'in', ['consu', 'product'])]",
    )

    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        related="product_id.uom_id",
        readonly=True,
    )

    qty_requested = fields.Float(string="Requested Qty", default=1.0)
    qty_consumed = fields.Float(string="Consumed Qty", default=0.0)

    qty_available = fields.Float(
        string="Available Stock",
        compute="_compute_qty_available",
        store=False,
    )

    state = fields.Selection(
        [
            ("requested", "Requested"),
            ("approved", "Approved by Storekeeper"),
            ("rejected", "Rejected by Storekeeper"),
            ("collected", "Collected"),
            ("pending_return", "Pending Return"),
            ("consumed", "Consumed"),
            ("returned", "Returned / Not Used"),
        ],
        default="requested",
        string="Status",
        tracking=True,
    )

    rejection_reason = fields.Text(string="Rejection Reason")
    approved_by = fields.Many2one("res.users", string="Approved By", readonly=True)
    approved_date = fields.Datetime(string="Approved On", readonly=True)

    notes = fields.Char(string="Notes")

    is_consumable = fields.Boolean(
        string="Consumable",
        compute="_compute_is_consumable",
        store=True,
        help="Consumable = used up (materials, parts). "
             "Not consumable = tool/equipment to be returned to store.",
    )

    picking_id = fields.Many2one(
        "stock.picking",
        string="Stock Reservation",
        readonly=True,
        copy=False,
        help="Internal picking created to reserve stock for this material line.",
    )

    move_id = fields.Many2one(
        "stock.move",
        string="Stock Move",
        readonly=True,
        copy=False,
    )

    qty_reserved = fields.Float(
        string="Reserved Qty",
        compute="_compute_qty_reserved",
        store=False,
    )

    @api.depends("move_id", "move_id.move_line_ids", "move_id.move_line_ids.quantity")
    def _compute_qty_reserved(self):
        for line in self:
            if line.move_id and line.move_id.move_line_ids:
                line.qty_reserved = sum(line.move_id.move_line_ids.mapped("quantity"))
            else:
                line.qty_reserved = 0.0

    @api.depends("product_id")
    def _compute_is_consumable(self):
        for line in self:
            if line.product_id:
                line.is_consumable = line.product_id.is_consumable_in_inspection
            else:
                line.is_consumable = True

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.qty_available = self.product_id.free_qty

    @api.depends("product_id")
    def _compute_qty_available(self):
        for line in self:
            line.qty_available = line.product_id.free_qty if line.product_id else 0.0

    def action_approve(self):
        self._check_storekeeper_access()

        for rec in self:
            if rec.product_id.free_qty < rec.qty_requested:
                raise UserError(_(
                    "Cannot approve %(product)s: only %(available)s %(unit)s available "
                    "but %(requested)s %(unit)s requested.\n\n"
                    "Either reduce the requested quantity or replenish stock first.",
                    product=rec.product_id.name,
                    available=rec.product_id.qty_available,
                    unit=rec.product_uom_id.name,
                    requested=rec.qty_requested,
                ))

            picking = rec._create_stock_reservation()

            rec.write({
                "state": "approved",
                "approved_by": rec.env.user.id,
                "approved_date": fields.Datetime.now(),
                "picking_id": picking.id,
                "move_id": picking.move_ids[:1].id if picking.move_ids else False,
            })

            rec.inspection_id.message_post(
                body=_(
                    "Material <b>%(product)s</b> approved by <b>%(user)s</b>. "
                    "%(qty)s %(unit)s reserved from stock. "
                    "(Reservation: <b>%(picking)s</b>)",
                    product=rec.product_id.name,
                    user=rec.env.user.name,
                    qty=rec.qty_requested,
                    unit=rec.product_uom_id.name,
                    picking=picking.name,
                ),
                subtype_xmlid="mail.mt_note",
            )

    def _create_stock_reservation(self):
        self.ensure_one()

        warehouse = self.env["stock.warehouse"].search([], limit=1)
        src_location = warehouse.lot_stock_id if warehouse else self.env.ref(
            "stock.stock_location_stock"
        )

        dest_location = self._get_or_create_inspection_location()

        picking_type = self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            ("warehouse_id", "=", warehouse.id),
        ], limit=1)

        if not picking_type:
            raise UserError(_(
                "No internal picking type found for warehouse %s. "
                "Please configure stock operations."
            ) % warehouse.name)

        picking = self.env["stock.picking"].sudo().create({
            "picking_type_id": picking_type.id,
            "location_id": src_location.id,
            "location_dest_id": dest_location.id,
            "origin": f"INSP-{self.inspection_id.name}",
            "move_ids": [(0, 0, {
                "product_id": self.product_id.id,
                "product_uom": self.product_uom_id.id,
                "product_uom_qty": self.qty_requested,
                "location_id": src_location.id,
                "location_dest_id": dest_location.id,
            })],
        })

        picking.action_confirm()
        picking.action_assign()

        return picking

    def _get_or_create_inspection_location(self):
        location = self.env["stock.location"].search([
            ("name", "=", "Inspection Reserved"),
            ("usage", "=", "internal"),
        ], limit=1)

        if not location:
            parent = self.env.ref("stock.stock_location_locations", raise_if_not_found=False)
            location = self.env["stock.location"].sudo().create({
                "name": "Inspection Reserved",
                "usage": "internal",
                "location_id": parent.id if parent else False,
                "active": True,
            })

        return location

    def action_reject(self):
        self._check_storekeeper_access()
        return {
            "name": _("Reject Material Request"),
            "type": "ir.actions.act_window",
            "res_model": "asset.material.rejection.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_material_ids": self.ids},
        }

    def _cancel_stock_reservation(self):
        for rec in self:
            if rec.picking_id and rec.picking_id.state not in ("done", "cancel"):
                rec.picking_id.action_cancel()
                rec.inspection_id.message_post(
                    body=_(
                        "Stock reservation <b>%(picking)s</b> for <b>%(product)s</b> "
                        "has been cancelled and stock returned to available.",
                        picking=rec.picking_id.name,
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

    def _do_reject(self, reason=""):
        self._cancel_stock_reservation()
        self.write({
            "state": "rejected",
            "rejection_reason": reason,
        })

    def action_mark_collected(self):

        for rec in self:
            if rec.picking_id and rec.picking_id.state == "assigned":
                for move in rec.picking_id.move_ids:
                    move.quantity = move.product_uom_qty
                rec.picking_id.sudo().button_validate()

            rec.write({"state": "collected"})
            rec.inspection_id.message_post(
                body=_(
                    "Material <b>%(product)s</b> collected from store by <b>%(user)s</b>.",
                    product=rec.product_id.name,
                    user=rec.env.user.name,
                ),
                subtype_xmlid="mail.mt_note",
            )

    def action_return_unused(self):
        for rec in self:
            if not rec.picking_id:
                rec.write({"state": "returned"})
                rec.inspection_id.message_post(
                    body=_(
                        "Equipment <b>%(product)s</b> marked as returned "
                        "(no stock picking was linked).",
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )
                continue

            picking_state = rec.picking_id.state

            if picking_state == "done":
                original = rec.picking_id
                warehouse = rec.env["stock.warehouse"].search([], limit=1)

                return_picking = rec.env["stock.picking"].sudo().create({
                    "picking_type_id": original.picking_type_id.id,
                    "location_id": original.location_dest_id.id,
                    "location_dest_id": original.location_id.id,
                    "origin": f"Return of {original.name}",
                    "move_ids": [(0, 0, {
                        "product_id": rec.product_id.id,
                        "product_uom": rec.product_uom_id.id,
                        "product_uom_qty": rec.qty_requested,
                        "quantity": rec.qty_requested,
                        "location_id": original.location_dest_id.id,
                        "location_dest_id": original.location_id.id,
                        "origin_returned_move_id": original.move_ids[:1].id,
                    })],
                })

                return_picking.action_confirm()

                for move in return_picking.move_ids:
                    rec.env["stock.move.line"].sudo().create({
                        "picking_id": return_picking.id,
                        "move_id": move.id,
                        "product_id": move.product_id.id,
                        "product_uom_id": move.product_uom.id,
                        "quantity": rec.qty_requested,
                        "location_id": move.location_id.id,
                        "location_dest_id": move.location_dest_id.id,
                    })
                    move.sudo().write({"quantity": rec.qty_requested})

                return_picking.sudo().with_context(
                    skip_backorder=True,
                    skip_immediate=True,
                    immediate_transfer=True,
                ).button_validate()

                rec.write({"state": "returned"})
                rec.inspection_id.message_post(
                    body=_(
                        "Equipment <b>%(product)s</b> returned to stock. "
                        "Return picking: <b>%(picking)s</b>.",
                        product=rec.product_id.name,
                        picking=return_picking.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

            elif picking_state in ("assigned", "confirmed", "waiting"):
                rec.picking_id.action_cancel()
                rec.write({"state": "returned"})
                rec.inspection_id.message_post(
                    body=_(
                        "Stock reservation for <b>%(product)s</b> cancelled and "
                        "equipment marked as returned.",
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

            elif picking_state == "cancel":
                rec.write({"state": "returned"})
                rec.inspection_id.message_post(
                    body=_(
                        "Equipment <b>%(product)s</b> marked as returned "
                        "(reservation was already cancelled).",
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

    def _check_storekeeper_access(self):
        if not self.env.user.has_group("asset_management.group_asset_storekeeper"):
            raise AccessError(
                _("Only the Storekeeper can approve or reject material requests.")
            )

    def _all_approved(self):
        active_lines = self.filtered(lambda l: l.state != "rejected")
        return all(l.state in ("approved", "collected", "consumed", "returned") for l in active_lines)

    def action_confirm_return(self):
        self._check_storekeeper_access()
        for rec in self:
            if rec.state != "pending_return":
                continue
            qty_to_return = rec.qty_requested - rec.qty_consumed
            if qty_to_return <= 0:
                rec.write({"state": "returned"})
                continue
            rec._return_partial_qty_to_stock(qty_to_return)

    def _return_partial_qty_to_stock(self, qty_to_return):
        """
        Return qty_to_return units back to the original source location
        by creating a proper reverse picking against the original picking.
        """
        self.ensure_one()

        if not self.picking_id:
            # No picking linked — just mark returned
            self.write({"state": "returned"})
            self.inspection_id.message_post(
                body=_(
                    "Equipment <b>%(product)s</b> marked as returned (no stock picking linked).",
                    product=self.product_id.name,
                ),
                subtype_xmlid="mail.mt_note",
            )
            return

        original = self.picking_id

        if original.state != "done":
            # Picking not yet validated — just cancel it and mark returned
            if original.state not in ("cancel",):
                original.action_cancel()
            self.write({"state": "returned"})
            self.inspection_id.message_post(
                body=_(
                    "Stock reservation for <b>%(product)s</b> cancelled. "
                    "%(qty)s %(unit)s marked as returned.",
                    product=self.product_id.name,
                    qty=qty_to_return,
                    unit=self.product_uom_id.name,
                ),
                subtype_xmlid="mail.mt_note",
            )
            return

        # Original picking is done — create a proper reverse picking
        return_picking = self.env["stock.picking"].sudo().create({
            "picking_type_id": original.picking_type_id.id,
            "location_id": original.location_dest_id.id,  # Inspection Reserved
            "location_dest_id": original.location_id.id,  # WH/Stock
            "origin": f"Return of {original.name} [{self.inspection_id.name}]",
            "move_ids": [(0, 0, {
                "product_id": self.product_id.id,
                "product_uom": self.product_uom_id.id,
                "product_uom_qty": qty_to_return,
                "quantity": qty_to_return,
                "location_id": original.location_dest_id.id,
                "location_dest_id": original.location_id.id,
                "origin_returned_move_id": original.move_ids[:1].id,
            })],
        })

        return_picking.action_confirm()

        for move in return_picking.move_ids:
            self.env["stock.move.line"].sudo().create({
                "picking_id": return_picking.id,
                "move_id": move.id,
                "product_id": move.product_id.id,
                "product_uom_id": move.product_uom.id,
                "quantity": qty_to_return,
                "location_id": move.location_id.id,
                "location_dest_id": move.location_dest_id.id,
            })
            move.sudo().write({"quantity": qty_to_return})

        return_picking.sudo().with_context(
            skip_backorder=True,
            skip_immediate=True,
            immediate_transfer=True,
        ).button_validate()

        self.write({"state": "returned"})
        self.inspection_id.message_post(
            body=_(
                "%(qty)s × <b>%(product)s</b> returned to stock. "
                "Return picking: <b>%(picking)s</b>.",
                qty=qty_to_return,
                product=self.product_id.name,
                picking=return_picking.name,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def _return_qty_to_stock(self, qty_to_return):
        """Return a specific quantity back to stock from Inspection Reserved location."""
        self.ensure_one()

        inspection_location = self.env["stock.location"].search([
            ("name", "=", "Inspection Reserved"),
            ("usage", "=", "internal"),
        ], limit=1)

        warehouse = self.env["stock.warehouse"].search([], limit=1)
        stock_location = warehouse.lot_stock_id if warehouse else self.env.ref("stock.stock_location_stock")

        if not inspection_location:
            # Fallback: use picking-based return (original logic)
            self.action_return_unused()
            return

        # Direct stock move: Inspection Reserved → main stock
        move = self.env["stock.move"].sudo().create({
            "description_picking": f"Return: {self.product_id.name} [{self.inspection_id.name}]",
            "product_id": self.product_id.id,
            "product_uom": self.product_uom_id.id,
            "product_uom_qty": qty_to_return,
            "quantity": qty_to_return,
            "location_id": inspection_location.id,
            "location_dest_id": stock_location.id,
            "origin": f"RETURN-{self.inspection_id.name}",
            "state": "confirmed",
        })
        self.env["stock.move.line"].sudo().create({
            "move_id": move.id,
            "product_id": self.product_id.id,
            "product_uom_id": self.product_uom_id.id,
            "quantity": qty_to_return,
            "location_id": inspection_location.id,
            "location_dest_id": stock_location.id,
        })
        move.sudo()._action_done()

        self.write({"state": "returned"})
        self.inspection_id.message_post(
            body=_(
                "%(qty)s × <b>%(product)s</b> returned to stock from Inspection Reserved. "
                "Origin: <b>%(origin)s</b>.",
                qty=qty_to_return,
                product=self.product_id.name,
                origin=move.origin,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def _post_consumption_move(self):
        """
        Write off consumed qty: Inspection Reserved → Inspection Consumed (production virtual).
        Uses a raw stock.move — pickings cannot route to virtual locations.
        """
        self.ensure_one()

        if not self.qty_consumed or self.qty_consumed <= 0:
            return

        inspection_location = self.env["stock.location"].search([
            ("name", "=", "Inspection Reserved"),
            ("usage", "=", "internal"),
        ], limit=1)

        if not inspection_location:
            self.inspection_id.message_post(
                body=_(
                    "⚠ 'Inspection Reserved' location not found — consumption move skipped for <b>%s</b>.") % self.product_id.name,
                subtype_xmlid="mail.mt_note",
            )
            return

        # Look for consumption location: prefer child of Inspection Reserved,
        # fall back to any production-type location
        consumed_location = self.env["stock.location"].search([
            ("location_id", "=", inspection_location.id),
            ("usage", "=", "production"),
        ], limit=1)

        if not consumed_location:
            # Fallback: any production virtual location by name
            consumed_location = self.env["stock.location"].search([
                ("usage", "=", "production"),
                ("name", "in", ["Inspection Consumed", "Consumption", "Production"]),
            ], limit=1)

        if not consumed_location:
            self.inspection_id.message_post(
                body=_("⚠ No consumption/production virtual location found — move skipped for <b>%s</b>. "
                       "Please create a child location under 'Inspection Reserved' with type 'Production'.") % self.product_id.name,
                subtype_xmlid="mail.mt_note",
            )
            return

        move = self.env["stock.move"].sudo().create({
            "description_picking": f"Consumed: {self.product_id.name} [{self.inspection_id.name}]",
            "product_id": self.product_id.id,
            "product_uom": self.product_uom_id.id,
            "product_uom_qty": self.qty_consumed,
            "quantity": self.qty_consumed,
            "location_id": inspection_location.id,
            "location_dest_id": consumed_location.id,
            "origin": f"CONSUMED-{self.inspection_id.name}",
            "state": "confirmed",
        })

        self.env["stock.move.line"].sudo().create({
            "move_id": move.id,
            "product_id": self.product_id.id,
            "product_uom_id": self.product_uom_id.id,
            "quantity": self.qty_consumed,
            "location_id": inspection_location.id,
            "location_dest_id": consumed_location.id,
        })

        move.sudo()._action_done()

        if self.state != "pending_return":
            self.write({"state": "consumed"})

        self.inspection_id.message_post(
            body=_(
                "%(qty)s × <b>%(product)s</b> written off "
                "(Inspection Reserved → %(dest)s). Origin: <b>%(origin)s</b>.",
                qty=self.qty_consumed,
                product=self.product_id.name,
                dest=consumed_location.complete_name,
                origin=move.origin,
            ),
            subtype_xmlid="mail.mt_note",
        )
