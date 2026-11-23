# Propuesta: caja en Odoo, pedidos de NexoTienda y stock compartido

Estado: pasos 1 y 2 **IMPLEMENTADOS** en `nexo_pos`, sin probar contra la API real (23/11/2026). El paso 3 sigue **PROPUESTO**.

## La decisión de German
Un comercio que usa Odoo **cobra en el Punto de Venta de Odoo**, y ahí también atiende los pedidos de NexoTienda (DEC-016).

## Lo que hay hoy (leído del código de NexoPOS, 23/11/2026)
- **NexoTienda solo habla con NexoPOS.** Le pide catálogo, precios y stock, y le manda los pedidos (`POST /v1/orders`). NexoPOS es el dueño declarado del pedido de tienda y del stock de mostrador (`nexo-ecosystem/architecture/DATA-OWNERSHIP.md`).
- **El pedido de tienda en NexoPOS:**
  - Estados: `recibido → aceptado → listo → en_camino → entregado`, más `cancelado`. `en_camino` es solo para reparto.
  - El stock se descuenta **al aceptar**, y vuelve si se cancela después (`backend/src/modules/pedidos.ts`).
  - Al entregarse queda como nota de venta.
- **API para ERP de NexoPOS** (`/api/erp/v1`, clave `npos_…` por comercio, `backend/src/modules/erp.ts`):
  - `GET /productos`, con stock y paginado de a 500.
  - `POST /productos`: alta masiva, en segundo plano.
  - `PUT /precios`.
  - `PUT /stock`, en modo `ajuste` o `absoluto`.
  - `GET /ventas?desde=`: trae los pedidos de tienda **solo cuando están entregados**.
  - **No tiene pedidos pendientes ni avisos (webhooks).**
- Una escritura de stock desde el ERP **no** se le avisa a NexoB2B: el aviso solo sale de las ventas del mostrador de NexoPOS.
- Odoo 20 trae `pos_sale`: la caja abre un pedido de venta y lo cobra («Settle the order»).

## Cómo quedaría
NexoPOS sigue siendo la base de NexoTienda: catálogo, precio y stock que ve el comprador, y donde caen los pedidos. Odoo es la caja y la verdad del stock de ese comercio. NexoTienda no cambia.

| Qué | De → a | Cómo | Estado |
|---|---|---|---|
| Alta de productos en la tienda | Odoo → NexoPOS | `POST /productos` con EAN, precio y stock | Se puede hacer hoy |
| Precio al público | Odoo → NexoPOS | `PUT /precios` cuando cambia `list_price` | Se puede hacer hoy |
| Stock | Odoo → NexoPOS | `PUT /stock` en modo `ajuste`, con cada movimiento de Odoo (venta en caja, recepción de compra, ajuste de inventario) | Se puede hacer hoy |
| Pedido de tienda nuevo | NexoPOS → Odoo | Pedido de venta en Odoo (equipo «Tienda online»), que la caja abre y cobra con `pos_sale` | **Falta en NexoPOS** |
| Aceptar, listo, en camino, entregado, cancelar | Odoo → NexoPOS | El cajero lo hace en Odoo y NexoPOS le avisa a NexoTienda, como hoy | **Falta en NexoPOS** |
| Pago con Mercado Pago | NexoPOS → Odoo | `paymentStatus` del pedido; en Odoo entra ya pagado | **Falta en NexoPOS** |

### Por qué `ajuste` y no `absoluto`
- Con `absoluto`, Odoo pisaría las unidades que NexoPOS descontó al aceptar un pedido de tienda y que Odoo todavía no registró: reaparecerían y se venderían dos veces.
- Con `ajuste`, cada lado suma o resta lo que le pasó.
- **Regla para no contar dos veces:** el descuento de un pedido de tienda lo hace NexoPOS al aceptar. Odoo lo registra en su stock pero **no** se lo vuelve a mandar a NexoPOS. Los movimientos que vienen de un pedido de tienda se marcan y no se envían.
- **Una vez al día, Odoo compara** su número con `GET /productos` y muestra las diferencias. No corrige solo: decide el comerciante.

### Qué pasa con la caja de NexoPOS
- Si el comercio también vende en el mostrador de NexoPOS, esas ventas entran a Odoo por `GET /ventas` y bajan el stock de Odoo.
- **Riesgo:** `GET /ventas` no dice si una venta vino del mostrador o de un pedido de tienda. Si Odoo ya registró el pedido, lo contaría dos veces. Mientras NexoPOS no lo diga, la opción «también vendo en NexoPOS» queda apagada.

## Lo que hay que pedir
- **A NexoPOS:** pedidos para ERP. Está en `docs/PARA-NEXOPOS_de-NEXO-ODOO_pedidos-de-tienda-para-erp.md`.
- **A nexo-ecosystem:** en un comercio con Odoo, NexoPOS sigue siendo el dueño del pedido de tienda, pero la caja, el stock y el precio los maneja Odoo por la API de ERP. Hay que registrarlo en `DATA-OWNERSHIP.md`. Lo tiene que decidir German con nexo-ecosystem; no se toca desde acá.

## Sin resolver
- **Cuenta corriente:** un pedido de tienda pagado «a la cuenta» deja la deuda en NexoPOS, que es el dueño de la cuenta corriente. En Odoo eso es un cliente con saldo. **PENDIENTE DE VALIDAR** con German qué sistema lleva la libreta de un comercio con Odoo.
- **Facturación:** si factura Odoo (l10n_ar), la venta de tienda se factura desde el pedido de venta. No se tocó.
- **Publicado en tienda:** la API de ERP no deja elegir qué productos se publican (`publicado_en_tienda` es solo lectura). **PENDIENTE DE VALIDAR** si el comercio lo hace desde NexoPOS.
- **Varios locales o depósitos:** NexoPOS tiene un solo stock por comercio. Se toma el depósito principal de Odoo.

## Cómo se prueba
- **Tests con NexoPOS simulado**, como con NexoB2B.
- **Contra producción, en Jure Hnos SRL** (decisión de German, DEC-017):
  - Las lecturas las puede hacer el conector.
  - Las escrituras de precio y stock, y los pedidos de prueba en su NexoTienda, los hace German.
  - Antes de escribir stock: anotar el stock de NexoPOS de los productos que se van a tocar, para poder volver atrás.

## Orden propuesto
1. **Conexión con NexoPOS y productos:** clave en Ajustes, leer el catálogo de NexoPOS y vincular por EAN.
2. **Precios y stock** de Odoo → NexoPOS, en modo `ajuste`, más la comparación diaria. Funciona con la API de hoy.
3. **Pedidos de tienda en la caja de Odoo**, cuando NexoPOS tenga lo pedido.
