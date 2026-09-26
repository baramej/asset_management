# -*- coding: utf-8 -*-
from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.web.controllers.home import SIGN_UP_REQUEST_PARAMS

from ..models.portal_employee_link import SIGNUP_CONTEXT_KEY

EMPLOYEE_SIGNUP_PARAMS = {"employee_company_id", "employee_code", "manager_code"}
# get_auth_signup_qcontext() only keeps whitelisted POST keys.
SIGN_UP_REQUEST_PARAMS.update(EMPLOYEE_SIGNUP_PARAMS)


def _employee_form_values():
    """Portal companies offered on the form (asset.portal.company)."""
    companies = request.env["asset.portal.company"]._asset_portal_selectable()
    return {
        "employee_companies": [
            {"id": c.id, "name": c.name, "code": c.code} for c in companies
        ],
    }


class AssetPortalAuthSignup(AuthSignupHome):

    def get_auth_signup_qcontext(self):
        qcontext = super().get_auth_signup_qcontext()
        qcontext.update(_employee_form_values())
        return qcontext

    def do_signup(self, qcontext, do_login=True):
        # Validate before any user is created so errors show on the form.
        # A reset-password POST goes through do_signup too, with a token for
        # an existing user: leave that alone.
        # Only enforced once at least one portal company is configured.
        if qcontext.get("employee_companies") and not self._is_existing_user_reset(qcontext):
            resolved = request.env["hr.employee"].sudo()._asset_portal_resolve(
                company_id=qcontext.get("employee_company_id"),
                employee_code=qcontext.get("employee_code"),
                manager_code=qcontext.get("manager_code"),
                email=qcontext.get("login"),
            )
            request.update_context(**{SIGNUP_CONTEXT_KEY: resolved})
        return super().do_signup(qcontext, do_login=do_login)

    def _is_existing_user_reset(self, qcontext):
        token = qcontext.get("token")
        if not token:
            return False
        partner = request.env["res.partner"].sudo()._signup_retrieve_partner(
            token, raise_exception=False
        )
        return bool(partner and partner.user_ids)


class AssetPortalEmployeeLink(http.Controller):
    """For portal users that exist already (created before this fix, invited
    by email, or signed in through OAuth) and have no employee yet."""

    @http.route("/my/employee/link", type="http", auth="user", website=True,
                methods=["GET", "POST"])
    def portal_employee_link(self, redirect=None, **post):
        user = request.env.user
        values = _employee_form_values()
        values.update({
            "page_name": "employee_link",
            "redirect": redirect if (redirect or "").startswith("/") and not redirect.startswith("//")
            else "/my/tickets/new",
            "post": post,
            "error": False,
        })
        if user._asset_portal_employee():
            return request.redirect(values["redirect"])

        if request.httprequest.method == "POST":
            try:
                resolved = request.env["hr.employee"].sudo()._asset_portal_resolve(
                    company_id=post.get("employee_company_id"),
                    employee_code=post.get("employee_code"),
                    manager_code=post.get("manager_code"),
                    email=user.email or user.login,
                    user=user,
                )
                request.env["hr.employee"].sudo()._asset_portal_link_user(user, resolved)
                return request.redirect(values["redirect"])
            except UserError as e:
                values["error"] = e.args[0]

        return request.render("asset_management.portal_employee_link", values)
