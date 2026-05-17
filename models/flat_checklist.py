from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetFlatChecklistTemplate(models.Model):
    _name = "asset.flat.checklist.template"
    _description = "Flat Inspection Checklist Template"
    _order = "name"

    name = fields.Char(string="Template Name", required=True)
    location = fields.Char(string="Location / Building")
    description = fields.Text(string="Description")
    active = fields.Boolean(default=True)

    line_ids = fields.One2many(
        "asset.flat.checklist.template.line",
        "template_id",
        string="Checklist Items",
        copy=True,
    )

    line_keys_ids = fields.One2many(
        "asset.flat.checklist.template.line", "template_id",
        string="Keys",
        domain=[("section", "=", "keys")],
        context={"default_section": "keys"},
    )
    line_electrical_ids = fields.One2many(
        "asset.flat.checklist.template.line", "template_id",
        string="Electrical",
        domain=[("section", "=", "electrical")],
        context={"default_section": "electrical"},
    )
    line_plumbing_ids = fields.One2many(
        "asset.flat.checklist.template.line", "template_id",
        string="Plumbing",
        domain=[("section", "=", "plumbing")],
        context={"default_section": "plumbing"},
    )
    line_general_ids = fields.One2many(
        "asset.flat.checklist.template.line", "template_id",
        string="General / Damages",
        domain=[("section", "=", "general")],
        context={"default_section": "general"},
    )
    line_other_ids = fields.One2many(
        "asset.flat.checklist.template.line", "template_id",
        string="Other",
        domain=[("section", "=", "other")],
        context={"default_section": "other"},
    )

    line_count = fields.Integer(compute="_compute_line_count", string="Items")

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)


class AssetFlatChecklistTemplateLine(models.Model):
    _name = "asset.flat.checklist.template.line"
    _description = "Flat Checklist Template Line"
    _order = "section, sequence, id"

    template_id = fields.Many2one(
        "asset.flat.checklist.template",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)

    section = fields.Selection([
        ("keys", "Keys"),
        ("electrical", "Electrical"),
        ("plumbing", "Plumbing"),
        ("general", "General / Damages"),
        ("other", "Other"),
    ], required=True, default="keys", string="Section")

    description = fields.Char(string="Item Description", required=True)
    expected_qty = fields.Char(
        string="Expected Qty",
        help="e.g. '3 Nos', '1 No' — displayed for reference during inspection.",
    )
    is_meter_reading = fields.Boolean(
        string="Meter Reading",
        help="Tick if this line requires a numeric meter reading (water/electricity).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        default_section = self.env.context.get("default_section")
        for vals in vals_list:
            if default_section and not vals.get("section"):
                vals["section"] = default_section
        return super().create(vals_list)



class AssetFlatChecklistLine(models.Model):
    _name = "asset.flat.checklist.line"
    _description = "Flat Inspection Checklist Line"
    _order = "section, sequence, id"

    inspection_id = fields.Many2one(
        "asset.inspection",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)

    section = fields.Selection([
        ("keys", "Keys"),
        ("electrical", "Electrical"),
        ("plumbing", "Plumbing"),
        ("general", "General / Damages"),
        ("other", "Other"),
    ], required=True, default="keys", string="Section")

    description = fields.Char(string="Item", required=True)
    expected_qty = fields.Char(string="Qty", readonly=True)

    status = fields.Selection([
        ("ok", "OK"),
        ("missing", "Missing"),
        ("damaged", "Damaged"),
        ("na", "N/A"),
    ], string="Status")

    remarks = fields.Char(string="Remarks")
    is_meter_reading = fields.Boolean(readonly=True)
    meter_value = fields.Float(string="Reading", digits=(10, 2))
    meter_date = fields.Date(string="Reading Date")

    source_line_id = fields.Many2one(
        "asset.flat.checklist.template.line",
        readonly=True,
        string="Template Line",
    )

    @api.model_create_multi
    def create(self, vals_list):
        default_section = self.env.context.get("default_section")
        for vals in vals_list:
            if default_section and not vals.get("section"):
                vals["section"] = default_section
        return super().create(vals_list)



class AssetInspectionFlatExtension(models.Model):
    _inherit = "asset.inspection"

    inspection_type = fields.Selection([
        ("general", "General Inspection"),
        ("flat", "Flat Inspection"),
    ], string="Inspection Type", default="general", required=True, tracking=True)

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

    checklist_line_ids = fields.One2many(
        "asset.flat.checklist.line", "inspection_id",
        string="Checklist",
    )
    checklist_keys_ids = fields.One2many(
        "asset.flat.checklist.line", "inspection_id",
        string="Keys",
        context={"default_section": "keys"},
        domain=lambda self: [("section", "=", "keys")],
    )
    checklist_electrical_ids = fields.One2many(
        "asset.flat.checklist.line", "inspection_id",
        string="Electrical",
        context={"default_section": "electrical"},
        domain=lambda self: [("section", "=", "electrical")],
    )
    checklist_plumbing_ids = fields.One2many(
        "asset.flat.checklist.line", "inspection_id",
        string="Plumbing",
        context={"default_section": "plumbing"},
        domain=lambda self: [("section", "=", "plumbing")],
    )
    checklist_general_ids = fields.One2many(
        "asset.flat.checklist.line", "inspection_id",
        string="General / Damages",
        context={"default_section": "general"},
        domain=lambda self: [("section", "=", "general")],
    )

    checked_by = fields.Char(string="Checked By (M/S)")
    resident_signature = fields.Binary(string="Resident Signature")
    resident_signature_name = fields.Char(string="Resident Name (Signature)")

    checklist_total = fields.Integer(compute="_compute_checklist_stats")
    checklist_done = fields.Integer(compute="_compute_checklist_stats")
    checklist_progress = fields.Float(
        compute="_compute_checklist_stats", string="Checklist Progress %"
    )

    @api.depends("checklist_line_ids.status")
    def _compute_checklist_stats(self):
        for rec in self:
            lines = rec.checklist_line_ids
            total = len(lines)
            done = len(lines.filtered(lambda l: l.status in ("ok", "missing", "damaged", "na")))
            rec.checklist_total = total
            rec.checklist_done = done
            rec.checklist_progress = (done / total * 100) if total else 0.0

    def action_load_checklist(self):
        self.ensure_one()
        if not self.checklist_template_id:
            raise UserError(_("Please select a Checklist Template first."))
        self.checklist_line_ids.unlink()
        lines = []
        for tpl_line in self.checklist_template_id.line_ids:
            lines.append((0, 0, {
                "sequence": tpl_line.sequence,
                "section": tpl_line.section,
                "description": tpl_line.description,
                "expected_qty": tpl_line.expected_qty,
                "is_meter_reading": tpl_line.is_meter_reading,
                "source_line_id": tpl_line.id,
            }))
        self.checklist_line_ids = lines
        return True

    @api.onchange("checklist_template_id")
    def _onchange_checklist_template(self):
        if self.checklist_template_id and self.inspection_type == "flat":
            self.action_load_checklist()
