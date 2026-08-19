from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def action_cancel(self):
        bundles = self.move_line_ids._get_pinout_bundles()
        result = super().action_cancel()
        bundles._sync_reservation_state()
        return result

    def _action_done(self):
        result = super()._action_done()
        tracked_move_lines = self.env["stock.move.line"].search(
            [
                ("picking_id", "in", self.ids),
                ("state", "=", "done"),
                ("quantity", ">", 0),
                ("lot_id", "!=", False),
            ]
        )
        devices = self.env["pinout.device"].search(
            [("final_lot_id", "in", tracked_move_lines.lot_id.ids)]
        )
        bundles = devices.bundle_id
        if bundles:
            bundles.invalidate_recordset(
                [
                    "customer_id",
                    "sale_order_id",
                    "delivery_id",
                    "reservation_delivery_id",
                ]
            )

        delivered_move_lines = tracked_move_lines.filtered(
            lambda line: line.location_dest_id.usage == "customer"
        )
        if delivered_move_lines:
            bundles._sync_sold_state_from_move_lines(delivered_move_lines)

        return_move_lines = tracked_move_lines.filtered(
            lambda line: (
                line.location_id.usage == "customer"
                and line.location_dest_id.usage == "internal"
            )
        )
        if return_move_lines:
            return_bundles = devices.filtered(
                lambda device: device.final_lot_id in return_move_lines.lot_id
            ).bundle_id
            return_bundles._sync_return_state_from_devices(self)
        return result
