from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        result = super()._action_done(cancel_backorder=cancel_backorder)
        done_moves = (self | result).filtered(lambda move: move.state == "done")
        lots = done_moves.lot_ids
        if lots:
            devices = self.env["pinout.device"].search(
                [("final_lot_id", "in", lots.ids)]
            )
            devices._sync_state_from_stock_moves()
            bundles = devices.bundle_id
            bundles.invalidate_recordset(
                ["customer_id", "sale_order_id", "delivery_id"]
            )
            bundles._sync_sold_state_from_pickings(done_moves.picking_id)
        return result
