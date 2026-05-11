from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AssetMaintenanceTask(models.Model):
    _inherit = "asset.maintenance.task"

    service_type = fields.Selection([
        ("preventive_maintenance",   "Preventive Maintenance"),
        ("housekeeping_routine",     "Housekeeping — Routine"),
        ("housekeeping_deep",        "Housekeeping — Deep Clean"),
        ("housekeeping_adhoc",       "Housekeeping — Ad-hoc"),
        ("gardening_daily",          "Gardening — Daily/Weekly"),
        ("gardening_monthly",        "Gardening — Monthly/Seasonal"),
        ("gardening_adhoc",          "Gardening — Ad-hoc"),
        ("facility_soft",            "Facility — Soft Services"),
        ("facility_hard",            "Facility — Hard Services"),
        ("emergency",                "Emergency / Breakdown"),
    ], string="Service Type", tracking=True)

    pm_schedule_id = fields.Many2one(
        "asset.pm.schedule",
        string="PM Schedule",
        readonly=True,
        help="The schedule that auto-generated this task.",
    )

    checklist_line_ids = fields.One2many(
        "asset.task.checklist.line",
        "task_id",
        string="Checklist",
    )
    checklist_progress = fields.Float(
        string="Checklist Progress (%)",
        compute="_compute_checklist_progress",
        store=False,
    )
    checklist_complete = fields.Boolean(
        string="Checklist Complete",
        compute="_compute_checklist_progress",
        store=False,
    )

    is_emergency = fields.Boolean(
        string="Emergency / Breakdown",
        default=False,
        tracking=True,
        help="If enabled, skips inspection and routes directly to job order.",
    )

    requires_supervisor_signoff = fields.Boolean(
        string="Supervisor Sign-off Required",
        default=False,
    )
    supervisor_signoff_state = fields.Selection([
        ("not_required", "Not Required"),
        ("pending",      "Pending"),
        ("approved",     "Approved"),
        ("rejected",     "Rejected"),
    ], default="not_required", string="Supervisor Sign-off", tracking=True)

    supervisor_signoff_by   = fields.Many2one("res.users", readonly=True)
    supervisor_signoff_date = fields.Datetime(readonly=True)
    supervisor_signoff_note = fields.Text(string="Sign-off Notes")

    billing_entity_id = fields.Many2one(
        "res.partner",
        string="Billing Entity",
        compute="_compute_billing_entity",
        store=True,
        readonly=False,
        tracking=True,
        help="Customer/company to be billed for this task. Pulled from the asset.",
    )

    @api.depends("asset_id", "asset_id.customer_id")
    def _compute_billing_entity(self):
        for rec in self:
            rec.billing_entity_id = rec.asset_id.customer_id if rec.asset_id else False

    @api.depends("checklist_line_ids.is_done", "checklist_line_ids.is_mandatory")
    def _compute_checklist_progress(self):
        for rec in self:
            lines = rec.checklist_line_ids
            total = len(lines)
            done  = len(lines.filtered("is_done"))

            if total:
                rec.checklist_progress = (done / total) * 100
                mandatory_undone = lines.filtered(
                    lambda l: l.is_mandatory and not l.is_done
                )
                rec.checklist_complete = not bool(mandatory_undone)
            else:
                rec.checklist_progress = 100.0
                rec.checklist_complete  = True

    @api.onchange("service_type")
    def _onchange_service_type_load_checklist(self):
        if not self.service_type:
            return
        template = self.env["asset.checklist.template"].search(
            [("service_type", "=", self.service_type), ("active", "=", True)],
            limit=1,
        )
        if not template:
            return

        self.checklist_line_ids = [(5, 0, 0)]  # clear existing
        lines = []
        for tl in template.line_ids:
            lines.append((0, 0, {
                "sequence":    tl.sequence,
                "description": tl.description,
                "is_mandatory": tl.is_mandatory,
                "notes":       tl.notes,
            }))
        self.checklist_line_ids = lines

    def _load_checklist_from_template(self, template=None):
        """
        Populate checklist lines from the given template (or auto-detect
        from service_type if no template is passed).
        Existing lines are replaced.
        """
        self.ensure_one()
        if not template:
            if not self.service_type:
                return
            template = self.env["asset.checklist.template"].search(
                [("service_type", "=", self.service_type), ("active", "=", True)],
                limit=1,
            )
        if not template:
            return

        self.checklist_line_ids.unlink()

        vals_list = []
        for tl in template.line_ids:
            vals_list.append({
                "task_id":     self.id,
                "sequence":    tl.sequence,
                "description": tl.description,
                "is_mandatory": tl.is_mandatory,
                "notes":       tl.notes,
            })
        if vals_list:
            self.env["asset.task.checklist.line"].create(vals_list)

    def action_done(self):
        for rec in self:
            if not rec.checklist_complete:
                undone = rec.checklist_line_ids.filtered(
                    lambda l: l.is_mandatory and not l.is_done
                ).mapped("description")
                raise UserError(_(
                    "Cannot close task — the following mandatory checklist items "
                    "are not completed:\n• %s"
                ) % "\n• ".join(undone))

            if (
                rec.requires_supervisor_signoff
                and rec.supervisor_signoff_state not in ("approved", "not_required")
            ):
                if rec.supervisor_signoff_state == "pending":
                    raise UserError(_(
                        "Supervisor sign-off is still pending. "
                        "Please wait for the supervisor to approve before closing."
                    ))
                if rec.supervisor_signoff_state == "rejected":
                    raise UserError(_(
                        "Supervisor sign-off was rejected. "
                        "Please address the issues and request sign-off again."
                    ))
                # not yet requested → auto-request it
                rec.action_request_supervisor_signoff()
                raise UserError(_(
                    "Supervisor sign-off is required. "
                    "A sign-off request has been sent. "
                    "You can close the task once it is approved."
                ))

        return super().action_done()

    def action_request_supervisor_signoff(self):
        self.ensure_one()
        self.write({
            "requires_supervisor_signoff": True,
            "supervisor_signoff_state": "pending",
        })
        self.message_post(
            body=_("Supervisor sign-off requested by %s") % self.env.user.name
        )

    def action_supervisor_approve_signoff(self):
        self.ensure_one()
        self.write({
            "supervisor_signoff_state": "approved",
            "supervisor_signoff_by":   self.env.user.id,
            "supervisor_signoff_date": fields.Datetime.now(),
        })
        self.message_post(
            body=_("Task sign-off approved by %s") % self.env.user.name
        )

    def action_supervisor_reject_signoff(self):
        self.ensure_one()
        self.write({
            "supervisor_signoff_state": "rejected",
            "supervisor_signoff_by":   self.env.user.id,
            "supervisor_signoff_date": fields.Datetime.now(),
        })
        self.message_post(
            body=_("Task sign-off rejected by %s. Please review and re-request.")
            % self.env.user.name
        )