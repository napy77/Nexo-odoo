# Arquitectura — Nexo-odoo (rama 20.0)

## Módulos
```
nexo_core   (depende de base)
  lib/client.py      NexoClient: HTTPS, cabeceras, traducción de errores (NexoError). Python puro.
  nexo.token         sesión por (servicio, compañía): token y vencimiento. Solo administradores.
  nexo.log           registro de llamadas sin secretos + «Descargar diagnóstico».

nexo_b2b    (depende de nexo_core, purchase)
  nexo.b2b.service   login, test_connection, search_catalog, add_product (AbstractModel)
  nexo.b2b.tax.map   alícuota → impuesto de venta y de compra, por compañía (DEC-006)
  nexo.b2b.category.map  taxonomía NexoB2B → product.category, se arma a medida que se usa (DEC-007)
  campos             product.template.nexo_b2b_id / nexo_marca
                     product.supplierinfo.nexo_presentacion_* / nexo_factor / nexo_ean_propio
                     res.partner.nexo_mayorista_id
  static/src/catalog acción de cliente OWL «nexo_b2b.catalog»
```

## Flujo
1. El navegador llama a `nexo.b2b.service` con `orm.call`. **El token nunca sale del servidor de Odoo.**
2. `nexo.b2b.service` toma el token de `nexo.token` (con `sudo`), arma un `NexoClient` y llama a NexoB2B.
3. Cada llamada se guarda en `nexo.log` con un cursor propio, así que queda registrada aunque la operación falle.
4. «Agregar» vuelve a leer el producto desde NexoB2B. El precio y los datos nunca vienen del navegador.

## Representación en Odoo (DEC-008)
- Producto en Unidades. Cada presentación con `factor` > 1 se representa con la unidad «x<factor> Unidades» (`uom.uom` relativa a Unidades), que se comparte entre productos.
- Hay una `product.supplierinfo` por presentación del mayorista, con `uom_id` = esa unidad y `price` = el precio de la presentación tal cual.
- El `ean_propio` va a `product.uom` (producto + unidad + código de barras).

## Permisos
- Catálogo y «Agregar»: `purchase.group_purchase_user`. Los productos y proveedores se crean con los permisos del usuario. Las unidades, categorías y códigos de empaque se crean con `sudo`, de forma acotada.
- Iniciar sesión, Ajustes y registro: `base.group_system`.
