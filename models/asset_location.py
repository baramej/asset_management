from odoo import fields, models, api, _
from odoo.exceptions import ValidationError, UserError
import logging
import re
from urllib.parse import urlparse, parse_qs
import requests

_logger = logging.getLogger(__name__)


class AssetLocation(models.Model):
    _name = "asset.location"
    _description = "Asset Location"
    _parent_store = True

    name = fields.Char(required=True)
    code = fields.Char(help="Short code for this location, e.g. BLDG2, FL01, R101")
    company_id = fields.Many2one("res.company", string="Company")
    parent_id = fields.Many2one("asset.location", string="Parent Location", index=True)
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("asset.location", "parent_id", string="Sub Locations")

    map_url = fields.Char(string="Google Maps Link")
    gps_latitude = fields.Float(
        string="Latitude",
        digits=(16, 7),
    )
    gps_longitude = fields.Float(
        string="Longitude",
        digits=(16, 7),
    )

    map_iframe = fields.Html(
        string="Map",
        compute="_compute_map_iframe",
        store=True,
        sanitize=False,
    )

    full_code = fields.Char(
        string="Full Code",
        compute="_compute_full_code",
        store=True,
        help="Hierarchical code like MAIN/BLDG2/FL01/R101"
    )

    asset_count = fields.Integer(
        string="Assets",
        compute="_compute_asset_count",
        store=False,
    )

    @api.depends("code", "parent_id", "parent_id.full_code")
    def _compute_full_code(self):
        for loc in self:
            this_code = loc.code or loc.name
            if loc.parent_id and loc.parent_id.full_code:
                loc.full_code = "%s/%s" % (loc.parent_id.full_code, this_code)
            else:
                loc.full_code = this_code

    def _compute_asset_count(self):
        Asset = self.env["account.asset"]
        for loc in self:
            loc.asset_count = Asset.search_count([("location_id", "=", loc.id)])

    @api.depends("gps_latitude", "gps_longitude")
    def _compute_map_iframe(self):
        for rec in self:
            if rec.gps_latitude and rec.gps_longitude:
                rec.map_iframe = f"""
                    <iframe
                        width="100%"
                        height="400"
                        frameborder="0"
                        scrolling="no"
                        marginheight="0"
                        marginwidth="0"
                        src="https://maps.google.com/maps?q={rec.gps_latitude:.7f},{rec.gps_longitude:.7f}&z=18&output=embed"
                    </iframe>
                """
            else:
                rec.map_iframe = "<div class='alert alert-warning'>No coordinates set for this location.</div>"

    @api.constrains("map_url")
    def _validate_map_url(self):
        for rec in self:
            if rec.map_url and not rec._is_valid_google_maps_url(rec.map_url):
                raise ValidationError(_("Please provide a valid Google Maps URL"))

    def _is_valid_google_maps_url(self, url):
        if not url:
            return True

        patterns = [
            r"^https?://(www\.)?google\.[a-z]{2,3}/maps/",
            r"^https?://maps\.google\.[a-z]{2,3}/",
            r"^https?://goo\.gl/maps/",
            r"^https?://maps\.app\.goo\.gl/",
        ]
        return any(re.match(pattern, url) for pattern in patterns)

    @api.onchange("map_url")
    def _onchange_map_url(self):
        if self.map_url and self._is_valid_google_maps_url(self.map_url):
            try:
                coords = self._extract_coordinates_from_url(self.map_url)
                if coords:
                    self.gps_latitude = coords["latitude"]
                    self.gps_longitude = coords["longitude"]
            except Exception as e:
                _logger.warning(f"Failed to process map URL: {str(e)}")
                return {
                    "warning": {
                        "title": _("Map URL Error"),
                        "message": _(
                            "Could not extract coordinates from this URL. Please check the link or enter coordinates manually."
                        ),
                    }
                }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("map_url"):
                try:
                    coords = self._extract_coordinates_from_url(vals["map_url"])
                    if coords:
                        vals.setdefault("gps_latitude", coords["latitude"])
                        vals.setdefault("gps_longitude", coords["longitude"])
                except Exception as e:
                    _logger.error(f"Error processing map URL during creation: {str(e)}")

        return super(AssetLocation, self).create(vals_list)

    def write(self, vals):
        if vals.get("map_url"):
            try:
                for rec in self:
                    coords = rec._extract_coordinates_from_url(vals["map_url"])
                    if coords:
                        vals.setdefault("gps_latitude", coords["latitude"])
                        vals.setdefault("gps_longitude", coords["longitude"])
            except Exception as e:
                _logger.error(f"Error processing map URL during update: {str(e)}")

        return super(AssetLocation, self).write(vals)

    def _extract_coordinates_from_url(self, url):
        if not url:
            return None

        try:
            if "goo.gl" in url or "maps.app.goo.gl" in url:
                session = requests.Session()
                response = session.head(url, allow_redirects=True, timeout=10)
                url = response.url

            parsed = urlparse(url)

            if "@" in url:
                parts = url.split("@")
                if len(parts) > 1:
                    coords_part = parts[1].split(",")
                    if len(coords_part) >= 2:
                        try:
                            lat = float(coords_part[0])
                            lon = float(coords_part[1].split(",")[0])
                            return {"latitude": lat, "longitude": lon}
                        except ValueError:
                            pass

            query_params = parse_qs(parsed.query)
            if "q" in query_params:
                q_value = query_params["q"][0]
                if "," in q_value:
                    try:
                        lat, lon = q_value.split(",")[0:2]
                        return {"latitude": float(lat), "longitude": float(lon)}
                    except ValueError:
                        pass

            path_parts = parsed.path.split("/")
            if "place" in path_parts:
                place_index = path_parts.index("place")
                if place_index + 1 < len(path_parts):
                    place_part = path_parts[place_index + 1]
                    if "@" in place_part:
                        coords_part = place_part.split("@")[1].split(",")[0:2]
                        try:
                            return {
                                "latitude": float(coords_part[0]),
                                "longitude": float(coords_part[1]),
                            }
                        except ValueError:
                            pass

            return None

        except Exception as e:
            _logger.error(f"Error extracting coordinates: {str(e)}")
            raise UserError(
                _("Could not extract coordinates from this URL. Please check the format.")
            )

    def open_google_maps(self):
        self.ensure_one()
        if not self.gps_latitude or not self.gps_longitude:
            raise UserError(_("Coordinates are not set for this location"))

        return {
            "type": "ir.actions.act_url",
            "url": f"https://www.google.com/maps?q={self.gps_latitude},{self.gps_longitude}",
            "target": "new",
        }

class HrDepartment(models.Model):
    _inherit = "hr.department"

    code = fields.Char(string="Code", help="Short code for this department, e.g. IT, HR, FIN")
