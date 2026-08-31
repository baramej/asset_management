from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetScheduleTaskWizard(models.TransientModel):
    _name = "asset.schedule.task.wizard"
    _description = "Schedule Maintenance Task from Ticket"

    ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Helpdesk Ticket",
        required=True,
        readonly=True,
    )

    job_type = fields.Selection([
        ("general", "General Job"),
        ("flat", "Flat Inspection"),
    ], string="Job Type", default="general", required=True)

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        string="Maintenance Team",
    )

    employee_ids = fields.Many2many(
        "hr.employee",
        string="Employees",
        help="Assign individual employees instead of (or in addition to) a team.",
    )

    # Flat-inspection specific fields
    flat_asset_id = fields.Many2one(
        "account.asset",
        string="Flat / Unit",
        help="The specific flat/unit being inspected.",
    )
    resident_name = fields.Char(string="Resident Name")
    flat_no = fields.Char(string="Flat No.")
    checklist_template_id = fields.Many2one(
        "asset.flat.checklist.template",
        string="Checklist Template",
        help="Select the template matching this flat type.",
    )

    scheduled_date = fields.Datetime(
        string="Scheduled Date & Time",
        required=False,
    )

    notes = fields.Text(string="Notes")

    team_schedule_ids = fields.One2many(
        "asset.schedule.task.wizard.schedule.line",
        "wizard_id",
        string="Team Schedule",
        readonly=True,
    )

    property_id = fields.Many2one(
        related="ticket_id.property_id", store=False, readonly=True
    )
    inspection_type = fields.Selection(
        related="ticket_id.inspection_type", store=False, readonly=True
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ticket_id = res.get("ticket_id") or self.env.context.get("default_ticket_id")
        if ticket_id and "checklist_template_id" in fields_list:
            ticket = self.env["helpdesk.ticket"].browse(ticket_id)
            if ticket.inspection_type == "move_out" and ticket.property_id:
                last_move_in = self.env["asset.maintenance.task"].sudo().search(
                    [
                        ("property_id", "=", ticket.property_id.id),
                        ("inspection_type", "=", "move_in"),
                        ("checklist_template_id", "!=", False),
                    ],
                    order="request_date desc",
                    limit=1,
                )
                if last_move_in:
                    res["checklist_template_id"] = last_move_in.checklist_template_id.id
        return res

    @api.onchange("maintenance_team_id")
    def _onchange_maintenance_team_id(self):
        self._load_team_schedule()

    def _load_team_schedule(self):
        self.team_schedule_ids = [(5, 0, 0)]

        if not self.maintenance_team_id:
            return

        team = self.maintenance_team_id
        lines = []

        tasks = self.env["asset.maintenance.task"].search([
            ("maintenance_team_id", "=", team.id),
            ("state", "in", ["assigned", "in_progress"]),
            ("scheduled_date", "!=", False),
        ], order="scheduled_date asc")

        for task in tasks:
            lines.append((0, 0, {
                "work_type": "preventive" if task.maintenance_type == "preventive" else "corrective",
                "reference": task.name,
                "asset_name": task.asset_id.name if task.asset_id else "-",
                "scheduled_date": task.scheduled_date,
                "state": dict(task._fields["state"].selection).get(task.state, task.state),
                "color_state": task.state,
            }))

        job_orders = self.env["asset.job.order"].search([
            ("maintenance_team_id", "=", team.id),
            ("state", "in", ["request_material", "material_approved", "in_progress"]),
            ("scheduled_date", "!=", False),
        ], order="scheduled_date asc")

        for job in job_orders:
            lines.append((0, 0, {
                "work_type": "job_order",
                "reference": job.name,
                "asset_name": job.asset_id.name if job.asset_id else "-",
                "scheduled_date": job.scheduled_date,
                "state": dict(job._fields["state"].selection).get(job.state, job.state),
                "color_state": job.state,
            }))

        lines.sort(key=lambda l: l[2]["scheduled_date"])
        self.team_schedule_ids = lines

    @api.constrains("maintenance_team_id", "employee_ids")
    def _check_assignment(self):
        for rec in self:
            if not rec.maintenance_team_id and not rec.employee_ids:
                raise UserError(
                    _("Please assign at least a Maintenance Team or one Employee.")
                )

    def _send_task_assigned_email(self, task):
        if not self.maintenance_team_id:
            return

        team = self.maintenance_team_id
        leader_email = team.team_leader_email
        if not leader_email and team.team_leader_id:
            leader_email = team.team_leader_id.work_email
        if not leader_email:
            return

        leader_name = team.team_leader_id.name if team.team_leader_id else team.name
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref(
            "asset_management.action_asset_maintenance_task", raise_if_not_found=False
        )
        action_id = action.id if action else "asset_maintenance_task"
        task_url = f"{base}/odoo/action-{action_id}/{task.id}"

        member_names = ", ".join(team.member_ids.mapped("name")) or "—"
        employee_names = ", ".join(self.employee_ids.mapped("name")) or "—"
        scheduled_label = str(self.scheduled_date) if self.scheduled_date else "To be determined"

        subject = f"New Maintenance Task Assigned – {task.name}"

        body_html = f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                        border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
                <div style="background-color:#00695C;padding:24px 32px;">
                    <h2 style="color:#ffffff;margin:0;">New Maintenance Task Assigned to Your Team</h2>
                </div>
                <div style="padding:24px 32px;background-color:#ffffff;">
                    <p style="color:#333;font-size:15px;">
                        Hello <b>{leader_name}</b>,
                    </p>
                    <p style="color:#333;font-size:15px;">
                        A new maintenance task has been scheduled and assigned to your team:
                    </p>
                    <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">Task Reference</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{task.name}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Asset</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{task.asset_id.name if task.asset_id else 'N/A'}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Job Type</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{'Flat Inspection' if self.job_type == 'flat' else 'General Job'}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Scheduled Date</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{scheduled_label}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Maintenance Team</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{team.name}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Team Members</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{member_names}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Assigned Employees</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{employee_names}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Helpdesk Ticket</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{self.ticket_id.name if self.ticket_id else 'N/A'}</td>
                        </tr>
                        {"" if not self.notes else f'''
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">Notes</td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{self.notes}</td>
                        </tr>'''}
                    </table>
                    <div style="text-align:center;margin:28px 0;">
                        <a href="{task_url}"
                           style="background-color:#00695C;color:white;padding:12px 28px;
                                  border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                            View Maintenance Task
                        </a>
                    </div>
                    <p style="color:#888;font-size:13px;text-align:center;">
                        Please ensure your team is ready for the scheduled date.
                    </p>
                </div>
                <div style="background-color:#f5f5f5;padding:14px 32px;text-align:center;">
                    <p style="color:#aaa;font-size:12px;margin:0;">
                        This is an automated notification from the Asset Management system.
                    </p>
                </div>
            </div>"""

        mail = self.env["mail.mail"].sudo().create({
            "subject": subject,
            "body_html": body_html,
            "email_to": leader_email,
            "author_id": self.env.user.partner_id.id,
            "auto_delete": False,
            "state": "outgoing",
        })

        try:
            mail.send(raise_exception=False)
        except Exception as e:
            task.message_post(
                body=_(
                    "Failed to send task assignment email to <b>%(email)s</b>: %(error)s",
                    email=leader_email,
                    error=str(e),
                ),
                subtype_xmlid="mail.mt_note",
            )
            return

        task.message_post(
            body=_(
                "Task assignment email sent to <b>%(name)s</b> (%(email)s).",
                name=leader_name,
                email=leader_email,
            ),
            subtype_xmlid="mail.mt_note",
        )

    team_leader_notify_email = fields.Char(
        string="Notification Email",
        compute="_compute_team_leader_notify_email",
    )

    @api.depends("maintenance_team_id")
    def _compute_team_leader_notify_email(self):
        for rec in self:
            team = rec.maintenance_team_id
            if team:
                rec.team_leader_notify_email = (
                        team.team_leader_email
                        or (team.team_leader_id.work_email if team.team_leader_id else "")
                        or ""
                )
            else:
                rec.team_leader_notify_email = ""

    def action_confirm(self):
        self.ensure_one()

        if self.job_type == "flat" and not self.checklist_template_id:
            raise UserError(_("Please select a Checklist Template for flat inspections."))

        if not self.maintenance_team_id and not self.employee_ids:
            raise UserError(_("Please assign at least a Maintenance Team or one Employee."))

        ticket = self.ticket_id
        asset = ticket.asset_id

        task_vals = {
            "name": f"Task from Ticket - {ticket.name}",
            "asset_id": asset.id if asset else False,
            "maintenance_type": "corrective",
            "description": ticket.description or self.notes or "",
            "maintenance_team_id": self.maintenance_team_id.id or False,
            "helpdesk_ticket_id": ticket.id,
            "job_type": self.job_type,
            "state": "draft",
        }

        if self.scheduled_date:
            task_vals["scheduled_date"] = self.scheduled_date

        # Flat-specific fields
        if self.job_type == "flat":
            task_vals.update({
                "flat_asset_id": self.flat_asset_id.id if self.flat_asset_id else False,
                "resident_name": self.resident_name or "",
                "flat_no": self.flat_no or "",
                "checklist_template_id": self.checklist_template_id.id if self.checklist_template_id else False,
            })

        task = self.env["asset.maintenance.task"].create(task_vals)

        # Assign labour lines from team + employees
        employees = self.env["hr.employee"]
        if self.maintenance_team_id:
            team = self.maintenance_team_id
            if team.team_leader_id:
                employees |= team.team_leader_id
            employees |= team.member_ids
        employees |= self.employee_ids

        if employees:
            # asset.task.labour.line uses user_id (res.users), not employee_id
            user_vals = []
            for emp in employees:
                if emp.user_id:
                    user_vals.append((0, 0, {
                        "user_id": emp.user_id.id,
                        "role": emp.job_title or "",
                        "planned_hours": 0.0,
                    }))
            if user_vals:
                task.write({"planned_labour_ids": user_vals})

        # Load flat checklist into job order (will be transferred on job order creation)
        if self.job_type == "flat" and self.checklist_template_id:
            task.action_load_checklist()

        # Move ticket to "In Progress" stage
        in_progress_stage = self.env["helpdesk.stage"].search(
            [("name", "ilike", "in progress")], limit=1
        )
        ticket_vals = {"maintenance_task_id": task.id}
        if in_progress_stage:
            ticket_vals["stage_id"] = in_progress_stage.id

        ticket.write(ticket_vals)

        ticket.message_post(
            body=_(
                "Maintenance task created%(date)s.%(team)s%(employees)s",
                date=(
                    f" scheduled for {self.scheduled_date}"
                    if self.scheduled_date else ""
                ),
                team=(
                    f"<br/>Team: <b>{self.maintenance_team_id.name}</b>"
                    if self.maintenance_team_id else ""
                ),
                employees=(
                    "<br/>Employees: <b>"
                    + ", ".join(self.employee_ids.mapped("name"))
                    + "</b>"
                    if self.employee_ids else ""
                ),
            ),
            subtype_xmlid="mail.mt_note",
        )

        self._send_task_assigned_email(task)

        # Only redirect to the task if the user has asset management access
        if self.env.user.has_group('asset_management.group_asset_inspector') or \
                self.env.user.has_group('asset_management.group_asset_manager'):
            return {
                "name": _("Maintenance Task"),
                "type": "ir.actions.act_window",
                "res_model": "asset.maintenance.task",
                "view_mode": "form",
                "res_id": task.id,
                "target": "current",
                "views": [(False, "form")],
            }

        # Ticket Supervisor: just close the wizard and stay on the ticket
        return {"type": "ir.actions.act_window_close"}


class AssetScheduleTaskWizardScheduleLine(models.TransientModel):
    _name = "asset.schedule.task.wizard.schedule.line"
    _description = "Team Schedule Preview Line"
    _order = "scheduled_date asc"

    wizard_id = fields.Many2one(
        "asset.schedule.task.wizard",
        required=True,
        ondelete="cascade",
    )

    work_type = fields.Selection([
        ("preventive", "Preventive Maintenance"),
        ("corrective", "Corrective Maintenance"),
        ("job_order", "Job Order"),
    ], string="Type", readonly=True)

    reference = fields.Char(string="Reference", readonly=True)
    asset_name = fields.Char(string="Asset", readonly=True)
    scheduled_date = fields.Datetime(string="Scheduled", readonly=True)
    state = fields.Char(string="Status", readonly=True)
    color_state = fields.Char(readonly=True)
