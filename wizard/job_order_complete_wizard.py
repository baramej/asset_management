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

        existing_by_product = {
            line.product_id.id: line
            for line in job.material_line_ids
        }

        for line in self.line_ids:
            if line.product_id.id in existing_by_product:
                existing_line = existing_by_product[line.product_id.id]
                if existing_line.quantity_requested != line.quantity_requested:
                    existing_line.write({"quantity_requested": line.quantity_requested})
            else:
                self.env["asset.job.finding.material.line"].create({
                    "job_order_id": job.id,
                    "product_id": line.product_id.id,
                    "description": line.description,
                    "quantity_requested": line.quantity_requested,
                    "source": "manual",
                    "approval_state": "requested",
                })

        job.write({"state": "request_material"})
        job._send_material_request_email()
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
    product_id = fields.Many2one(
        "product.product",
        required=True,
        domain="[('type', 'in', ['consu', 'product'])]",
    )
    description = fields.Char()
    quantity_requested = fields.Float(default=1.0)
    product_uom_id = fields.Many2one(
        "uom.uom", related="product_id.uom_id", readonly=True
    )
    qty_available = fields.Float(
        string="In Stock",
        compute="_compute_qty_available",
        store=False,
    )
    is_consumable = fields.Boolean(
        string="Consumable",
        compute="_compute_is_consumable",
        store=False,
    )

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.qty_available = self.product_id.free_qty

    @api.depends("product_id")
    def _compute_qty_available(self):
        for rec in self:
            rec.qty_available = rec.product_id.free_qty if rec.product_id else 0.0

    @api.depends("product_id")
    def _compute_is_consumable(self):
        for rec in self:
            rec.is_consumable = (
                rec.product_id.is_consumable_in_inspection
                if rec.product_id else True
            )


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

    has_non_consumables = fields.Boolean(
        compute="_compute_has_non_consumables",
    )

    @api.depends("line_ids.is_consumable")
    def _compute_has_non_consumables(self):
        for rec in self:
            rec.has_non_consumables = any(
                not l.is_consumable for l in rec.line_ids
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        job_id = self.env.context.get("default_job_order_id")
        if not job_id:
            return res
        job = self.env["asset.job.order"].browse(job_id)
        lines = []
        for m in job.material_line_ids.filtered(
                lambda l: l.approval_state == "collected"
        ):
            if m.is_consumable:
                consumption_state = "consumed"
                quantity_used = m.quantity_requested
            else:
                consumption_state = "pending_return"
                quantity_used = 0.0

            lines.append((0, 0, {
                "material_line_id": m.id,
                "material_line_real_id": m.id,
                "product_id": m.product_id.id,
                "is_consumable": m.is_consumable,
                "quantity_requested": m.quantity_requested,
                "quantity_used": quantity_used,
                "consumption_state": consumption_state,
            }))
        res["line_ids"] = lines
        return res

    def action_confirm(self):
        self.ensure_one()
        job = self.job_order_id
        MatLine = self.env["asset.job.finding.material.line"]

        for line in self.line_ids:
            # Primary: use the Many2one directly (works when not GC'd)
            mat = line.material_line_id if line.material_line_id else None

            # Secondary: browse by stored integer ID
            if not mat and line.material_line_real_id:
                mat = MatLine.browse(line.material_line_real_id)
                if not mat.exists():
                    mat = None

            # Last resort: match by product + collected state
            if not mat and line.product_id:
                mat = job.material_line_ids.filtered(
                    lambda m, l=line: m.product_id.id == l.product_id.id
                                      and m.approval_state == "collected"
                )[:1]

            if not mat:
                _logger.warning(
                    "AssetJobCompleteWizard: could not resolve material line for "
                    "product %s (material_line_id=%s, real_id=%s) — skipping.",
                    line.product_id.display_name if line.product_id else "False",
                    line.material_line_id.id if line.material_line_id else 0,
                    line.material_line_real_id,
                )
                continue

            _logger.info(
                "EndWork: resolved mat=%s product=%s is_consumable=%s qty_used=%s state=%s",
                mat.id,
                mat.product_id.display_name,
                line.is_consumable,
                line.quantity_used,
                line.consumption_state,
            )

            if line.is_consumable:
                qty = line.quantity_used if line.consumption_state != "returned" else 0.0
                mat.write({"quantity_used": qty})
                # Do NOT set approval_state here — _do_end_work will handle it:
                # qty_used == 0  → pending_return (storekeeper confirms physical return)
                # qty_used > 0  → consumed or partial → pending_return for remainder
            else:
                mat.write({"quantity_used": line.quantity_used})
                # Non-consumables: _do_end_work sets pending_return, storekeeper confirms

        if self.technician_notes:
            job.write({"technician_notes": self.technician_notes})

        self.env.cr.flush()
        MatLine.invalidate_model(["quantity_used", "approval_state"])

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
        "asset.job.finding.material.line"
    )
    # Store the real record ID as a plain integer — Many2one on transient models
    # can be garbage-collected before action_confirm runs
    material_line_real_id = fields.Integer(string="Material Line ID")
    product_id = fields.Many2one("product.product", readonly=True)
    is_consumable = fields.Boolean(string="Consumable", readonly=True)
    quantity_requested = fields.Float(string="Planned", readonly=True)
    quantity_used = fields.Float(string="Used", default=0.0)
    consumption_state = fields.Selection([
        ("consumed", "Consumed"),
        ("returned", "Returned / Not Used"),
        ("pending_return", "Pending Return to Store"),
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
    customer_approval = fields.Boolean(
        related="job_order_id.customer_approval",
        readonly=True,
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
