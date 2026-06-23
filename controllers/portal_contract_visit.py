from odoo import http, fields, _
from odoo.http import request
import json
import base64

class ContractVisitPortal(http.Controller):

    def _get_contract(self, contract_id, token):
        contract = request.env["asset.maintenance.contract"].sudo().browse(contract_id)
        if not contract.exists() or contract.contractor_portal_token != token:
            return None
        return contract

    @http.route(
        "/contract/<int:contract_id>/submit-visit",
        type="http", auth="public", website=True,
    )
    def visit_form_page(self, contract_id, token=None, **kw):
        contract = self._get_contract(contract_id, token)
        if not contract:
            return request.render("website.404")

        return request.render("asset_management.portal_contractor_visit_form", {
            "contract": contract,
            "token": token,
            "assets": contract.contract_line_ids.mapped("asset_id"),
            "visit_types": [
                ("preventive", "Preventive Maintenance"),
                ("corrective", "Corrective / Repair"),
                ("inspection", "Inspection"),
                ("emergency", "Emergency"),
                ("warranty", "Warranty Claim"),
            ],
        })

    @http.route(
        "/contract/<int:contract_id>/submit-visit/post",
        type="http", auth="public", website=True, methods=["POST"], csrf=True,
    )
    def visit_form_submit(self, contract_id, token=None, **kw):
        contract = self._get_contract(contract_id, token)
        if not contract:
            return request.render("website.404")

        post  = request.httprequest.form
        files = request.httprequest.files

        asset_id = int(post.get("asset_id") or 0) or False

        visit = request.env["asset.contract.service.visit"].sudo().create({
            "contract_id":            contract.id,
            "asset_id":               asset_id,
            "visit_date":             post.get("visit_date") or fields.Date.today(),
            "visit_type":             post.get("visit_type", "preventive"),
            "technician_name":        post.get("technician_name", ""),
            "technician_company":     post.get("technician_company", ""),
            "work_done":              post.get("work_done", ""),
            "findings":               post.get("findings", ""),
            "parts_used":             post.get("parts_used_text", ""),
            "contractor_notes":       post.get("contractor_notes", ""),
            "next_visit_date":        post.get("next_visit_date") or False,
            "report_ref":             post.get("report_ref", ""),
            "status":                 "completed",
            "is_covered_under_contract": True,
            "submitted_by_name":      post.get("submitted_by_name", ""),
            "submitted_on":           fields.Datetime.now(),
            "portal_state":           "submitted",
        })

        tech_names = post.getlist("tech_name[]")
        tech_roles = post.getlist("tech_role[]")
        tech_hours = post.getlist("tech_hours[]")
        tech_rates = post.getlist("tech_rate[]")
        for i, name in enumerate(tech_names):
            if not name.strip():
                continue
            request.env["asset.contract.visit.labour.line"].sudo().create({
                "visit_id":    visit.id,
                "technician":  name.strip(),
                "role":        tech_roles[i] if i < len(tech_roles) else "",
                "hours":       float(tech_hours[i]) if i < len(tech_hours) and tech_hours[i] else 0.0,
                "hourly_rate": float(tech_rates[i]) if i < len(tech_rates) and tech_rates[i] else 0.0,
            })

        mat_descs  = post.getlist("mat_desc[]")
        mat_qtys   = post.getlist("mat_qty[]")
        mat_units  = post.getlist("mat_unit[]")
        mat_prices = post.getlist("mat_price[]")
        for i, desc in enumerate(mat_descs):
            if not desc.strip():
                continue
            request.env["asset.contract.visit.material.line"].sudo().create({
                "visit_id":    visit.id,
                "description": desc.strip(),
                "quantity":    float(mat_qtys[i]) if i < len(mat_qtys) and mat_qtys[i] else 1.0,
                "unit":        mat_units[i] if i < len(mat_units) else "pcs",
                "unit_price":  float(mat_prices[i]) if i < len(mat_prices) and mat_prices[i] else 0.0,
            })

        attachment_ids = []
        for f in files.getlist("attachments"):
            if f and f.filename:
                att = request.env["ir.attachment"].sudo().create({
                    "name":      f.filename,
                    "datas":     base64.b64encode(f.read()),
                    "res_model": "asset.contract.service.visit",
                    "res_id":    visit.id,
                    "mimetype":  f.content_type,
                })
                attachment_ids.append(att.id)
        if attachment_ids:
            visit.sudo().write({
                "portal_attachment_ids": [(4, aid) for aid in attachment_ids]
            })

        contract.sudo().message_post(
            body=_(
                "New contractor visit report submitted by <b>%(name)s</b> (%(company)s).<br/>"
                "Visit date: <b>%(date)s</b> | Type: <b>%(vtype)s</b>",
                name=post.get("submitted_by_name", "Contractor"),
                company=post.get("technician_company", "—"),
                date=post.get("visit_date", ""),
                vtype=post.get("visit_type", ""),
            ),
            subtype_xmlid="mail.mt_note",
        )

        return request.redirect(
            f"/contract/{contract_id}/submit-visit?token={token}&done=1"
        )

class ContractVisitPortal(http.Controller):


    def _get_visit(self, visit_id, token):
        visit = request.env["asset.contract.service.visit"].sudo().browse(visit_id)
        if not visit.exists() or visit.access_token != token:
            return None
        return visit

    @http.route(
        "/contract/visit/<int:visit_id>/report",
        type="http", auth="public", website=True,
    )
    def visit_report_page(self, visit_id, token=None, **kw):
        visit = self._get_visit(visit_id, token)
        if not visit:
            return request.render("website.404")
        return request.render("asset_management.portal_contract_visit_report", {
            "visit": visit,
            "token": token,
            "submitted": visit.portal_state == "submitted",
        })

    @http.route(
        "/contract/visit/<int:visit_id>/submit",
        type="http", auth="public", website=True, methods=["POST"], csrf=True,
    )
    def visit_report_submit(self, visit_id, token=None, **kw):
        visit = self._get_visit(visit_id, token)
        if not visit:
            return request.render("website.404")

        if visit.portal_state == "submitted":
            return request.redirect(
                f"/contract/visit/{visit_id}/report?token={token}&done=1"
            )

        post = request.httprequest.form
        files = request.httprequest.files

        visit.sudo().write({
            "technician_name":      post.get("technician_name", ""),
            "technician_company":   post.get("technician_company", ""),
            "work_done":            post.get("work_done", ""),
            "findings":             post.get("findings", ""),
            "parts_used":           post.get("parts_used_text", ""),
            "contractor_notes":     post.get("contractor_notes", ""),
            "next_visit_date":      post.get("next_visit_date") or False,
            "submitted_by_name":    post.get("submitted_by_name", ""),
            "submitted_on":         fields.Datetime.now(),
            "portal_state":         "submitted",
        })

        visit.sudo().labour_line_ids.unlink()
        technicians = post.getlist("tech_name[]")
        roles       = post.getlist("tech_role[]")
        hours_list  = post.getlist("tech_hours[]")
        rates       = post.getlist("tech_rate[]")
        for i, name in enumerate(technicians):
            if not name.strip():
                continue
            request.env["asset.contract.visit.labour.line"].sudo().create({
                "visit_id":    visit.id,
                "technician":  name.strip(),
                "role":        roles[i] if i < len(roles) else "",
                "hours":       float(hours_list[i]) if i < len(hours_list) and hours_list[i] else 0,
                "hourly_rate": float(rates[i]) if i < len(rates) and rates[i] else 0,
            })

        visit.sudo().material_line_ids.unlink()
        mat_desc  = post.getlist("mat_desc[]")
        mat_qty   = post.getlist("mat_qty[]")
        mat_unit  = post.getlist("mat_unit[]")
        mat_price = post.getlist("mat_price[]")
        for i, desc in enumerate(mat_desc):
            if not desc.strip():
                continue
            request.env["asset.contract.visit.material.line"].sudo().create({
                "visit_id":   visit.id,
                "description": desc.strip(),
                "quantity":   float(mat_qty[i]) if i < len(mat_qty) and mat_qty[i] else 1,
                "unit":       mat_unit[i] if i < len(mat_unit) else "pcs",
                "unit_price": float(mat_price[i]) if i < len(mat_price) and mat_price[i] else 0,
            })

        uploaded_files = files.getlist("attachments")
        attachment_ids = []
        for f in uploaded_files:
            if f and f.filename:
                data = base64.b64encode(f.read())
                att = request.env["ir.attachment"].sudo().create({
                    "name":      f.filename,
                    "datas":     data,
                    "res_model": "asset.contract.service.visit",
                    "res_id":    visit.id,
                    "mimetype":  f.content_type,
                })
                attachment_ids.append(att.id)
        if attachment_ids:
            visit.sudo().write({
                "portal_attachment_ids": [(4, aid) for aid in attachment_ids]
            })

        visit.sudo().message_post(
            body=_(
                "Contractor report submitted by <b>%(name)s</b> (%(company)s) on %(date)s.",
                name=post.get("submitted_by_name", "Contractor"),
                company=post.get("technician_company", "—"),
                date=fields.Datetime.now(),
            ),
            subtype_xmlid="mail.mt_note",
        )

        return request.redirect(
            f"/contract/visit/{visit_id}/report?token={token}&done=1"
        )