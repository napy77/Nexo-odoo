from odoo import _, fields, models
from odoo.exceptions import UserError


class NexoTiendaStep(models.TransientModel):
    """Aceptar y cancelar piden un dato que ve el comprador."""
    _name = 'nexo.tienda.step'
    _description = 'Paso de un pedido de NexoTienda'

    order_id = fields.Many2one('nexo.tienda.order', required=True, ondelete='cascade')
    step = fields.Selection([('aceptar', 'Aceptar'), ('cancelar', 'Cancelar')], required=True)
    revision_texto = fields.Char(related='order_id.revision_texto')
    listo_para = fields.Char('¿Para cuándo lo tienen?', default=lambda self: _("30 minutos"),
                             help="Lo ve el comprador en NexoTienda. En palabras: «30 minutos», «mañana a las 10».")
    motivo = fields.Text('Motivo', help="Lo ve el comprador. Hasta 400 letras.")

    def action_confirm(self):
        self.ensure_one()
        if self.step == 'aceptar':
            if not (self.listo_para or '').strip():
                raise UserError(_("Escribí para cuándo lo tienen: lo ve el comprador."))
            return self.order_id._step_from_ui('aceptar', {'listo_para': self.listo_para.strip()})
        motivo = (self.motivo or '').strip()
        if not motivo:
            raise UserError(_("Escribí el motivo: lo ve el comprador."))
        if len(motivo) > 400:
            raise UserError(_("El motivo puede tener hasta 400 letras."))
        return self.order_id._step_from_ui('cancelar', {'motivo': motivo})
