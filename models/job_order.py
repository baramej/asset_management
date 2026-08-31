from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import uuid  # ← add this

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
    helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket", related="maintenance_task_id.helpdesk_ticket_id",
        store=True, readonly=True,
    )
    assigned_user_id = fields.Many2one(
        "hr.employee", related="maintenance_task_id.assigned_user_id",
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
    approved_by_name = fields.Char(string="Approved By (Signature)", readonly=True)
    approved_by_signature = fields.Binary(string="Approval Signature", readonly=True)

    state = fields.Selection([
        ("draft", "Draft"),
        ("request_material", "Material Requested"),
        ("material_approved", "Material Approved"),
        ("in_progress", "In Progress"),
        ("waiting_outsource", "Waiting Outsource"),
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
    customer_approval = fields.Boolean(
        string="Customer Approval Required",
        default=True,
        tracking=True,
        help="When enabled, a separate customer signature is required to close the job order "
             "after supervisor approval. When disabled, the approval signature also closes the job order.",
    )

    findings_completion_rate = fields.Float(compute="_compute_completion_rate")

    approved_by = fields.Many2one("res.users", readonly=True)
    approved_date = fields.Datetime(readonly=True)
    closed_by = fields.Many2one("res.users", readonly=True)
    closed_date = fields.Datetime(readonly=True)

    closed_by_name = fields.Char(string="Closed By (Signature)", readonly=True)
    closed_by_signature = fields.Binary(string="Signature", readonly=True)

    access_token = fields.Char(
        string="Access Token",
        copy=False,
        readonly=True,
    )
    portal_signature = fields.Binary(
        string="Customer Signature",
        copy=False,
        readonly=True,
    )
    portal_signed_by = fields.Char(
        string="Signed By",
        copy=False,
        readonly=True,
    )
    portal_signed_on = fields.Datetime(
        string="Signed On",
        copy=False,
        readonly=True,
    )
    portal_signed_ip = fields.Char(
        string="Signed From IP",
        copy=False,
        readonly=True,
    )
    signature_state = fields.Selection([
        ("pending", "Not Sent"),
        ("sent", "Sent — Awaiting Signature"),
        ("signed", "Signed"),
    ], string="Signature Status", default="pending", tracking=True, copy=False)

    signature_sent_on = fields.Datetime(
        string="Signature Request Sent On",
        copy=False,
        readonly=True,
        help="Timestamp when the signature email was sent. Used for auto-close after 5 days.",
    )

    is_outsourced = fields.Boolean(
        string="Outsource Job?",
        default=False,
        tracking=True,
        help="When enabled, the job is handled by an external party. "
             "Attach their job order document to close.",
    )
    outsource_vendor_id = fields.Many2one(
        "res.partner",
        string="Outsource Vendor",
        tracking=True,
        help="The external company or contractor handling this job.",
    )
    outsource_reference = fields.Char(
        string="Vendor Reference",
        tracking=True,
        help="The external job order number or reference from the vendor.",
    )
    outsource_attachment_ids = fields.Many2many(
        "ir.attachment",
        "job_order_outsource_attachment_rel",
        "job_order_id",
        "attachment_id",
        string="Vendor Job Documents",
        help="Attach the external vendor's job order, report, or completion certificate.",
    )
    outsource_notes = fields.Text(
        string="Outsource Notes",
        help="Any notes about the outsourced work.",
    )

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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = (
                        self.env["ir.sequence"].next_by_code("asset.job.order")
                        or "JO-0001"
                )
        records = super().create(vals_list)
        for rec in records:
            rec._populate_technician_log_lines()
        return records

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

    def write(self, vals):
        result = super().write(vals)
        if "material_line_ids" in vals:
            for rec in self:
                if rec.state in ("request_material", "material_approved", "in_progress"):
                    new_requested = rec.material_line_ids.filtered(
                        lambda l: l.approval_state == "requested"
                                  and not l.approved_by
                                  and not l.rejection_reason
                    )
                    if new_requested:
                        rec._send_additional_material_request_email(new_requested)
        return result

    def action_request_material(self):
        self.ensure_one()
        return {
            "name": _("Request Materials"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.material.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_order_id": self.id},
        }

    pending_return_count = fields.Integer(
        string="Pending Returns",
        compute="_compute_pending_return_count",
        store=True,
    )

    @api.depends("material_line_ids.approval_state")
    def _compute_pending_return_count(self):
        for rec in self:
            rec.pending_return_count = len(
                rec.material_line_ids.filtered(
                    lambda l: l.approval_state == "pending_return"
                )
            )
            # Auto-advance state when all materials are approved
            self._sync_material_state()

    def _sync_material_state(self):
        """Called whenever a material line approval_state changes.
        Advances job order from request_material → material_approved."""
        for rec in self:
            if rec.state != "request_material":
                continue
            lines = rec.material_line_ids
            if not lines:
                continue
            # If any are still sitting at 'requested', not ready yet
            if any(l.approval_state == "requested" for l in lines):
                continue
            # If any rejected, don't auto-advance (storekeeper must handle)
            if any(l.approval_state == "rejected" for l in lines):
                continue
            # All lines are approved/collected/consumed/returned — advance state
            rec.write({"state": "material_approved"})
            rec.message_post(
                body=_("All materials approved by Storekeeper. Job Order is ready to start.")
            )

    def action_start(self):
        self.ensure_one()
        pending = self.material_line_ids.filtered(
            lambda l: l.approval_state == "requested"
        )
        if pending:
            raise UserError(_(
                "Materials are still pending Storekeeper approval. "
                "Please wait for all material requests to be approved before starting work."
            ))
        if self.is_outsourced:
            self.write({
                "state": "waiting_outsource",
                "start_datetime": fields.Datetime.now(),
            })
            self.message_post(
                body=_("Outsourced job submitted by %s — waiting for vendor completion.") % self.env.user.name
            )
        else:
            self.write({
                "state": "in_progress",
                "start_datetime": fields.Datetime.now(),
            })
            if self.maintenance_task_id and self.maintenance_task_id.state == "assigned":
                self.maintenance_task_id.state = "in_progress"
            self.message_post(
                body=_("Work started by %s.") % self.env.user.name
            )

    def action_end_work(self):
        self.ensure_one()
        return {
            "name": _("End Work — Confirm Materials"),
            "type": "ir.actions.act_window",
            "res_model": "asset.job.complete.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_job_order_id": self.id},
        }

    def action_wait_outsource(self):
        self.ensure_one()
        if not self.is_outsourced:
            raise UserError(_("This job order is not marked as outsourced."))
        self.write({"state": "waiting_outsource"})
        self.message_post(
            body=_(
                "Job Order is now <b>Waiting for Outsource Completion</b>. "
                "Submitted by <b>%s</b>. Attach the vendor's job order document when received."
            ) % self.env.user.name
        )

    def action_complete_outsource(self):
        self.ensure_one()
        if not self.outsource_attachment_ids:
            raise UserError(_(
                "Please attach the vendor's job order or completion document "
                "before closing the outsourced job."
            ))
        self.write({
            "state": "done",
            "end_datetime": fields.Datetime.now(),
        })
        self.message_post(
            body=_(
                "Outsourced job completed by <b>%s</b>. "
                "Vendor document(s) attached: <b>%s</b>."
            ) % (
                     self.env.user.name,
                     ", ".join(self.outsource_attachment_ids.mapped("name")),
                 )
        )

    def _get_or_create_access_token(self):
        self.ensure_one()
        if not self.access_token:
            self.access_token = str(uuid.uuid4())
        return self.access_token

    def action_send_signature_email(self):
        self.ensure_one()
        token = self._get_or_create_access_token()

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        portal_url = f"{base_url}/my/job_order/{self.id}?access_token={token}"

        customer = (
                self.billing_partner_id
                or (self.asset_id.customer_id if self.asset_id else False)
                or (self.helpdesk_ticket_id.partner_id if self.helpdesk_ticket_id else False)
        )

        if not customer or not customer.email:
            raise UserError(_(
                "No customer email found. "
                "Please set the Billing Entity (with a valid email) before sending the signature request."
            ))

        customer_name = customer.name or "Customer"
        asset_name = self.asset_id.name if self.asset_id else "N/A"
        scheduled = str(self.scheduled_date or "N/A")
        team = self.maintenance_team_id.name if self.maintenance_team_id else "N/A"

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:#1a1a2e;padding:24px 32px;">
                <h2 style="color:#ffffff;margin:0;">Job Order Sign-Off Request</h2>
            </div>
            <div style="padding:24px 32px;background-color:#ffffff;">
                <p style="color:#333;font-size:15px;">Dear {customer_name},</p>
                <p style="color:#333;font-size:15px;">
                    Your job order <strong>{self.name}</strong> has been completed
                    and is ready for your review and signature.
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">Job Order</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{asset_name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Scheduled Date</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{scheduled}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Maintenance Team</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{team}</td>
                    </tr>
                </table>
                <p style="color:#333;font-size:15px;">
                    Please click the button below to review the work performed and sign off:
                </p>
                <div style="text-align:center;margin:28px 0;">
                    <a href="{portal_url}"
                       style="background-color:#1a1a2e;color:white;padding:12px 28px;
                              border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                        Review &amp; Sign Job Order
                    </a>
                </div>
                <p style="color:#888;font-size:13px;text-align:center;">
                    This link is unique to you. Please do not share it.
                </p>
            </div>
            <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                <p style="color:#aaa;font-size:12px;margin:0;">
                    Automated notification — Asset Management System.
                </p>
            </div>
        </div>"""

        self.env["mail.mail"].sudo().create({
            "subject": f"Job Order {self.name} — Your Signature Required",
            "body_html": body_html,
            "email_to": customer.email,
            "author_id": self.env.user.partner_id.id,
            "auto_delete": False,
            "state": "outgoing",
        }).send(raise_exception=False)

        self.write({
            "signature_state": "sent",
            "signature_sent_on": fields.Datetime.now(),
        })
        self.message_post(
            body=_(
                "Signature request sent to <b>%(name)s</b> (%(email)s). "
                "Portal link: <a href='%(url)s'>%(url)s</a>",
                name=customer_name,
                email=customer.email,
                url=portal_url,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def action_sign(self, signature, signed_by, ip_address=None):
        self.ensure_one()
        # Strip data URI prefix if present
        if signature and signature.startswith("data:image"):
            signature = signature.split(",", 1)[1]
        import base64
        self.write({
            "portal_signature": signature,
            "portal_signed_by": signed_by,
            "portal_signed_on": fields.Datetime.now(),
            "portal_signed_ip": ip_address or "",
            "signature_state": "signed",
        })
        self.message_post(
            body=_(
                "Job Order signed by <b>%(name)s</b> on %(date)s.",
                name=signed_by,
                date=fields.Datetime.now(),
            )
        )

    def action_print_report(self):
        return self.env.ref(
            "asset_management.action_report_asset_job_order"
        ).report_action(self)

    def _do_end_work(self):
        self.ensure_one()
        self.write({
            "state": "done",
            "end_datetime": fields.Datetime.now(),
        })

        self.env["asset.job.finding.material.line"].invalidate_model(
            ["quantity_used", "approval_state"]
        )
        material_lines = self.env["asset.job.finding.material.line"].search(
            [("job_order_id", "=", self.id)]
        )

        non_consumables = material_lines.filtered(
            lambda l: not l.is_consumable and l.approval_state == "collected"
        )
        if non_consumables:
            non_consumables.write({"approval_state": "pending_return"})
            items = ", ".join(non_consumables.mapped("product_id.name"))
            self.message_post(
                body=_(
                    "Work completed. The following non-consumable equipment must be "
                    "returned to the store: <b>%(items)s</b>.",
                    items=items,
                )
            )

        # Partially consumed → flag remainder for return
        partial_consumables = material_lines.filtered(
            lambda l: l.is_consumable
                      and l.approval_state == "collected"
                      and l.quantity_used > 0
                      and l.quantity_used < l.quantity_requested
        )
        if partial_consumables:
            partial_consumables.write({"approval_state": "pending_return"})
            items = ", ".join(partial_consumables.mapped("product_id.name"))
            self.message_post(
                body=_(
                    "Partially consumed materials flagged for return: <b>%(items)s</b>.",
                    items=items,
                )
            )

        # Fully consumed → post stock write-off move
        # AFTER — post move for ANY positive consumption, not just fully consumed
        consumed_lines = material_lines.filtered(
            lambda l: l.is_consumable
                      and l.approval_state == "collected"
                      and l.quantity_used > 0
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

        # Consumables with qty_used = 0 and still collected → treat as returned
        not_used = material_lines.filtered(
            lambda l: l.is_consumable
                      and l.approval_state == "collected"
                      and l.quantity_used == 0
        )
        if not_used:
            not_used.write({"approval_state": "pending_return"})
            items = ", ".join(not_used.mapped("product_id.name"))
            self.message_post(
                body=_(
                    "The following unused materials must be returned to the store: "
                    "<b>%(items)s</b>.",
                    items=items,
                )
            )

    def action_view_pending_returns(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "asset.job.order",
            "view_mode": "form",
            "res_id": self.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_approve(self):
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

        # If customer approval is not required, immediately close as well
        if not self.customer_approval:
            self._do_close(
                signed_by_name=signed_by_name,
                signature=signature,
            )

    def action_reject(self):
        self.ensure_one()
        self.write({"state": "rejected"})
        self.message_post(
            body=_("Job Order rejected by %s — returned for revision.") % self.env.user.name
        )

    def _send_material_request_email(self):
        self.ensure_one()

        pending = self.material_line_ids.filtered(
            lambda l: l.approval_state == "requested"
        )
        if not pending:
            return

        storekeepers = self._get_storekeeper_users()
        if not storekeepers:
            self.message_post(
                body=_(
                    "⚠ Material request submitted but <b>no Storekeeper users found</b>. "
                    "Please assign the Storekeeper group to at least one user."
                ),
                subtype_xmlid="mail.mt_note",
            )
            return

        rows_html = ""
        for line in pending:
            stock_color = (
                "#d9534f" if line.on_hand_qty == 0
                else "#f0ad4e" if line.on_hand_qty < line.quantity_requested
                else "#5cb85c"
            )
            rows_html += f"""
                <tr>
                    <td style="padding:8px 12px;border:1px solid #ddd;">{line.product_id.name}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">{line.quantity_requested}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">
                        {line.uom_id.name if line.uom_id else ''}
                    </td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;color:{stock_color};">
                        {line.on_hand_qty}
                    </td>
                </tr>"""

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref(
            "asset_management.action_asset_job_order", raise_if_not_found=False
        )
        action_id = action.id if action else "asset_job_order"
        job_url = (
            f"{base}/odoo/action-{action_id}/{self.id}"
            if base else f"/odoo/action-{action_id}/{self.id}"
        )

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
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">Job Order</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.asset_id.name if self.asset_id else 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Scheduled Date</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.scheduled_date or 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Maintenance Team</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.maintenance_team_id.name if self.maintenance_team_id else 'N/A'}</td>
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
                    <a href="{job_url}"
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

        subject = f"New Material Request – {self.name}"
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
        emails = ", ".join(storekeepers.mapped("email"))
        self.message_post(
            body=_(
                "Material request notification sent to Storekeeper(s): "
                "<b>%(names)s</b> (%(emails)s).",
                names=names,
                emails=emails,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def _send_additional_material_request_email(self, new_lines):
        self.ensure_one()

        storekeepers = self._get_storekeeper_users()
        product_names = ", ".join(new_lines.mapped("product_id.name"))

        rows_html = ""
        for line in new_lines:
            stock_color = (
                "#d9534f" if line.on_hand_qty == 0
                else "#f0ad4e" if line.on_hand_qty < line.quantity_requested
                else "#5cb85c"
            )
            rows_html += f"""
                <tr>
                    <td style="padding:8px 12px;border:1px solid #ddd;">{line.product_id.name}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">{line.quantity_requested}</td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;">
                        {line.uom_id.name if line.uom_id else ''}
                    </td>
                    <td style="padding:8px 12px;text-align:center;border:1px solid #ddd;color:{stock_color};">
                        {line.on_hand_qty}
                    </td>
                </tr>"""

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref("asset_management.action_asset_job_order", raise_if_not_found=False)
        action_id = action.id if action else "asset_job_order"
        job_url = f"{base}/odoo/action-{action_id}/{self.id}" if base else f"/odoo/action-{action_id}/{self.id}"

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:#875A7B;padding:24px 32px;">
                <h2 style="color:#ffffff;margin:0;">Additional Materials Requested</h2>
            </div>
            <div style="padding:24px 32px;background-color:#ffffff;">
                <p style="color:#333;font-size:15px;">
                    Additional materials have been requested for an ongoing job order:
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">Job Order</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.asset_id.name if self.asset_id else 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Current Status</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">
                            {dict(self._fields['state'].selection).get(self.state, self.state)}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Team</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.maintenance_team_id.name if self.maintenance_team_id else 'N/A'}</td>
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
                    <a href="{job_url}"
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

    def action_close(self):
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
        compute="_compute_billing_partner",
        store=True,
        readonly=False,
        tracking=True,
        help="Auto-filled from the asset's customer. Can be overridden manually.",
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
        self.ensure_one()
        self.write({
            "state": "closed",
            "closed_by": self.env.user.id,
            "closed_date": fields.Datetime.now(),
            "closed_by_name": signed_by_name,
            "closed_by_signature": signature,
        })

        task = self.maintenance_task_id
        if task and self.total_hours:
            task.actual_hours = self.total_hours

        if task and task.state not in ("done", "cancel"):
            task.write({"state": "done", "done_date": fields.Datetime.now()})
            task._handle_done_side_effects()

        self.message_post(
            body=_(
                "Job Order closed and signed by <b>%s</b>."
            ) % signed_by_name
        )

    @api.depends("asset_id", "asset_id.customer_id")
    def _compute_billing_partner(self):
        for rec in self:
            rec.billing_partner_id = rec.asset_id.customer_id if rec.asset_id else False

    @api.depends("state", "invoice_id")
    def _compute_can_create_invoice(self):
        for rec in self:
            rec.can_create_invoice = rec.state in ("approved", "closed") and not rec.invoice_id

    def _populate_technician_log_lines(self):
        """Populate technician log lines from assigned team and user."""
        self.ensure_one()
        employees = self.env["hr.employee"]

        if self.maintenance_team_id:
            team = self.maintenance_team_id
            if team.team_leader_id:
                employees |= team.team_leader_id  # already hr.employee
            employees |= team.member_ids

        if self.assigned_user_id:
            employees |= self.assigned_user_id

        if not employees:
            return

        existing_employee_ids = self.technician_log_ids.mapped("employee_id").ids

        new_lines = []
        for emp in employees:
            if emp.id not in existing_employee_ids:
                new_lines.append((0, 0, {
                    "employee_id": emp.id,
                    "date": fields.Date.today(),
                    "clock_in": 0.0,
                    "clock_out": 0.0,
                    "activity": "",
                }))

        if new_lines:
            self.write({"technician_log_ids": new_lines})

    @api.onchange("assigned_user_id", "maintenance_team_id")
    def _onchange_populate_log_lines(self):
        """Onchange wrapper — calls the real logic so it works both on create and UI change."""
        employees = self.env["hr.employee"]

        if self.maintenance_team_id:
            team = self.maintenance_team_id
            if team.team_leader_id:
                employees |= team.team_leader_id  # already hr.employee
            employees |= team.member_ids  # already hr.employee

        if self.assigned_user_id:
            employees |= self.assigned_user_id  # already hr.employee

        if not employees:
            return

        existing_employee_ids = self.technician_log_ids.mapped("employee_id").ids

        new_lines = []
        for emp in employees:
            if emp.id not in existing_employee_ids:
                new_lines.append((0, 0, {
                    "employee_id": emp.id,
                    "date": fields.Date.today(),
                    "clock_in": 0.0,
                    "clock_out": 0.0,
                    "activity": "",
                }))

        if new_lines:
            self.technician_log_ids = list(self.technician_log_ids) + new_lines

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

        # ── Labour Hours ──────────────────────────────────────────────────────
        job_hours = sum(self.technician_log_ids.mapped("hours"))

        total_hours = job_hours

        if total_hours > 0 and self.labour_rate > 0:
            labour_product = self.env["product.product"].search(
                [("name", "ilike", "labour"), ("type", "=", "service")],
                limit=1,
            )
            description_parts = []
            if job_hours:
                description_parts.append("Job Order: %.2f hrs" % job_hours)

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

        # ── Job Order Consumed Materials ──────────────────────────────────────
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

        # ── Additional Services ───────────────────────────────────────────────
        for svc in self.additional_service_ids:
            invoice_lines.append((0, 0, {
                "name": svc.name,
                "product_id": svc.product_id.id if svc.product_id else False,
                "quantity": svc.quantity,
                "price_unit": svc.unit_price,
                "account_id": self._get_income_account(
                    svc.product_id if svc.product_id else False
                ),
            }))

        # ── Guard: nothing to bill ────────────────────────────────────────────
        if not invoice_lines:
            self.message_post(
                body=_(
                    "⚠ No billable items found. Possible causes:<br/>"
                    "• Labour Rate is 0 (set it in the Billing group)<br/>"
                    "• No materials marked as consumed (Qty Used > 0)<br/>"
                    "• No additional services added<br/>"

                )
            )
            _logger.warning(
                "asset.job.order %s: _create_invoice — no billable lines, invoice not created.",
                self.name,
            )
            return

        # ── Build invoice ─────────────────────────────────────────────────────
        invoice_vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "invoice_date": fields.Date.today(),
            "ref": "Job Order: %s" % self.name,
            "narration": (
                             "Invoice for Job Order %(jo)s\n"
                             "Asset: %(asset)s\n"
                             "Closed by: %(closed_by)s\n"
                             "Closed on: %(closed_date)s"
                         ) % {
                             "jo": self.name,
                             "asset": self.asset_id.name if self.asset_id else "N/A",
                             "closed_by": self.closed_by_name or (
                                 self.closed_by.name if self.closed_by else ""
                             ),
                             "closed_date": str(self.closed_date or ""),
                         },
            "invoice_line_ids": invoice_lines,
        }

        try:
            invoice = self.env["account.move"].create(invoice_vals)
            self.invoice_id = invoice.id
            self.message_post(
                body=_(
                    "Draft invoice <a href='/odoo/accounting/customer-invoices/%(id)d'>"
                    "%(name)s</a> created successfully. Review and confirm when ready."
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

    @api.model
    def cron_auto_close_unsigned_jobs(self):
        """
        Runs daily. Auto-closes job orders where:
        - signature_state == 'sent' (email was sent but customer hasn't signed)
        - signature_sent_on is set and is more than 5 days ago
        Sets job order to 'closed' and linked helpdesk ticket to its closed/solved stage.
        """
        cutoff = fields.Datetime.now() - timedelta(days=5)
        jobs = self.search([
            ("signature_state", "=", "sent"),
            ("signature_sent_on", "<=", cutoff),
            ("state", "not in", ["closed", "rejected"]),
        ])

        for job in jobs:
            job.write({
                "state": "closed",
                "closed_date": fields.Datetime.now(),
                "closed_by_name": "Auto-closed (no signature after 5 days)",
            })
            job.message_post(
                body=_(
                    "Job order auto-closed: customer did not sign within 5 days of "
                    "signature request sent on <b>%s</b>."
                ) % job.signature_sent_on.strftime("%d %b %Y"),
                subtype_xmlid="mail.mt_note",
            )

            # Close the linked maintenance task
            task = job.maintenance_task_id
            if task and task.state not in ("done", "cancel"):
                task.write({
                    "state": "done",
                    "done_date": fields.Datetime.now(),
                })

            # Close the linked helpdesk ticket
            ticket = task.helpdesk_ticket_id if task else False
            if ticket:
                closed_stage = self.env["helpdesk.stage"].search(
                    ["|",
                     ("name", "ilike", "solved"),
                     ("name", "ilike", "closed")],
                    order="sequence desc",
                    limit=1,
                )
                if not closed_stage and ticket.team_id:
                    closed_stage = ticket.team_id.close_stage_id
                if closed_stage:
                    ticket.write({"stage_id": closed_stage.id})
                ticket.message_post(
                    body=_(
                        "Ticket auto-closed: linked job order <b>%s</b> was closed "
                        "automatically after customer did not sign within 5 days."
                    ) % job.name,
                    subtype_xmlid="mail.mt_note",
                )

        _logger.info(
            "cron_auto_close_unsigned_jobs: auto-closed %d job order(s).", len(jobs)
        )


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
    notes = fields.Text(string="Additional Notes")  # ← add this
    is_from_inspection = fields.Boolean(readonly=True, default=False)
    image = fields.Image(  # ← add this (primary photo)
        string="Photo",
        max_width=1920,
        max_height=1920,
    )
    image_filename = fields.Char(string="Image Filename")  # ← add this
    image_1 = fields.Image(max_width=1024, max_height=1024)
    image_2 = fields.Image(max_width=1024, max_height=1024)
    image_3 = fields.Image(max_width=1024, max_height=1024)


class AssetJobMaterialLine(models.Model):
    _name = "asset.job.finding.material.line"
    _description = "Job Order Material Line"
    _inherit = ["mail.thread"]
    _order = "id"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        "product.product",
        required=True,
        domain="[('type', 'in', ['consu', 'product'])]",
    )
    description = fields.Char()
    quantity_requested = fields.Float(string="Qty Planned", default=1.0)
    quantity_used = fields.Float(string="Qty Used", default=0.0)
    uom_id = fields.Many2one(
        "uom.uom", related="product_id.uom_id", readonly=True
    )
    on_hand_qty = fields.Float(
        string="Available Stock",
        compute="_compute_on_hand",
        store=False,
    )
    source = fields.Selection([
        ("task", "From Task Plan"),
        ("inspection", "From Inspection"),
        ("manual", "Added on Job"),
    ], default="manual", readonly=True)

    approval_state = fields.Selection([
        ("requested", "Requested"),
        ("approved", "Approved by Storekeeper"),
        ("rejected", "Rejected by Storekeeper"),
        ("collected", "Collected"),
        ("pending_return", "Pending Return"),
        ("consumed", "Consumed"),
        ("returned", "Returned / Not Used"),
    ], default="requested", string="Status", tracking=True)

    rejection_reason = fields.Text(string="Rejection Reason")
    approved_by = fields.Many2one("res.users", string="Approved By", readonly=True)
    approved_date = fields.Datetime(string="Approved On", readonly=True)

    is_consumable = fields.Boolean(
        string="Consumable",
        compute="_compute_is_consumable",
        store=True,
        help="Consumable = used up. Not consumable = tool/equipment to be returned.",
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
        for rec in self:
            rec.is_consumable = (
                rec.product_id.is_consumable_in_inspection
                if rec.product_id else True
            )

    @api.depends("product_id")
    def _compute_on_hand(self):
        for rec in self:
            rec.on_hand_qty = rec.product_id.free_qty if rec.product_id else 0.0

    def _check_storekeeper_access(self):
        if not self.env.user.has_group("asset_management.group_asset_storekeeper"):
            from odoo.exceptions import AccessError
            raise AccessError(
                _("Only the Storekeeper can approve or reject material requests.")
            )

    def action_approve(self):
        self._check_storekeeper_access()
        for rec in self:
            if rec.product_id.free_qty < rec.quantity_requested:
                raise UserError(_(
                    "Cannot approve %(product)s: only %(available)s %(unit)s available "
                    "but %(requested)s %(unit)s requested.\n\n"
                    "Either reduce the requested quantity or replenish stock first.",
                    product=rec.product_id.name,
                    available=rec.product_id.free_qty,
                    unit=rec.uom_id.name,
                    requested=rec.quantity_requested,
                ))
            picking = rec._create_stock_reservation()
            rec.write({
                "approval_state": "approved",
                "approved_by": rec.env.user.id,
                "approved_date": fields.Datetime.now(),
                "picking_id": picking.id,
                "move_id": picking.move_ids[:1].id if picking.move_ids else False,
            })
            rec.job_order_id.message_post(
                body=_(
                    "Material <b>%(product)s</b> approved by <b>%(user)s</b>. "
                    "%(qty)s %(unit)s reserved from stock. "
                    "(Reservation: <b>%(picking)s</b>)",
                    product=rec.product_id.name,
                    user=rec.env.user.name,
                    qty=rec.quantity_requested,
                    unit=rec.uom_id.name,
                    picking=picking.name,
                ),
                subtype_xmlid="mail.mt_note",
            )

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

    def _do_reject(self, reason=""):
        self._cancel_stock_reservation()
        self.write({
            "approval_state": "rejected",
            "rejection_reason": reason,
        })

    def action_mark_collected(self):
        for rec in self:
            if rec.picking_id and rec.picking_id.state == "assigned":
                for move in rec.picking_id.move_ids:
                    move.quantity = move.product_uom_qty
                rec.picking_id.sudo().button_validate()
            rec.write({"approval_state": "collected"})
            rec.job_order_id.message_post(
                body=_(
                    "Material <b>%(product)s</b> collected from store by <b>%(user)s</b>.",
                    product=rec.product_id.name,
                    user=rec.env.user.name,
                ),
                subtype_xmlid="mail.mt_note",
            )

    def _post_consumption_move(self):
        """
        Write off consumed qty: Job Order Reserved → Job Order Consumed (production virtual).
        Uses a raw stock.move — pickings cannot route to virtual locations.
        """
        self.ensure_one()

        if not self.quantity_used or self.quantity_used <= 0:
            return

        job_location = self.env["stock.location"].search([
            ("name", "=", "Job Order Reserved"),
            ("usage", "=", "internal"),
        ], limit=1)

        if not job_location:
            self.job_order_id.message_post(
                body=_(
                    "⚠ 'Job Order Reserved' location not found — consumption move skipped for <b>%s</b>.") % self.product_id.name,
                subtype_xmlid="mail.mt_note",
            )
            return

        # Prefer child production location, fall back to any production location
        consumed_location = self.env["stock.location"].search([
            ("location_id", "=", job_location.id),
            ("usage", "=", "production"),
        ], limit=1)

        if not consumed_location:
            consumed_location = self.env["stock.location"].search([
                ("usage", "=", "production"),
                ("name", "in", ["Job Order Consumed", "Inspection Consumed", "Consumption", "Production"]),
            ], limit=1)

        if not consumed_location:
            self.job_order_id.message_post(
                body=_("⚠ No consumption/production virtual location found — move skipped for <b>%s</b>. "
                       "Please create a child location under 'Job Order Reserved' with type 'Production'.") % self.product_id.name,
                subtype_xmlid="mail.mt_note",
            )
            return

        move = self.env["stock.move"].sudo().create({
            "description_picking": f"Consumed: {self.product_id.name} [{self.job_order_id.name}]",
            "product_id": self.product_id.id,
            "product_uom": self.uom_id.id,
            "product_uom_qty": self.quantity_used,
            "quantity": self.quantity_used,
            "location_id": job_location.id,
            "location_dest_id": consumed_location.id,
            "origin": f"CONSUMED-{self.job_order_id.name}",
            "state": "confirmed",
        })

        self.env["stock.move.line"].sudo().create({
            "move_id": move.id,
            "product_id": self.product_id.id,
            "product_uom_id": self.uom_id.id,
            "quantity": self.quantity_used,
            "location_id": job_location.id,
            "location_dest_id": consumed_location.id,
        })

        move.sudo()._action_done()

        self.write({"approval_state": "consumed"})

        self.job_order_id.message_post(
            body=_(
                "%(qty)s × <b>%(product)s</b> written off "
                "(Job Order Reserved → %(dest)s). Origin: <b>%(origin)s</b>.",
                qty=self.quantity_used,
                product=self.product_id.name,
                dest=consumed_location.complete_name,
                origin=move.origin,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def action_return_unused(self):
        for rec in self:
            # For consumables, return only the unused remainder
            qty_to_return = (
                rec.quantity_requested - rec.quantity_used
                if rec.is_consumable and rec.quantity_used > 0
                else rec.quantity_requested
            )

            if not rec.picking_id:
                rec.write({"approval_state": "returned"})
                rec.job_order_id.message_post(
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
                return_picking = rec.env["stock.picking"].sudo().create({
                    "picking_type_id": original.picking_type_id.id,
                    "location_id": original.location_dest_id.id,
                    "location_dest_id": original.location_id.id,
                    "origin": f"Return of {original.name}",
                    "move_ids": [(0, 0, {
                        "product_id": rec.product_id.id,
                        "product_uom": rec.uom_id.id,
                        "product_uom_qty": qty_to_return,
                        "quantity": qty_to_return,
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
                rec.write({"approval_state": "returned"})
                rec.job_order_id.message_post(
                    body=_(
                        "%(qty)s × <b>%(product)s</b> returned to stock. "
                        "Return picking: <b>%(picking)s</b>.",
                        product=rec.product_id.name,
                        qty=qty_to_return,
                        picking=return_picking.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

            elif picking_state in ("assigned", "confirmed", "waiting"):
                rec.picking_id.action_cancel()
                rec.write({"approval_state": "returned"})
                rec.job_order_id.message_post(
                    body=_(
                        "Stock reservation for <b>%(product)s</b> cancelled and "
                        "equipment marked as returned.",
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

            elif picking_state == "cancel":
                rec.write({"approval_state": "returned"})
                rec.job_order_id.message_post(
                    body=_(
                        "Equipment <b>%(product)s</b> marked as returned "
                        "(reservation was already cancelled).",
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

    def action_confirm_return(self):
        self._check_storekeeper_access()
        for rec in self:
            if rec.approval_state != "pending_return":
                continue
            rec.action_return_unused()

    def _cancel_stock_reservation(self):
        for rec in self:
            if rec.picking_id and rec.picking_id.state not in ("done", "cancel"):
                rec.picking_id.action_cancel()
                rec.job_order_id.message_post(
                    body=_(
                        "Stock reservation <b>%(picking)s</b> for <b>%(product)s</b> "
                        "cancelled and stock returned to available.",
                        picking=rec.picking_id.name,
                        product=rec.product_id.name,
                    ),
                    subtype_xmlid="mail.mt_note",
                )

    def _create_stock_reservation(self):
        self.ensure_one()
        maintenance_company = self._get_maintenance_company()
        env = self.env(context=dict(
            self.env.context,
            allowed_company_ids=[maintenance_company.id],
            force_company=maintenance_company.id,
        ))

        warehouse = env["stock.warehouse"].search(
            [("company_id", "=", maintenance_company.id)], limit=1
        )
        if not warehouse:
            raise UserError(_(
                "No warehouse found for maintenance company '%s'. "
                "Please configure a warehouse for that company."
            ) % maintenance_company.name)

        src_location = warehouse.lot_stock_id
        dest_location = self._get_or_create_job_order_location()

        picking_type = env["stock.picking.type"].search([
            ("code", "=", "internal"),
            ("warehouse_id", "=", warehouse.id),
            ("company_id", "=", maintenance_company.id),
        ], limit=1)
        if not picking_type:
            raise UserError(_(
                "No internal picking type found for warehouse '%s' "
                "(company: %s). Please configure stock operations."
            ) % (warehouse.name, maintenance_company.name))

        picking = env["stock.picking"].sudo().create({
            "picking_type_id": picking_type.id,
            "location_id": src_location.id,
            "location_dest_id": dest_location.id,
            "origin": f"JO-{self.job_order_id.name}",
            "company_id": maintenance_company.id,
            "move_ids": [(0, 0, {
                "product_id": self.product_id.id,
                "product_uom": self.uom_id.id,
                "product_uom_qty": self.quantity_requested,
                "location_id": src_location.id,
                "location_dest_id": dest_location.id,
                "company_id": maintenance_company.id,
            })],
        })
        picking.action_confirm()
        picking.action_assign()
        return picking

    def _get_maintenance_company(self):
        """
        Returns the company that owns the maintenance/stock operations.
        Looks for a company with 'maintenance' in the name, falling back
        to the job order's own company, then env.company.
        You can also hardcode by setting the parameter
        'asset_management.maintenance_company_id' in System Parameters.
        """
        # Check system parameter first (most reliable)
        param = self.env["ir.config_parameter"].sudo().get_param(
            "asset_management.maintenance_company_id"
        )
        if param:
            company = self.env["res.company"].sudo().browse(int(param))
            if company.exists():
                return company

        # Fall back: company linked to the maintenance task/job order record
        if hasattr(self, "job_order_id") and self.job_order_id.company_id:
            return self.job_order_id.company_id
        if hasattr(self, "company_id") and self.company_id:
            return self.company_id

        # Last resort: current company
        return self.env.company

    def _get_or_create_job_order_location(self):
        maintenance_company = self._get_maintenance_company()
        location = self.env["stock.location"].sudo().search([
            ("name", "=", "Job Order Reserved"),
            ("usage", "=", "internal"),
            ("company_id", "=", maintenance_company.id),
        ], limit=1)

        if not location:
            parent = self.env["stock.location"].sudo().search([
                ("usage", "=", "view"),
                ("company_id", "=", maintenance_company.id),
            ], limit=1)
            location = self.env["stock.location"].sudo().create({
                "name": "Job Order Reserved",
                "usage": "internal",
                "location_id": parent.id if parent else False,
                "company_id": maintenance_company.id,
            })
        return location


class AssetTechnicianLog(models.Model):
    _name = "asset.technician.log"
    _description = "Technician Time Log"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, ondelete="cascade"
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
    )
    date = fields.Date(default=fields.Date.today, required=True)
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
    activity = fields.Char(string="Work Description")

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


class AssetTaskMaterialLine(models.Model):
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
            rec.on_hand_qty = rec.product_id.free_qty if rec.product_id else 0.0


class AssetTaskLabourLine(models.Model):
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

class AssetJobOrderInspection(models.Model):
    _inherit = "asset.job.order"

    property_id = fields.Many2one(
        related="maintenance_task_id.property_id", store=True, string="Property"
    )
    inspection_type = fields.Selection(
        related="maintenance_task_id.inspection_type", store=True,
        string="Inspection Type",
    )