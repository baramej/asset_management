import io
import xlsxwriter
import base64
from odoo import fields, models

SECTION_LABELS = {
    "keys": "Keys", "electrical": "Electrical", "plumbing": "Plumbing",
    "general": "General / Damages", "other": "Other",
}
SECTION_ORDER = ["keys", "electrical", "plumbing", "general", "other"]
STATUS_LABELS = {"ok": "OK", "missing": "NOT OK", "damaged": "NOT OK", "na": "N/A"}

