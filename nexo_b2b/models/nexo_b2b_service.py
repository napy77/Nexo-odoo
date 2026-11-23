"""Conexión con NexoB2B: login, búsqueda en el catálogo maestro y «Agregar».

Todo pasa por el servidor de Odoo: el token nunca llega al navegador.
Contrato verificado contra el código de NexoB2B (apps/backend/src/api/store):
- POST /store/comercios/auth → {token, comercio, usuario}
- GET  /store/comercios/me   → valida el token
- GET  /store/productos      → q, page, pageSize (máx. 100), con_stock=1, mayorista_id
"""
import base64
import logging
from urllib.parse import urljoin

import requests

from odoo import _, api, fields, models, Command
from odoo.exceptions import AccessError, UserError

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

_logger = logging.getLogger(__name__)

SERVICE = 'nexob2b'
PAGE_SIZE = 20
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_TIMEOUT = 10

# unidad_base de NexoB2B → unidad de Odoo. Lo que no esté acá se agrega en
# Unidades con un aviso (PENDIENTE DE VALIDAR: qué valores usa el maestro).
UNIDADES = {
    'unidad': 'uom.product_uom_unit',
    'kg': 'uom.product_uom_kgm',
    'litro': 'uom.product_uom_litre',
}


class NexoB2bService(models.AbstractModel):
    _name = 'nexo.b2b.service'
    _description = 'Servicio NexoB2B'

    # ------------------------------------------------------------------
    # Cliente
    # ------------------------------------------------------------------

    @api.model
    def _check_user(self):
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise AccessError(_("Necesitás permisos de Compras para usar el catálogo de NexoB2B."))

    @api.model
    def _config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return ICP.get_str('nexo_b2b.url'), ICP.get_str('nexo_b2b.publishable_key')

    @api.model
    def _connector(self):
        module = self.env['ir.module.module'].sudo().search([('name', '=', 'nexo_b2b')], limit=1)
        return f"odoo/{module.latest_version or '20.0'}"

    @api.model
    def _client(self, token=None):
        url, key = self._config()
        if not url or not key:
            raise UserError(_("Falta configurar la URL y la clave publicable de NexoB2B en Ajustes."))
        return NexoClient(url, key, token=token, connector=self._connector(),
                          on_call=self.env['nexo.log']._on_call(SERVICE))

    @api.model
    def _call(self, method, path, params=None, json_body=None, auth=True):
        token = None
        if auth:
            token = self.env['nexo.token']._valid_token(SERVICE)
            if not token:
                raise NexoError('auth', message=_("No hay una sesión activa con NexoB2B. Iniciá sesión."))
        try:
            return self._client(token).request(method, path, params=params, json_body=json_body, auth=auth)
        except NexoError as e:
            if e.kind == 'auth' and auth:
                # Token vencido o revocado: se descarta para pedir login de nuevo.
                self.env['nexo.token']._clear(SERVICE)
            raise

    # ------------------------------------------------------------------
    # Sesión
    # ------------------------------------------------------------------

    @api.model
    def get_status(self):
        url, key = self._config()
        record = self.env['nexo.token']._get(SERVICE)
        token = self.env['nexo.token']._valid_token(SERVICE)
        if not url or not key:
            message = _("Sin configurar")
        elif not token:
            message = _("Sesión vencida: iniciá sesión de nuevo") if record else _("Sin sesión")
        else:
            who = record.user_name or _("dueño")
            message = _("Conectado como %(comercio)s (%(who)s)", comercio=record.account_name, who=who)
        return {
            'configured': bool(url and key),
            'connected': bool(token),
            'comercio': record.account_name if token else None,
            'usuario': record.user_name if token else None,
            'expires_at': fields.Datetime.to_string(record.expires_at) if token and record.expires_at else None,
            'message': message,
        }

    @api.model
    def login(self, email, password):
        """Inicia sesión y guarda solo el token. La contraseña no se guarda ni
        se escribe en el log (el cliente no registra cuerpos)."""
        self._check_user()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_("Solo un administrador puede conectar la compañía con NexoB2B."))
        try:
            data = self._client().request('POST', '/store/comercios/auth',
                                          json_body={'email': email, 'password': password}, auth=False)
        except NexoError as e:
            if e.kind == 'auth':
                return {'ok': False, 'message': _("Email o contraseña incorrectos.")}
            return {'ok': False, 'message': e.user_message()}
        token = data.get('token')
        comercio = data.get('comercio') or {}
        usuario = data.get('usuario')
        if not token:
            return {'ok': False, 'message': NexoError('invalid_response').user_message()}
        # /store/productos no exige permisos, pero el desafío 3 (órdenes) sí va
        # a exigir «comprar»: se rechaza desde ahora para no dar una conexión
        # que después no sirve. usuario = None es el dueño (tiene todos).
        if usuario is not None and 'comprar' not in (usuario.get('permisos') or []):
            return {'ok': False, 'message': _(
                "El usuario %(email)s no tiene el permiso «comprar» en NexoB2B. "
                "Pedile al dueño del comercio que se lo habilite.", email=email)}
        self.env['nexo.token']._store(
            SERVICE, token,
            account_id=comercio.get('id'),
            account_name=comercio.get('nombre'),
            user_name=(usuario or {}).get('nombre') or False,
            login_email=email,
        )
        return {'ok': True, 'message': self.get_status()['message'], 'status': self.get_status()}

    @api.model
    def test_connection(self):
        try:
            data = self._call('GET', '/store/comercios/me')
        except NexoError as e:
            return {'ok': False, 'message': e.user_message()}
        except UserError as e:
            return {'ok': False, 'message': str(e)}
        nombre = (data.get('comercio') or {}).get('nombre')
        return {'ok': True, 'message': _("Conexión OK con NexoB2B: %s", nombre)}

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------

    @api.model
    def _image_url(self, path):
        if not path:
            return None
        url, _key = self._config()
        # Ruta relativa (/uploads/…) o URL externa: urljoin resuelve las dos.
        return urljoin(url.rstrip('/') + '/', path)

    @api.model
    def search_catalog(self, query, page=1, con_stock=True):
        self._check_user()
        params = {'page': max(1, int(page or 1)), 'pageSize': PAGE_SIZE}
        if query and query.strip():
            params['q'] = query.strip()
        if con_stock:
            params['con_stock'] = '1'
        try:
            data = self._call('GET', '/store/productos', params=params)
        except NexoError as e:
            return {'ok': False, 'kind': e.kind, 'message': e.user_message()}
        productos = data.get('productos') or []
        return {
            'ok': True,
            'total': data.get('total', 0),
            'page': data.get('page', 1),
            'totalPages': data.get('totalPages', 1),
            'productos': [self._for_display(p) for p in productos],
        }

    @api.model
    def _for_display(self, producto):
        """Lo que muestra la pantalla, más qué cosas ya están en Odoo."""
        template = self._find_template(producto, active_test=True)
        existing = set(template.seller_ids.mapped('nexo_presentacion_id')) if template else set()
        return {
            'id': producto.get('id'),
            'ean': producto.get('ean'),
            'nombre': producto.get('nombre'),
            'marca': producto.get('marca'),
            'imagen_url': self._image_url(producto.get('imagen_url')),
            'alicuota_iva': producto.get('alicuota_iva'),
            'taxonomia': ' / '.join(filter(None, [producto.get('pasillo_nombre'), producto.get('rubro_nombre'),
                                                  producto.get('subrubro_nombre')])),
            'odoo_product_id': template.id if template else None,
            'mayoristas': [{
                'id': m.get('mayorista_id'),
                'nombre': m.get('mayorista_nombre'),
                'muestra_precio': m.get('muestra_precio'),
                'agregado': bool(m.get('presentaciones')) and all(
                    pr.get('id') in existing for pr in m.get('presentaciones') or []),
                'presentaciones': [{
                    'id': pr.get('id'),
                    'nombre': pr.get('nombre'),
                    'factor': pr.get('factor'),
                    'precio_mostrar': pr.get('precio_mostrar'),
                    'precio_lista_mostrar': pr.get('precio_lista_mostrar'),
                    'stock': pr.get('stock'),
                } for pr in m.get('presentaciones') or []],
            } for m in producto.get('mayoristas') or []],
        }

    # ------------------------------------------------------------------
    # Agregar
    # ------------------------------------------------------------------

    @api.model
    def _fetch_product(self, producto_id, ean, mayorista_id):
        """Vuelve a leer el producto de NexoB2B: el navegador no es fuente de
        datos (precio ni nada). No hay GET por id; se busca por EAN."""
        data = self._call('GET', '/store/productos', params={
            'q': ean, 'mayorista_id': mayorista_id, 'pageSize': 100,
        })
        for producto in data.get('productos') or []:
            if producto.get('id') == producto_id:
                return producto
        return None

    @api.model
    def _find_template(self, producto, active_test=False):
        Template = self.env['product.template'].with_context(active_test=active_test)
        template = Template.search([('nexo_b2b_id', '=', producto.get('id'))], limit=1)
        if not template and producto.get('ean'):
            template = Template.search([('barcode', '=', producto['ean'])], limit=1)
        return template

    @api.model
    def _resolve_tax(self, producto, choices):
        """Devuelve (mapeo, pedido) — pedido es lo que hay que preguntarle al
        usuario, o None."""
        TaxMap = self.env['nexo.b2b.tax.map']
        alicuota = producto.get('alicuota_iva')
        if alicuota is None:
            return TaxMap.browse(), None
        alicuota = float(alicuota)
        tax_map = TaxMap._for(alicuota)
        if tax_map.confirmed:
            return tax_map, None
        tax_choice = choices.get('tax')
        if tax_choice:
            vals = {
                'sale_tax_id': tax_choice.get('sale_tax_id') or False,
                'purchase_tax_id': tax_choice.get('purchase_tax_id') or False,
                'confirmed': True,
            }
            if not vals['sale_tax_id'] and not vals['purchase_tax_id'] and not tax_choice.get('without_tax'):
                raise UserError(_("Elegí los impuestos o confirmá que querés agregarlo sin impuestos."))
            if tax_map:
                tax_map.write(vals)
            else:
                tax_map = TaxMap.create({'alicuota': alicuota, **vals})
            return tax_map, None
        company = self.env.company
        Tax = self.env['account.tax']
        return tax_map, {
            'alicuota': alicuota,
            'sale_tax_id': (tax_map.sale_tax_id or TaxMap._candidate(alicuota, 'sale', company)).id or False,
            'purchase_tax_id': (tax_map.purchase_tax_id or TaxMap._candidate(alicuota, 'purchase', company)).id or False,
            'sale_taxes': [{'id': t.id, 'name': t.display_name} for t in Tax.search(
                [('company_id', '=', company.id), ('type_tax_use', '=', 'sale')])],
            'purchase_taxes': [{'id': t.id, 'name': t.display_name} for t in Tax.search(
                [('company_id', '=', company.id), ('type_tax_use', '=', 'purchase')])],
        }

    @api.model
    def _resolve_category(self, producto, choices):
        CategoryMap = self.env['nexo.b2b.category.map']
        taxonomy = CategoryMap._taxonomy(producto)
        if not taxonomy:
            return self.env['product.category'], None
        category_map = CategoryMap._for(producto)
        if category_map:
            return category_map.categ_id, None
        choice = choices.get('category')
        if choice:
            if choice.get('mode') == 'existing' and choice.get('categ_id'):
                categ = self.env['product.category'].browse(int(choice['categ_id'])).exists()
                if not categ:
                    raise UserError(_("La categoría elegida ya no existe."))
            elif choice.get('mode') == 'create':
                categ = CategoryMap.sudo()._create_tree(taxonomy[2])
            else:
                raise UserError(_("Elegí crear la categoría o usar una existente."))
            CategoryMap.sudo()._remember(producto, categ)
            return categ, None
        return self.env['product.category'], {
            'ruta': ' / '.join(['NexoB2B', *taxonomy[2]]),
            'categories': [{'id': c.id, 'name': c.display_name}
                           for c in self.env['product.category'].search([], limit=500)],
        }

    @api.model
    def _uom_for_factor(self, base_uom, factor):
        """La unidad «x<factor> <base>», compartida entre productos (DEC-008)."""
        if not factor or float(factor) == 1.0:
            return base_uom
        factor = float(factor)
        Uom = self.env['uom.uom'].sudo().with_context(active_test=False)
        uom = Uom.search([('relative_uom_id', '=', base_uom.id), ('relative_factor', '=', factor)], limit=1)
        if uom:
            if not uom.active:
                uom.active = True
            return uom
        label = f"{factor:g}"
        return Uom.create({
            'name': f"x{label} {base_uom.name}",
            'relative_uom_id': base_uom.id,
            'relative_factor': factor,
        })

    @api.model
    def _download_image(self, url):
        if not url:
            return None
        try:
            response = requests.get(url, timeout=IMAGE_TIMEOUT, stream=True)
            if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('image/'):
                return None
            content = response.raw.read(IMAGE_MAX_BYTES + 1, decode_content=True)
            if len(content) > IMAGE_MAX_BYTES:
                return None
            return base64.b64encode(content)
        except requests.exceptions.RequestException:
            return None

    @api.model
    def _vendor(self, mayorista):
        Partner = self.env['res.partner'].with_context(active_test=False)
        partner = Partner.search([('nexo_mayorista_id', '=', mayorista['mayorista_id'])], limit=1)
        if partner:
            return partner, False
        return Partner.create({
            'name': mayorista.get('mayorista_nombre') or mayorista['mayorista_id'],
            'is_company': True,
            'supplier_rank': 1,
            'nexo_mayorista_id': mayorista['mayorista_id'],
        }), True

    @api.model
    def add_product(self, producto_id, ean, mayorista_id, choices=None):
        """Agrega el producto con las presentaciones de un mayorista.

        Si falta mapear la alícuota o la categoría devuelve
        {'status': 'needs_input', ...} sin escribir nada; la pantalla pregunta y
        vuelve a llamar con `choices`.
        """
        self._check_user()
        choices = choices or {}
        warnings = []
        try:
            producto = self._fetch_product(producto_id, ean, mayorista_id)
        except NexoError as e:
            return {'status': 'error', 'message': e.user_message()}
        if not producto:
            return {'status': 'error', 'message': _(
                "NexoB2B ya no devuelve este producto para ese mayorista. Volvé a buscar.")}
        mayorista = next((m for m in producto.get('mayoristas') or []
                          if m.get('mayorista_id') == mayorista_id), None)
        if not mayorista:
            return {'status': 'error', 'message': _("Ese mayorista ya no ofrece este producto.")}

        template = self._find_template(producto)
        if template and not template.active:
            return {'status': 'error', 'message': _(
                "El producto %(name)s existe en Odoo pero está archivado. Desarchivalo y volvé a agregarlo.",
                name=template.display_name)}

        created = not template
        if created:
            tax_map, tax_needed = self._resolve_tax(producto, choices)
            categ, categ_needed = self._resolve_category(producto, choices)
            if tax_needed or categ_needed:
                return {'status': 'needs_input', 'tax': tax_needed, 'category': categ_needed,
                        'producto': producto.get('nombre')}
            if tax_map and not tax_map.sale_tax_id and not tax_map.purchase_tax_id:
                warnings.append(_("Se agregó sin impuestos (alícuota %s%% sin mapear).", tax_map.alicuota))

            unidad = (producto.get('unidad_base') or 'unidad').lower()
            uom = self.env.ref(UNIDADES.get(unidad, 'uom.product_uom_unit'))
            if unidad not in UNIDADES:
                warnings.append(_("Unidad «%s» de NexoB2B no reconocida: se cargó en Unidades.", unidad))

            vals = {
                'name': producto.get('nombre') or producto.get('ean'),
                'barcode': producto.get('ean') or False,
                'nexo_b2b_id': producto.get('id'),
                'nexo_marca': producto.get('marca') or False,
                'type': 'consu',
                'is_storable': True,
                'purchase_ok': True,
                'sale_ok': True,
                'uom_id': uom.id,
                'taxes_id': [Command.set(tax_map.sale_tax_id.ids)],
                'supplier_taxes_id': [Command.set(tax_map.purchase_tax_id.ids)],
            }
            if categ:
                vals['categ_id'] = categ.id
            image = self._download_image(self._image_url(producto.get('imagen_url')))
            if image:
                vals['image_1920'] = image
            elif producto.get('imagen_url'):
                warnings.append(_("No se pudo descargar la foto de NexoB2B."))
            template = self.env['product.template'].create(vals)
        elif not template.nexo_b2b_id:
            # El producto ya era del comercio (mismo EAN): se vincula, pero no
            # se pisa nada de lo suyo. El ERP es el dueño del dato.
            template.nexo_b2b_id = producto.get('id')

        partner, partner_created = self._vendor(mayorista)
        lines_created, lines_updated = self._sync_presentations(template, partner, mayorista, warnings)
        if not self.env.user.has_group('uom.group_uom'):
            warnings.append(_("Activá «Unidades de medida y empaques» en Ajustes de Inventario "
                              "para ver las presentaciones en las compras."))
        return {
            'status': 'ok',
            'product_id': template.id,
            'product_created': created,
            'vendor_created': partner_created,
            'lines_created': lines_created,
            'lines_updated': lines_updated,
            'warnings': warnings,
        }

    @api.model
    def _sync_presentations(self, template, partner, mayorista, warnings):
        Supplierinfo = self.env['product.supplierinfo']
        variant = template.product_variant_id
        created = updated = 0
        for pr in mayorista.get('presentaciones') or []:
            if pr.get('precio') is None:
                warnings.append(_("La presentación %s no tiene precio: no se agregó.", pr.get('nombre')))
                continue
            factor = float(pr.get('factor') or 1)
            uom = self._uom_for_factor(template.uom_id, factor)
            vals = {
                'partner_id': partner.id,
                'product_tmpl_id': template.id,
                'uom_id': uom.id,
                # Precio de la presentación tal cual, neto y con la lista del
                # comercio aplicada (DEC-004, DEC-005).
                'price': float(pr['precio']),
                'min_qty': float(pr.get('cantidad_minima') or 1),
                'product_code': pr.get('sku') or False,
                'product_name': pr.get('nombre') or False,
                'nexo_presentacion_id': pr.get('id'),
                'nexo_presentacion_maestro_id': pr.get('presentacion_id'),
                'nexo_presentacion_nombre': pr.get('nombre'),
                'nexo_factor': factor,
                'nexo_ean_propio': pr.get('ean_propio') or False,
                'company_id': self.env.company.id,
            }
            line = Supplierinfo.search([('product_tmpl_id', '=', template.id),
                                        ('nexo_presentacion_id', '=', pr.get('id'))], limit=1)
            if line:
                line.write(vals)
                updated += 1
            else:
                Supplierinfo.create(vals)
                created += 1
            self._packaging_barcode(variant, uom, pr.get('ean_propio'), template, warnings)
        return created, updated

    @api.model
    def _packaging_barcode(self, variant, uom, barcode, template, warnings):
        """Guarda el EAN de la caja o del pack en product.uom, para escanearlo."""
        if not barcode or barcode == template.barcode or uom == template.uom_id:
            return
        ProductUom = self.env['product.uom'].sudo()
        existing = ProductUom.search([('barcode', '=', barcode)], limit=1)
        if existing:
            if existing.product_id != variant or existing.uom_id != uom:
                warnings.append(_("El código %s ya está usado en otro producto o empaque: no se cargó.", barcode))
            return
        if self.env['product.product'].with_context(active_test=False).search_count([('barcode', '=', barcode)]):
            warnings.append(_("El código %s ya es el de otro producto: no se cargó.", barcode))
            return
        ProductUom.create({'product_id': variant.id, 'uom_id': uom.id, 'barcode': barcode})
