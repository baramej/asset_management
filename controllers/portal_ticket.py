import logging
from odoo import http, fields, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class HelpdeskPortalTicketController(http.Controller):

    def _get_employee(self, user):
        """hr.employee linked to this portal/internal user, if any."""
        return user._asset_portal_employee()

    def _get_company(self, user, employee):
        """The employee's company is authoritative; the user's is a fallback."""
        return employee.company_id if employee and employee.company_id else user.company_id

    def _get_scope_company(self, user, employee):
        """Company that limits the assets offered, or False for no limit.
        (Locations are never limited.) Employees whose portal company has
        "See All Assets" ticked (e.g. Al Manar) see every company's assets.
        """
        if employee and employee.portal_company_id.portal_full_access:
            return False
        return self._get_company(user, employee)

    def _top_locations(self, scope_company=None):
        """First level of the location picker (e.g. WATTAYAH, RUWI ...).

        Locations are not company-restricted: everyone sees the full tree,
        starting at the root locations. Only assets are filtered by company
        (see _get_assets_for_user / assets_by_location).
        """
        return request.env["asset.location"].sudo().search(
            [("parent_id", "=", False)], order="name"
        )

    def _user_top_locations(self):
        return self._top_locations()

    @staticmethod
    def _location_chain(location, tops):
        """Ancestors of `location`, itself included, starting at the first one
        that is offered on the first level of the picker.
        WATTAYAH / FORD SHOWROOM / BOILER ROOM -> [WATTAYAH, FORD SHOWROOM, BOILER ROOM]."""
        if not location:
            return location
        ids = [int(x) for x in (location.parent_path or "").split("/") if x] or [location.id]
        chain = location.browse(ids)
        for index, loc in enumerate(chain):
            if loc in tops:
                return chain[index:]
        return location.browse()

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
        user_company = self._get_company(user, employee)
        user_department = employee.department_id if employee else False

        scope_company = self._get_scope_company(user, employee)
        locations = self._top_locations()

        job_categories = request.env["helpdesk.job.category"].sudo().search(
            [("active", "=", True)], order="sequence, name"
        )
        assets = self._get_assets_for_user(scope_company.id if scope_company else None)
        asset_location_paths = {
            a.id: ",".join(str(i) for i in self._location_chain(a.location_id, locations).ids)
            for a in assets
        }
        initial_location = request.env["asset.location"]
        if post:
            leaf_id = post.get("sublocation_id") or post.get("location_id")
            if leaf_id and str(leaf_id).isdigit():
                initial_location = initial_location.sudo().browse(int(leaf_id)).exists()
        initial_location_path = ",".join(
            str(i) for i in self._location_chain(initial_location, locations).ids
        )
        approvers = request.env["asset.portal.approver"]._portal_selectable()
        if post and post.get("portal_approver_id"):
            selected_approver_id = str(post.get("portal_approver_id"))
        else:
            last = self._last_approver(partner, approvers)
            selected_approver_id = str(last.id) if last else ""

        return {
            "user_company": user_company,
            # What the employee recognises (e.g. Auto Arab); user_company is
            # the Odoo company it is held under (e.g. SBA) and scopes the data.
            "display_company_name": (
                employee.portal_company_id.name if employee and employee.portal_company_id
                else user_company.name
            ),
            "user_department": user_department,
            "locations": locations,
            "job_categories": job_categories,
            "assets": assets,
            "asset_location_paths": asset_location_paths,
            "initial_location_path": initial_location_path,
            "approvers": approvers,
            "selected_approver_id": selected_approver_id,
            "page_name": "new_ticket",
            "error": error or {},
            "error_message": error_message or [],
            "post": post or {},
        }

    def _last_approver(self, partner, approvers):
        """Approver this employee picked on their latest request, if still listed."""
        last_ticket = request.env["helpdesk.ticket"].sudo().search(
            [("partner_id", "=", partner.id), ("portal_approver_id", "!=", False)],
            order="create_date desc", limit=1,
        )
        approver = last_ticket.portal_approver_id
        return approver if approver and approver in approvers else False

    def _resolve_approver(self, value):
        """Approver for a posted id, only if it is on the selectable list."""
        try:
            approver_id = int(value or 0)
        except (TypeError, ValueError):
            return False
        if not approver_id:
            return False
        approvers = request.env["asset.portal.approver"]._portal_selectable()
        approver = approvers.filtered(lambda a: a.id == approver_id)
        return approver or False

    def _posted_locations(self, post):
        """(top, deepest) locations from the form, checked against the tree:
        the top must be offered to this user and the deepest must sit under it."""
        Location = request.env["asset.location"].sudo()
        top = leaf = Location
        top_id, leaf_id = post.get("location_id"), post.get("sublocation_id")
        if top_id and str(top_id).isdigit():
            top = Location.browse(int(top_id)).exists()
            if top and top not in self._user_top_locations():
                top = Location
        if top and leaf_id and str(leaf_id).isdigit():
            leaf = Location.search([("id", "=", int(leaf_id)), ("id", "child_of", top.id)], limit=1)
            if leaf == top:
                leaf = Location
        return top, leaf

    # ── GET ──────────────────────────────────────────────────────────────────

    @http.route("/my/tickets/new", auth="user", website=True, methods=["GET"])
    def portal_new_ticket(self, **kwargs):
        user = request.env.user
        partner = user.partner_id
        if not self._get_employee(user):
            return request.redirect("/my/employee/link?redirect=/my/tickets/new")

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

    def _align_partner_company(self, partner, team):
        """Helpdesk refuses a ticket whose customer belongs to another company
        than the ticket (= its team's company). Employees are often held under
        one company (e.g. SBG) while their portal contact sits in another
        (e.g. SBA), so make the contact shared (no company) in that case."""
        ticket_company = team.company_id
        if partner.company_id and ticket_company and partner.company_id != ticket_company:
            _logger.info(
                "Portal ticket: clearing company %s on contact %s (ticket company %s).",
                partner.company_id.name, partner.display_name, ticket_company.name,
            )
            partner.sudo().company_id = False

    # ── POST ─────────────────────────────────────────────────────────────────

    @http.route("/my/tickets/new", auth="user", website=True, methods=["POST"], csrf=True)
    def portal_new_ticket_submit(self, **post):
        user = request.env.user
        partner = user.partner_id
        if not self._get_employee(user):
            return request.redirect("/my/employee/link?redirect=/my/tickets/new")

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
        approver = self._resolve_approver(post.get("portal_approver_id"))
        if not approver and request.env["asset.portal.approver"]._portal_selectable():
            errors["portal_approver_id"] = True
            error_message.append(_("Please select an approver from the list."))

        if errors:
            ctx = self._get_form_context(
                user, partner, post=post, error=errors, error_message=error_message
            )
            return request.render(
                "asset_management.portal_helpdesk_ticket_form", ctx
            )

        employee = self._get_employee(user)
        company = self._get_company(user, employee)
        user_department = employee.department_id if employee else False
        team = self._get_helpdesk_team(company)
        self._align_partner_company(partner, team)

        # Priority: portal sends '0','1','2','3' matching helpdesk stars
        priority = post.get("priority", "0")
        if priority not in ("0", "1", "2", "3"):
            priority = "0"

        ticket_vals = {
            "name": post["name"].strip(),
            "description": post.get("description", "").strip(),
            "team_id": team.id,
            "partner_id": partner.id,
            "priority": priority,
        }
        if post.get("asset_id"):
            ticket_vals["asset_id"] = int(post["asset_id"])
        if approver:
            ticket_vals["portal_approver_id"] = approver.id

        # helpdesk.ticket.create() routes to the selected approver, moves the ticket to
        # "Waiting Manager Approval" and emails the approval link.
        ticket = request.env["helpdesk.ticket"].sudo().create(ticket_vals)
        # Company / department are trusted from the server-side user record only —
        # never from POST — since these fields are now locked/non-editable on the form.
        extra_vals = {
            "ticket_id": ticket.id,
            "company_id": company.id,
            "department_id": user_department.id if user_department else False,
        }
        top_location, leaf_location = self._posted_locations(post)
        if top_location:
            extra_vals["location_id"] = top_location.id
        if leaf_location:
            extra_vals["sublocation_id"] = leaf_location.id
        if post.get("building"):
            extra_vals["building"] = post["building"].strip()
        if post.get("job_category_id"):
            extra_vals["job_category_id"] = int(post["job_category_id"])
        if post.get("asset_id"):
            extra_vals["asset_id"] = int(post["asset_id"])
        if ticket.manager_id:
            extra_vals["approver_id"] = ticket.manager_id.id

        request.env["helpdesk.portal.ticket.line"].sudo().create(extra_vals)

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
                approver=ticket._approver_display_name(),
            ),
            subtype_xmlid="mail.mt_note",
        )

        return request.redirect("/my/tickets/submitted")

    # ── My Service Requests ───────────────────────────────────────────────────

    def _my_service_requests(self, user):
        """Tickets this portal user raised. Read with sudo on purpose: Helpdesk's
        own portal only shows tickets the user follows on portal-visible teams."""
        return request.env["helpdesk.ticket"].sudo().search(
            [("partner_id", "=", user.partner_id.id)], order="create_date desc"
        )

    @http.route("/my/service-requests", auth="user", website=True)
    def portal_my_service_requests(self, **kwargs):
        tickets = self._my_service_requests(request.env.user)
        return request.render("asset_management.portal_my_service_requests", {
            "tickets": tickets,
            "page_name": "service_requests",
        })

    @http.route("/my/service-requests/<int:ticket_id>", auth="user", website=True)
    def portal_my_service_request(self, ticket_id, **kwargs):
        ticket = self._my_service_requests(request.env.user).filtered(
            lambda t: t.id == ticket_id
        )
        if not ticket:
            return request.redirect("/my/service-requests")
        return request.render("asset_management.portal_my_service_request", {
            "ticket": ticket,
            "line": ticket.portal_line_ids[:1],
            "page_name": "service_requests",
        })

    @http.route("/my/tickets/submitted", auth="user", website=True)
    def portal_ticket_submitted(self, **kwargs):
        return request.render(
            "asset_management.portal_helpdesk_ticket_submitted", {}
        )

    # ── AJAX endpoints ────────────────────────────────────────────────────────

    @http.route("/my/tickets/sublocations", auth="user", type="json", methods=["POST"])
    def sublocations(self, parent_id=None, **kwargs):
        """Children of a location, for the next level of the picker."""
        if not parent_id:
            return []
        locs = request.env["asset.location"].sudo().search(
            [("parent_id", "=", int(parent_id))], order="name"
        )
        return [{"id": l.id, "name": l.name, "has_children": bool(l.child_ids)} for l in locs]

    def _assets_payload(self, assets):
        tops = self._user_top_locations()
        payload = []
        for a in assets:
            chain = self._location_chain(a.location_id, tops)
            payload.append({
                "id": a.id,
                "name": a.name,
                "location_id": a.location_id.id or False,
                "location_name": " / ".join(chain.mapped("name")) or a.location_id.name or "",
                "location_path": ",".join(str(i) for i in chain.ids),
            })
        return payload

    @http.route("/my/tickets/assets_by_location", auth="user", type="json", methods=["POST"])
    def assets_by_location(self, location_id=None, sublocation_id=None, **kwargs):
        """Assets at the deepest selected location and everything below it,
        limited to the user's company (unless their portal company has
        "See All Assets"). WATTAYAH -> all of Wattayah; + FORD
        SHOWROOM -> the showroom incl. its Boiler Room; + BOILER ROOM -> just
        the boiler room. Nothing selected -> all of the user's assets."""
        user = request.env.user
        scope_company = self._get_scope_company(user, self._get_employee(user))
        domain = []
        if scope_company:
            domain.append(("company_id", "=", scope_company.id))
        leaf_id = sublocation_id or location_id
        if leaf_id:
            domain.append(("location_id", "child_of", int(leaf_id)))
        assets = request.env["account.asset"].sudo().search(domain, order="name asc")
        return self._assets_payload(assets)
