"""Conexión con la API para ERP de NexoPOS.

NexoPOS es la base de NexoTienda: el catálogo, el precio y el stock que ve el
comprador salen de ahí. En un comercio con Odoo, Odoo es la caja y la verdad
del stock (DEC-016), y se los escribe a NexoPOS.

Contrato leído del código de NexoPOS (backend/src/modules/erp.ts), base
`/api/erp/v1`, clave `npos_…` del comercio en `Authorization: Bearer`:
- GET  /productos?page=      → {productos[], page, pageSize: 500, total}
- POST /productos            → alta de hasta 5000, 202 {importacion_id}
- GET  /importaciones/:id    → en qué quedó el alta
- PUT  /precios              → hasta 1000, {aplicados, no_encontrados}
- PUT  /stock                → modo ajuste|absoluto, hasta 1000
Importes en centavos enteros. Los productos se identifican por EAN.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

_logger = logging.getLogger(__name__)

SERVICE = 'nexopos'
LABEL = 'NexoPOS'
DEFAULT_URL = 'https://nexopos.app/api/erp/v1'
PAGE_SIZE = 500
# `unidad` de NexoPOS → unidad de Odoo. Lo demás («BOTELLA», «CAJA»…) va en Unidades.
UNIDADES = {
    'unidad': 'uom.product_uom_unit',
    'kg': 'uom.product_uom_kgm',
    'litro': 'uom.product_uom_litre',
}
BATCH = 1000          # máximo de /precios y /stock
ALTA_BATCH = 5000     # máximo de POST /productos
# Sin respuesta no se sabe si NexoPOS aplicó el ajuste: reintentarlo podría
# sumarlo dos veces. Se marca y lo resuelve la comparación.
UNCERTAIN = ('timeout', 'network', 'invalid_response')


def centavos(value):
    return int(round((value or 0.0) * 100))


class NexoPosService(models.AbstractModel):
    _name = 'nexo.pos.service'
    _description = 'Servicio NexoPOS'

    # ------------------------------------------------------------------
    # Cliente
    # ------------------------------------------------------------------

    @api.model
    def _check_user(self):
        if not self.env.user.has_group('stock.group_stock_manager'):
            raise AccessError(_("Necesitás ser administrador de Inventario para usar la conexión con NexoPOS."))

    @api.model
    def _url(self):
        return self.env['ir.config_parameter'].sudo().get_str('nexo_pos.url') or DEFAULT_URL

    @api.model
    def _connector(self):
        module = self.env['ir.module.module'].sudo().search([('name', '=', 'nexo_pos')], limit=1)
        return f"odoo/{module.latest_version or '20.0'}"

    @api.model
    def _client(self, key):
        return NexoClient(self._url(), token=key, connector=self._connector(), service=LABEL,
                          on_call=self.env['nexo.log']._on_call(SERVICE))

    @api.model
    def _key(self, company=None):
        return self.env['nexo.token']._valid_token(SERVICE, company)

    @api.model
    def _call(self, method, path, params=None, json_body=None, key=None):
        key = key or self._key()
        if not key:
            raise NexoError('auth', service=LABEL,
                            message=_("No hay una clave de NexoPOS cargada. Cargala en Ajustes → NexoPOS."))
        return self._client(key).request(method, path, params=params, json_body=json_body)

    @api.model
    def _warehouse(self, company=None):
        company = company or self.env.company
        wh_id = int(self.env['ir.config_parameter'].sudo().get_str(f'nexo_pos.warehouse_id.{company.id}') or 0)
        warehouse = self.env['stock.warehouse'].browse(wh_id).exists() if wh_id else None
        return warehouse or self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)

    @api.model
    def _push_enabled(self, company=None):
        """Apagado, Odoo solo lee y compara: no escribe precio ni stock en
        NexoPOS. Viene apagado para que vincular un comercio real no le pise
        los precios con los de una base de prueba (DEC-017)."""
        company = company or self.env.company
        return self.env['ir.config_parameter'].sudo().get_str(f'nexo_pos.push.{company.id}') == '1'

    @api.model
    def _set_push(self, enabled, company=None):
        company = company or self.env.company
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_pos.push.{company.id}', '1' if enabled else '0')

    # ------------------------------------------------------------------
    # Clave
    # ------------------------------------------------------------------

    @api.model
    def get_status(self):
        record = self.env['nexo.token']._get(SERVICE)
        if not record:
            return {'connected': False, 'message': _("Sin clave")}
        linked = self.env['product.product'].search_count([('nexo_pos_id', '!=', False)])
        modo = _("manda precios y stock") if self._push_enabled() else _("solo lee")
        return {'connected': True,
                'message': _("Clave cargada · %(n)s productos vinculados · %(modo)s", n=linked, modo=modo)}

    @api.model
    def set_key(self, key):
        """Prueba la clave contra NexoPOS y, si anda, la guarda. La clave no se
        escribe en ningún log; en Odoo queda solo en `nexo.token`."""
        self._check_user()
        key = (key or '').strip()
        if not key:
            raise UserError(_("Pegá la clave que generaste en NexoPOS → Configuración → Conectar tu sistema."))
        try:
            data = self._call('GET', '/productos', params={'page': 1}, key=key)
        except NexoError as e:
            raise UserError(e.user_message()) from e
        if 'productos' not in data:
            raise UserError(NexoError('invalid_response', service=LABEL).user_message())
        self.env['nexo.token']._store(SERVICE, key, account_name=_("%s productos en NexoPOS", data.get('total')))
        return data.get('total')

    @api.model
    def test_connection(self):
        try:
            data = self._call('GET', '/productos', params={'page': 1})
        except NexoError as e:
            return {'ok': False, 'message': e.user_message()}
        return {'ok': True, 'message': _("Conectado. NexoPOS tiene %s productos de este comercio.", data.get('total'))}

    # ------------------------------------------------------------------
    # Productos
    # ------------------------------------------------------------------

    @api.model
    def _fetch_products(self):
        """Todo el catálogo del comercio en NexoPOS. No se cachea: el stock
        cambia con cada pedido de la tienda."""
        productos, page = [], 1
        while True:
            data = self._call('GET', '/productos', params={'page': page})
            batch = data.get('productos') or []
            productos += batch
            if len(batch) < (data.get('pageSize') or PAGE_SIZE) or len(productos) >= (data.get('total') or 0):
                return productos
            page += 1

    @api.model
    def _odoo_by_ean(self, eans):
        products = self.env['product.product'].with_context(active_test=False).search([('barcode', 'in', list(eans))])
        return {p.barcode: p for p in products}

    @api.model
    def link_products(self):
        """Vincula por EAN los productos de Odoo con los de NexoPOS. Solo lee
        en NexoPOS; en Odoo escribe el id. Devuelve qué quedó de cada lado."""
        self._check_user()
        try:
            remotos = self._fetch_products()
        except NexoError as e:
            raise UserError(e.user_message()) from e
        repetidos = {}
        for r in remotos:
            if r.get('ean'):
                repetidos[r['ean']] = repetidos.get(r['ean'], 0) + 1
        por_ean = {r['ean']: r for r in remotos if r.get('ean') and repetidos[r['ean']] == 1}
        odoo = self._odoo_by_ean(por_ean)
        vinculados = 0
        for ean, product in odoo.items():
            if product.nexo_pos_id != por_ean[ean]['id']:
                product.nexo_pos_id = por_ean[ean]['id']
                # Recién vinculado: el precio de Odoo todavía no se mandó.
                product.nexo_pos_price_dirty = True
            vinculados += 1
        # Vinculados antes que ya no están en NexoPOS.
        sueltos = self.env['product.product'].search([('nexo_pos_id', '!=', False),
                                                      ('nexo_pos_id', 'not in', [r['id'] for r in remotos])])
        sueltos.write({'nexo_pos_id': False, 'nexo_pos_price_dirty': False})
        solo_odoo = self.env['product.product'].search_count([
            ('nexo_pos_id', '=', False), ('sale_ok', '=', True), ('is_storable', '=', True), ('barcode', '!=', False)])
        return {
            'vinculados': vinculados,
            'solo_nexopos': len(por_ean) - len(odoo),
            'solo_odoo': solo_odoo,
            'ean_repetido': sorted(e for e, n in repetidos.items() if n > 1),
            'sin_ean': sum(1 for r in remotos if not r.get('ean')),
            'desvinculados': len(sueltos),
        }

    @api.model
    def _sale_price(self, product):
        """El precio de NexoPOS es el que paga el consumidor: con IVA. El de
        Odoo puede no incluirlo, según cómo esté cargado el impuesto."""
        company = self.env.company
        taxes = product.taxes_id.filtered(lambda t: t.company_id in company.parent_ids)
        price = product.lst_price
        if not taxes:
            return price
        return taxes.compute_all(price, currency=company.currency_id, product=product)['total_included']

    @api.model
    def create_in_nexopos(self, products):
        """Da de alta en NexoPOS productos de Odoo que no están. Es una escritura
        en el comercio real: el stock que se manda es el de Odoo y reemplaza."""
        self._check_user()
        products = products.filtered(lambda p: p.barcode and not p.nexo_pos_id)
        if not products:
            raise UserError(_("Elegí productos con código de barras que todavía no estén en NexoPOS."))
        if len(products) > ALTA_BATCH:
            raise UserError(_("Se pueden dar de alta hasta %s productos por vez.", ALTA_BATCH))
        warehouse = self._warehouse()
        filas = []
        for p in products:
            fila = {'ean': p.barcode, 'nombre': p.name,
                    'stock': max(p.with_context(warehouse_id=warehouse.id).qty_available, 0.0)}
            if p.lst_price > 0:
                fila['precio_centavos'] = centavos(self._sale_price(p))
            if p.standard_price > 0:
                fila['costo_centavos'] = centavos(p.standard_price)
            filas.append(fila)
        try:
            data = self._call('POST', '/productos', json_body={'productos': filas})
        except NexoError as e:
            raise UserError(e.user_message()) from e
        self.env['ir.config_parameter'].sudo().set_str(
            f'nexo_pos.importacion.{self.env.company.id}', str(data.get('importacion_id') or ''))
        return data

    @api.model
    def _price_without_tax(self, precio, taxes):
        """El precio de NexoPOS lleva IVA; `list_price` en Odoo, depende del
        impuesto. Solo se descuenta un impuesto porcentual que no esté incluido."""
        excluded = taxes.filtered(lambda t: not t.price_include)
        if not excluded:
            return precio, True
        if any(t.amount_type != 'percent' for t in excluded):
            return precio, False
        return precio / (1 + sum(excluded.mapped('amount')) / 100), True

    @api.model
    def _clean_name(self, nombre, unidad):
        """NexoPOS agrega la presentación al nombre («… — unidad»); en Odoo eso
        es la unidad de medida."""
        nombre = (nombre or '').strip()
        cola = f" — {unidad}" if unidad else None
        if cola and nombre.lower().endswith(cola.lower()):
            nombre = nombre[:-len(cola)].strip()
        return nombre

    @api.model
    def import_from_nexopos(self):
        """Trae a Odoo los productos de NexoPOS que no están: código de barras,
        nombre, precio, costo y stock inicial, ya vinculados. Solo lee en
        NexoPOS; el stock inicial no se le vuelve a mandar."""
        self._check_user()
        try:
            remotos = self._fetch_products()
        except NexoError as e:
            raise UserError(e.user_message()) from e
        self.link_products()
        Product = self.env['product.product'].with_context(active_test=False)
        vinculados = set(Product.search([('nexo_pos_id', '!=', False)]).mapped('nexo_pos_id'))
        repetidos = {}
        for r in remotos:
            if r.get('ean'):
                repetidos[r['ean']] = repetidos.get(r['ean'], 0) + 1
        existentes = set(Product.search([('barcode', 'in', list(repetidos))]).mapped('barcode'))

        company = self.env.company
        taxes = company.account_sale_tax_id
        warehouse = self._warehouse()
        Quant = self.env['stock.quant'].with_context(inventory_mode=True)
        result = {'creados': 0, 'ya_estaban': 0, 'ean_repetido': [], 'ean_en_odoo': [], 'precio_con_iva': 0,
                  'insumos': 0}
        quants = Quant.browse()
        for r in remotos:
            if r['id'] in vinculados:
                result['ya_estaban'] += 1
                continue
            ean = r.get('ean') or False
            if ean and repetidos[ean] > 1:
                if ean not in result['ean_repetido']:
                    result['ean_repetido'].append(ean)
                continue
            if ean and ean in existentes:
                # Hay un producto con ese código que no se pudo vincular (por
                # ejemplo, archivado o en otra compañía): no se duplica.
                result['ean_en_odoo'].append(ean)
                continue
            if r.get('es_insumo'):
                result['insumos'] += 1
            vals = {
                'name': self._clean_name(r.get('nombre'), r.get('unidad')) or ean or _("Producto de NexoPOS"),
                'barcode': ean,
                'is_storable': True,
                'sale_ok': not r.get('es_insumo'),
                'nexo_pos_id': r['id'],
                'taxes_id': [(6, 0, taxes.ids)],
                'company_id': False,
            }
            uom = UNIDADES.get((r.get('unidad') or '').lower())
            if uom:
                vals['uom_id'] = self.env.ref(uom).id
            if r.get('precio_centavos') is not None:
                precio, ok = self._price_without_tax(r['precio_centavos'] / 100, taxes)
                vals['list_price'] = precio
                result['precio_con_iva'] += 0 if ok else 1
            if r.get('costo_centavos') is not None:
                vals['standard_price'] = r['costo_centavos'] / 100
            product = Product.create(vals)
            product.nexo_pos_price_dirty = False
            result['creados'] += 1
            stock = float(r.get('stock') or 0)
            if stock > 0 and warehouse.lot_stock_id:
                quants |= Quant.create({'product_id': product.id, 'location_id': warehouse.lot_stock_id.id,
                                        'inventory_quantity': stock})
        if quants:
            # Es el stock que ya tiene NexoPOS: no se le vuelve a mandar.
            quants.with_context(nexo_pos_skip=True).action_apply_inventory()
        return result

    @api.model
    def _action_create_from_templates(self, templates):
        data = self.create_in_nexopos(templates.product_variant_ids)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'NexoPOS', 'sticky': True, 'type': 'success',
            'message': _("NexoPOS está dando de alta los productos (importación %s). Tarda unos minutos; "
                         "después tocá «Vincular productos» en Ajustes → NexoPOS.", data.get('importacion_id')),
        }}

    @api.model
    def import_status(self):
        imp = self.env['ir.config_parameter'].sudo().get_str(f'nexo_pos.importacion.{self.env.company.id}')
        if not imp:
            return None
        return self._call('GET', f'/importaciones/{imp}')

    # ------------------------------------------------------------------
    # Precios
    # ------------------------------------------------------------------

    @api.model
    def push_prices(self):
        """Manda los precios que cambiaron. Un precio es absoluto: reintentarlo
        no hace daño, así que se reintenta hasta que NexoPOS lo confirme."""
        products = self.env['product.product'].search([('nexo_pos_id', '!=', False), ('nexo_pos_price_dirty', '=', True)])
        sent = 0
        for batch in [products[i:i + BATCH] for i in range(0, len(products), BATCH)]:
            # Por id y no por EAN: en NexoPOS un mismo EAN puede estar en varios
            # productos (una presentación por producto), y por EAN no se aplica.
            lineas = [{'id': p.nexo_pos_id, 'precio_centavos': centavos(self._sale_price(p))}
                      for p in batch if p.lst_price > 0]
            if lineas:
                data = self._call('PUT', '/precios', json_body={'precios': lineas})
                faltan = {n.get('id') for n in data.get('no_encontrados') or []}
            else:
                faltan = set()
            ok = batch.filtered(lambda p: p.nexo_pos_id not in faltan)
            ok.write({'nexo_pos_price_dirty': False, 'nexo_pos_sync_at': fields.Datetime.now(), 'nexo_pos_error': False})
            for p in batch - ok:
                _logger.info("NexoPOS no encontró el producto %s al mandar el precio", p.nexo_pos_id)
            (batch - ok).write({'nexo_pos_price_dirty': False,
                                'nexo_pos_error': _("NexoPOS ya no tiene este producto: volvé a vincular")})
            sent += len(ok)
        return sent

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _companies_with_key(self):
        tokens = self.env['nexo.token'].sudo().search([('service', '=', SERVICE)])
        return tokens.company_id

    @api.model
    def _cron_push(self):
        """Cada minuto: precios y stock pendientes, compañía por compañía."""
        for company in self._companies_with_key():
            if not self._push_enabled(company):
                continue
            try:
                with self.env.cr.savepoint():
                    self.with_company(company).push_prices()
                    self.env['nexo.pos.stock.queue'].with_company(company)._send_pending()
            except NexoError as e:
                _logger.warning("NexoPOS (%s): no se pudo sincronizar: %s", company.name, e.kind)

    @api.model
    def _cron_compare(self):
        for company in self._companies_with_key():
            try:
                with self.env.cr.savepoint():
                    self.env['nexo.pos.stock.diff'].with_company(company)._compare()
            except NexoError as e:
                _logger.warning("NexoPOS (%s): no se pudo comparar el stock: %s", company.name, e.kind)
