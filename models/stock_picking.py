from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _action_done(self):
        result = super()._action_done()
        delivered_move_lines = self.env["stock.move.line"].search(
            [
                ("picking_id", "in", self.ids),
                ("state", "=", "done"),
                ("quantity", ">", 0),
                ("location_dest_id.usage", "=", "customer"),
                ("lot_id", "!=", False),
            ]
        )
        if delivered_move_lines:
            devices = self.env["pinout.device"].search(
                [("final_lot_id", "in", delivered_move_lines.lot_id.ids)]
            )
            bundles = devices.bundle_id
            bundles.invalidate_recordset(
                ["customer_id", "sale_order_id", "delivery_id"]
            )
            bundles._sync_sold_state_from_move_lines(delivered_move_lines)
        return result
