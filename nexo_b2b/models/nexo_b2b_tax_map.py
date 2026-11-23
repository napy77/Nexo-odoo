import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.nexo_core.lib.client import NexoError

_logger = logging.getLogger(__name__)

# Las que se proponen aunque NexoB2B no conteste: las alícuotas de IVA vigentes.
BASE_ALICUOTAS = (0.0, 10.5, 21.0, 27.0)


class NexoB2bTaxMap(models.Model):
    """Alícuota de NexoB2B → impuestos de Odoo (DEC-006).

    NexoB2B manda `alicuota_iva` como número, sin id de impuesto, así que la
    clave es el número. Nunca se crean impuestos: solo se eligen los que
    existen en la compañía.
    """
    _name = 'nexo.b2b.tax.map'
    _description = 'Mapeo de alícuotas de NexoB2B'
    _order = 'company_id, alicuota'

    company_id = fields.Many2one('res.company', required=True, ondelete='cascade',
                                 default=lambda self: self.env.company)
    alicuota = fields.Float('Alícuota NexoB2B (%)', required=True, digits=(5, 2))
    sale_tax_id = fields.Many2one(
        'account.tax', 'Impuesto de venta', ondelete='set null', check_company=True,
        domain="[('type_tax_use', '=', 'sale'), ('company_id', '=', company_id)]")
    purchase_tax_id = fields.Many2one(
        'account.tax', 'Impuesto de compra', ondelete='set null', check_company=True,
        domain="[('type_tax_use', '=', 'purchase'), ('company_id', '=', company_id)]")
    # Si se borra el impuesto (por ejemplo, al cambiar la localización
    # fiscal), la fila queda sin él y se vuelve a preguntar: `without_tax`
    # distingue «el usuario eligió sin impuestos» de «el impuesto ya no existe».
    without_tax = fields.Boolean(
        'Sin impuestos', help="El usuario confirmó agregar los productos de esta alícuota sin impuestos.")
    confirmed = fields.Boolean(
        'Confirmado',
        help="Mientras no esté confirmado es solo una propuesta: al agregar un producto con "
             "esta alícuota se pide revisarlo.")

    _company_alicuota_uniq = models.Constraint(
        'unique (company_id, alicuota)',
        'Esa alícuota ya está mapeada en esta compañía.',
    )

    @api.model
    def _candidate(self, alicuota, use, company):
        """El impuesto con ese porcentaje, si hay exactamente uno."""
        taxes = self.env['account.tax'].search([
            ('company_id', '=', company.id),
            ('type_tax_use', '=', use),
            ('amount_type', '=', 'percent'),
            ('amount', '=', alicuota),
        ])
        return taxes if len(taxes) == 1 else taxes.browse()

    @api.model
    def _known_alicuotas(self):
        alicuotas = set(BASE_ALICUOTAS)
        try:
            data = self.env['nexo.b2b.service']._call('GET', '/store/taxonomia', auth=False)
            for item in data.get('alicuotas') or []:
                # `porcentaje`: modules/taxonomia/models/alicuota_iva.ts de NexoB2B.
                value = item.get('porcentaje') if isinstance(item, dict) else None
                if value is not None:
                    alicuotas.add(float(value))
        except NexoError as e:
            _logger.info("No se pudo leer la taxonomía de NexoB2B: %s", e.kind)
        return alicuotas

    @api.model
    def _propose(self, company=None):
        """Agrega las alícuotas que falten, proponiendo el impuesto del mismo
        porcentaje cuando hay uno solo. No toca las filas existentes."""
        company = company or self.env.company
        existing = set(self.search([('company_id', '=', company.id)]).mapped('alicuota'))
        for alicuota in sorted(self._known_alicuotas() - existing):
            self.create({
                'company_id': company.id,
                'alicuota': alicuota,
                'sale_tax_id': self._candidate(alicuota, 'sale', company).id,
                'purchase_tax_id': self._candidate(alicuota, 'purchase', company).id,
            })

    @api.constrains('sale_tax_id', 'purchase_tax_id')
    def _check_tax_type(self):
        for row in self:
            if row.sale_tax_id and row.sale_tax_id.type_tax_use != 'sale':
                raise ValidationError(_("«%s» no es un impuesto de venta.", row.sale_tax_id.display_name))
            if row.purchase_tax_id and row.purchase_tax_id.type_tax_use != 'purchase':
                raise ValidationError(_("«%s» no es un impuesto de compra.", row.purchase_tax_id.display_name))

    def _sale_tax(self):
        """El impuesto de venta, solo si de verdad es de venta (puede haber
        filas viejas, anteriores a la validación, con el tipo cambiado)."""
        return self.sale_tax_id.filtered(lambda t: t.type_tax_use == 'sale')

    def _purchase_tax(self):
        return self.purchase_tax_id.filtered(lambda t: t.type_tax_use == 'purchase')

    def _is_usable(self):
        """Confirmada, sin impuestos de tipo equivocado, y con impuestos o
        confirmada sin impuestos a propósito."""
        if not self:
            return False
        self.ensure_one()
        wrong = (self.sale_tax_id and not self._sale_tax()) or (self.purchase_tax_id and not self._purchase_tax())
        return bool(self.confirmed and not wrong and (self.sale_tax_id or self.purchase_tax_id or self.without_tax))

    @api.model
    def _for(self, alicuota, company=None):
        company = company or self.env.company
        return self.search([('company_id', '=', company.id), ('alicuota', '=', float(alicuota))], limit=1)
