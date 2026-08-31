from odoo import _, api, fields, models


class PropertyInspectionWizard(models.TransientModel):
    _name = "property.inspection.wizard"
    _description = "Schedule Property Inspection"

    property_id = fields.Many2one(
        "property.details", string="Property", required=True, readonly=True
    )
    building_id = fields.Many2one(
        "property.sub.project", string="Building", readonly=True
    )
    flat_no = fields.Char(string="Flat No.", readonly=True)
    occupant_id = fields.Many2one(
        "res.partner", string="Occupant", readonly=True
    )

    inspection_type = fields.Selection(
        [("move_in", "Move In Inspection"), ("move_out", "Move Out Inspection")],
        string="Inspection Type",
        required=True,
        default="move_in",
    )
    scheduled_date = fields.Datetime(string="Scheduled Date & Time")
    notes = fields.Text(string="Notes")

    checklist_template_id = fields.Many2one(
        "asset.flat.checklist.template", string="Checklist Template"
    )

    @api.onchange("inspection_type", "property_id")
    def _onchange_pick_template(self):
        for rec in self:
            if not rec.property_id:
                continue
            if rec.inspection_type == "move_out":
                last_move_in = self.env["asset.maintenance.task"].sudo().search(
                    [
                        ("property_id", "=", rec.property_id.id),
                        ("inspection_type", "=", "move_in"),
                        ("checklist_template_id", "!=", False),
                    ],
                    order="request_date desc",
                    limit=1,
                )
                if last_move_in:
                    rec.checklist_template_id = last_move_in.checklist_template_id
            elif not rec.checklist_template_id:
                template = False
                if rec.property_id.unit_type:
                    bhk_label = f"{rec.property_id.unit_type}BHK"
                    template = self.env["asset.flat.checklist.template"].search(
                        [("name", "=ilike", bhk_label)], limit=1
                    )
                if not template:
                    template = self.env["asset.flat.checklist.template"].search([], limit=1)
                rec.checklist_template_id = template

    def action_confirm(self):
        self.ensure_one()

        type_label = dict(self._fields["inspection_type"].selection).get(
            self.inspection_type
        )
        building_name = self.building_id.name or ""
        occupant_name = self.occupant_id.name if self.occupant_id else "Vacant"

        title_parts = [p for p in [type_label, self.flat_no, building_name, occupant_name] if p]
        ticket_name = " - ".join(title_parts)

        description = _(
            "%(type)s for unit %(unit)s%(building)s.\n%(occupant)s%(notes)s",
            type=type_label,
            unit=self.flat_no or "",
            building=f" in {building_name}" if building_name else "",
            occupant=(
                f"Occupant: {self.occupant_id.name}"
                if self.occupant_id else "Unit currently vacant."
            ),
            notes=f"\n\nNotes: {self.notes}" if self.notes else "",
        )

        ticket_vals = {
            "name": ticket_name,
            "description": description,
            "property_id": self.property_id.id,
            "inspection_type": self.inspection_type,
        }
        if self.occupant_id:
            ticket_vals["partner_id"] = self.occupant_id.id

        ticket = self.env["helpdesk.ticket"].sudo().create(ticket_vals)

        if self.scheduled_date and "scheduled_date" in ticket._fields:
            ticket.scheduled_date = self.scheduled_date

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Inspection Ticket Created"),
                "message": _("Ticket %s has been created.", ticket.name),
                "sticky": False,
            },
        }