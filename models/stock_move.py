from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        done_moves = super()._action_done(cancel_backorder=cancel_backorder)
        lots = (self | done_moves).filtered(lambda move: move.state == "done").lot_ids
        if lots:
            self.env["pinout.device"].search(
                [("final_lot_id", "in", lots.ids)]
            )._sync_state_from_stock_moves()
        return done_moves
