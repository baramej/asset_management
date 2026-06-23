from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetInspectionWizard(models.TransientModel):
    _name = "asset.inspection.wizard"
    _description = "Schedule Asset Inspection"

    ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Helpdesk Ticket",
        required=True,
        readonly=True,
    )

    inspection_type = fields.Selection([
        ("general", "General Inspection"),
        ("flat", "Flat Inspection"),
    ], string="Inspection Type", default="general", required=True)

    maintenance_team_id = fields.Many2one(
        "asset.maintenance.team",
        string="Maintenance Team",
    )

    flat_asset_id = fields.Many2one(
        "account.asset",
        string="Flat / Unit",
    )
    resident_name = fields.Char(string="Resident Name")
    flat_no = fields.Char(string="Flat No.")
    checklist_template_id = fields.Many2one(
        "asset.flat.checklist.template",
        string="Checklist Template",
    )
    employee_ids = fields.Many2many(
        "hr.employee",
        string="Employees",
        help="Assign individual employees instead of (or in addition to) a team.",
    )

    scheduled_date = fields.Datetime(
        string="Scheduled Date & Time",
        required=True,
        default=fields.Datetime.now,
    )

    notes = fields.Text(string="Notes")

    team_schedule_ids = fields.One2many(
        "asset.inspection.wizard.schedule.line",
        "wizard_id",
        string="Team Schedule",
        readonly=True,
    )

    @api.onchange("maintenance_team_id")
    def _onchange_maintenance_team_id(self):
        self._load_team_schedule()

    def _load_team_schedule(self):
        self.team_schedule_ids = [(5, 0, 0)]

        if not self.maintenance_team_id:
            return

        team = self.maintenance_team_id
        lines = []

        inspections = self.env["asset.inspection"].search([
            ("maintenance_team_id", "=", team.id),
            ("state", "in", ["scheduled", "request_material", "material_collected", "in_progress"]),
            ("scheduled_date", "!=", False),
        ], order="scheduled_date asc")

        for insp in inspections:
            lines.append((0, 0, {
                "work_type": "inspection",
                "reference": insp.name,
                "asset_name": insp.asset_id.name if insp.asset_id else "-",
                "scheduled_date": insp.scheduled_date,
                "state": dict(insp._fields["state"].selection).get(insp.state, insp.state),
                "color_state": insp.state,
            }))

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

    def _send_inspection_assigned_email(self, inspection):

        if not self.maintenance_team_id or not self.maintenance_team_id.team_leader_id:
            return

        leader = self.maintenance_team_id.team_leader_id  # hr.employee
        leader_email = leader.work_email  # employees use work_email
        if not leader_email:
            return

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        action = self.env.ref(
            "asset_management.action_asset_inspection", raise_if_not_found=False
        )
        action_id = action.id if action else "asset_inspection"
        inspection_url = f"{base}/odoo/action-{action_id}/{inspection.id}"

        member_names = ", ".join(self.maintenance_team_id.member_ids.mapped("name")) or "—"

        employee_names = ", ".join(self.employee_ids.mapped("name")) or "—"

        subject = f"New Inspection Assigned – {inspection.name}"

        body_html = f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;
                        border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">

                <!-- Header -->
                <div style="background-color:#00695C;padding:24px 32px;">
                    <h2 style="color:#ffffff;margin:0;">New Inspection Assigned to Your Team</h2>
                </div>

                <!-- Body -->
                <div style="padding:24px 32px;background-color:#ffffff;">
                    <p style="color:#333;font-size:15px;">
                        Hello <b>{leader.name}</b>,
                    </p>
                    <p style="color:#333;font-size:15px;">
                        A new inspection has been scheduled and assigned to your team:
                    </p>

                    <!-- Details table -->
                    <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;width:40%;border:1px solid #ddd;">
                                Inspection Reference
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{inspection.name}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Asset
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">
                                {inspection.asset_id.name if inspection.asset_id else 'N/A'}
                            </td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Scheduled Date
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{inspection.scheduled_date}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Maintenance Team
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">
                                {self.maintenance_team_id.name}
                            </td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Team Members
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{member_names}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Assigned Employees
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{employee_names}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Helpdesk Ticket
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">
                                {inspection.ticket_id.name if inspection.ticket_id else 'N/A'}
                            </td>
                        </tr>
                        {"" if not self.notes else f'''
                        <tr>
                            <td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;border:1px solid #ddd;">
                                Notes
                            </td>
                            <td style="padding:8px 12px;border:1px solid #ddd;">{self.notes}</td>
                        </tr>'''}
                    </table>

                    <div style="text-align:center;margin:28px 0;">
                        <a href="{inspection_url}"
                           style="background-color:#00695C;color:white;padding:12px 28px;
                                  border-radius:4px;text-decoration:none;font-size:15px;font-weight:bold;">
                            View Inspection
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
            inspection.message_post(
                body=_(
                    "Failed to send inspection assignment email to team leader "
                    "<b>%(name)s</b> (%(email)s): %(error)s",
                    name=leader.name,
                    email=leader_email,
                    error=str(e),
                ),
                subtype_xmlid="mail.mt_note",
            )
            return

        inspection.message_post(
            body=_(
                "Inspection assignment email sent to team leader "
                "<b>%(name)s</b> (%(email)s).",
                name=leader.name,
                email=leader_email,
            ),
            subtype_xmlid="mail.mt_note",
        )

    def action_confirm(self):
        self.ensure_one()

        if self.inspection_type == "flat":
            if not self.checklist_template_id:
                raise UserError(_("Please select a Checklist Template for flat inspections."))
            if not (self.maintenance_team_id or self.employee_ids):
                raise UserError(_("Please assign at least a Maintenance Team or one Employee."))

        inspection = self.env["asset.inspection"].create({
            "ticket_id": self.ticket_id.id,
            "asset_id": self.ticket_id.asset_id.id if self.ticket_id.asset_id else False,
            "maintenance_team_id": self.maintenance_team_id.id or False,
            "employee_ids": [(6, 0, self.employee_ids.ids)],
            "scheduled_date": self.scheduled_date,
            "notes": self.notes,
            "state": "scheduled",
            "inspection_type": self.inspection_type,
            "flat_asset_id": self.flat_asset_id.id if self.flat_asset_id else False,
            "resident_name": self.resident_name,
            "flat_no": self.flat_no,
            "checklist_template_id": self.checklist_template_id.id if self.checklist_template_id else False,
        })

        employees = self.env["hr.employee"]
        if self.maintenance_team_id:
            team = self.maintenance_team_id
            if team.team_leader_id:
                employees |= team.team_leader_id  # already hr.employee
            employees |= team.member_ids  # already hr.employee
        employees |= self.employee_ids

        if employees:
            inspection.write({"labor_ids": [(0, 0, {
                "employee_id": emp.id,
                "date": self.scheduled_date.date() if self.scheduled_date else fields.Date.today(),
                "hours": 0.0,
                "description": "",
            }) for emp in employees]})

        if self.inspection_type == "flat" and self.checklist_template_id:
            inspection.action_load_checklist()

        under_inspection_stage = self.env["helpdesk.stage"].search(
            [("name", "=", "Under Inspection")], limit=1
        )
        if not under_inspection_stage:
            under_inspection_stage = self.env["helpdesk.stage"].create({
                "name": "Under Inspection",
                "sequence": 5,
            })

        self.ticket_id.write({
            "stage_id": under_inspection_stage.id,
            "inspection_id": inspection.id,
        })

        self.ticket_id.message_post(
            body=_(
                "Inspection scheduled on %(date)s.%(team)s%(employees)s",
                date=self.scheduled_date,
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

        self._send_inspection_assigned_email(inspection)

        return {
            "name": _("Inspection"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "form",
            "res_id": inspection.id,
            "target": "current",
            "views": [(False, "form")],
        }


class AssetInspectionWizardScheduleLine(models.TransientModel):
    _name = "asset.inspection.wizard.schedule.line"
    _description = "Team Schedule Preview Line"
    _order = "scheduled_date asc"

    wizard_id = fields.Many2one(
        "asset.inspection.wizard",
        required=True,
        ondelete="cascade",
    )

    work_type = fields.Selection([
        ("inspection", "Inspection"),
        ("preventive", "Preventive Maintenance"),
        ("corrective", "Corrective Maintenance"),
        ("job_order", "Job Order"),
    ], string="Type", readonly=True)

    reference = fields.Char(string="Reference", readonly=True)
    asset_name = fields.Char(string="Asset", readonly=True)
    scheduled_date = fields.Datetime(string="Scheduled", readonly=True)
    state = fields.Char(string="Status", readonly=True)

    color_state = fields.Char(readonly=True)
