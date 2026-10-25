# -*- coding: utf-8 -*-
"""Print the worker job card straight from the browser, without a PDF download.

The Job Order button opens /asset/job_order/<id>/job_card/print in a new tab:
the job card is rendered as HTML, the browser's print dialog opens right away,
and the tab closes itself once printing is done or cancelled.
"""
from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

REPORT_XMLID = "asset_management.action_report_asset_job_order_worker"

PRINT_HEAD = """
<style>
    @page { size: A4 portrait; margin: 8mm; }
    html, body { background: #fff !important; }
    body { margin: 0 !important; padding: 0 !important; max-width: none !important; }
    body > .container, body > .container-fluid, main { max-width: none !important; padding: 0 !important; margin: 0 !important; }
    .o_jobcard { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    @media screen { body { padding: 12px !important; } }
</style>
"""

PRINT_SCRIPT = """
<script>
    window.addEventListener("load", function () {
        window.addEventListener("afterprint", function () { window.close(); });
        setTimeout(function () { window.print(); }, 300);
    });
</script>
"""


class AssetJobCardPrint(http.Controller):

    @http.route("/asset/job_order/<int:job_id>/job_card/print", type="http", auth="user")
    def print_job_card(self, job_id, **kwargs):
        job = request.env["asset.job.order"].browse(job_id)
        try:
            job.check_access("read")
            job.ensure_one()
        except (AccessError, MissingError, ValueError):
            return request.not_found()

        html, _ = request.env["ir.actions.report"]._render_qweb_html(REPORT_XMLID, job.ids)
        # Plain str on purpose: a Markup would escape the tags spliced in below.
        html = html.decode() if isinstance(html, bytes) else str(html)
        if "</head>" in html:
            html = html.replace("</head>", PRINT_HEAD + "</head>", 1)
        else:
            html = PRINT_HEAD + html
        if "</body>" in html:
            html = html.replace("</body>", PRINT_SCRIPT + "</body>", 1)
        else:
            html = html + PRINT_SCRIPT
        return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])