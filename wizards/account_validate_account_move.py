from odoo import models


class ValidateAccountMove(models.TransientModel):
    _inherit = "validate.account.move"

    def validate_move(self):
        return super(
            ValidateAccountMove, self.with_context(from_validate_move_wiz=True)
        ).validate_move()
