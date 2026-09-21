import logging
from odoo import http, fields, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class HelpdeskPortalTicketController(http.Controller):

    def _get_employee_manager(self, partner):
        Employee = request.env["hr.employee"].sudo()
        emp = Employee.search([("work_email", "=", partner.email)], limit=1)
        if not emp:
            emp = Employee.search(
                [("user_id.partner_id", "=", partner.id)], limit=1
            )
        if not emp or not emp.parent_id or not emp.parent_id.user_id:
            return False
        mgr = emp.parent_id.user_id
        return mgr if mgr.email else False

    def _get_employee(self, user):
        """Return the hr.employee record linked to this portal/internal user, if any."""
        Employee = request.env["hr.employee"].sudo()
        emp = Employee.search([("user_id", "=", user.id)], limit=1)
        if not emp and user.partner_id.email:
            emp = Employee.search(
                [("work_email", "=", user.partner_id.email)], limit=1
            )
        return emp

    def _get_or_create_manager_approval_stage(self):
        Stage = request.env["helpdesk.stage"].sudo()
        stage = Stage.search([("name", "ilike", "Waiting Manager Approval")], limit=1)
        if not stage:
            stage = Stage.create({
                "name": "Waiting Manager Approval",
                "sequence": 0,
                "fold": False,
            })
        return stage

    def _get_assets_for_user(self, company_id=None):
        """Return all assets, optionally filtered by their accounting Company."""
        domain = []
        if company_id:
            domain = [("company_id", "=", company_id)]
        return request.env["account.asset"].sudo().search(
            domain, order="name asc"
        )

    def _get_form_context(self, user, partner, post=None, error=None, error_message=None):
        """Shared context builder for both the GET form and POST error re-render."""
        employee = self._get_employee(user)
        user_company = user.company_id
        user_department = employee.department_id if employee else False

        locations = request.env["asset.location"].sudo().search([
            ("company_id", "=", user_company.id),
            "|",
            ("parent_id", "=", False),
            ("parent_id.company_id", "!=", user_company.id),
        ], order="name")

        job_categories = request.env["helpdesk.job.category"].sudo().search(
            [("active", "=", True)], order="sequence, name"
        )
        assets = self._get_assets_for_user(user_company.id)
        manager = self._get_employee_manager(partner)

        return {
            "user_company": user_company,
            "user_department": user_department,
            "locations": locations,
            "job_categories": job_categories,
            "assets": assets,
            "manager": manager,
            "page_name": "new_ticket",
            "error": error or {},
            "error_message": error_message or [],
            "post": post or {},
        }

    # ── GET ──────────────────────────────────────────────────────────────────

    @http.route("/my/tickets/new", auth="user", website=True, methods=["GET"])
    def portal_new_ticket(self, **kwargs):
        user = request.env.user
        partner = user.partner_id

        ctx = self._get_form_context(user, partner)
        return request.render(
            "asset_management.portal_helpdesk_ticket_form", ctx
        )

    def _get_helpdesk_team(self, company):
        Team = request.env["helpdesk.team"].sudo()
        team = Team.search([("company_id", "=", company.id)], limit=1)
        if not team:
            # fall back to a company-agnostic team if you have one
            team = Team.search([("company_id", "=", False)], limit=1)
        return team

    # ── POST ─────────────────────────────────────────────────────────────────

    @http.route("/my/tickets/new", auth="user", website=True, methods=["POST"], csrf=True)
    def portal_new_ticket_submit(self, **post):
        user = request.env.user
        partner = user.partner_id

        errors = {}
        error_message = []

        if not post.get("name", "").strip():
            errors["name"] = True
            error_message.append(_("Subject is required."))
        if not post.get("description", "").strip():
            errors["description"] = True
            error_message.append(_("Description is required."))
        if not post.get("job_category_id"):
            errors["job_category_id"] = True
            error_message.append(_("Job Category is required."))

        if errors:
            ctx = self._get_form_context(
                user, partner, post=post, error=errors, error_message=error_message
            )
            return request.render(
                "asset_management.portal_helpdesk_ticket_form", ctx
            )

        employee = self._get_employee(user)
        user_department = employee.department_id if employee else False
        manager = self._get_employee_manager(partner)
        stage = self._get_or_create_manager_approval_stage()



        team = self._get_helpdesk_team(user.company_id)

        # Priority: portal sends '0','1','2','3' matching helpdesk stars
        priority = post.get("priority", "0")
        if priority not in ("0", "1", "2", "3"):
            priority = "0"

        ticket_vals = {
            "name": post["name"].strip(),
            "description": post.get("description", "").strip(),
            "team_id": team.id,
            "partner_id": partner.id,
            "stage_id": stage.id,
            "priority": priority,
        }
        if post.get("asset_id"):
            ticket_vals["asset_id"] = int(post["asset_id"])

        ticket = request.env["helpdesk.ticket"].sudo().create(ticket_vals)

        import uuid as _uuid
        # Company / department are trusted from the server-side user record only —
        # never from POST — since these fields are now locked/non-editable on the form.
        extra_vals = {
            "ticket_id": ticket.id,
            "company_id": user.company_id.id,
            "department_id": user_department.id if user_department else False,
        }
        if post.get("location_id"):
            extra_vals["location_id"] = int(post["location_id"])
        if post.get("sublocation_id"):
            extra_vals["sublocation_id"] = int(post["sublocation_id"])
        if post.get("building"):
            extra_vals["building"] = post["building"].strip()
        if post.get("job_category_id"):
            extra_vals["job_category_id"] = int(post["job_category_id"])
        if post.get("asset_id"):
            extra_vals["asset_id"] = int(post["asset_id"])
        if manager:
            extra_vals["approver_id"] = manager.id

        request.env["helpdesk.portal.ticket.line"].sudo().create(extra_vals)

        if manager and manager.email:
            token = str(_uuid.uuid4())
            ticket.sudo().write({
                "manager_approval_state": "waiting_manager_approval",
                "approval_access_token": token,
                "manager_id": manager.id,
            })
            ticket.sudo()._send_manager_approval_email(manager, token)

        category = request.env["helpdesk.job.category"].sudo().browse(
            int(post["job_category_id"])
        ) if post.get("job_category_id") else False
        asset = request.env["account.asset"].sudo().browse(
            int(post["asset_id"])
        ) if post.get("asset_id") else False

        priority_label = {"0": "Normal", "1": "Low", "2": "High", "3": "Very High"}.get(priority, "Normal")

        ticket.sudo().message_post(
            body=_(
                "Ticket submitted via portal.<br/>"
                "<b>Job Category:</b> %(cat)s<br/>"
                "<b>Asset / Equipment:</b> %(asset)s<br/>"
                "<b>Priority:</b> %(priority)s<br/>"
                "<b>Approver:</b> %(approver)s",
                cat=category.name if category else "—",
                asset=asset.name if asset else "—",
                priority=priority_label,
                approver=manager.name if manager else "—",
            ),
            subtype_xmlid="mail.mt_note",
        )

        return request.redirect("/my/tickets/submitted")

    @http.route("/my/tickets/submitted", auth="user", website=True)
    def portal_ticket_submitted(self, **kwargs):
        return request.render(
            "asset_management.portal_helpdesk_ticket_submitted", {}
        )

    # ── AJAX endpoints ────────────────────────────────────────────────────────

    @http.route("/my/tickets/sublocations", auth="user", type="json", methods=["POST"])
    def sublocations(self, parent_id=None, **kwargs):
        if not parent_id:
            return []
        locs = request.env["asset.location"].sudo().search(
            [("parent_id", "=", int(parent_id))], order="name"
        )
        return [{"id": l.id, "name": l.name} for l in locs]

    def _assets_payload(self, assets):
        return [
            {
                "id": a.id,
                "name": a.name,
                "location_id": a.location_id.id or False,
                "location_name": a.location_id.name or "",
            }
            for a in assets
        ]

    @http.route("/my/tickets/assets_by_location", auth="user", type="json", methods=["POST"])
    def assets_by_location(self, location_id=None, sublocation_id=None, **kwargs):
        """Return assets for the given location.
        - sublocation chosen  -> assets at that exact sub-location only
        - only main location  -> assets at that location AND all its descendants
        - neither             -> fall back to full company-scoped list
        """
        user = request.env.user

        if sublocation_id:
            assets = request.env["account.asset"].sudo().search(
                [("location_id", "=", int(sublocation_id))], order="name asc"
            )
        elif location_id:
            assets = request.env["account.asset"].sudo().search(
                [("location_id", "child_of", int(location_id))], order="name asc"
            )
        else:
            assets = self._get_assets_for_user(user.company_id.id)
            return self._assets_payload(assets)

        return self._assets_payload(assets)
