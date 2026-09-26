import logging

from odoo import http, fields, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class PortalProfileController(http.Controller):

    # ── helpers ──────────────────────────────────────────────────────────

    def _employee_for_user(self, user):
        return request.env["hr.employee"].sudo().search(
            [("user_id", "=", user.id)], limit=1
        )

    def _safe_next(self, next_url):
        """Only allow internal relative redirects — never an open redirect."""
        if next_url and isinstance(next_url, str) and next_url.startswith("/") \
                and not next_url.startswith("//"):
            return next_url
        return "/my/tickets/new"

    def _allowed_companies(self):
        return request.env["res.company"].sudo().search(
            [("portal_signup_allowed", "=", True)], order="name"
        )

    def _render_form(self, error=None, error_message=None, post=None, next_url="/my/tickets/new"):
        return request.render("asset_management.portal_profile_complete", {
            "companies": self._allowed_companies(),
            "error": error or {},
            "error_message": error_message or [],
            "post": post or {},
            "next": self._safe_next(next_url),
        })

    # ── GET ──────────────────────────────────────────────────────────────

    @http.route("/my/profile/complete", auth="user", website=True, methods=["GET"])
    def profile_form(self, **kw):
        user = request.env.user
        next_url = self._safe_next(kw.get("next"))

        if self._employee_for_user(user):
            return request.redirect(next_url)

        return self._render_form(next_url=next_url)

    # ── POST ─────────────────────────────────────────────────────────────

    @http.route("/my/profile/complete", auth="user", website=True,
                methods=["POST"], csrf=True)
    def profile_submit(self, **post):
        user = request.env.user
        next_url = self._safe_next(post.get("next"))

        # Already linked (e.g. double submit) — just move on
        existing_link = self._employee_for_user(user)
        if existing_link:
            return request.redirect(next_url)

        errors = {}
        error_message = []

        Company = request.env["res.company"].sudo()
        Employee = request.env["hr.employee"].sudo()

        company_id = post.get("company_id")
        company = Company.browse(int(company_id)) if company_id else Company.browse()

        if not company.exists() or not company.portal_signup_allowed:
            errors["company_id"] = True
            error_message.append(_("Please select a valid company."))

        employee_number = (post.get("employee_number") or "").strip()
        if not employee_number:
            errors["employee_number"] = True
            error_message.append(_("Employee number is required."))

        if errors:
            return self._render_form(
                error=errors, error_message=error_message,
                post=post, next_url=next_url,
            )

        full_code = f"{company.legacy_company_code or ''}{employee_number}"

        # Try to match an existing employee by code within this company first,
        # then fall back to a company-agnostic match on the raw code.
        employee = Employee.search([
            ("employee_code", "=", full_code),
            ("company_id", "=", company.id),
        ], limit=1)
        if not employee:
            employee = Employee.search(
                [("employee_code", "=", full_code)], limit=1
            )

        manager_id = post.get("manager_id")
        manager = Employee.browse(int(manager_id)) if manager_id else Employee.browse()
        if manager and manager.company_id and manager.company_id != company:
            errors["manager_id"] = True
            error_message.append(_("Selected manager does not belong to the chosen company."))
            return self._render_form(
                error=errors, error_message=error_message,
                post=post, next_url=next_url,
            )

        if employee:
            if employee.user_id and employee.user_id != user:
                errors["employee_number"] = True
                error_message.append(_(
                    "This employee code is already linked to another portal "
                    "account. Please contact HR if this is a mistake."
                ))
                return self._render_form(
                    error=errors, error_message=error_message,
                    post=post, next_url=next_url,
                )
            employee.write({
                "user_id": user.id,
                "work_email": employee.work_email or user.partner_id.email,
            })
        else:
            employee = Employee.create({
                "name": user.name,
                "company_id": company.id,
                "employee_code": full_code,
                "work_email": user.partner_id.email,
                "user_id": user.id,
                "parent_id": manager.id if manager else False,
                "portal_self_created": True,
                "portal_pending_hr_review": True,
            })
            _logger.info(
                "Portal profile completion: created new employee %s (code=%s) "
                "for portal user %s, flagged for HR review.",
                employee.id, full_code, user.login,
            )

        user.sudo().write({
            "company_id": company.id,
            "company_ids": [(4, company.id)],
        })

        return request.redirect(next_url)

    # ── AJAX: managers for a given company ──────────────────────────────

    @http.route("/my/profile/managers", auth="user", type="json", methods=["POST"])
    def managers_for_company(self, company_id=None, **kw):
        if not company_id:
            return []
        employees = request.env["hr.employee"].sudo().search(
            [("company_id", "=", int(company_id))], order="name"
        )
        return [{"id": e.id, "name": e.name} for e in employees]