# -*- coding: utf-8 -*-
"""Predefined list of service-request approvers.

Instead of routing a portal ticket to the requester's hr.employee manager,
the employee picks an approver from this list on the service request form.
Columns mirror the client's approver sheet so it can be imported as-is:

    App Dept | App Dept Name | App Company | Approver EmpCode | EGNAME | App Name
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import email_normalize


class AssetPortalApprover(models.Model):
    _name = "asset.portal.approver"
    _description = "Service Request Approver"
    _order = "dept_name, name"
    _rec_names_search = ["name", "eg_name", "emp_code", "dept_code", "dept_name", "company_code"]

    dept_code = fields.Char(string="App Dept", index=True, help="Department code, e.g. A2")
    dept_name = fields.Char(string="App Dept Name", index=True, help="e.g. KIA SALES")
    company_code = fields.Char(string="App Company", help="Company code, e.g. B")
    emp_code = fields.Char(string="Approver EmpCode", required=True, index=True)
    eg_name = fields.Char(string="EGNAME", help="Approver name as held in the legacy HR system.")
    name = fields.Char(string="App Name", required=True)

    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        compute="_compute_employee_id",
        store=True,
        readonly=False,
        help="Matched automatically on Approver EmpCode; can be set manually.",
    )
    email = fields.Char(
        string="Email",
        help="Where the approval link is sent. Leave empty to use the linked "
             "employee's work email.",
    )
    approval_email = fields.Char(
        string="Approval Email",
        compute="_compute_approval_email",
        store=True,
        help="Email actually used: Email above, else the employee's work email, "
             "else the employee's user email.",
    )
    active = fields.Boolean(default=True)

    _dept_emp_uniq = models.Constraint(
        "UNIQUE(dept_code, emp_code)",
        "This approver is already listed for this department.",
    )

    # ── computes ────────────────────────────────────────────────────────────

    @api.depends("emp_code", "company_code")
    def _compute_employee_id(self):
        for rec in self:
            rec.employee_id = rec._match_employee() or rec.employee_id

    def _match_employee(self):
        """hr.employee for this EmpCode. HR codes carry a company prefix
        (e.g. B25728) while the sheet may hold only the number (25728)."""
        self.ensure_one()
        Employee = self.env["hr.employee"].sudo()
        code = (self.emp_code or "").strip().upper()
        if not code or "employee_code" not in Employee._fields:
            return Employee.browse()
        employee = Employee.search([("employee_code", "=ilike", code)], limit=1)
        if not employee and self.company_code:
            employee = Employee.search(
                [("employee_code", "=ilike", self.company_code.strip() + code)], limit=1
            )
        if not employee:
            candidates = Employee.search([("employee_code", "=ilike", "%" + code)]).filtered(
                lambda e: re.sub(r"^[A-Z]+", "", (e.employee_code or "").upper()) == code
            )
            if len(candidates) == 1:
                employee = candidates
        return employee

    @api.depends("email", "employee_id", "employee_id.work_email", "employee_id.user_id.email")
    def _compute_approval_email(self):
        for rec in self:
            rec.approval_email = (
                rec.email
                or rec.employee_id.work_email
                or rec.employee_id.user_id.email
                or False
            )

    @api.depends("name", "emp_code", "dept_name", "dept_code", "company_code")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec._portal_label()

    def _portal_label(self):
        """Text shown (and searched) in the portal dropdown."""
        self.ensure_one()
        dept = self.dept_name or ""
        if self.dept_code:
            dept = f"{dept} ({self.dept_code})" if dept else self.dept_code
        parts = [self.name or self.eg_name or "", self.emp_code or "", dept, self.company_code or ""]
        return " · ".join(p for p in parts if p)

    @api.constrains("email")
    def _check_email(self):
        for rec in self:
            if rec.email and not email_normalize(rec.email):
                raise ValidationError(_("%s is not a valid email address.", rec.email))

    @api.model
    def _normalize_dept_code(self, code):
        """'010', '10' and ' 10 ' are the same department; 'A2' stays 'A2'."""
        code = (code or "").strip().upper()
        return code.lstrip("0") or code

    @api.model
    def _portal_selectable(self, employee=None):
        """Approvers offered on the portal: active, with an email to send to.

        With an employee, only the approvers whose App Dept matches the
        employee's legacy department code (hr.employee.emp_dept_code, filled
        by the legacy HR sync). Nobody is offered when no approver is set up
        for that department (the request then follows the standard manager
        approval). Everyone is offered when the employee's portal company has
        "See All" (e.g. Al Manar) or the employee has no department code.
        """
        approvers = self.sudo().search([("approval_email", "!=", False)])
        if not employee or employee.portal_company_id.portal_full_access:
            return approvers
        dept, _dept_name = employee._asset_legacy_department()
        if not dept:
            return approvers
        key = self._normalize_dept_code(dept)
        return approvers.filtered(lambda a: self._normalize_dept_code(a.dept_code) == key)

    def action_rematch_employees(self):
        """Re-run the EmpCode → employee match (e.g. after an HR sync)."""
        for rec in self:
            rec.employee_id = rec._match_employee() or rec.employee_id
        return True
