from odoo import http, fields, _
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import AccessError, MissingError


class AssetJobOrderPortal(CustomerPortal):

    @http.route(
        ["/my/job_order/<int:job_order_id>"],
        type="http",
        auth="public",
        website=True,
    )
    def portal_job_order(self, job_order_id=None, access_token=None, **kw):
        try:
            job = request.env["asset.job.order"].sudo().browse(job_order_id)

            if not job.exists() or job.access_token != access_token:
                return request.render("website.404")

            values = {
                "job_order": job,
                "page_name": "job_order",
                "access_token": access_token,
            }
            return request.render(
                "asset_management.portal_job_order_sign_page", values
            )

        except (AccessError, MissingError):
            return request.render("website.404")

    @http.route(
        ["/my/job_order/<int:job_order_id>/sign"],
        type="json",
        auth="public",
    )
    def job_order_sign(
        self,
        job_order_id,
        access_token=None,
        signature=None,
        signed_by=None,
        **kwargs,
    ):
        try:
            job = request.env["asset.job.order"].sudo().browse(job_order_id)

            if not job.exists() or job.access_token != access_token:
                return {"error": "Invalid access token"}

            if job.signature_state == "signed":
                return {"error": "This job order has already been signed"}

            ip_address = request.httprequest.environ.get("REMOTE_ADDR")
            job.action_sign(signature, signed_by, ip_address)

            return {"success": True}

        except Exception as e:
            return {"error": str(e)}