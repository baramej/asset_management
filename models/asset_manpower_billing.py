from odoo import api, fields, models, _
from odoo.exceptions import UserError
import base64
import io
from datetime import date


# ─────────────────────────────────────────────────────────────────────────────
# Global Configuration
# ─────────────────────────────────────────────────────────────────────────────

class ManpowerBillingConfig(models.Model):
    _name = "manpower.billing.config"
    _description = "Manpower Billing Configuration"

    name = fields.Char(default="Global Manpower Settings", required=True)

    default_daily_rate = fields.Float(
        string="Default Daily Rate",
        default=250.0,
        help="Default salary / billing rate per manday (one day per employee).",
    )
    working_days_per_month = fields.Integer(
        string="Working Days / Month",
        default=30,
        help="Divisor used to convert total mandays into headcount equivalent.",
    )
    default_labour_product_id = fields.Many2one(
        "product.product",
        string="Labour Product",
        domain="[('type','=','service')]",
        help="Service product used on invoices for manpower charges.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    _sql_constraints = [
        ("single_config", "CHECK(1=1)", ""),  # allow multiple rows if needed later
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Import Batch — one per Excel upload
# ─────────────────────────────────────────────────────────────────────────────

class ManpowerImportBatch(models.Model):
    _name = "manpower.import.batch"
    _description = "Manpower Attendance Import Batch"
    _order = "period_year desc, period_month desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(string="Batch Name", required=True, tracking=True)

    period_month = fields.Selection([
        ("1","January"), ("2","February"), ("3","March"), ("4","April"),
        ("5","May"), ("6","June"), ("7","July"), ("8","August"),
        ("9","September"), ("10","October"), ("11","November"), ("12","December"),
    ], string="Month", required=True, tracking=True)
    period_year = fields.Integer(
        string="Year", required=True,
        default=lambda self: date.today().year,
        tracking=True,
    )
    period_label = fields.Char(
        string="Period",
        compute="_compute_period_label",
        store=True,
    )

    state = fields.Selection([
        ("draft", "Draft"),
        ("imported", "Imported"),
        ("confirmed", "Confirmed"),
    ], default="draft", tracking=True)
    

    import_file = fields.Binary(string="Excel File (.xlsx)", attachment=True)
    import_filename = fields.Char(string="File Name")

    raw_line_ids = fields.One2many(
        "manpower.import.line", "batch_id", string="Raw Attendance Lines"
    )
    summary_line_ids = fields.One2many(
        "manpower.billing.summary", "batch_id", string="Company Summaries"
    )

    total_mandays = fields.Integer(
        string="Total Mandays", compute="_compute_totals", store=True
    )
    total_companies = fields.Integer(
        string="Companies", compute="_compute_totals", store=True
    )
    total_employees = fields.Integer(
        string="Total Employees", compute="_compute_totals", store=True
    )
    total_billed_amount = fields.Float(
        string="Total Billed Amount", compute="_compute_totals", store=True
    )

    notes = fields.Text(string="Notes")

    @api.depends("period_month", "period_year")
    def _compute_period_label(self):
        month_names = {
            "1":"Jan","2":"Feb","3":"Mar","4":"Apr","5":"May","6":"Jun",
            "7":"Jul","8":"Aug","9":"Sep","10":"Oct","11":"Nov","12":"Dec",
        }
        for rec in self:
            m = month_names.get(rec.period_month or "", "")
            rec.period_label = f"{m} {rec.period_year}" if m else str(rec.period_year)

    @api.depends("summary_line_ids.total_mandays",
                 "summary_line_ids.billed_amount",
                 "summary_line_ids.employee_equivalent")
    def _compute_totals(self):
        for rec in self:
            rec.total_mandays = sum(rec.summary_line_ids.mapped("total_mandays"))
            rec.total_companies = len(rec.summary_line_ids)
            rec.total_employees = sum(rec.summary_line_ids.mapped("employee_equivalent"))
            rec.total_billed_amount = sum(rec.summary_line_ids.mapped("billed_amount"))

    def action_import_excel(self):
        """Parse the uploaded Excel and populate raw lines + summaries."""
        self.ensure_one()
        if not self.import_file:
            raise UserError(_("Please upload an Excel file before importing."))

        try:
            import openpyxl
        except ImportError:
            raise UserError(_("openpyxl is required. Run: pip install openpyxl"))

        file_data = base64.b64decode(self.import_file)
        wb = openpyxl.load_workbook(io.BytesIO(file_data), data_only=True)
        ws = wb.active

        # Find header row (contains "Employee Code")
        header_row = None
        col_emp = col_date = col_atsecn = None
        for row in ws.iter_rows():
            for cell in row:
                if cell.value and str(cell.value).strip().lower() in (
                    "employee code", "employee_code", "emp code", "emp_code"
                ):
                    header_row = cell.row
                    col_emp = cell.column
                    break
            if header_row:
                break

        if not header_row:
            raise UserError(_(
                "Could not find header row with 'Employee Code' in the Excel file."
            ))

        # Find other column headers in same row
        for cell in ws[header_row]:
            if not cell.value:
                continue
            val = str(cell.value).strip().lower()
            if val in ("sh date", "shift date", "shdate", "date"):
                col_date = cell.column
            elif val in ("atsecn", "company code", "company_code", "atsecn code"):
                col_atsecn = cell.column

        if not col_date or not col_atsecn:
            raise UserError(_(
                "Could not find 'Sh date' or 'ATSECN' columns. "
                "Found Employee Code at col %s, Date at col %s, ATSECN at col %s."
            ) % (col_emp, col_date, col_atsecn))

        # Delete existing raw lines and summaries
        self.raw_line_ids.unlink()
        self.summary_line_ids.unlink()

        # Parse data rows
        raw_vals = []
        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            emp_code = row[col_emp - 1]
            shift_date_raw = row[col_date - 1]
            atsecn = row[col_atsecn - 1]

            if not emp_code or not shift_date_raw or not atsecn:
                continue

            emp_code = str(int(emp_code)) if isinstance(emp_code, float) else str(emp_code).strip()
            atsecn = str(atsecn).strip()

            # Parse date — handles dd.mm.yyyy or standard formats
            if isinstance(shift_date_raw, (date,)):
                shift_date = shift_date_raw
            else:
                date_str = str(shift_date_raw).strip()
                for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                    try:
                        shift_date = date(*[int(x) for x in (
                            date_str.split(".")[::-1] if "." in date_str
                            else date_str.split("-") if "-" in date_str
                            else date_str.split("/")
                        )])
                        break
                    except Exception:
                        continue
                else:
                    continue  # skip unparseable dates

            raw_vals.append({
                "batch_id": self.id,
                "employee_code": emp_code,
                "shift_date": shift_date,
                "atsecn": atsecn,
            })

        if not raw_vals:
            raise UserError(_("No valid data rows found in the Excel file."))

        self.env["manpower.import.line"].create(raw_vals)

        # Build summaries per ATSECN
        self._build_summaries()
        self.write({"state": "imported"})
        self.message_post(
            body=_(
                "Imported <b>%d</b> attendance records across <b>%d</b> company codes."
            ) % (len(raw_vals), self.total_companies)
        )

    def _build_summaries(self):
        """Aggregate raw lines into per-company summary records."""
        self.ensure_one()

        config = self.env["manpower.billing.config"].search([], limit=1)
        daily_rate = config.default_daily_rate if config else 250.0
        working_days = config.working_days_per_month if config else 30

        # Group by ATSECN
        groups = {}
        for line in self.raw_line_ids:
            key = line.atsecn
            if key not in groups:
                groups[key] = {"mandays": 0, "employees": set()}
            groups[key]["mandays"] += 1
            groups[key]["employees"].add(line.employee_code)

        summary_vals = []
        for atsecn, data in groups.items():
            # Try to find partner by company_registry or ref
            partner = self.env["res.partner"].search([
                "|",
                ("company_registry", "=", atsecn),
                ("ref", "=", atsecn),
            ], limit=1)

            mandays = data["mandays"]
            emp_count = len(data["employees"])
            emp_equiv = round(mandays / working_days, 2) if working_days else 0
            amount = round(mandays / working_days * daily_rate, 2) if working_days else 0

            summary_vals.append({
                "batch_id": self.id,
                "atsecn": atsecn,
                "partner_id": partner.id if partner else False,
                "total_mandays": mandays,
                "unique_employees": emp_count,
                "employee_equivalent": emp_equiv,
                "daily_rate": daily_rate,
                "working_days_per_month": working_days,
                "billed_amount": amount,
                "period_month": self.period_month,
                "period_year": self.period_year,
            })

        self.env["manpower.billing.summary"].create(summary_vals)

    def action_confirm(self):
        self.ensure_one()
        if not self.summary_line_ids:
            raise UserError(_("No summary data to confirm. Please import first."))
        self.write({"state": "confirmed"})
        self.message_post(body=_("Batch confirmed by %s.") % self.env.user.name)

    def action_reset_draft(self):
        self.write({"state": "draft"})

    def action_view_summaries(self):
        self.ensure_one()
        return {
            "name": _("Company Summaries — %s") % self.name,
            "type": "ir.actions.act_window",
            "res_model": "manpower.billing.summary",
            "view_mode": "list,form",
            "domain": [("batch_id", "=", self.id)],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Raw Import Lines — one row per Excel row
# ─────────────────────────────────────────────────────────────────────────────

class ManpowerImportLine(models.Model):
    _name = "manpower.import.line"
    _description = "Manpower Attendance Raw Line"
    _order = "atsecn, shift_date, employee_code"

    batch_id = fields.Many2one(
        "manpower.import.batch", required=True, ondelete="cascade"
    )
    employee_code = fields.Char(string="Employee Code", required=True)
    shift_date = fields.Date(string="Shift Date", required=True)
    atsecn = fields.Char(string="ATSECN (Company Code)", required=True)

    # Resolved fields
    partner_id = fields.Many2one(
        "res.partner",
        string="Company",
        compute="_compute_partner",
        store=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        compute="_compute_employee",
        store=True,
    )

    @api.depends("atsecn")
    def _compute_partner(self):
        for rec in self:
            partner = self.env["res.partner"].search([
                "|",
                ("company_registry", "=", rec.atsecn),
                ("ref", "=", rec.atsecn),
            ], limit=1)
            rec.partner_id = partner

    @api.depends("employee_code")
    def _compute_employee(self):
        for rec in self:
            emp = self.env["hr.employee"].search([
                ("employee_code", "=", rec.employee_code)
            ], limit=1)
            rec.employee_id = emp


# ─────────────────────────────────────────────────────────────────────────────
# Billing Summary — one per company per batch
# ─────────────────────────────────────────────────────────────────────────────

class ManpowerBillingSummary(models.Model):
    _name = "manpower.billing.summary"
    _description = "Manpower Billing Summary (per Company per Batch)"
    _order = "period_year desc, period_month desc, atsecn"
    _inherit = ["mail.thread"]

    batch_id = fields.Many2one(
        "manpower.import.batch", string="Import Batch",
        required=True, ondelete="cascade", tracking=True,
    )
    period_month = fields.Selection([
        ("1","January"), ("2","February"), ("3","March"), ("4","April"),
        ("5","May"), ("6","June"), ("7","July"), ("8","August"),
        ("9","September"), ("10","October"), ("11","November"), ("12","December"),
    ], string="Month", required=True)
    period_year = fields.Integer(string="Year", required=True)
    period_label = fields.Char(
        string="Period", compute="_compute_period_label", store=True
    )

    atsecn = fields.Char(string="ATSECN Code", required=True)
    partner_id = fields.Many2one(
        "res.partner", string="Company / Client", tracking=True
    )

    # ── Manpower Figures ──────────────────────────────────────────────────────
    total_mandays = fields.Integer(string="Total Mandays", readonly=True)
    unique_employees = fields.Integer(string="Unique Employees", readonly=True)
    employee_equivalent = fields.Float(
        string="Employee Equivalent",
        digits=(10, 2),
        help="Total Mandays ÷ Working Days per Month",
        readonly=True,
    )

    # ── Billing Figures ───────────────────────────────────────────────────────
    daily_rate = fields.Float(
        string="Daily Rate", digits=(10, 3), tracking=True
    )
    working_days_per_month = fields.Integer(
        string="Working Days / Month", default=30
    )
    billed_amount = fields.Float(
        string="Billed Amount",
        compute="_compute_billed_amount",
        store=True,
        digits=(10, 3),
        tracking=True,
    )

    # ── Invoice ───────────────────────────────────────────────────────────────
    invoice_ids = fields.Many2many(
        "account.move",
        "manpower_summary_invoice_rel",
        "summary_id",
        "invoice_id",
        string="Invoices",
        copy=False,
    )
    invoice_count = fields.Integer(
        string="Invoices", compute="_compute_invoice_count", store=True
    )
    invoice_state = fields.Char(
        string="Payment Status", compute="_compute_invoice_state", store=True
    )
    total_invoiced = fields.Float(
        string="Total Invoiced", compute="_compute_invoice_count", store=True
    )

    notes = fields.Text(string="Notes")

    @api.depends("period_month", "period_year")
    def _compute_period_label(self):
        month_names = {
            "1":"January","2":"February","3":"March","4":"April",
            "5":"May","6":"June","7":"July","8":"August",
            "9":"September","10":"October","11":"November","12":"December",
        }
        for rec in self:
            m = month_names.get(rec.period_month or "", "")
            rec.period_label = f"{m} {rec.period_year}" if m else str(rec.period_year)

    @api.depends("total_mandays", "daily_rate", "working_days_per_month")
    def _compute_billed_amount(self):
        for rec in self:
            wd = rec.working_days_per_month or 30
            rec.billed_amount = round(
                (rec.total_mandays / wd) * rec.daily_rate, 3
            ) if wd else 0.0

    @api.depends("invoice_ids", "invoice_ids.amount_total", "invoice_ids.state")
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)
            rec.total_invoiced = sum(
                inv.amount_total for inv in rec.invoice_ids
                if inv.state not in ("cancel",)
            )

    @api.depends("invoice_ids", "invoice_ids.payment_state", "invoice_ids.state")
    def _compute_invoice_state(self):
        for rec in self:
            states = rec.invoice_ids.filtered(
                lambda i: i.state == "posted"
            ).mapped("payment_state")
            if not states:
                rec.invoice_state = "not_invoiced"
            elif all(s == "paid" for s in states):
                rec.invoice_state = "paid"
            elif any(s == "partial" for s in states):
                rec.invoice_state = "partial"
            else:
                rec.invoice_state = "unpaid"

    def action_view_raw_lines(self):
        self.ensure_one()
        return {
            "name": _("Attendance Lines — %s / %s") % (
                self.atsecn, self.period_label
            ),
            "type": "ir.actions.act_window",
            "res_model": "manpower.import.line",
            "view_mode": "list",
            "domain": [
                ("batch_id", "=", self.batch_id.id),
                ("atsecn", "=", self.atsecn),
            ],
        }

    def action_create_invoice(self):
        """Open wizard to create invoice with period / quantity options."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_(
                "Please link a Company (partner) to ATSECN '%s' before creating an invoice."
            ) % self.atsecn)
        return {
            "name": _("Create Manpower Invoice"),
            "type": "ir.actions.act_window",
            "res_model": "manpower.invoice.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_summary_ids": self.ids,
                "default_partner_id": self.partner_id.id,
                "default_daily_rate": self.daily_rate,
                "default_period_label": self.period_label,
            },
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            "name": _("Invoices — %s") % self.atsecn,
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.invoice_ids.ids)],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Invoice Wizard
# ─────────────────────────────────────────────────────────────────────────────

class ManpowerInvoiceWizard(models.TransientModel):
    _name = "manpower.invoice.wizard"
    _description = "Create Manpower Invoice"

    summary_ids = fields.Many2many(
        "manpower.billing.summary",
        "manpower_wizard_summary_rel",
        "wizard_id",
        "summary_id",
        string="Billing Summaries",
    )

    partner_id = fields.Many2one(
        "res.partner", string="Bill To", required=True
    )
    invoice_date = fields.Date(
        string="Invoice Date", default=fields.Date.today, required=True
    )
    billing_period = fields.Selection([
        ("monthly", "Monthly — current batch only"),
        ("quarterly", "Quarterly — last 3 months"),
        ("biannual", "Bi-Annual — last 6 months"),
        ("annual", "Annual — last 12 months"),
        ("custom", "Custom selection"),
    ], string="Billing Period", default="monthly", required=True)

    # Shown only for custom
    custom_summary_ids = fields.Many2many(
        "manpower.billing.summary",
        "manpower_wizard_custom_rel",
        "wizard_id",
        "summary_id",
        string="Select Periods",
    )

    daily_rate = fields.Float(string="Daily Rate Override", digits=(10, 3))
    period_label = fields.Char(string="Period Label")

    labour_product_id = fields.Many2one(
        "product.product",
        string="Labour Product",
        domain="[('type','=','service')]",
    )
    notes = fields.Text(string="Invoice Note / Narration")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        config = self.env["manpower.billing.config"].search([], limit=1)
        if config and config.default_labour_product_id:
            res["labour_product_id"] = config.default_labour_product_id.id
        return res

    def _get_summaries_for_period(self):
        """Return the set of summaries to invoice based on billing_period."""
        self.ensure_one()
        if self.billing_period == "custom":
            return self.custom_summary_ids

        # Always start from the selected summary's partner + batch context
        base_summaries = self.summary_ids
        if not base_summaries:
            return self.env["manpower.billing.summary"]

        partner = self.partner_id
        # Get reference month/year from first summary
        ref = base_summaries[0]
        ref_year = ref.period_year
        ref_month = int(ref.period_month)

        months_back = {
            "monthly": 0,
            "quarterly": 2,
            "biannual": 5,
            "annual": 11,
        }.get(self.billing_period, 0)

        # Build list of (year, month) tuples to include
        periods = []
        y, m = ref_year, ref_month
        for _ in range(months_back + 1):
            periods.append((y, str(m)))
            m -= 1
            if m == 0:
                m = 12
                y -= 1

        domain = [
            ("partner_id", "=", partner.id),
            ("period_year", "in", [p[0] for p in periods]),
            ("period_month", "in", [p[1] for p in periods]),
        ]
        return self.env["manpower.billing.summary"].search(domain)

    def action_create_invoice(self):
        self.ensure_one()

        summaries = self._get_summaries_for_period()
        if not summaries:
            raise UserError(_("No billing summaries found for the selected period."))

        if not self.partner_id:
            raise UserError(_("Please select a partner."))

        config = self.env["manpower.billing.config"].search([], limit=1)

        invoice_lines = []
        for s in summaries:
            rate = self.daily_rate or s.daily_rate or (
                config.default_daily_rate if config else 250.0
            )
            wd = s.working_days_per_month or 30
            qty = round(s.total_mandays / wd, 4) if wd else 0
            amount = round(qty * rate, 3)

            product = (
                self.labour_product_id
                or (config.default_labour_product_id if config else False)
            )

            description_parts = [
                f"Manpower Charge — {s.period_label} — {s.atsecn}",
                f"Mandays: {s.total_mandays}  |  Employees: {s.unique_employees}  |  "
                f"Equiv. FTE: {s.employee_equivalent:.2f}",
                f"Rate: {rate:.3f} / day  ×  {wd} working days/month  =  {amount:.3f}",
            ]
            if self.notes:
                description_parts.append(self.notes)

            line_vals = {
                "name": "\n".join(description_parts),
                "quantity": qty,
                "price_unit": rate,
            }
            if product:
                line_vals["product_id"] = product.id
            invoice_lines.append((0, 0, line_vals))

        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner_id.id,
            "invoice_date": self.invoice_date,
            "ref": "Manpower — %s" % " | ".join(summaries.mapped("period_label")),
            "narration": self.notes or "",
            "invoice_line_ids": invoice_lines,
        })

        # Link invoice back to all summaries
        for s in summaries:
            s.invoice_ids = [(4, invoice.id)]

        summaries.message_post(
            body=_("Invoice <b>%s</b> created for manpower billing.") % invoice.name
        )

        return {
            "name": _("Manpower Invoice"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": invoice.id,
            "views": [(False, "form")],
            "target": "current",
        }