from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetInspectionCompleteWizard(models.TransientModel):
    _name = "asset.inspection.complete.wizard"
    _description = "Complete Inspection — Confirm Parts & Escalation"

    inspection_id = fields.Many2one(
        "asset.inspection",
        required=True,
        readonly=True,
    )

    line_ids = fields.One2many(
        "asset.inspection.complete.wizard.line",
        "wizard_id",
        string="Materials",
    )

    escalation_required = fields.Selection(
        [
            ("no", "No — Issue resolved during inspection"),
            ("yes", "Yes — Escalate to Corrective Maintenance"),
        ],
        string="Escalation Required?",
        required=True,
        default="no",
    )

    escalation_notes = fields.Text(
        string="Escalation Notes",
        help="Describe what needs to be done in the maintenance task.",
    )

    resolution_notes = fields.Text(
        string="Resolution Notes",
        help="Describe how the issue was resolved during inspection.",
    )

    has_non_consumables = fields.Boolean(
        compute="_compute_has_non_consumables",
    )

    @api.depends("line_ids.is_consumable", "line_ids.qty_consumed", "line_ids.qty_requested")
    def _compute_has_non_consumables(self):
        for rec in self:
            rec.has_non_consumables = any(
                not l.is_consumable
                or (l.is_consumable and l.qty_consumed < l.qty_requested)
                for l in rec.line_ids
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        inspection_id = self.env.context.get("default_inspection_id")
        if not inspection_id:
            return res

        inspection = self.env["asset.inspection"].browse(inspection_id)
        lines = []
        for mat in inspection.material_ids.filtered(
                lambda m: m.state in ("collected", "approved")
        ):
            if mat.is_consumable:
                consumption_state = "consumed"
                qty_consumed = mat.qty_requested
            else:
                consumption_state = "pending_return"
                qty_consumed = 0.0

            lines.append((0, 0, {
                "material_id": mat.id,
                "product_id": mat.product_id.id,
                "qty_requested": mat.qty_requested,
                "is_consumable": mat.is_consumable,
                "qty_consumed": qty_consumed,
                "consumption_state": consumption_state,
            }))
        res["line_ids"] = lines
        return res

    @api.constrains("escalation_required", "escalation_notes")
    def _check_escalation_notes(self):
        for rec in self:
            if rec.escalation_required == "yes" and not rec.escalation_notes:
                raise UserError(
                    _("Please provide escalation notes describing what maintenance is needed.")
                )

    def action_confirm(self):
        self.ensure_one()
        inspection = self.inspection_id

        for line in self.line_ids:
            target = line.material_id

            if not target and line.product_id:
                target = inspection.material_ids.filtered(
                    lambda m: m.product_id == line.product_id
                              and m.state in ("collected", "approved")
                )[:1]

            if not target:
                continue

            if not line.is_consumable:
                target.write({"qty_consumed": line.qty_consumed})
            else:
                # If partially consumed, flag remainder for return
                if line.consumption_state == "consumed" and line.qty_consumed < line.qty_requested:
                    target.write({
                        "qty_consumed": line.qty_consumed,
                        "state": "pending_return",
                    })
                else:
                    target.write({
                        "qty_consumed": line.qty_consumed,
                        "state": line.consumption_state,
                    })

        inspection.write({
            "escalation_required": self.escalation_required == "yes",
            "resolution_notes": self.resolution_notes or "",
            "escalation_notes": self.escalation_notes or "",
        })

        inspection._do_complete()

        return {
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "form",
            "res_id": inspection.id,
            "views": [(False, "form")],
            "target": "current",
        }


class AssetInspectionCompleteWizardLine(models.TransientModel):
    _name = "asset.inspection.complete.wizard.line"
    _description = "Completion Wizard — Material Line"

    wizard_id = fields.Many2one(
        "asset.inspection.complete.wizard",
        required=True,
        ondelete="cascade",
    )
    material_id = fields.Many2one(
        "asset.inspection.material",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        readonly=True,
    )
    is_consumable = fields.Boolean(
        string="Consumable",
        readonly=True,
    )
    qty_requested = fields.Float(string="Requested", readonly=True)
    qty_consumed = fields.Float(string="Consumed", default=0.0)
    consumption_state = fields.Selection(
        [
            ("consumed", "Consumed"),
            ("returned", "Returned / Not Used"),
            ("pending_return", "Pending Return to Store"),  # ← for non-consumables display
        ],
        string="Status",
        default="consumed",
        required=True,
    )

    @api.onchange("consumption_state")
    def _onchange_consumption_state(self):
        if self.consumption_state == "returned":
            self.qty_consumed = 0.0
        elif self.consumption_state == "consumed" and self.qty_consumed == 0.0:
            self.qty_consumed = self.qty_requested
