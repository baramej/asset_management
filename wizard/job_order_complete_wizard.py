from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class AssetJobMaterialRequestWizard(models.TransientModel):
    _name = "asset.job.material.request.wizard"
    _description = "Job Order — Request Materials"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, readonly=True
    )
    line_ids = fields.One2many(
        "asset.job.material.request.wizard.line",
        "wizard_id",
        string="Materials",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        job_id = self.env.context.get("default_job_order_id")
        if not job_id:
            return res
        job = self.env["asset.job.order"].browse(job_id)
        lines = []
        for m in job.material_line_ids:
            lines.append((0, 0, {
                "product_id": m.product_id.id,
                "description": m.description or "",
                "quantity_requested": m.quantity_requested,
                "material_line_id": m.id,
            }))
        res["line_ids"] = lines
        return res

    def action_confirm(self):
        self.ensure_one()
        job = self.job_order_id

        # Build a lookup of existing job order material lines by product
        existing_by_product = {
            line.product_id.id: line
            for line in job.material_line_ids
        }

        for line in self.line_ids:
            if line.product_id.id in existing_by_product:
                # Existing product — update quantity if changed
                existing_line = existing_by_product[line.product_id.id]
                if existing_line.quantity_requested != line.quantity_requested:
                    existing_line.write({
                        "quantity_requested": line.quantity_requested,
                    })
            else:
                # Genuinely new product — create it
                self.env["asset.job.finding.material.line"].create({
                    "job_order_id": job.id,
                    "product_id": line.product_id.id,
                    "description": line.description,
                    "quantity_requested": line.quantity_requested,
                    "source": "manual",
                })

        job.write({
            "state": "request_material",
            "material_approval_state": "pending",
        })
        job.message_post(
            body=_("Materials requested by %s — awaiting storekeeper approval.")
                 % self.env.user.name
        )
        return {"type": "ir.actions.act_window_close"}

class AssetJobMaterialRequestWizardLine(models.TransientModel):
    _name = "asset.job.material.request.wizard.line"
    _description = "Job Material Request Wizard Line"

    wizard_id = fields.Many2one(
        "asset.job.material.request.wizard",
        required=True, ondelete="cascade"
    )
    material_line_id = fields.Many2one(
        "asset.job.finding.material.line", readonly=True
    )
    product_id = fields.Many2one("product.product", required=True)
    description = fields.Char()
    quantity_requested = fields.Float(default=1.0)

    is_consumable = fields.Boolean(
        string="Consumable",
        compute="_compute_is_consumable",
        store=False,
    )

    @api.depends("product_id")
    def _compute_is_consumable(self):
        for rec in self:
            if rec.product_id:
                rec.is_consumable = rec.product_id.is_consumable_in_inspection
            else:
                rec.is_consumable = True


class AssetJobCompleteWizard(models.TransientModel):
    _name = "asset.job.complete.wizard"
    _description = "Job Order — End Work & Confirm Materials"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, readonly=True
    )
    line_ids = fields.One2many(
        "asset.job.complete.wizard.line",
        "wizard_id",
        string="Materials",
    )
    technician_notes = fields.Text(string="Technician Notes")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        job_id = self.env.context.get("default_job_order_id")
        if not job_id:
            return res
        job = self.env["asset.job.order"].browse(job_id)
        lines = []
        for m in job.material_line_ids.filtered(
            lambda l: l.approval_state == "approved"
        ):
            lines.append((0, 0, {
                "material_line_id": m.id,
                "product_id": m.product_id.id,
                "quantity_requested": m.quantity_requested,
                "quantity_used": m.quantity_requested,
                "consumption_state": "consumed",
            }))
        res["line_ids"] = lines
        return res

    def action_confirm(self):
        self.ensure_one()
        job = self.job_order_id

        for line in self.line_ids:
            if line.material_line_id:
                line.material_line_id.write({
                    "quantity_used": line.quantity_used,
                })

        if self.technician_notes:
            job.technician_notes = self.technician_notes

        job._do_end_work()
        return {"type": "ir.actions.act_window_close"}


class AssetJobCompleteWizardLine(models.TransientModel):
    _name = "asset.job.complete.wizard.line"
    _description = "Job Complete Wizard Line"

    wizard_id = fields.Many2one(
        "asset.job.complete.wizard",
        required=True, ondelete="cascade"
    )
    material_line_id = fields.Many2one(
        "asset.job.finding.material.line", readonly=True
    )
    product_id = fields.Many2one("product.product", readonly=True)
    quantity_requested = fields.Float(string="Planned", readonly=True)
    quantity_used = fields.Float(string="Used", default=0.0)
    consumption_state = fields.Selection([
        ("consumed", "Consumed"),
        ("returned", "Returned / Not Used"),
    ], default="consumed", required=True)

    @api.onchange("consumption_state")
    def _onchange_consumption_state(self):
        if self.consumption_state == "returned":
            self.quantity_used = 0.0
        elif self.consumption_state == "consumed" and self.quantity_used == 0.0:
            self.quantity_used = self.quantity_requested


class AssetJobCloseWizard(models.TransientModel):
    _name = "asset.job.close.wizard"
    _description = "Job Order — Close with Signature"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, readonly=True
    )
    signed_by_name = fields.Char(
        string="Full Name (Signature)",
        required=True,
    )
    signature = fields.Binary(
        string="Signature",
        required=True,
    )
    supervisor_close_notes = fields.Text(string="Closing Notes")

    def action_confirm(self):
        self.ensure_one()
        job = self.job_order_id
        if self.supervisor_close_notes:
            job.supervisor_close_notes = self.supervisor_close_notes
        job._do_close(
            signed_by_name=self.signed_by_name,
            signature=self.signature,
        )
        return {"type": "ir.actions.act_window_close"}


class AssetJobApproveWizard(models.TransientModel):
    _name = "asset.job.approve.wizard"
    _description = "Job Order — Approve with Signature"

    job_order_id = fields.Many2one(
        "asset.job.order", required=True, readonly=True
    )
    signed_by_name = fields.Char(
        string="Full Name (Signature)",
        required=True,
    )
    signature = fields.Binary(
        string="Signature",
        required=True,
    )
    approval_notes = fields.Text(string="Approval Notes")

    def action_confirm(self):
        self.ensure_one()
        job = self.job_order_id
        if self.approval_notes:
            job.supervisor_close_notes = self.approval_notes
        job._do_approve(
            signed_by_name=self.signed_by_name,
            signature=self.signature,
        )
        return {"type": "ir.actions.act_window_close"}