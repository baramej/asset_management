from odoo import http, fields, _
from odoo.http import request


class HelpdeskManagerApprovalPortal(http.Controller):

    @http.route(
        "/helpdesk/ticket/<int:ticket_id>/manager-review",
        type="http",
        auth="public",
        website=True,
    )
    def manager_review(self, ticket_id, token=None, **kw):
        ticket = request.env["helpdesk.ticket"].sudo().browse(ticket_id)
        if not ticket.exists() or ticket.approval_access_token != token:
            return request.render("website.404")

        values = {
            "ticket": ticket,
            "token": token,
            "page_name": "manager_approval",
        }
        return request.render(
            "asset_management.portal_helpdesk_manager_approval", values
        )

    @http.route(
        "/helpdesk/ticket/<int:ticket_id>/manager-approve",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def manager_approve(self, ticket_id, token=None, **kw):
        ticket = request.env["helpdesk.ticket"].sudo().browse(ticket_id)
        if not ticket.exists() or ticket.approval_access_token != token:
            return request.render("website.404")
        if ticket.manager_approval_state == "waiting_manager_approval":
            ticket.action_manager_approve()
        return request.redirect(
            f"/helpdesk/ticket/{ticket_id}/manager-review?token={token}&done=approved"
        )

    @http.route(
        "/helpdesk/ticket/<int:ticket_id>/manager-deny",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def manager_deny(self, ticket_id, token=None, **kw):
        ticket = request.env["helpdesk.ticket"].sudo().browse(ticket_id)
        if not ticket.exists() or ticket.approval_access_token != token:
            return request.render("website.404")
        reason = kw.get("reason", "")
        if ticket.manager_approval_state == "waiting_manager_approval":
            ticket.action_manager_deny(reason=reason)
        return request.redirect(
            f"/helpdesk/ticket/{ticket_id}/manager-review?token={token}&done=denied"
        )