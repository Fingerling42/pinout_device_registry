from odoo import api, models


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    @api.model_create_multi
    def create(self, vals_list):
        move_lines = super().create(vals_list)
        move_lines._sync_pinout_bundle_reservations()
        return move_lines

    def write(self, vals):
        tracked_fields = {
            "lot_id",
            "picking_id",
            "quantity",
            "location_id",
            "location_dest_id",
        }
        if not tracked_fields.intersection(vals):
            return super().write(vals)

        bundles = self._get_pinout_bundles()
        result = super().write(vals)
        bundles |= self._get_pinout_bundles()
        bundles._sync_reservation_state()
        return result

    def unlink(self):
        bundles = self._get_pinout_bundles()
        result = super().unlink()
        bundles._sync_reservation_state()
        return result

    def _sync_pinout_bundle_reservations(self):
        self._get_pinout_bundles()._sync_reservation_state()

    def _get_pinout_bundles(self):
        lots = self.lot_id
        if not lots:
            return self.env["pinout.device.bundle"]
        devices = self.env["pinout.device"].search(
            [("final_lot_id", "in", lots.ids), ("bundle_id", "!=", False)]
        )
        return devices.bundle_id
