from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    billing_email = fields.Char(
        string="Billing Email",
        help="Email used for job order sign-off requests and billing "
             "correspondence for this company. Contacts of the company "
             "inherit it when they have none of their own.",
    )

    def _get_billing_email(self):
        """Billing email for this partner: its own Billing Email, then its
        company's Billing Email, then its normal email."""
        if not self:
            return False
        self.ensure_one()
        return (
            self.billing_email
            or self.commercial_partner_id.billing_email
            or self.email
            or self.commercial_partner_id.email
            or False
        )


class ResCompany(models.Model):
    _inherit = "res.company"

    billing_email = fields.Char(
        related="partner_id.billing_email",
        readonly=False,
        string="Billing Email",
    )
