from odoo import models, fields, api, _
from odoo.exceptions import UserError


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
    inspection_id = fields.Many2one(
        "asset.inspection",
        string="Inspection",
        readonly=True,
    )
    inspection_state = fields.Selection(
        related="inspection_id.state",
        string="Inspection Status",
        readonly=True,
    )

    inspection_count = fields.Integer(
        string="Inspections",
        compute="_compute_inspection_count",
    )
    maintenance_task_count = fields.Integer(
        string="Maintenance Tasks",
        compute="_compute_maintenance_task_count",
    )

    @api.depends("inspection_id")
    def _compute_inspection_count(self):
        for ticket in self:
            ticket.inspection_count = 1 if ticket.inspection_id else 0

    @api.depends("maintenance_task_id")
    def _compute_maintenance_task_count(self):
        for ticket in self:
            ticket.maintenance_task_count = 1 if ticket.maintenance_task_id else 0

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        for ticket in tickets:
            if ticket.asset_id:
                cafm = self.env["asset.service.ticket"].create({
                    "name": ticket.name,
                    "asset_id": ticket.asset_id.id,
                    "ticket_type": "incident",
                    "description": ticket.description or "",
                    "priority": ticket.priority,
                })
                ticket.cafm_ticket_id = cafm.id
        return tickets

    def action_schedule_inspection(self):
        self.ensure_one()
        return {
            "name": _("Schedule Inspection"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_ticket_id": self.id,
            },
        }

    def action_view_inspection(self):
        """Smart button — opens inspection form with full details."""
        self.ensure_one()
        return {
            "name": _("Inspection"),
            "type": "ir.actions.act_window",
            "res_model": "asset.inspection",
            "view_mode": "form",
            "res_id": self.inspection_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_view_maintenance_task(self):
        """Smart button — opens maintenance task form."""
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

    def action_create_maintenance_task(self):
        self.ensure_one()
        asset = self.asset_id or self.inspection_id.asset_id

        task = self.env["asset.maintenance.task"].create({
            "name": f"Maintenance from Helpdesk - {self.name}",
            "asset_id": asset.id,
            "maintenance_type": "corrective",
            "description": self.description,
            "maintenance_team_id": self.inspection_id.maintenance_team_id.id or False,
            "helpdesk_ticket_id": self.id,  # ← add this
            "inspection_id": self.inspection_id.id or False,
        })

        # Pre-populate findings from inspection
        if self.inspection_id and self.inspection_id.finding_ids:
            for finding in self.inspection_id.finding_ids:
                self.env["asset.task.finding.line"].create({
                    "task_id": task.id,
                    "description": finding.description,
                    "severity": finding.severity,
                    "source_finding_id": finding.id,
                    "is_from_inspection": True,
                    "image_1": finding.image_1 if hasattr(finding, "image_1") else False,
                    "image_2": finding.image_2 if hasattr(finding, "image_2") else False,
                    "image_3": finding.image_3 if hasattr(finding, "image_3") else False,
                })

        # Pre-populate materials from inspection spare parts
        if self.inspection_id and self.asset_id.spare_part_line_ids:
            for part in self.asset_id.spare_part_line_ids.filtered(
                    lambda p: p.usage_type in ("corrective", "both")
            ):
                self.env["asset.task.material.line"].create({
                    "task_id": task.id,
                    "product_id": part.product_id.id,
                    "quantity": part.quantity,
                    "source": "inspection",
                })

        self.write({"maintenance_task_id": task.id})
        return {
            "name": _("Maintenance Task"),
            "type": "ir.actions.act_window",
            "res_model": "asset.maintenance.task",
            "view_mode": "form",
            "res_id": task.id,
            "views": [(False, "form")],
            "target": "current",
        }
