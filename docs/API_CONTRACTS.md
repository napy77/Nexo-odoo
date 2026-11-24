# Contratos de API que usa Nexo-odoo

Verificado contra el **código** de NexoB2B (`/Users/german/Claude/Projects/nexob2b/nexob2b/apps/backend/src/api/store`), no contra la API en vivo. Donde el código y la documentación de NexoB2B no coinciden, gana el código.

## Cabeceras de todas las llamadas
| Cabecera | Valor |
|---|---|
| `X-Nexo-Connector` | `odoo/<versión del módulo nexo_b2b>`, ej. `odoo/20.0.1.0.0` |
| `X-Request-Id` | uuid4 hex, distinto en cada llamada (se guarda en `nexo.log`) |
| `x-publishable-api-key` | clave publicable de Medusa (Ajustes) |
| `Authorization` | `Bearer <token>` en las llamadas autenticadas |

PENDIENTE DE VALIDAR: si NexoB2B registra `X-Request-Id` de su lado.

## NexoB2B

### `POST /store/comercios/auth` — IMPLEMENTADO en el conector
- Cuerpo: `{ email, password }`. Sirve para el dueño o para un usuario del equipo.
- 200: `{ token, comercio, usuario }`. `usuario` es `null` si entró el dueño (tiene todos los permisos); si es del equipo, trae `permisos`.
- 401 `{ error: "Credenciales incorrectas" }`.
- 403 `{ error }`: usuario desactivado o comercio suspendido.
- El token dura 30 días, tanto para el dueño como para el equipo.
- El conector rechaza la sesión si el usuario es del equipo y no tiene `comprar`.

### `GET /store/comercios/me` — IMPLEMENTADO («Probar conexión»)
- Requiere el token. 200 `{ comercio, usuario }` y 401 si el token es inválido.

### `GET /store/productos` — IMPLEMENTADO (búsqueda y relectura al agregar)
- **No exige token**, pero con token aplica la lista de precios del comercio. El conector lo manda siempre y nunca usa `comercio_id` sin token.
- Parámetros que usa: `q`, `page`, `pageSize` (máximo 100; el conector usa 50, como el portal), `con_stock=1` (sin él no se filtra por stock), `pasillo_id`, `rubro_id`, `subrubro_id`, `mayorista_id` y `comercio_id`.
- `comercio_id` se toma del token guardado y va **siempre junto con el token**. Con él, NexoB2B filtra los mayoristas por la zona del comercio y completa `tiene_alta`. El portal del comercio lo manda sin token; el conector no.
- Verificado contra la API en producción (23/11/2026): con stock y sin filtros, 3879 productos para Jure Hnos SRL.
- Respuesta: `{ productos, total, page, pageSize, totalPages }`.
- Producto: `id` (pm_…), `ean`, `nombre`, `descripcion`, `marca`, `unidad_base`, `alicuota_iva`, `imagen_url`, `imagenes[]`, `pasillo_id`, `rubro_id`, `subrubro_id`, `pasillo_nombre`, `rubro_nombre`, `subrubro_nombre`, `actualizado_at` y `mayoristas[]`.
- Mayorista: `listing_id`, `mayorista_id`, `mayorista_nombre`, `muestra_precio`, `tiene_alta`, `presentaciones[]`.
- Presentación: `id` (de la presentación del mayorista), `presentacion_id` (del maestro), `nombre`, `factor`, `ean_propio`, `precio` (de la presentación, neto, con la lista ya aplicada), `precio_lista`, `stock`, `precio_final`, `precio_mostrar`, `precio_lista_mostrar`.
- **No trae** `sku` ni `cantidad_minima`: ver el pedido a NexoB2B en `docs/`.
- No hay GET por id. Para releer un producto, el conector busca `q=<ean>&mayorista_id=<may>` y filtra por `id`.

### `GET /store/taxonomia` — IMPLEMENTADO (filtros y propuesta de alícuotas)
- Público. Devuelve `{ rubros, subrubros, pasillos, tipos_impositivos, alicuotas }`.
- Pasillo: `id` y `nombre`. Rubro: `id`, `nombre` y `pasillo_id` (puede venir vacío). Subrubro: `id`, `nombre` y `rubro_id`. Alícuota: `id`, `nombre`, `porcentaje` y `activo`.
- Verificado en producción: 20 pasillos y 138 rubros, de los cuales 21 no tienen pasillo.

### `GET /store/mayoristas/lista` — IMPLEMENTADO (pantalla Mayoristas)
- Con token. Parámetros: `lat`, `lng`, `radio_km` (50 por defecto), `rubros` (se repite la clave, como el portal) y `busqueda` (texto en nombre, ciudad, provincia o rubros).
- Devuelve `{ mayoristas, meta }`. Cada mayorista trae `id`, `nombre`, `ciudad`, `provincia`, `rubros[]`, `descripcion`, `logo_url`, `portada_url`, `distancia_km` (null sin coordenadas), `productos_count` y `muestras[{nombre, imagen_url}]`.
- También trae `solicitud` (null o `{estado: pendiente|aceptado|rechazado}`) y `contacto`: el vendedor asignado si lo hay (`es_vendedor`), si no el mayorista.
- Fuente: `apps/backend/src/api/store/mayoristas/lista/route.ts`. Verificado en producción el 23/11/2026.

### `GET /store/tipos-comercio` — IMPLEMENTADO (rubros sugeridos)
- Público. `{ tipos, equivalencias }`, donde `equivalencias` dice a qué rubros de mayorista le compra cada tipo de comercio.
- Se cruza con `comercio.rubros` de `/store/comercios/me` (que también trae `lat` y `lng`).

### `POST /store/solicitudes` — IMPLEMENTADO, sin probar contra producción
- Con token. Cuerpo `{ mayorista_id, mensaje? }`. Devuelve `{ solicitud }` con estado `pendiente`.
- **409** si ya existe una solicitud con ese mayorista, en cualquier estado.
- No revisa permisos del usuario del equipo: el conector exige `purchase.group_purchase_manager` en Odoo.
- Fuente: `apps/backend/src/api/store/solicitudes/route.ts`.

### `POST /store/ordenes` — IMPLEMENTADO, sin enviar todavía contra producción
- Con token. Cuerpo `{ mayorista_id, items[{presentacion_id, cantidad}], notas?, medio_pago_id?, transporte_id?, sucursal_id? }`. `presentacion_id` es el id de `producto_mayorista_presentacion`.
- El precio lo calcula el servidor con la lista del comercio: el que manda Odoo no se usa. Odoo actualiza `price_unit` con `items[].precio_unitario` de la respuesta.
- **403** con `codigo` `sin_alta`, `alta_pendiente` o `alta_rechazada` si no hay alta aceptada.
- **Sin clave de idempotencia.** Odoo pone «Pedido de compra Odoo <número>» en la primera línea de `notas` y, si la respuesta se pierde, busca esa referencia en `GET /store/ordenes` antes de reenviar (DEC-013). Pedido en `docs/PARA-NEXOB2B_de-NEXO-ODOO_ordenes-desde-conectores.md`.
- Fuente: `apps/backend/src/api/store/ordenes/route.ts`.

### `GET /store/ordenes` y `GET /store/ordenes/:id` — IMPLEMENTADO (conciliación y estado)
- Con token. La lista trae todas las órdenes del comercio con sus ítems, sin paginar ni filtrar por fecha.
- Campos que usa Odoo: `id`, `numero`, `estado`, `total`, `costo_medio_pago`, `costo_transporte`, `monto_descuento`, `cantidad_bultos`, `peso_kg`, `numero_guia`, `mensaje_mayorista`, `is_pagada`, `is_facturada` e `items[{presentacion_id, precio_unitario}]`.
- **Los importes llegan como texto** (`"total": "66550"`, `"costo_medio_pago": "0.00"`, `"precio_unitario": "55000"`) y `cantidad_bultos` o `peso_kg` pueden venir `null`. Verificado con ORD-00055.
- Estados: `cargada → confirmado | devuelto | cancelado`; `confirmado → armando | cancelado`; `armando → listo`; `listo → en_transporte | entregado`; `en_transporte → entregado`; `devuelto → cancelado`.
- **No hay aviso al comercio** salvo email y push. Odoo consulta cada 5 minutos (DEC-011).

### `PUT /store/ordenes/:id/cancelar` — IMPLEMENTADO
- Solo en `cargada` o `devuelto`; en otro estado Odoo no deja cancelar y dice que se hable con el mayorista.

### `GET /store/ordenes/:id/documentos` — IMPLEMENTADO
- `{ documentos[{id, nombre, tipo, url}] }`. Odoo deja un enlace en el chatter por cada documento nuevo.

### `GET /store/mayoristas/:id/medios-pago` y `/transportes` — IMPLEMENTADO (ventana de envío)
- `{ medios_pago | transportes: [{id, nombre, porcentaje_costo}] }`. Verificado en producción con Truglio Hnos SRL el 23/11/2026.

### `POST /store/ordenes/:id/reenviar` — NO SE USA
- Toma el precio del cuerpo del pedido. Pedido de corrección en `docs/PARA-NEXOB2B_de-NEXO-ODOO_reenviar-toma-precio-del-cliente.md`.

## NexoPOS — API para ERP (`/api/erp/v1`)
Fuente: `nexo-pos/backend/src/modules/erp.ts` y la doc pública `https://nexopos.app/docs/api`. Clave del comercio (`npos_…`) en `Authorization: Bearer`; la genera el comerciante en NexoPOS → Configuración → Conectar tu sistema. Importes en **centavos enteros**. Los productos se identifican por `ean`, `sku` (código de balanza) o `id`; un EAN repetido en el comercio no se aplica y vuelve en `no_encontrados`.

### `GET /productos?page=` — IMPLEMENTADO (clave, vincular, comparar)
- `{ productos[{id, ean, sku, nombre, unidad, precio_centavos, costo_centavos, stock, stock_minimo, es_insumo, publicado_en_tienda, actualizado}], page, pageSize: 500, total }`. `precio_centavos: null` es sin precio de venta.

### `POST /productos` y `GET /importaciones/:id` — IMPLEMENTADO, sin probar contra producción
- Hasta 5000 por llamada: `{ productos[{ean, nombre, unidad, precio_centavos, costo_centavos, stock, stock_minimo}] }` → **202** `{ importacion_id, estado }`. Corre en segundo plano.
- El `stock` **reemplaza**, y un producto que ya existe se actualiza. Por eso Odoo solo manda los que no están vinculados.

### `PUT /precios` — IMPLEMENTADO, sin probar contra producción
- Hasta 1000: `{ precios[{ean, precio_centavos, costo_centavos?}] }` → `{ aplicados, no_encontrados }`. NexoPOS lo marca como precio manual (no lo toca su pantalla de márgenes).
- Odoo manda el precio **con IVA**: es el que paga el consumidor en NexoTienda.

### `PUT /stock` — IMPLEMENTADO, sin probar contra producción
- Hasta 1000: `{ modo: ajuste|absoluto, stock[{ean, cantidad}] }` → `{ modo, aplicados, no_encontrados }`. Odoo usa solo `ajuste`.
- **No es idempotente**, y no tiene clave para reintentar sin duplicar.
- **No le avisa a NexoB2B** (el aviso de stock compartido sale solo de las ventas del mostrador de NexoPOS).

### `GET /ventas?desde=` — NO SE USA TODAVÍA
- Trae las ventas del mostrador y los pedidos de tienda entregados. Desde el commit `cd15a2c` de NexoPOS cada venta trae `pedido_code` (`null` en el mostrador), y el reembolso de un pedido también.
- El `hasta` va con milisegundos: la última venta de una página vuelve a venir al principio de la siguiente. Hay que guardarlas por `id`.

### Pedidos de tienda — IMPLEMENTADO en `nexo_tienda`, sin probar contra producción
Hecho por NexoPOS en el commit `cd15a2c`, todavía sin desplegar en nexopos.app (23/11/2026): hasta el deploy, `/pedidos` contesta 404. Respuesta en `nexo-pos/docs/RESPUESTA-PEDIDOS-ERP-A-NEXO-ODOO.md`. Leído también en `backend/src/modules/erp.ts`.
- **Autenticación:** la misma clave `npos_…`, sin login ni vencimiento. No hay permisos por clave.
- **`GET /pedidos?actualizados_desde=<ISO>`** → `{pedidos[], hasta, hay_mas}`, de a 200, ordenados por `updated_at`. Sin la fecha, los que están en curso.
  - `hasta` va con microsegundos y queda **dos minutos atrás** a propósito: un pedido puede volver repetido. Odoo lo guarda tal cual y actualiza por `code`.
  - Cada pedido: `code`, `status`, `creado`, `actualizado`, `retiro {tipo, franja, direccion}`, `pago {medio, estado, id}`, `contacto {nombre, telefono}`, `cuenta_id`, `notas`, `listo_para`, `motivo_cancelacion`, `cancelado_por`, `revision {motivo}` (solo en `recibido`), `venta {id, ticket}`, `lineas[]` y los importes en centavos.
  - Línea: `producto_id` (texto, el `id` de `GET /productos`), `ean`, `sku`, `nombre`, `unidad`, `cantidad` (número) y `precio_centavos` (con IVA).
  - `pago.medio`: `efectivo_entrega`, `online` o `cuenta_corriente`. `pago.estado`: `no_aplica`, `pendiente`, `pagado`, `rechazado` o `reembolsado`.
- **`GET /pedidos/:code`**: uno solo, con la misma forma.
- **`POST /pedidos/:code/{aceptar,listo,en-camino,entregado,cancelar}`**: corre la misma función que la pantalla de NexoPOS y contesta el pedido completo.
  - `aceptar` pide `listo_para` (sin él, 400 `falta_listo_para`). `cancelar` pide `motivo` (hasta 400 letras).
  - Repetir un paso ya dado: 200 sin hacer nada. Un `cancelar` repetido conserva el primer motivo.
  - Un paso que no corresponde: 409 `{error, code: "paso_invalido", status}`. `en-camino` de un retiro: 409 `es_retiro`. Un `code` de otro comercio: 404 `pedido_inexistente`.
  - Pasos: `recibido → aceptado → listo → (en_camino, solo reparto) → entregado`, y `cancelado` desde cualquiera antes de entregado. No se puede saltear `listo`.
  - El stock se descuenta al **aceptar** (movimiento `PEDIDO-<code>`) y vuelve al cancelar.
- **Webhook al ERP:** PROPUESTO por NexoPOS, no hecho. Odoo consulta cada minuto.

## ClubPay — cobro con QR (`/pos/charges`)
Fuente: `clubapp/docs/nexopos-cobro-qr.md` del repo de ClubPay, el mismo contrato que usa NexoPOS. Base `https://api.clubpay.com.ar` (editable en Ajustes). Clave del comercio (`pos_…`) en `X-API-Key`. Importes en **centavos enteros**. **IMPLEMENTADO, probado solo con simulador.**
- `POST /pos/charges {ticket_total_cents, external_reference}` → 201 `{charge_id, qr_payload, expires_at, status}`. Es idempotente por `external_reference`; Odoo manda `odoo-<base>-<pos|sale>-<uuid>`.
- `GET /pos/charges/:id` → `status`, que puede ser `pending`, `applied`, `rejected`, `expired` o `cancelled`. Con `applied` vienen `member{name, member_number, club_name}`, `offer{description, discount_percent}`, `transaction_id`, `discount_cents`, `neto_cents` y `recorte` (`base`, `por_compra` o `mensual`).
- `POST /pos/charges/:id/cancel` → `cancelled`, o 409 si ya estaba resuelto (Odoo vuelve a leerlo).
- La clave se prueba con `GET /pos/charges/chg_prueba_de_odoo`: un 404 es clave válida.
- No hay anulación de un descuento aplicado.

## ClubPay — cobros con Mercado Pago (`/pos/payments`)
Fuente: `clubapp/docs/respuesta-pedido-cobros-mercadopago.md` y `backend/src/routes/pos.ts` del repo de ClubPay. Misma base y misma clave que los cupones. Importes en centavos. Decisiones en DEC-024. **IMPLEMENTADO, probado solo con simulador.**
- `GET /pos/payments/status` → `{mercadopago, enabled_for_pos, mostrador_listo}`. Se consulta al prender la casilla y antes de cada cobro.
- `POST /pos/payments {amount_cents, external_reference, channel, description?, return_url?}` → el cobro. `channel` es `mostrador` (trae `qr_data` EMVCo, vence a los 5 min) o `tienda` (trae `checkout_url`, vence a los 30 min). Odoo manda `external_reference` = `odoo-<base>-<pos|tx>-<id>`.
- `GET /pos/payments/:id` → `payment_id, status, amount_cents, fee_cents, refunded_cents, paid_at, mp_payment_id, qr_data, checkout_url, expires_at, error`. `status`: `pending`, `paid`, `rejected`, `expired`, `cancelled` o `refunded`.
- `POST /pos/payments/:id/cancel` → el cobro cancelado, o 409 con los datos del cobro si ya se pagó (Odoo toma ese pago).
- `POST /pos/payments/:id/refunds {amount_cents?, external_reference}` → el cobro con `refunded_cents` actualizado.
- Los errores 502 traen el texto en `message` o `error`.
- **Sin webhook para Odoo:** ClubPay avisa solo a NexoPOS. Odoo consulta con `GET`.
- **PENDIENTE DE VALIDAR con ClubPay:** hay una sola caja de Mercado Pago por comercio (compartida con NexoPOS). No está confirmado qué pasa con dos QR de mostrador abiertos a la vez.

## ClubPay — libreta del cliente (`/pos/customers`, `/pos/account/*`)
Fuente: `nexopos/docs/CLUBPAY-CUENTA-CORRIENTE.md` (contrato acordado con NexoPOS) y `clubapp/docs/API_CONTRACTS.md` («Cuenta corriente»). Misma base y misma clave que los cupones. Importes en centavos, con signo. Decisiones en DEC-025. **EN DESARROLLO: sin probar contra ClubPay real.**
- `POST /pos/customers {dni, external_id}` → `{encontrado, status, persona?, mensaje?}`. `status`: `sin_cuenta`, `propuesta`, `vinculada` (en su documentación, `aceptada`; se aceptan las dos) o `rechazada`. Idempotente; no pisa una vinculación aceptada.
- `GET /pos/customers/:external_id` → el mismo estado y `account_id`, sin mandar el DNI.
- `POST /pos/account/movements {external_id, movement_id, kind, amount_cents, occurred_at, description}`. `kind`: `compra`, `pago`, `ajuste` o `devolucion`. Positivo aumenta la deuda. Idempotente por `movement_id`. Contesta 200 aunque no lo registre.
- Odoo manda `external_id` = `ODOO-<id>` y `movement_id` = `odoo-<base>-aml-<id>` (o `…-saldo-…`, `…-anulado`).
- **PROPUESTO, no existe todavía:** `GET /pos/account/payments?desde=<ISO>` → `{pagos: [{clubpay_payment_id, external_id, amount_cents, paid_at, mp_payment_id}], hasta, hay_mas}`. Ver `PARA-CLUBPAY_de-NEXO-ODOO_libreta-en-odoo.md`.
- **Sin webhook para Odoo:** ClubPay avisa la vinculación y los pagos a `NEXOPOS_WEBHOOK_URL`. Odoo pregunta.

