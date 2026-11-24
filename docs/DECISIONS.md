# Decisiones — Nexo-odoo

Decisiones numeradas, con motivo. Si una cambia, se marca como reemplazada y se agrega la nueva; no se borra.

Origen: brief de nexo-ecosystem (30/10/2026) y respuesta con las decisiones de German (04/11/2026).

---

## DEC-001 — Un repo por ERP, una rama por versión de Odoo
- `main` tiene solo `README.md`, `CLAUDE.md` y `docs/`. El código vive en `20.0` (después `19.0` y `18.0`), y cada rama lleva su copia de `docs/`.
- **Motivo:** las APIs de Odoo cambian entre versiones; mantener una rama por versión es más simple que llenar un solo código de condicionales.

## DEC-002 — Dos módulos separados desde el inicio: `nexo_core` y `nexo_b2b`
- `nexo_core`: credenciales, cliente HTTP, errores, diagnóstico. `nexo_b2b`: catálogo y compras de NexoB2B, y depende de `nexo_core`.
- **Motivo:** los conectores futuros (`nexo_pos`, `nexo_clubpay`) reusan el núcleo y son opcionales entre sí.

## DEC-003 — No se guarda la contraseña del comercio
- Se usa solo para el login. Se guarda el token JWT con su vencimiento, en un modelo al que solo acceden los administradores. Si el token vence o da 401, se pide iniciar sesión de nuevo.
- El token se manda siempre: **nunca se usa `comercio_id` por parámetro sin token**.
- **Motivo:** si alguien accede a la base de Odoo, no obtiene la contraseña del comercio.

## DEC-004 — Precio de la línea de proveedor: el neto con la lista del comercio aplicada
- Se usa `precio` (neto, con la lista ya aplicada). `precio_mostrar` se usa solo en la pantalla de búsqueda, respetando `muestra_precio` (DEC-018 de NexoB2B).
- **Motivo:** en Odoo, las compras van a precio neto. El precio que se muestra en la búsqueda lo decide el servidor.

## DEC-005 — `precio` es el de la presentación, no el de la unidad
- **Verificado en el código de NexoB2B** (`api/store/ordenes/route.ts`): en un pedido, `precio_unitario` es el `precio` de la presentación y `cantidad` es la cantidad de presentaciones. Por ejemplo, «Caja x12» a $1200 cuesta $1200 la caja.
- El costo por unidad es `precio / factor`. Se calcula **sin redondear antes de tiempo**, con la precisión de Odoo para precios de producto, y el precio de la presentación se guarda tal cual.
- **Motivo:** si se confunde caja con unidad, los precios quedan ×12 o ÷12.

## DEC-006 — Impuestos: tabla de mapeo por alícuota en Ajustes
- La clave es el número `alicuota_iva` (21, 10.5, 27, 0…). Cada fila tiene dos columnas: el impuesto de **venta** de Odoo y el de **compra**. Los nombres de los campos de `product.template` en Odoo 20 están **PENDIENTES DE VALIDAR**. El mapeo es por compañía.
- La tabla se precarga proponiendo los impuestos con el mismo porcentaje, y el usuario confirma.
- Si al agregar un producto la alícuota no está mapeada, se pide mapearla en ese momento, o se agrega sin impuesto con una confirmación explícita. **Nunca se crea un impuesto.**
- La lista de alícuotas sale de `GET /store/taxonomia`, que devuelve `alicuotas` activas. Su forma exacta está **PENDIENTE DE VALIDAR contra la API real**.
- **Motivo:** NexoB2B no tiene ids de impuestos, y cada base de Odoo tiene los suyos.

## DEC-007 — Categorías: mapeo perezoso y recordado, por subrubro
- No se precarga la taxonomía. La primera vez que se agrega un producto de un subrubro sin mapear, se pregunta: crear en el árbol `NexoB2B / Pasillo / Rubro / Subrubro` o elegir una categoría existente.
- El mapeo se guarda con el id de NexoB2B y se ve y edita en Ajustes.
- **PENDIENTE DE VALIDAR en la base de Linware:** qué cuentas contables y qué método de valuación heredan las categorías nuevas.
- **Motivo:** la taxonomía es grande, y cada comercio organiza su Odoo a su manera.

## DEC-008 — Se compra en presentaciones y se almacena en unidades
- El producto de Odoo se maneja en **unidades**. La presentación del mayorista existe solo en la compra.
- En este desafío, la línea de proveedor guarda la presentación completa: `presentacion_id`, id de la presentación del mayorista, `factor`, nombre, precio de la presentación y `ean_propio` si lo tiene.
- Los packs de venta al consumidor (por ejemplo, x6) son combos de N unidades, no un concepto aparte.
- **Mecanismo nativo de Odoo 20 (aprobado por German el 23/11/2026).** En Odoo 20 no existen `product.packaging` ni `uom_po_id`: los empaques son unidades `uom.uom` con `relative_uom_id` y `relative_factor`. Cómo se arma:
  - El producto queda en Unidades (`uom.product_uom_unit`).
  - Cada presentación con `factor` > 1 se representa con una unidad «x<factor> Unidades», relativa a Unidades. Se busca por (referencia, factor), solo se crea si no existe, y se comparte entre productos. El nombre es genérico para que «Caja x12» y «Pack x12» no terminen como dos unidades distintas con el mismo factor. El nombre comercial de NexoB2B queda en la línea de proveedor.
  - La línea de proveedor (`product.supplierinfo`) lleva `uom_id` = esa unidad, `price` = `precio` de NexoB2B **tal cual** (el de la presentación), `min_qty` = 1 (o `cantidad_minima` cuando NexoB2B la exponga) y `product_code` = SKU (vacío hasta entonces).
  - Si la presentación trae `ean_propio`, se guarda en `product.uom` (producto + unidad + código de barras).
  - Requiere activar el grupo `uom.group_uom` («Units of Measure & Packagings») en la base.
- **Motivo:** el precio no se recalcula y no hay riesgo de ×12 o ÷12. Al recibir la compra, Odoo convierte solo de cajas a unidades, así que en el desafío 3 no hace falta código propio para eso.
- Candidata a regla de todo el ecosistema (la oficializa nexo-ecosystem).

## DEC-009 — Marca en un campo propio `nexo_marca`
- **Motivo:** Odoo base no tiene marca, y no queremos depender de módulos de terceros.

## DEC-010 — Tests con respuestas simuladas; la API real solo a mano y de lectura
- Los tests de Odoo simulan las respuestas HTTP de NexoB2B y cubren el mapeo, la idempotencia y los errores. Contra la API real solo se prueba a mano y en modo lectura.

## DEC-011 — Pedidos: el pedido de compra de Odoo se envía a NexoB2B, y el estado se consulta cada 5 minutos
- Se compra con el pedido de compra nativo de Odoo: proveedor = mayorista, y cada línea = una presentación. «+ Al pedido» del catálogo suma al borrador de ese mayorista, y «Enviar a NexoB2B» hace `POST /store/ordenes`.
- No se manda precio: lo pone NexoB2B con la lista del comercio, y Odoo copia en las líneas el `precio_unitario` que devuelve.
- NexoB2B no avisa a los comercios por API (solo email y push al celular). Un cron (`NexoB2B: actualizar estado de los pedidos`, cada 5 minutos) consulta `GET /store/ordenes/:id` de los pedidos abiertos. También hay un botón «Actualizar estado NexoB2B».
- Cada cambio de estado se avisa con una nota interna que menciona al comprador del pedido: le llega a su bandeja de Odoo y no se le manda correo al mayorista.
- **Motivo:** es el flujo que ya conoce quien compra en Odoo, y el precio no puede venir del cliente (así lo exige NexoB2B). El cron es lo único posible mientras NexoB2B no tenga avisos para comercios (pedido en `docs/PARA-NEXOB2B_de-NEXO-ODOO_ordenes-desde-conectores.md`).

## DEC-012 — El pedido se confirma en Odoo cuando lo confirma el mayorista (aprobado por German el 23/11/2026)
- Al enviarlo queda como «Solicitud enviada». Con `confirmado` (o cualquier estado posterior), Odoo ejecuta `button_confirm`, y con Inventario eso crea la recepción.
- `cancelado` en NexoB2B cancela el pedido en Odoo. `devuelto` crea una actividad para el comprador. `entregado` crea una actividad para validar la recepción: **no se valida sola**, porque hay que contar lo que llegó.
- Cancelar desde Odoo llama a `PUT /store/ordenes/:id/cancelar`, que NexoB2B permite solo en `cargada` o `devuelto`. Después Odoo no deja cancelar y explica que hay que hablarlo con el mayorista.
- **No se usa «reenviar» de NexoB2B:** esa ruta toma el precio del cuerpo del pedido. Con un pedido devuelto, lo seguro es cancelarlo y armar uno nuevo.

## DEC-013 — El envío se corta: se busca antes de reenviar
- `POST /store/ordenes` no tiene clave de idempotencia. Cada pedido lleva en la primera línea de `notas` «Pedido de compra Odoo <número>».
- Si el envío se corta sin respuesta (timeout, red o 5xx), el pedido queda marcado. Antes de reenviarlo, Odoo busca en `GET /store/ordenes` una orden de ese mayorista con esa referencia y, si la encuentra, la vincula en lugar de crear otra.
- **Motivo:** un pedido duplicado a un mayorista es mercadería de más.

## DEC-014 — La línea guarda la presentación elegida
- `purchase.order.line.nexo_seller_id` apunta a la línea de proveedor (la presentación) de NexoB2B. Sin ella, se deduce por la unidad solo si hay una sola presentación con esa unidad; si hay más, Odoo pide que la elijas.
- **Motivo:** en NexoB2B hay presentaciones distintas con el mismo factor (por ejemplo, «BOTELLA» y «CAJA», las dos con factor 1). En Odoo comparten unidad, y elegir por unidad mandaba la presentación equivocada.

## DEC-015 — Primera prueba de un pedido real en producción (decisión de German, 23/11/2026)
- NexoB2B no tiene entorno de pruebas. German decidió probar el envío con un pedido real de Jure Hnos SRL a «Mayorista de Prueba», que después se cancela.
- **El pedido lo envía German, no el asistente.** Contradice la regla del brief («jamás crear pedidos reales en un comercio real»), así que queda registrado para que lo vea nexo-ecosystem. El entorno de pruebas se sigue pidiendo.

## DEC-016 — Un comercio con Odoo cobra en el Punto de Venta de Odoo (decisión de German, 23/11/2026)
- La caja y la atención de los pedidos de NexoTienda van en Odoo. NexoTienda no cambia: sigue hablando con NexoPOS, que queda como base de la tienda (catálogo, precio, stock y pedidos).
- Odoo escribe precio y stock en NexoPOS por la API de ERP, en modo `ajuste`, y atiende los pedidos de tienda cuando NexoPOS los exponga. Propuesta completa en `docs/PROPUESTA-caja-odoo-y-nexotienda.md`.
- **Motivo:** German lo eligió frente a la opción de seguir cobrando en NexoPOS. Tiene que quedar registrado en `DATA-OWNERSHIP.md` de nexo-ecosystem.

## DEC-017 — Pruebas de NexoPOS en Jure Hnos SRL (decisión de German, 23/11/2026)
- No hay un comercio de prueba en NexoPOS. Se prueba en Jure Hnos SRL, que es real.
- El conector puede leer. Las escrituras de precio y stock, y los pedidos de prueba en su NexoTienda, los hace German. Antes de escribir stock se anota el número anterior para poder volver atrás.

## DEC-018 — `nexo_pos` arranca en «solo lee»
- Vincular, comparar y ver diferencias funciona sin escribir nada en NexoPOS. Precios y stock se mandan solo con «Mandar precios y stock a NexoPOS» prendido, que es por compañía y viene apagado. Apagado, tampoco se encolan movimientos.
- **Motivo:** la prueba es en un comercio real (DEC-017). Vincular deja los precios marcados para mandar, y sin el interruptor una base de prueba con tareas programadas le pisaría los precios a NexoTienda.

## DEC-019 — Stock a NexoPOS: ajustes y sin reintento a ciegas
- Odoo manda solo `ajuste`, sumado por producto. Sin respuesta, la fila queda «Sin respuesta» y no se reintenta sola: `PUT /stock` no es idempotente. La resuelve la comparación de stock o una persona que revisó NexoPOS.
- Las diferencias no se corrigen solas: decide el comerciante.
- **Motivo:** un ajuste duplicado o un absoluto que pisa un pedido de NexoTienda aceptado son unidades que se venden y no están.

## DEC-020 — El precio que va a NexoPOS lleva IVA
- `list_price` más los impuestos de venta del producto (`compute_all`, `total_included`), en centavos.
- **Motivo:** NexoTienda muestra y cobra el precio de NexoPOS, que es el precio al consumidor. PENDIENTE DE VALIDAR con German si algún comercio carga en NexoPOS precios sin IVA.

## DEC-021 — Pedidos de NexoTienda en Odoo: pedido de venta y stock que no se cuenta dos veces
- Cada pedido de tienda es un `nexo.tienda.order` (el espejo de NexoPOS) y, cuando todos sus productos están vinculados, un pedido de venta del equipo «NexoTienda», que la caja cobra con «Cobrar un pedido» (`pos_sale`). Sin todos los productos vinculados no se arma el pedido de venta: uno a medias se cobraría mal.
- NexoPOS sigue siendo el dueño del pedido. Odoo pide cada paso y se queda con lo que NexoPOS contesta. Ante un 409, Odoo vuelve a leer el pedido y muestra el estado real.
- **Stock:** lo que sale de Odoo por un pedido de tienda (la entrega del pedido de venta, o la de la caja en las líneas que vienen de uno) **no se manda** a NexoPOS, porque NexoPOS lo descontó al aceptar. Las devoluciones sí se mandan. La comparación diaria resta lo aceptado y sin entregar.
- **Cobrado o entregado en Odoo → entregado en NexoPOS**, paso por paso (aceptar, listo, entregado), porque NexoPOS no deja saltear. Si el pedido estaba `recibido`, aceptarlo ahí es lo que descuenta el stock de NexoPOS. Lo manda el cron cada minuto: repetir no duplica, así que un corte se reintenta solo.
- **Entregado en NexoPOS → la entrega de Odoo se valida sola.**
- Viene **apagado** por compañía: prenderlo es decidir que la caja es Odoo (DEC-016).
- **PENDIENTE DE VALIDAR con German:** un pedido pagado con Mercado Pago o a la libreta no se cobra en la caja (Odoo lo avisa), pero tampoco se registra ese pago en Odoo. Falta decidir cómo entra a la contabilidad.

## DEC-022 — El pedido de tienda entra solo a la caja como orden en borrador
- Pedido de German (23/11/2026): el cajero que está vendiendo no mira la lista de pedidos, así que el pedido tiene que aparecer en la caja.
- Cada pedido en curso con pedido de venta se pone como **orden en borrador** (`pos.order`, nombre = código del pedido) en la caja elegida en Ajustes; si no hay una elegida, la única de la compañía o la única abierta. Se avisa a la caja con la sincronización de Odoo, que carga los borradores al momento.
- No entran los pagados online o a la libreta: no se cobran en la caja.
- Cancelado en NexoPOS → se cancela el borrador. Borrado por el cajero → no se repone en esa sesión. Cierre de caja → los borradores de tienda salen de la sesión y entran a la próxima.
- Solo cuenta como cobrado en la caja una orden pagada (`paid`, `done`, `invoiced`), nunca el borrador.

## DEC-023 — Descuento de ClubPay: cupón como forma de pago, QR a demanda (pedido de German, 23/11/2026)
- **Clave en Odoo.** Odoo habla directo con ClubPay usando la clave `pos_…` del comercio, en `X-API-Key`. No pasa por NexoPOS. La pega el administrador en Ajustes y se guarda como token de `nexo_core`; nunca se muestra.
- **ClubPay decide el descuento.** Odoo no calcula porcentajes ni topes.
- **El descuento no achica la venta**, igual que en NexoPOS. Entra como forma de pago «Cupón ClubPay»: en la caja, una línea de pago; en Ventas, un pago parcial de la factura con el diario «Cupones ClubPay».
- **Caja:** el QR sale al elegir la forma de pago, sin un paso de más. El servidor rechaza un cupón que no esté aplicado, que no coincida con el importe o que ya se haya usado.
- **Ventas:** el QR no va en el mail porque vence a los 3 minutos. El cliente lo genera desde el portal cuando lo va a usar. Cada intento es un cobro nuevo, y uno nuevo cancela el pendiente.
- **Sin anulación:** ClubPay no tiene cómo anular un descuento aplicado. Si el socio ya confirmó, el cupón se aplica aunque el cajero cancele. La caja no hace devoluciones con ClubPay.
- **PENDIENTE DE VALIDAR con German:** cómo se concilia el diario «Cupones ClubPay» con lo que ClubPay le paga al comercio.

## DEC-024 — Mercado Pago a través de ClubPay, sin credenciales de Mercado Pago en Odoo (pedido de German, 24/11/2026)
- **Odoo no se integra con Mercado Pago.** Cobra con el Mercado Pago que el comercio ya conectó en ClubPay, con la misma clave `pos_…` (rutas `/pos/payments`). No se usa el módulo `payment_mercado_pago` de Odoo, y en Odoo no hay tokens de Mercado Pago.
- **Se prende con una casilla** en Ajustes → ClubPay → «Cobrar con Mercado Pago a través de ClubPay». Al prenderla se comprueba que ClubPay tenga Mercado Pago conectado y el interruptor de cobros en el POS. El QR de mostrador además pide la dirección del local.
- **Caja:** forma de pago «Mercado Pago (ClubPay)», con un QR de mostrador por el importe de la línea. Sirve sola, sin descuento, o después del cupón: la ventana del cupón tiene «Cobrar el resto con Mercado Pago». El efectivo sigue disponible.
- **Ventas:** proveedor de pago «Mercado Pago (ClubPay)» en el portal, con el link de pago de ClubPay (canal `tienda`, vive 30 min). Al volver, el pedido se confirma y el pago entra al diario «Mercado Pago (ClubPay)».
- **El cupón cuenta como pagado** en `sale.order.amount_paid`. Así el portal cobra solo el neto, y el pedido se confirma cuando cupón más pago cubren el total. Efecto visible: el portal muestra el cupón como «Pagado».
- **Sin webhook:** ClubPay avisa solo a NexoPOS. Odoo pregunta: la caja cada 2 s mientras el QR está abierto, y un cron cada 2 min revisa los cobros de las últimas 48 h.
- **Reglas de ClubPay que se respetan:** `paid` nunca vuelve atrás; `rejected` no es final (se puede probar otra tarjeta con el mismo QR); un `cancelled` o `expired` todavía puede terminar `paid`, y entonces gana el pago. Antes de ofrecer otro medio se cancela el cobro; si ClubPay contesta que ya se pagó, la venta se cierra con ese pago.
- **El servidor vuelve a comprobar** el pago al recibir la venta de la caja: pagado, por el mismo importe y usado una sola vez.
- **Devoluciones:** en Ventas, con «Devolver» de la transacción de Odoo (parcial o total). En la caja no: se hacen desde «Cobros con Mercado Pago» → «Devolver», solo administradores.
- **Pagos sin venta** (se pagó pero la caja nunca cerró la venta): a los 30 min se crea una actividad para un administrador. **No se devuelven solos.** NexoPOS sí los devuelve solo; PENDIENTE DE VALIDAR con German cuál conviene.
- **Simulador:** con el simulador de ClubPay prendido, el QR y el link no son de Mercado Pago; el pago se acredita a los 8 s (caja) o con una página de prueba (portal).
