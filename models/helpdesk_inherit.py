from odoo import models, fields, api, _
from odoo.exceptions import UserError
import uuid as _uuid


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    asset_id = fields.Many2one(
        "account.asset",
        string="Asset",
        help="Asset related to this issue",
    )
    cafm_ticket_id = fields.Many2one(
        "asset.service.ticket",
        string="CAFM Service Ticket",
        readonly=True,
    )
    maintenance_task_id = fields.Many2one(
        "asset.maintenance.task",
        string="Maintenance Task",
        readonly=True,
    )
    portal_line_ids = fields.One2many(
        "helpdesk.portal.ticket.line", "ticket_id",
        string="Portal Data", readonly=True
    )
    portal_data_count = fields.Integer(
        compute="_compute_portal_data_count"
    )

    @api.depends("portal_line_ids")
    def _compute_portal_data_count(self):
        for rec in self:
            rec.portal_data_count = len(rec.portal_line_ids)

    def action_view_portal_data(self):
        self.ensure_one()
        line = self.portal_line_ids[:1]
        if not line:
            return
        return {
            "name": _("Portal Submission Data"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.portal.ticket.line",
            "view_mode": "form",
            "res_id": line.id,
            "target": "new",
        }

    maintenance_task_count = fields.Integer(
        string="Maintenance Tasks",
        compute="_compute_maintenance_task_count",
    )

    contract_id = fields.Many2one(
        "asset.maintenance.contract",
        string="AMC Contract",
        compute="_compute_asset_contract",
        store=False,
    )
    contract_name = fields.Char(
        string="Contract Name",
        compute="_compute_asset_contract",
        store=False,
    )
    contract_reference = fields.Char(
        string="Contract Reference",
        compute="_compute_asset_contract",
        store=False,
    )

    manager_approval_state = fields.Selection([
        ("waiting_manager_approval", "Waiting Manager Approval"),
        ("approved", "Approved by Manager"),
        ("denied", "Denied by Manager"),
    ], string="Manager Approval", default=False, tracking=True)

    approval_access_token = fields.Char(string="Approval Token", copy=False, readonly=True)
    manager_id = fields.Many2one("res.users", string="Employee Manager", readonly=True)
    manager_employee_id = fields.Many2one(
        "hr.employee", string="Approving Manager", readonly=True,
        help="Requester's manager (hr.employee.parent_id). The manager does not "
        "need an Odoo login: the approval link is emailed to them.",
    )
    manager_email = fields.Char(string="Manager Email", readonly=True)

    def action_debug_approval(self):
        """Temporary debug button — remove after fixing."""
        self.ensure_one()
        ticket = self
        lines = []

        lines.append(f"partner_id: {ticket.partner_id} (id={ticket.partner_id.id})")
        lines.append(f"partner email: {ticket.partner_id.email!r}")

        # Search 1: by work_email
        emp1 = self.env["hr.employee"].sudo().search(
            [("work_email", "=", ticket.partner_id.email)], limit=1
        )
        lines.append(f"employee by work_email: {emp1} (id={emp1.id if emp1 else None})")

        # Search 2: by user partner
        emp2 = self.env["hr.employee"].sudo().search(
            [("user_id.partner_id", "=", ticket.partner_id.id)], limit=1
        )
        lines.append(f"employee by user partner: {emp2} (id={emp2.id if emp2 else None})")

        emp = emp1 or emp2
        if emp:
            lines.append(
                f"parent_id (manager employee): {emp.parent_id} (id={emp.parent_id.id if emp.parent_id else None})")
            if emp.parent_id:
                lines.append(
                    f"manager user_id: {emp.parent_id.user_id} (id={emp.parent_id.user_id.id if emp.parent_id.user_id else None})")
                lines.append(f"manager email: {emp.parent_id.user_id.email!r}")

        result = "\n".join(lines)
        raise UserError(result)

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        for ticket in tickets:

            if not ticket.partner_id and ticket.user_id:
                ticket.partner_id = ticket.user_id.partner_id
            # CAFM ticket creation
            if ticket.asset_id:
                cafm = self.env["asset.service.ticket"].create({
                    "name": ticket.name,
                    "asset_id": ticket.asset_id.id,
                    "ticket_type": "incident",
                    "description": ticket.description or "",
                    "priority": ticket.priority,
                })
                ticket.cafm_ticket_id = cafm.id

            # Manager approval logic
            manager = ticket._get_manager_employee()
            email = self._manager_email_of(manager)
            if email:
                token = str(_uuid.uuid4())
                ticket.write({
                    "manager_approval_state": "waiting_manager_approval",
                    "approval_access_token": token,
                    "manager_id": manager.user_id.id or False,
                    "manager_employee_id": manager.id,
                    "manager_email": email,
                    "stage_id": ticket._get_waiting_approval_stage().id,
                })
                ticket._send_manager_approval_email(manager.name, email, token)

        return tickets

    def _get_requester_employee(self):
        """hr.employee behind the ticket's customer (portal user first)."""
        self.ensure_one()
        partner = self.partner_id or self.env.user.partner_id
        Employee = self.env["hr.employee"].sudo()
        employee = Employee.browse()
        for user in partner.user_ids:
            employee = user._asset_portal_employee()
            if employee:
                break
        if not employee:
            employee = Employee.search([("work_contact_id", "=", partner.id)], limit=1)
        if not employee and partner.email:
            employee = Employee.search([("work_email", "=ilike", partner.email)], limit=1)
        # Last resort: match by the ticket's assigned user (backend tickets)
        if not employee and self.user_id:
            employee = Employee.search([("user_id", "=", self.user_id.id)], limit=1)
        return employee

    def _get_manager_employee(self):
        """Requester's manager as hr.employee (may have no Odoo user)."""
        self.ensure_one()
        return self._get_requester_employee().parent_id

    @api.model
    def _manager_email_of(self, manager):
        if not manager:
            return False
        return manager.work_email or manager.user_id.email or False

    def _get_employee_manager(self):
        """Kept for compatibility: the manager's res.users, if they have one."""
        self.ensure_one()
        user = self._get_manager_employee().user_id
        return user if user and user.email else False

    def _get_waiting_approval_stage(self):
        """Get or create a 'Waiting Manager Approval' stage."""
        stage = self.env["helpdesk.stage"].search(
            [("name", "ilike", "Waiting Manager Approval")], limit=1
        )
        if not stage:
            stage = self.env["helpdesk.stage"].sudo().create({
                "name": "Waiting Manager Approval",
                "sequence": 0,
                "fold": False,
            })
        return stage

    def _send_manager_approval_email(self, manager_name, manager_email, token):
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        approval_url = f"{base_url}/helpdesk/ticket/{self.id}/manager-review?token={token}"

        employee_name = self.partner_id.name or "An employee"
        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                    border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
            <div style="background-color:#1a1a2e;padding:24px 32px;">
                <h2 style="color:#ffffff;margin:0;">Helpdesk Ticket — Manager Approval Required</h2>
            </div>
            <div style="padding:24px 32px;background-color:#ffffff;">
                <p style="color:#333;font-size:15px;">Dear {manager_name},</p>
                <p style="color:#333;font-size:15px;">
                    <strong>{employee_name}</strong> has submitted a helpdesk ticket that requires your approval
                    before it is processed by the support team.
                </p>
                <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:35%;border:1px solid #ddd;">Ticket</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Submitted By</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{employee_name}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Description</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{self.description or '—'}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Priority</td>
                        <td style="padding:8px 12px;border:1px solid #ddd;">{dict(self._fields['priority'].selection).get(self.priority, self.priority)}</td>
                    </tr>
                </table>
                <p style="color:#333;font-size:15px;">
                    Please review and take action:
                </p>
                <div style="text-align:center;margin:28px 0;">
                    <a href="{approval_url}"
                       style="background-color:#1a1a2e;color:white;padding:12px 28px;
                              border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                        Review Ticket &amp; Decide
                    </a>
                </div>
                <p style="color:#888;font-size:13px;text-align:center;">
                    This link is unique to you. Do not share it.
                </p>
            </div>
            <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                <p style="color:#aaa;font-size:12px;margin:0;">
                    Automated notification — Asset Management System.
                </p>
            </div>
        </div>"""

        self.env["mail.mail"].sudo().create({
            "subject": f"Approval Required — Helpdesk Ticket: {self.name}",
            "body_html": body_html,
            "email_to": manager_email,
            "author_id": self.env.user.partner_id.id,
            "auto_delete": False,
            "state": "outgoing",
        }).send(raise_exception=False)

        self.message_post(
            body=_(
                "Manager approval email sent to <b>%(name)s</b> (%(email)s). "
                "Ticket is pending approval.",
                name=manager_name,
                email=manager_email,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def action_manager_approve(self):
        """Called when manager approves via portal or backend."""
        self.ensure_one()
        new_stage = self.env["helpdesk.stage"].search(
            [("name", "ilike", "New")], limit=1
        )
        vals = {"manager_approval_state": "approved"}
        if new_stage:
            vals["stage_id"] = new_stage.id
        self.write(vals)
        self.message_post(
            body=_("Ticket approved by manager <b>%s</b>. Moved to New.")
            % (self.manager_employee_id.name or self.manager_id.name)
        )

    def action_manager_deny(self, reason=""):
        """Called when manager denies via portal or backend."""
        self.ensure_one()
        closed_stage = self.env["helpdesk.stage"].search(
            [("name", "ilike", "Closed"), ("fold", "=", True)], limit=1
        )
        if not closed_stage:
            closed_stage = self.env["helpdesk.stage"].search(
                [("name", "ilike", "Solved")], limit=1
            )
        vals = {"manager_approval_state": "denied"}
        if closed_stage:
            vals["stage_id"] = closed_stage.id
        self.write(vals)
        self.message_post(
            body=_(
                "Ticket denied by manager <b>%(manager)s</b>. Reason: %(reason)s",
                manager=self.manager_employee_id.name or self.manager_id.name,
                reason=reason or "—",
            )
        )

    @api.depends("asset_id")
    def _compute_asset_contract(self):
        for ticket in self:
            if ticket.asset_id:
                contract_line = self.env["asset.maintenance.contract.line"].search([
                    ("asset_id", "=", ticket.asset_id.id),
                ], limit=1)
                if contract_line and contract_line.contract_id:
                    contract = contract_line.contract_id
                    ticket.contract_id = contract.id
                    ticket.contract_name = contract.name
                    ticket.contract_reference = contract.reference or ""
                else:
                    ticket.contract_id = False
                    ticket.contract_name = ""
                    ticket.contract_reference = ""
            else:
                ticket.contract_id = False
                ticket.contract_name = ""
                ticket.contract_reference = ""

    @api.depends("maintenance_task_id")
    def _compute_maintenance_task_count(self):
        for ticket in self:
            ticket.maintenance_task_count = 1 if ticket.maintenance_task_id else 0

    def action_schedule_inspection(self):
        """Opens the schedule task wizard (replaces old inspection wizard)."""
        self.ensure_one()
        return {
            "name": _("Schedule Maintenance Task"),
            "type": "ir.actions.act_window",
            "res_model": "asset.schedule.task.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_ticket_id": self.id,
            },
        }

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

class HelpdeskTicketInspection(models.Model):
    _inherit = "helpdesk.ticket"

    property_id = fields.Many2one("property.details", string="Property", readonly=True)
    inspection_type = fields.Selection(
        [("move_in", "Move In"), ("move_out", "Move Out")],
        string="Inspection Type", readonly=True,
    )