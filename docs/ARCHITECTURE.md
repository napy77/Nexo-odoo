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

nexo_pos    (depende de nexo_core, stock, account)
  nexo.pos.service   clave, vincular por EAN, alta, precios (AbstractModel)
  nexo.pos.stock.queue  ajustes de stock pendientes / enviados / sin respuesta (DEC-019)
  nexo.pos.stock.diff   diferencias Odoo ↔ NexoPOS, con las dos formas de resolverlas
  stock.move._action_done  encola lo que entra o sale del depósito elegido
  campos             product.product.nexo_pos_id / nexo_pos_price_dirty
  crons              cada minuto: precios y stock; cada día: comparar

nexo_tienda (depende de nexo_pos, sale_stock, pos_sale_stock)
  nexo.tienda.order  espejo del pedido de NexoTienda en NexoPOS, con sus pasos (DEC-021)
  sale.order         un pedido de venta por pedido de tienda, equipo «NexoTienda», que cobra la caja
  stock.move         lo que sale por un pedido de tienda no se manda a NexoPOS
  pos.order / stock.picking  cobrado o entregado en Odoo → «entregado» en NexoPOS
  cron               cada minuto: avisar entregas y traer pedidos

nexo_clubpay (depende de nexo_core, point_of_sale, sale; payment llega por sale) — DEC-023 y DEC-024
  nexo.clubpay.charge  un cobro de ClubPay (QR), con socio, descuento y a qué venta quedó
  pos.payment.method   terminal «clubpay»; pos.payment comprueba el cupón al recibir la venta
  static/src/pos       PaymentClubPay + ventana del QR (como NexoPOS) + ticket del QR
  sale.order / portal  botón «Descuento ClubPay» y QR a demanda en el portal
  account.move._post   el cupón entra como pago parcial (diario «Cupones ClubPay»)
  — Mercado Pago a través de ClubPay (DEC-024) —
  nexo.clubpay.payment un cobro de Mercado Pago (QR de mostrador o link), con a qué venta quedó
  payment.provider     código «clubpay»: «Mercado Pago (ClubPay)» en el portal
  payment.transaction  link de ClubPay, estados y devoluciones con el flujo estándar de Odoo
  controllers/payment  vuelta de Mercado Pago (/payment/clubpay/return) y página del simulador
  pos.payment.method   terminal «clubpay_mp»; pos.payment comprueba el pago al recibir la venta
  static/src/pos       PaymentClubPayMp + ventana del QR; «Cobrar el resto con Mercado Pago»
  cron                 cada 2 min: estado de los cobros y aviso de pagos sin venta
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
