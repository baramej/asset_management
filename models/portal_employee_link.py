# -*- coding: utf-8 -*-
"""Link portal users to their hr.employee record (Bahwan Group).

A portal user who raises a service request must be tied to an hr.employee so
that the request can be routed to their manager (employee.parent_id).

Portal companies (asset.portal.company) decouple what the employee picks from
the Odoo company records:
  * the employee picks e.g. "Auto Arab" (code "AA") on the sign-up form,
  * the portal user is created under that entry's Default Company,
  * the employee is created under the "Create Employees Under" company set in
    the Legacy HR settings (e.g. SBG), or the Default Company when empty,
  * the employee keeps "Auto Arab" in hr.employee.portal_company_id, and the
    entry's code is what the legacy HR service is queried with.
"""
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

SIGNUP_CONTEXT_KEY = "asset_portal_employee_signup"


def _clean_code(value):
    return (value or "").strip().upper().replace(" ", "")


class AssetPortalCompany(models.Model):
    _name = "asset.portal.company"
    _description = "Portal Company (shown on employee sign-up)"
    _order = "sequence, name"

    name = fields.Char(
        string="Company", required=True,
        help="Name the employee sees and picks on the portal, e.g. Auto Arab.",
    )
    code = fields.Char(
        string="Company Code", required=True,
        help="Company code in the legacy HR system, e.g. 'AA'. It prefixes "
        "employee codes (AA78723) and is used to query the legacy HR service.",
    )
    company_id = fields.Many2one(
        "res.company", string="Default Company", required=True,
        default=lambda self: self.env.company,
        help="Odoo company the portal user is created under, and which "
        "scopes the locations and assets they see, e.g. SBA.",
    )
    portal_full_access = fields.Boolean(
        string="See All Locations & Assets",
        help="Employees of this company see every location and asset on the "
        "service request form, across all companies (e.g. Al Manar).",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    employee_count = fields.Integer(compute="_compute_employee_count")

    _code_uniq = models.Constraint(
        "UNIQUE(code)", "Each portal company must have its own company code.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("code"):
                vals["code"] = _clean_code(vals["code"])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("code"):
            vals["code"] = _clean_code(vals["code"])
        return super().write(vals)

    def _compute_employee_count(self):
        counts = dict(self.env["hr.employee"].sudo()._read_group(
            [("portal_company_id", "in", self.ids)], ["portal_company_id"], ["__count"],
        ))
        for rec in self:
            rec.employee_count = counts.get(rec, 0)

    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.name} ({rec.code})" if rec.code else rec.name

    def action_view_employees(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Employees of %s", self.name),
            "res_model": "hr.employee",
            "view_mode": "list,form",
            "domain": [("portal_company_id", "=", self.id)],
            "context": {"default_portal_company_id": self.id,
                        "default_company_id": self.company_id.id},
        }

    @api.model
    def _asset_portal_selectable(self):
        return self.sudo().search([])


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    portal_company_id = fields.Many2one(
        "asset.portal.company",
        string="Actual Company",
        index="btree_not_null",
        tracking=True,
        help="The company the employee really belongs to, e.g. Auto Arab, "
        "when it is held under another Odoo company (e.g. SBG).",
    )
    portal_self_registered = fields.Boolean(
        string="Created from Portal Sign-up",
        readonly=True,
        copy=False,
        groups="hr.group_hr_user",
        help="This employee record was created by the employee themselves "
        "when signing up on the portal, because no matching employee code "
        "existed and the legacy HR system could not be checked.",
    )

    # ------------------------------------------------------------------ #
    # Company that HR-sync employees are created under (e.g. SBG)
    # ------------------------------------------------------------------ #
    @api.model
    def _asset_hr_employee_company(self):
        """Company set in Settings > Real Estate > Legacy HR Integration >
        "Create Employees Under". Empty when not set."""
        value = self.env["ir.config_parameter"].sudo().get_param(
            "rental_management_hr_sync.employee_company_id"
        )
        try:
            company_id = int(value or 0)
        except (TypeError, ValueError):
            company_id = 0
        return self.env["res.company"].sudo().browse(company_id).exists()

    # ------------------------------------------------------------------ #
    # Legacy HR sync: query with the employee's real company code
    # ------------------------------------------------------------------ #
    def _legacy_company_code(self):
        """Employees held under SBG are known to the legacy HR system by
        their real company's code (Auto Arab "AA", "B", ...), not SBG's."""
        self.ensure_one()
        if self.portal_company_id.code:
            return self.portal_company_id.code
        if "emp_company_code" in self._fields and (self.emp_company_code or "").strip():
            return self.emp_company_code.strip()
        parent = getattr(super(), "_legacy_company_code", None)
        return parent() if parent else ""

    @api.model
    def _legacy_sync_domain(self):
        return [
            ("employee_code", "!=", False),
            "|", "|",
            ("portal_company_id.code", "!=", False),
            ("emp_company_code", "!=", False),
            ("company_id.legacy_company_code", "!=", False),
        ]

    def _asset_matches_legacy_code(self, company_code):
        """Is this employee known to the legacy system under company_code?

        With everyone held under one company, the same number can belong to
        several real companies, so the real company decides.
        """
        self.ensure_one()
        code = _clean_code(company_code)
        if self.portal_company_id:
            return _clean_code(self.portal_company_id.code) == code
        if "emp_company_code" in self._fields and self.emp_company_code:
            return _clean_code(self.emp_company_code) == code
        if "legacy_company_code" in self.company_id._fields and self.company_id.legacy_company_code:
            return _clean_code(self.company_id.legacy_company_code) == code
        return True

    # ------------------------------------------------------------------ #
    # Occupy wizard "Sync"
    # ------------------------------------------------------------------ #
    @api.model
    def _asset_portal_company_for_code(self, company_code):
        code = _clean_code(company_code)
        if not code:
            return self.env["asset.portal.company"]
        return self.env["asset.portal.company"].sudo().search(
            [("code", "=", code)], limit=1
        )

    @api.model
    def _asset_legacy_home_company(self, company_code):
        """Company a legacy code maps to: a Portal Company's Default Company,
        or the Odoo company carrying that legacy code."""
        portal_company = self._asset_portal_company_for_code(company_code)
        if portal_company:
            return portal_company.company_id
        return self.env["res.company"].sudo().search(
            [("legacy_company_code", "=ilike", (company_code or "").strip())], limit=1
        )

    @api.model
    def _upsert_from_legacy(self, company_code, employee_code):
        """Accept Portal Company codes (e.g. "AA") as well as Odoo company
        codes, and create new employees under the "Create Employees Under"
        company when it is set."""
        portal_company = self._asset_portal_company_for_code(company_code)
        if portal_company:
            company_code = portal_company.code
            employee_code = self._asset_portal_canonical_code(portal_company, employee_code)
        employee = super(
            HrEmployee, self.with_context(asset_legacy_code=_clean_code(company_code))
        )._upsert_from_legacy(company_code, employee_code)
        if portal_company and not employee.portal_company_id:
            employee.sudo().portal_company_id = portal_company
        return employee

    @api.model
    def _company_for_legacy_code(self, company_code):
        home = self._asset_legacy_home_company(company_code)
        if not home:
            return home  # unknown code: keep the original error message
        return self._asset_hr_employee_company() or home

    @api.model
    def _find_by_legacy_key(self, company, employee_code):
        """Look in the creation company and in the code's own company, and
        only accept the employee of that real company."""
        code = self.env.context.get("asset_legacy_code")
        if not code:
            return super()._find_by_legacy_key(company, employee_code)
        companies = company | self._asset_legacy_home_company(code)
        portal_company = self._asset_portal_company_for_code(code)
        variants = (
            self._asset_portal_code_variants(portal_company, employee_code)
            if portal_company else [employee_code]
        )
        employees = self.with_context(active_test=False).sudo().search([
            ("employee_code", "in", variants),
            ("company_id", "in", companies.ids),
        ]).filtered(lambda e: e._asset_matches_legacy_code(code))
        employees = employees.sorted(lambda e: (
            not e.active,
            not (portal_company and e.portal_company_id == portal_company),
            e.company_id != company,
        ))
        return employees[:1]

    # ------------------------------------------------------------------ #
    # Code matching
    # ------------------------------------------------------------------ #
    @api.model
    def _asset_portal_canonical_code(self, portal_company, raw_code):
        """Employee code without the company prefix: "AA78723" -> "78723".

        This is the form the legacy service expects ({company},{employee}) and
        the form _upsert_from_legacy stores in employee_code.
        """
        code = _clean_code(raw_code)
        prefix = _clean_code(portal_company.code)
        if prefix and code.startswith(prefix) and len(code) > len(prefix):
            code = code[len(prefix):]
        return code

    @api.model
    def _asset_portal_code_variants(self, portal_company, raw_code):
        """Spellings to look for: HR may have stored "78723" or "AA78723"."""
        code = self._asset_portal_canonical_code(portal_company, raw_code)
        if not code:
            return []
        return list({code, _clean_code(portal_company.code) + code, _clean_code(raw_code)})

    def _asset_portal_belongs_to(self, portal_company):
        """Could this employee be the one registered under portal_company?"""
        self.ensure_one()
        return self._asset_matches_legacy_code(portal_company.code)

    @api.model
    def _asset_portal_find_by_code(self, portal_company, raw_code, strict=True):
        variants = self._asset_portal_code_variants(portal_company, raw_code)
        if not variants:
            return self.browse()
        companies = portal_company.company_id | self._asset_hr_employee_company()
        employees = self.sudo().with_context(active_test=False).search([
            ("company_id", "in", companies.ids),
            ("employee_code", "in", variants),
        ])
        if strict:
            employees = employees.filtered(lambda e: e._asset_portal_belongs_to(portal_company))
        active = employees.filtered("active")
        if len(active) > 1:
            exact = active.filtered(lambda e: e.portal_company_id == portal_company)
            if len(exact) == 1:
                return exact
            raise UserError(_(
                "More than one employee in %(company)s uses the code %(code)s. "
                "Please contact HR.", company=portal_company.name, code=raw_code,
            ))
        return active or employees[:1]

    @api.model
    def _asset_portal_legacy_lookup(self, portal_company, raw_code):
        """Ask the legacy HR service about an employee Odoo does not have.

        Returns (status, values):
          ("found", {hr.employee values})   the legacy system knows them
          ("not_found", None)               the legacy system does not
          ("unavailable", None)             integration missing, not set up,
                                            or the service could not be reached
        Nothing is written here; the employee is created at link time.
        """
        if not hasattr(self, "_fetch_legacy_employee"):
            return "unavailable", None  # legacy HR integration not installed
        settings = self._legacy_sync_settings()
        if not settings["base_url"]:
            return "unavailable", None
        company_code = _clean_code(portal_company.code)
        code = self._asset_portal_canonical_code(portal_company, raw_code)
        try:
            record = self._fetch_legacy_employee(settings, company_code, code)
        except (requests.exceptions.RequestException, ValueError) as error:
            _logger.warning("Portal sign-up: legacy HR lookup for %s,%s failed: %s",
                            company_code, code, error)
            return "unavailable", None
        if not record:
            return "not_found", None
        return "found", self._legacy_employee_values(record)

    # ------------------------------------------------------------------ #
    # Resolution (validation only, no writes)
    # ------------------------------------------------------------------ #
    @api.model
    def _asset_portal_resolve(self, company_id, employee_code, manager_code, email, user=None):
        """Validate the portal user's answers and return plain ids.

        ``company_id`` is the id of the asset.portal.company picked on the form.
        Raises UserError with a message fit to show on the sign-up form.
        Returns a dict that can travel through the context.
        """
        try:
            company_id = int(company_id or 0)
        except (TypeError, ValueError):
            company_id = 0
        portal_company = self.env["asset.portal.company"]._asset_portal_selectable().filtered(
            lambda c: c.id == company_id
        )
        if not portal_company:
            raise UserError(_("Please select your company."))
        if not (employee_code or "").strip():
            raise UserError(_("Please enter your employee code."))

        employee = self._asset_portal_find_by_code(portal_company, employee_code)
        legacy_values = None
        if not employee:
            status, legacy_values = self._asset_portal_legacy_lookup(portal_company, employee_code)
            if status == "not_found":
                raise UserError(_(
                    "Employee code %(code)s was not found in the HR system for "
                    "%(company)s. Please check the code or contact HR.",
                    code=employee_code, company=portal_company.name,
                ))

        if employee:
            linked_user = employee.user_id
            if linked_user and (not user or linked_user != user):
                raise UserError(_(
                    "Employee code %(code)s is already linked to another account. "
                    "If this is you, please log in with that account or contact HR.",
                    code=employee_code,
                ))
            work_email = (employee.work_email or "").strip().lower()
            if work_email and email and work_email != email.strip().lower():
                raise UserError(_(
                    "The email you entered does not match the one HR has for "
                    "employee %(code)s. Please use your work email or contact HR.",
                    code=employee_code,
                ))

        manager = employee.parent_id if employee else self.browse()
        if not manager and (manager_code or "").strip():
            # Same entity first; a manager may also sit in a sister entity
            # held under the same Odoo company.
            manager = self._asset_portal_find_by_code(portal_company, manager_code)
            if not manager:
                manager = self._asset_portal_find_by_code(portal_company, manager_code, strict=False)
            if not manager:
                raise UserError(_(
                    "No employee with code %(code)s was found in %(company)s. "
                    "Please check your manager's employee code.",
                    code=manager_code, company=portal_company.name,
                ))
            if employee and manager == employee:
                raise UserError(_("You cannot be your own manager."))

        return {
            "portal_company_id": portal_company.id,
            "company_id": portal_company.company_id.id,
            "employee_company_id": (
                self._asset_hr_employee_company() or portal_company.company_id
            ).id,
            "employee_id": employee.id or False,
            "employee_code": self._asset_portal_canonical_code(portal_company, employee_code),
            "legacy_values": legacy_values or {},
            "manager_id": manager.id or False,
        }

    # ------------------------------------------------------------------ #
    # Apply (writes)
    # ------------------------------------------------------------------ #
    @api.model
    def _asset_portal_link_user(self, user, resolved):
        """Put the user in the default company and link/create the employee."""
        user = user.sudo()
        company = self.env["res.company"].sudo().browse(resolved["company_id"])
        portal_company = self.env["asset.portal.company"].sudo().browse(resolved["portal_company_id"])

        employee_company = self.env["res.company"].sudo().browse(
            resolved.get("employee_company_id") or company.id
        )
        existing = self.sudo().browse(resolved.get("employee_id") or [])
        # The user must belong to the employee's company to be linked to it.
        user.write({
            "company_ids": [fields.Command.link(c.id)
                            for c in company | employee_company | existing.company_id],
            "company_id": company.id,
        })
        if user.partner_id.company_id and user.partner_id.company_id != company:
            user.partner_id.company_id = company

        Employee = self.sudo()
        employee = Employee.browse(resolved.get("employee_id") or [])
        manager = Employee.browse(resolved.get("manager_id") or [])

        if employee:
            vals = {"user_id": user.id}
            if not employee.portal_company_id:
                vals["portal_company_id"] = portal_company.id
            if not employee.work_email:
                vals["work_email"] = user.email or user.login
            if not employee.parent_id and manager:
                vals["parent_id"] = manager.id
            if not employee.active:
                vals["active"] = True
            employee.write(vals)
            employee.message_post(body=_(
                "Linked to portal user %(user)s by the employee on portal sign-up.",
                user=user.login,
            ))
        else:
            legacy_values = resolved.get("legacy_values") or {}
            vals = {
                "name": user.name,
                "company_id": employee_company.id,
                "portal_company_id": portal_company.id,
                "employee_code": resolved["employee_code"],
                "work_email": user.email or user.login,
                "parent_id": manager.id or False,
                "user_id": user.id,
            }
            if legacy_values:
                # Real record from the legacy HR system (name, department...).
                vals.update(legacy_values)
                if "legacy_sync_status" in Employee._fields:
                    vals.update({
                        "legacy_sync_date": fields.Datetime.now(),
                        "legacy_sync_status": "synced",
                    })
                note = _("Created from the legacy HR system on portal sign-up "
                         "(%(company)s, code %(code)s).",
                         company=portal_company.name, code=resolved["employee_code"])
            else:
                vals["portal_self_registered"] = True
                note = _("Created from portal sign-up (%(company)s, code %(code)s): the "
                         "code is not in Odoo and the legacy HR system could not be "
                         "checked. Please review.",
                         company=portal_company.name, code=resolved["employee_code"])
            employee = Employee.with_company(employee_company).create(vals)
            employee.message_post(body=note)
        _logger.info(
            "Portal user %s linked to employee %s (%s) of %s, held under %s",
            user.login, employee.id, employee.employee_code, portal_company.name,
            employee.company_id.name,
        )
        return employee


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _signup_create_user(self, values):
        # website's override forces the website company on every new portal
        # user; we let it run, then move the user to the default company of
        # the portal company they picked.
        user = super()._signup_create_user(values)
        resolved = self.env.context.get(SIGNUP_CONTEXT_KEY)
        if resolved:
            self.env["hr.employee"]._asset_portal_link_user(user, resolved)
        return user

    def _asset_portal_employee(self):
        """The hr.employee this user acts as on the portal."""
        self.ensure_one()
        Employee = self.env["hr.employee"].sudo()
        employee = Employee.search([("user_id", "=", self.id)], limit=1)
        if not employee:
            employee = Employee.search([("work_contact_id", "=", self.partner_id.id)], limit=1)
        if not employee and self.partner_id.email:
            employee = Employee.search(
                [("work_email", "=ilike", self.partner_id.email)], limit=1
            )
        return employee