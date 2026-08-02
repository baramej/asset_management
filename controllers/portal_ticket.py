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
        """Return all assets, optionally filtered by company."""
        domain = []
        if company_id:
            # Filter assets where the customer partner belongs to the given company
            # Try both direct company match and partner with that company
            partners = request.env["res.partner"].sudo().search([
                "|",
                ("company_id", "=", company_id),
                ("id", "=", company_id),  # partner IS the company
            ])
            if partners:
                domain = [("customer_id", "in", partners.ids)]
        return request.env["account.asset"].sudo().search(
            domain, order="name asc"
        )

    # ── GET ──────────────────────────────────────────────────────────────────

    @http.route("/my/tickets/new", auth="user", website=True, methods=["GET"])
    def portal_new_ticket(self, **kwargs):
        user = request.env.user
        partner = user.partner_id

        companies = request.env["res.company"].sudo().search([], order="name")
        # Top-level locations only (no parent) — sub-locations loaded separately
        locations = request.env["asset.location"].sudo().search(
            [("parent_id", "=", False)], order="name"
        )
        departments = request.env["hr.department"].sudo().search(
            [("company_id", "=", user.company_id.id)], order="name"
        )
        job_categories = request.env["helpdesk.job.category"].sudo().search(
            [("active", "=", True)], order="sequence, name"
        )
        # Load ALL assets initially — company filter via AJAX
        assets = request.env["account.asset"].sudo().search([], order="name asc")
        manager = self._get_employee_manager(partner)

        return request.render(
            "asset_management.portal_helpdesk_ticket_form",
            {
                "companies": companies,
                "departments": departments,
                "locations": locations,
                "job_categories": job_categories,
                "assets": assets,
                "user_company_id": user.company_id.id,
                "manager": manager,
                "page_name": "new_ticket",
                "error": {},
                "error_message": [],
                "post": {},
            },
        )

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
            companies = request.env["res.company"].sudo().search([], order="name")
            locations = request.env["asset.location"].sudo().search(
                [("parent_id", "=", False)], order="name"
            )
            departments = request.env["hr.department"].sudo().search(
                [("company_id", "=", user.company_id.id)], order="name"
            )
            job_categories = request.env["helpdesk.job.category"].sudo().search(
                [("active", "=", True)], order="sequence, name"
            )
            assets = request.env["account.asset"].sudo().search([], order="name asc")
            manager = self._get_employee_manager(partner)
            return request.render(
                "asset_management.portal_helpdesk_ticket_form",
                {
                    "companies": companies,
                    "departments": departments,
                    "locations": locations,
                    "job_categories": job_categories,
                    "assets": assets,
                    "user_company_id": user.company_id.id,
                    "manager": manager,
                    "page_name": "new_ticket",
                    "error": errors,
                    "error_message": error_message,
                    "post": post,
                },
            )

        manager = self._get_employee_manager(partner)
        stage = self._get_or_create_manager_approval_stage()

        # Priority: portal sends '0','1','2','3' matching helpdesk stars
        priority = post.get("priority", "0")
        if priority not in ("0", "1", "2", "3"):
            priority = "0"

        ticket_vals = {
            "name": post["name"].strip(),
            "description": post.get("description", "").strip(),
            "partner_id": partner.id,
            "stage_id": stage.id,
            "priority": priority,
        }
        # Asset
        if post.get("asset_id"):
            ticket_vals["asset_id"] = int(post["asset_id"])

        ticket = request.env["helpdesk.ticket"].sudo().create(ticket_vals)

        import uuid as _uuid
        extra_vals = {
            "ticket_id": ticket.id,
            "remarks": post.get("remarks", "").strip() or False,
        }
        if post.get("company_id"):
            extra_vals["company_id"] = int(post["company_id"])
        if post.get("department_id"):
            extra_vals["department_id"] = int(post["department_id"])
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

    @http.route("/my/tickets/assets_by_company", auth="user", type="json", methods=["POST"])
    def assets_by_company(self, company_id=None, **kwargs):
        """Return assets filtered by company. If no company, return all."""
        if not company_id:
            assets = request.env["account.asset"].sudo().search([], order="name asc")
        else:
            cid = int(company_id)
            # Match assets whose customer is a partner of that company
            company_rec = request.env["res.company"].sudo().browse(cid)
            partner_ids = request.env["res.partner"].sudo().search([
                "|",
                ("company_id", "=", cid),
                ("id", "=", company_rec.partner_id.id),
            ]).ids
            assets = request.env["account.asset"].sudo().search(
                [("customer_id", "in", partner_ids)] if partner_ids else [],
                order="name asc",
            )
            # Fallback: if nothing found, return all (better UX than empty list)
            if not assets:
                assets = request.env["account.asset"].sudo().search([], order="name asc")

        return [
            {
                "id": a.id,
                "name": a.name,
                "location_id": a.location_id.id or False,
                "location_name": a.location_id.name or "",
                "location_full": a.location_id.full_code or a.location_id.name or "",
            }
            for a in assets
        ]

    @http.route("/my/tickets/departments_by_company", auth="user", type="json", methods=["POST"])
    def departments_by_company(self, company_id=None, **kwargs):
        if not company_id:
            depts = request.env["hr.department"].sudo().search([], order="name")
        else:
            depts = request.env["hr.department"].sudo().search(
                [("company_id", "=", int(company_id))], order="name"
            )
        return [{"id": d.id, "name": d.name} for d in depts]

    @http.route("/my/tickets/sublocations", auth="user", type="json", methods=["POST"])
    def sublocations(self, parent_id=None, **kwargs):
        if not parent_id:
            return []
        locs = request.env["asset.location"].sudo().search(
            [("parent_id", "=", int(parent_id))], order="name"
        )
        return [{"id": l.id, "name": l.name} for l in locs]