# Estado actual — Nexo-odoo (rama 20.0)

Actualizado al 24/11/2026. Se lee al empezar cada tarea y se actualiza al terminarla.

## Resumen
| Tema | Estado |
|---|---|
| `nexo_core`: cliente HTTP, errores, sesión (token), registro y diagnóstico | PARCIAL: instalado y con tests pasando en una base de prueba; sin API real |
| `nexo_b2b`: Ajustes, login, catálogo (OWL), «Agregar», mapeos | PARCIAL: instalado y con tests pasando en una base de prueba; pantallas vistas en el navegador; sin API real |
| Tests con respuestas simuladas | IMPLEMENTADO: `nexo_core`, `nexo_b2b`, `nexo_pos`, `nexo_tienda` y `nexo_clubpay`, **0 fallas** (24/11/2026, base `nexo_test_ci`: 71 casos en una corrida de `nexo_core`, `nexo_pos`, `nexo_tienda` y `nexo_clubpay`) |
| Login y búsqueda contra la API real de NexoB2B (`https://nexob2b.app`) | IMPLEMENTADO: probado por German con Jure Hnos SRL (dueño), 3879 productos |
| «Agregar» contra la API real | IMPLEMENTADO: probado por German el 23/11/2026 con «Accesorios Lona Pelopincho 1130» (Rivera Hogar) |
| Compras vía NexoB2B (pedido de compra → orden, estado y avisos) | PARCIAL: envío real y confirmación probados (ORD-00055); faltan armado, transporte, entrega y recepción |
| `nexo_pos`: precios y stock de Odoo en NexoPOS (base de NexoTienda) | PARCIAL: 14 tests; lectura e importación probadas con Jure; escritura sin probar |
| Pedidos de NexoTienda en la caja de Odoo (`nexo_tienda`) | PARCIAL: tests con NexoPOS simulado; **lectura y pasos aceptar/listo/entregado probados contra NexoPOS real** (P-0962559F de Jure, los pasos los hizo German); **el pedido entra solo a la caja abierta** (P-3C1D1EBC); cobrarlo en la caja sin probar |
| Descuento de ClubPay en la caja y en Ventas (`nexo_clubpay`) | PARCIAL: probado con el simulador y **con la clave real de Jure, cargada por German, que confirmó el circuito** (24/11/2026) |
| Mercado Pago a través de ClubPay (`nexo_clubpay`, DEC-024) | PARCIAL: caja y portal probados en navegador **solo con el simulador**; **sin un cobro real de Mercado Pago** |
| Libreta del cliente en ClubPay (`nexo_clubpay`, DEC-025) | PARCIAL: instalada en `nexo_test_ci` el 24/11/2026, 15 tests pasando; **sin probar en el navegador ni contra ClubPay real**. Los pagos desde la app llegan por `GET /pos/account/payments`: **ClubPay la confirmó y sale en su próximo despliegue**; hasta entonces da 404 |
| Acceso al Odoo 20 de Linware | IMPLEMENTADO (SSH con llave, lectura sin `sudo`) |
| Base de prueba en Odoo 20 | PENDIENTE DE VALIDAR (todavía no existe) |
| Acceso a la API de NexoB2B | IMPLEMENTADO: host `https://nexob2b.app`; la clave y el usuario los carga German en Ajustes de la base de prueba |

## Lo que sí se probó (23/11/2026, Odoo 20 de Linware, base descartable `nexo_test_ci`)
- **Instalación** de `nexo_core` y `nexo_b2b` en una base nueva, sin datos de demo y sin localización: modelos, permisos `ir.access.csv`, vistas, menús y assets cargan sin errores.
- **Tests: 19, 0 fallas.** En `nexo_core` (9): HTTPS obligatorio, cabeceras, registro sin secretos, mapeo de errores 401/403/404/400/5xx/redirect/no-JSON, timeout, red y certificado, y lectura del vencimiento del JWT. En `nexo_b2b` (10, con NexoB2B simulado): el login guarda el token y no la contraseña, la contraseña equivocada, el rechazo sin «comprar», probar conexión, los parámetros de búsqueda y la URL de la foto, que pide alícuota y categoría, que crea producto, proveedor y líneas (caja «x12» con precio tal cual y EAN de la caja en `product.uom`), que repetir no duplica y actualiza el precio, que un producto propio con el mismo EAN no se pisa, y que los mapeos se recuerdan.
- **En el navegador** (túnel SSH a esa base, usuario admin de la base de prueba):
  - Ajustes → NexoB2B se ve completo.
  - La pantalla «Catálogo NexoB2B» (OWL 3) se dibuja y muestra el login.
  - Un login contra una URL que no existe (`https://nexob2b.invalid`) muestra «No se pudo conectar con NexoB2B… — Solicitud <id>», y ese id quedó en `nexo.log`.
  - Ni la contraseña ni la clave aparecen en el log del servidor.
  - Se encontró y corrigió que el campo de contraseña quedaba lleno después del intento.

- **Con la API real** (German inició sesión con un usuario de Jure Hnos SRL):
  - Búsqueda: 3879 productos con stock.
  - Filtros por pasillo → rubro → subrubro.
  - «Mostrar sin stock» y «Solo mayoristas con alta aprobada», con las marcas «✓ Alta» y «Sin alta» por mayorista.
  - Las vistas Lista, Chica y Grande (la elegida queda guardada en el navegador), el detalle al tocar una tarjeta y «Cargar más productos» (de 50 a 100).
  - Sin errores en la consola.

**No se probó:**
- **«Agregar» con la API real** (German, 23/11/2026, «Accesorios Lona Pelopincho 1130», mayorista Rivera Hogar). En la base quedó:
  - El producto con `barcode` igual al EAN, marca PELOPINCHO, foto y categoría `NexoB2B / Piletas / Accesorios Piletas`, creada en el momento.
  - El proveedor «Rivera Hogar» con su `nexo_mayorista_id`.
  - Una línea de proveedor: presentación «1», factor 1, unidad Units y precio 69.929,30. Es el precio sin IVA: el portal muestra 84.614 con IVA (69.929,30 × 1,21).
  - Los impuestos elegidos en el panel: venta 21 % y compra «0% Imports».
  - El mapeo de alícuotas recordado para 21 %.
- Una base con `l10n_ar`, que es donde aparecen los impuestos reales.
- La descarga del diagnóstico desde la interfaz.

## NexoPOS: precios y stock (`nexo_pos`, 23/11/2026)
**Pasos reales hechos por German** (23/11/2026, 22:43–22:44): aceptar, listo y entregado de P-0962559F desde Odoo. NexoPOS contestó 200 a los tres (registro `nexo.log`) y el pedido quedó entregado.

**El pedido entra solo a la caja** (pedido de German del 23/11: «si el usuario está parado vendiendo, no se entera»):
- Cada minuto, el cron pone cada pedido en curso, con su pedido de venta, como **una orden en borrador de la caja abierta**. Se llama como el pedido (P-…), trae cliente, líneas con el precio de NexoPOS, y en la nota: teléfono, retiro o reparto con franja y dirección, forma de pago, «listo para» y la revisión. La caja abierta se entera al instante (aviso de sincronización de Odoo).
- **Qué caja:** la elegida en Ajustes («Caja que recibe los pedidos»). Si no se eligió, la única caja de la compañía; si hay varias, la única con sesión abierta. Si la caja está cerrada, el pedido entra cuando se abre.
- **No entran** los pagados con Mercado Pago o a la libreta (no se cobran en la caja), ni los que no tienen pedido de venta.
- **Cancelado en NexoPOS:** la orden en borrador se cancela y desaparece de la caja.
- **El cajero la borra:** no se vuelve a poner en esa sesión; en la próxima sesión sí, si sigue en curso.
- **Cerrar la caja** con pedidos de tienda en borrador: se sacan de la sesión (Odoo no deja cerrar con borradores) y entran a la próxima.
- **Cobrarla en la caja** la marca entregada y el cron avisa a NexoPOS, paso por paso.
- **Probado en la base de prueba** (23/11/2026, cron prendido): P-3C1D1EBC (Pizza Muzzarella Lista, $9.000) entró sola a la sesión abierta Jure Hnos SRL/00002 como orden en borrador «P-3C1D1EBC» por $9.000. En la pantalla de la caja, al tocarla quedaba en blanco: la caja lee la nota interna como JSON (`OrderDisplay.getInternalNotes`) y se mandaba texto. Corregido: va una etiqueta por renglón, como las del cajero. Vista en la caja: Pizza Muzzarella Lista $9.000, ligada a S00038, cliente German Yovan, etiquetas del pedido, teléfono, retiro y pago. Cobrarla está sin probar contra datos reales.
- Tests: 8 casos nuevos (`TestNexoTiendaPos`); la corrida completa de `nexo_tienda`, 0 fallas de 21 (1 salteado: necesita que no haya otra caja abierta en la base).

**Por qué hubo que tocar «Traer pedidos» con P-3C1D1EBC:** el Odoo de prueba corría sin cron. Además, el servicio `odoo.service` de producción del servidor también corre los crons de la base `nexo_test_ci`, pero sin `/opt/nexo-odoo` en sus módulos, así que los crons de Nexo fallan ahí (63 fallas desde el 23/11 22:29) y chocan con las actualizaciones (`-u`). En un Odoo bien configurado el cron trae los pedidos solo. **PENDIENTE DE VALIDAR con German:** excluir `nexo_test_ci` de los crons del servicio de producción (no se tocó su configuración).

**IMPLEMENTADO, con tests contra NexoPOS simulado** (`nexo_pos/tests/test_nexo_pos.py`, 12 tests):
- **Clave:** Ajustes → NexoPOS. Se prueba contra `GET /productos` antes de guardarla y queda solo en `nexo.token`. No va al registro.
- **«Mandar precios y stock a NexoPOS»:** viene **apagado**. Apagado, Odoo solo lee y compara (DEC-018).
- **Vincular productos:** por código de barras y solo leyendo. No vincula un EAN repetido en NexoPOS, y desvincula lo que ya no está allá.
- **Alta en NexoPOS:** acción «Dar de alta en NexoPOS» en la lista de productos, solo para los que no están. Manda precio con IVA, costo y stock de Odoo.
- **Precios:** cuando cambia el precio o el IVA del producto, se manda el precio **con IVA** en centavos, cada minuto.
- **Stock:** cada movimiento hecho que entra o sale del depósito elegido va a una cola. Se manda sumado por producto, en modo `ajuste`, cada minuto.
  - Sin respuesta (timeout o red): queda «Sin respuesta» y **no se reintenta solo**, porque se sumaría dos veces.
  - Error del servidor: se reintenta.
  - Con `nexo_pos_skip` en el contexto no se encola (para lo que NexoPOS ya descontó).
- **Diferencias de stock:** una vez por día o con «Comparar ahora». Para cada diferencia, «Dejar NexoPOS como Odoo» manda la diferencia como ajuste, y «Dejar Odoo como NexoPOS» hace un ajuste de inventario que no se reenvía.

**Visto en el navegador** (base de prueba, sin clave): Ajustes → NexoPOS, Inventario → Configuración → NexoPOS → Diferencias y Ajustes enviados.

**Probado contra la API real, solo lectura** (German cargó la clave de Jure Hnos SRL, 23/11/2026):
- La clave se probó y quedó guardada. «Vincular productos» y «Comparar ahora» funcionan.
- NexoPOS tiene 12 productos. **Ninguno quedó vinculado:**
  - La base de prueba tiene solo 2 productos con código de barras.
  - La lona Pelopincho no está en NexoPOS.
  - El EAN del Malbec Las Perdices está en 3 productos de NexoPOS (dos «BOTELLA» con precios distintos y una «CAJA»). Preguntado a NexoPOS en `docs/PARA-NEXOPOS_de-NEXO-ODOO_pedidos-de-tienda-para-erp.md`.
- Errores encontrados y corregidos:
  - «Comparar ahora» desde la lista daba `TypeError` (el método no aceptaba los registros del botón).
  - Sin productos vinculados decía «dicen lo mismo»; ahora dice que no hay vinculados.
- Precio y stock ahora se escriben por `id` de NexoPOS, no por EAN.

- **«Traer productos de NexoPOS»** (Ajustes → NexoPOS): se trajeron 9 de los 12 productos de Jure a la base de prueba, ya vinculados, con el precio sin IVA (21 %) y el stock como inventario inicial. No se encoló nada para mandar. Quedaron afuera los 3 del EAN repetido del Malbec Las Perdices. Solo leyó en NexoPOS.

**Sin probar:**
- Escribir precio y stock en NexoPOS: el interruptor sigue apagado.
- El cron de cada minuto: el servidor de prueba corre sin tareas programadas.
- Un catálogo de más de 500 productos (la paginación de `GET /productos`).
- Ventas del Punto de Venta de Odoo: `point_of_sale` no está instalado. El enganche está en `stock.move._action_done`, así que debería tomarlas, pero no se probó.

## Pedidos de NexoTienda (`nexo_tienda`, 23/11/2026 noche)
NexoPOS hizo lo pedido (commit `cd15a2c`, respuesta en `nexo-pos/docs/RESPUESTA-PEDIDOS-ERP-A-NEXO-ODOO.md`) y ya está desplegado en nexopos.app. Decisiones en DEC-021.

**Probado contra NexoPOS real, solo lectura** (23/11/2026 noche, base de prueba, clave de Jure Hnos SRL):
- «Traer pedidos ahora» trajo **P-0962559F** (el pedido de German del chocolate): German Yovan, teléfono, libreta CLI-2, retiro en el local, paga al retirar.
- La línea se vinculó sola con «Tableta Chocolate Extra Fino Aguila x 150 Grs», por el `producto_id` de NexoPOS.
- El pedido de venta S00037 quedó en borrador: $4.132,23 + IVA 21 % = **$5.000, igual que NexoPOS**. Sin avisos ni actividades.
- El cursor quedó guardado con microsegundos, tal cual lo mandó NexoPOS.
- Se encontró y corrigió un error: «Traer pedidos ahora» desde la lista daba un error del servidor, porque el botón de la cabecera llega con registros y el método era `@api.model`. Es el mismo error que tuvo «Comparar ahora». Ahora hay un test que lo llama como el botón.

**IMPLEMENTADO, con tests contra NexoPOS simulado** (`nexo_tienda/tests/test_nexo_tienda.py`, 12 casos):
- **Interruptor** «Atender los pedidos de NexoTienda en Odoo» en Ajustes → NexoPOS, por compañía y **apagado**.
- **Cada minuto** (cron) o con «Traer pedidos ahora»: `GET /pedidos` con el cursor de NexoPOS tal cual. Un pedido repetido se actualiza por `code`.
- **Pedido de venta** del equipo «NexoTienda», en el depósito de NexoPOS, con el precio de NexoPOS pasado a sin IVA. Si un producto no está vinculado, no se arma y el pedido muestra cuál falta.
- **Botones** Aceptar (pide «para cuándo», muestra la revisión de CR-0003), Listo, Salió (solo reparto), Entregado, Cancelar (pide motivo) y Actualizar.
  - Ante un 409, Odoo vuelve a leer el pedido y se queda con el estado de NexoPOS.
  - Sin respuesta, avisa que se puede tocar de nuevo.
- **Stock:** lo que sale por un pedido de tienda no se manda a NexoPOS (ya lo descontó al aceptar). La comparación resta lo aceptado sin entregar (columna «En pedidos de tienda»).
- **Cobrado en la caja o entregado en Odoo:** el cron lo pasa a entregado en NexoPOS, paso por paso.
- **Entregado en NexoPOS:** la entrega de Odoo se valida sola. **Cancelado en NexoPOS:** se cancela el pedido de venta; si ya se había entregado en Odoo, queda una actividad.
- **Pagado con Mercado Pago o a la libreta:** el pedido y el pedido de venta avisan «No lo cobres en la caja».

**Visto en el navegador** (base de prueba, con dos pedidos de demo, P-DEMO0001 y P-DEMO0002, creados con `_upsert` sin llamar a NexoPOS): la lista, la ficha con la revisión y el producto sin vincular, la ventana «Aceptar» (cerrada sin aceptar), el aviso en el pedido de venta S00012 (total $500 igual a NexoPOS) y el interruptor en Ajustes.

**Sin probar:**
- Cancelar contra NexoPOS real: escribe en Jure, así que lo hace German (DEC-017).
- **Cobrar en el Punto de Venta** («Cobrar un pedido»): no hay test. Lo que no se manda a NexoPOS en la entrega de la caja se calcula con las líneas que vienen de un pedido de tienda. Con «mover el stock al cerrar la sesión», toma los pedidos de la sesión. PENDIENTE DE VALIDAR.
- **Cómo entra a la contabilidad un pago de Mercado Pago o a la libreta.** PENDIENTE DE VALIDAR con German.

## Descuento de ClubPay (`nexo_clubpay`, 23/11/2026 noche)
Mismo contrato que NexoPOS (`clubapp/docs/nexopos-cobro-qr.md`). Decisiones en DEC-023.

**Configuración:** Ajustes → ClubPay (solo administradores).
- Se pega la clave `pos_…` del comercio. Se prueba al guardar y no se vuelve a mostrar.
- «Crear y sumar a las cajas» crea el diario «Cupones ClubPay» (CLUB) y la forma de pago «Cupón ClubPay» (terminal ClubPay) y la suma a las cajas de la compañía. Una caja abierta no se puede tocar: avisa que hay que cerrarla y volver a tocar. Después hay que recargar la caja.
- Interruptor **Simulador**: no llama a ClubPay. Aplica 20 % con tope de $8.000 a los 6 segundos y vence a los 3 minutos, igual que el de NexoPOS.

**IMPLEMENTADO:**
- **Caja (la fila, como NexoPOS):**
  - Al elegir «Cupón ClubPay» sale el QR solo, por el total de la orden, con la hora de vencimiento.
  - «Imprimir QR» lo manda a la ticketera.
  - Se pregunta cada 2 s. Cuando el socio confirma, muestra nombre, número y club para que el cajero vea que es quien tiene delante.
  - «Aplicar cupón» deja la línea por el descuento que dijo ClubPay; el resto se cobra con otra forma de pago.
  - Cancelar con el QR pendiente cancela el cobro en ClubPay. Si ya se había aplicado, se aplica igual, porque no se puede anular.
  - El servidor vuelve a comprobar el cupón al recibir la venta: tiene que estar aplicado, por el mismo importe y usado una sola vez.
- **Ventas (relajado, por mail):**
  - El QR no va en el mail porque vence a los 3 minutos.
  - En el portal del presupuesto, el cliente toca «Usar mi descuento ClubPay» cuando lo va a usar. Ve el QR o el link para abrir la app si está en el teléfono. Al confirmar, la página muestra Total / Descuento / A pagar.
  - Desde el pedido, el botón «Descuento ClubPay» abre la misma ventana con QR, «Ver si ya escaneó», «Imprimir QR» (PDF) y «Cancelar el cobro».
  - Al publicar la factura, el cupón entra como pago parcial con el diario «Cupones ClubPay».
  - Si el total del pedido cambia después de aplicar el descuento, el pedido lo avisa.
- Lista «Cobros ClubPay» en Ventas y en Punto de Venta.

**Probado en `nexo_test_ci` con el simulador:**
- **Caja:** orden «Jure Hnos SRL - 000001» con Efectivo $17.983,48 + Cupón ClubPay $4.495,87, vinculada al cobro `chg_sim_89ce0bf4bdaa`.
- **Portal:** S00203 (descuento −$2.975,21, a pagar $11.900,82), sin errores en la consola.
- **Ventana del pedido:** S00204 y S00231.
- **Factura:** por shell y sin guardar, FA-B 00001-00000001 por $14.876,03 quedó con saldo $11.900,82 y el pago del diario «Cupones ClubPay».
- **PDF del QR:** generado por shell.

**Estado de la base de prueba (24/11/2026):** clave real de ClubPay cargada por German, simulador **apagado**, «Cupón ClubPay» sumado a las dos cajas de Jure Hnos SRL.

**Clave real:** al guardarla daba «ClubPay tardó demasiado en responder». La causa era que Odoo y ClubPay están en la misma LAN y el servidor no llega a la IP pública de ClubPay (NAT de horquilla). German cargó `api.clubpay.com.ar` en el DNS del router con la IP interna, y desde ahí funciona. Lo mismo puede pasarles a otros servidores de esa red.

**Sin probar:**
- «Imprimir QR» en una ticketera de la caja.
- **Anulación:** ClubPay no tiene cómo anular un descuento aplicado. La caja no deja usar ClubPay en devoluciones.
- **Cómo se concilia el diario «Cupones ClubPay»** con lo que ClubPay le paga al comercio. PENDIENTE DE VALIDAR con German.

## Mercado Pago a través de ClubPay (`nexo_clubpay`, 24/11/2026)
Contrato `/pos/payments` de ClubPay (ver API_CONTRACTS). Decisiones en DEC-024. Odoo no tiene credenciales de Mercado Pago: usa el Mercado Pago que el comercio conectó en ClubPay.

**Configuración:** Ajustes → ClubPay → «Cobrar con Mercado Pago a través de ClubPay». Después de guardar, «Crear y sumar a las cajas» crea:
- el diario «Mercado Pago (ClubPay)» (CPMP);
- la forma de pago de la caja «Mercado Pago (ClubPay)» (terminal `clubpay_mp`), sumada a las cajas;
- el proveedor de pago «Mercado Pago (ClubPay)» del portal, publicado y con ese diario.

**IMPLEMENTADO:**
- **Caja:**
  - Con la forma de pago «Mercado Pago (ClubPay)» sale un QR de mostrador por el importe de la línea. Sirve sin descuento.
  - Con el cupón aplicado, «Cobrar el resto con Mercado Pago» sigue directo al QR por el resto.
  - Se pregunta cada 2 s. Al pagarse, la línea queda hecha y la venta se valida sola si está cubierta.
  - Un rechazo no cierra la ventana: se puede probar otra tarjeta con el mismo QR.
  - Cancelar o «Cobrar con otro medio» cancela el cobro en ClubPay. Si ClubPay contesta que ya se pagó, la venta se cierra con ese pago.
  - Las otras opciones son «Imprimir QR» (ticket «Pagá con Mercado Pago») y «Generar otro».
  - El servidor vuelve a comprobar el pago al recibir la venta: pagado, por el mismo importe y usado una sola vez.
- **Ventas (portal):**
  - El cliente aplica el cupón y paga el resto con «Mercado Pago (ClubPay)». Va al link de pago de ClubPay y vuelve a Odoo.
  - El pedido se confirma y el pago entra al diario «Mercado Pago (ClubPay)».
  - También sirve sin cupón.
- **Devoluciones:**
  - En Ventas, desde la transacción, con el flujo estándar de Odoo.
  - En la caja, desde «Cobros con Mercado Pago» → «Devolver» (solo administradores).
- **Lista «Cobros con Mercado Pago»** en Ventas y en Punto de Venta.
- **Cron cada 2 min:** actualiza los cobros abiertos de las últimas 48 h. A un pago sin venta después de 30 min le crea una actividad a un administrador.

**Probado en `nexo_test_ci` con el simulador (24/11/2026):**
- **Caja:** total $4.134,23. El cupón aplicó $826,85 y el QR de Mercado Pago cobró $3.307,38. La venta «Jure Hnos SRL - 000001» se validó sola, con las dos líneas vinculadas a sus cobros.
- **Portal:** S00263, por $41.322,30.
  - Descuento de $8.000; Mercado Pago cobró $33.322,30.
  - El pedido quedó confirmado, con `amount_paid` 41.322,30 y el pago PCPMP/2026/00002.
  - La factura (por shell, sin guardar) quedó con saldo 0: $33.322,30 de Mercado Pago y $8.000 de cupones.
- **Tests:** 12 casos de Mercado Pago dentro de los 71. Cubren QR y pago, rechazo y después pago, cancelación con el pago ya hecho, devolución, pago sin venta, caja y portal.

**Datos que quedaron en la base de prueba:**
- S00263 confirmado y pagado con el simulador.
- La venta de caja 000001.
- Una sesión de caja abierta en la caja 1.

**Sin probar / riesgos:**
- **Un cobro real de Mercado Pago.** Crea un cobro real en el Mercado Pago de Jure, así que lo decide y lo hace German.
- **Una sola caja de Mercado Pago por comercio** en ClubPay, compartida con NexoPOS. No está confirmado qué pasa con dos QR de mostrador abiertos a la vez. PENDIENTE DE VALIDAR con ClubPay.
- **Qué apps leen el QR:** el ticket dice «Mercado Pago o tu billetera». Que la app de ClubPay lo lea no está verificado.
- **`web.base.url`:** la vuelta del portal usa la URL base de Odoo. En producción tiene que ser la pública. En `nexo_test_ci` es `localhost:8098`.
- **Pagos sin venta:** Odoo no los devuelve solo, NexoPOS sí. PENDIENTE DE VALIDAR con German.
- **El portal muestra el cupón como «Pagado»**, porque usa la etiqueta estándar de `amount_paid`.
- **La conciliación de los diarios «Mercado Pago (ClubPay)» y «Cupones ClubPay»** con lo que ClubPay o Mercado Pago le pagan al comercio. PENDIENTE DE VALIDAR con German.

## Libreta del cliente en ClubPay (`nexo_clubpay`, 24/11/2026) — PARCIAL
Lo mismo que hace NexoPOS con su cuenta corriente, sobre el saldo a cobrar de Contabilidad. Decisiones en DEC-025; contrato en API_CONTRACTS («ClubPay — libreta»).

**Configuración:** Ajustes → ClubPay → «Libreta en ClubPay». Pide la clave de ClubPay o el simulador.

**En el contacto** (empresa o persona sin empresa), solapa «Libreta ClubPay»:
- Campo **DNI** (solo números, 7 u 8 dígitos) y botón **«Enviar invitación»**.
- Respuestas posibles: «Invitación enviada» (propuesta), «Sin cuenta en ClubPay», «Vinculada» o «Rechazada por la persona». Con un rechazo, el chatter avisa que revisen el DNI, y aparece «Corregir el DNI y volver a invitar».
- «Ver si aceptó» consulta sin mandar el DNI. El cron pregunta solo cada 2 minutos por las invitaciones de los últimos 30 días.
- Botón «Libreta ClubPay» con los movimientos enviados. También en Contabilidad → Clientes.

**Qué ve la persona en la app:**
- Al aceptar, un `ajuste` «Saldo al vincular la libreta» con todo lo que debe ese día.
- Desde ahí, cada factura (`compra`), cada nota de crédito (`devolucion`), cada cobro (`pago`) y cada ajuste.
- Se encola al publicar y sale con el cron cada 2 minutos. Reintenta a 1, 5, 15 y 60 min, después cada 6 h, y a los 12 intentos queda «No se pudo enviar» con «Reintentar».
- Una factura que se pasa a borrador o se borra después de avisarla manda un `ajuste` que la anula.

**Pagos desde la app:**
- Se registran como cobro del cliente en el diario «Mercado Pago (ClubPay)».
- Se imputan primero a la factura más vieja, y lo que sobra queda a favor del cliente.
- No se le devuelven a ClubPay como movimiento.
- **Todavía no llegan:** ClubPay avisa solo a NexoPOS. Odoo pregunta `GET /pos/account/payments` y sigue de largo con el 404. ClubPay la confirmó el 24/11/2026 (`respuesta-libreta-odoo.md`) y avisa cuando la despliegue. Mientras tanto, el comercio registra estos pagos a mano.
- Odoo sigue las páginas (`hay_mas`), descarta los repetidos del margen de 2 minutos, saltea los pagos de NexoPOS (`CLI-`) y guarda el número de pago de Mercado Pago.

**Simulador:** un DNI terminado en 0 da «sin cuenta», cualquier otro queda propuesto. En el contacto aparecen «Simular que aceptó» y «Simular pago desde la app» (por todo lo que debe, o $10.000).

**Sin verificar:**
- **Tests en Odoo 20 (24/11/2026):** se instaló en `nexo_test_ci` y pasan (hoy 15 de la libreta, 86 en total con los otros módulos). Al correrlos aparecieron y se corrigieron:
  - `_register` es un atributo reservado de los modelos de Odoo: el método de los pagos desde la app pasó a `_record_app_payment`.
  - Una anulación ya avisada se volvía a anular en cada corrida del cron, y lo mismo le habría pasado al saldo inicial. Ahora solo se anulan movimientos que salieron de un apunte (`-aml-`) y que no son anulaciones.
  - En los tests: el DNI va en `additional_identifiers` (`AR_DNI`), porque Odoo 20 no tiene `l10n_latam_base`. Además, el patch de ClubPay se hace una sola vez.
- **Sin probar en el navegador:** la solapa del contacto, «Enviar invitación» y los botones del simulador.
- Partes de Odoo 20 que se usan sin haberlas mirado en el `addons/` instalado: el `button_box` del formulario de contacto, `account.menu_finance_receivables`, `account.payment.register` con importe parcial y el campo `memo` del pago (con alternativa a `ref`).
- Una venta de caja facturada y pagada en efectivo a un cliente con libreta aparece en la app como compra y pago del mismo día. El saldo queda bien. PENDIENTE DE VALIDAR con German si se filtra.
- **`ODOO-<id>`:** ClubPay lo acepta. Si la persona ya tiene la libreta de NexoPOS en el mismo comercio, la invitación da 409 y Odoo muestra el motivo de ClubPay (probado con ClubPay simulado). El comercio lleva la libreta en un solo sistema.
- **Prueba con ClubPay real:** cuando ClubPay avise que desplegó. Usa la clave de Jure en `nexo_test_ci` y tiene cinco pasos; el quinto es invitar como `ODOO-…` a alguien que ya tiene una `CLI-` y ver el 409. Toca datos reales de ClubPay, así que la hace German.

## Compras vía NexoB2B (23/11/2026)
**IMPLEMENTADO en código, con tests:**
- «+ Al pedido» en el catálogo, por presentación y solo con alta aceptada, arma o completa un borrador de pedido de compra del mayorista.
- Cada línea guarda su presentación (`nexo_seller_id`). Si hay dos presentaciones con la misma unidad, la pide (DEC-014).
- «Enviar a NexoB2B» en el pedido de compra abre una ventana con los medios de pago y transportes reales del mayorista, y manda `POST /store/ordenes`. El precio final lo pone NexoB2B y Odoo lo copia.
- Al abrir la ventana de envío se releen los precios de NexoB2B, por EAN y mayorista. Si cambiaron, se actualizan la línea y la presentación, la ventana lo muestra («$50.000 → $55.000») y queda una nota. Si una presentación ya no está, avisa. Si NexoB2B no responde, se envía con el último precio conocido. Probado en el navegador con P00091 (base de prueba, sin enviar).
- Si no hay respuesta, el pedido queda «incierto» y, antes de reenviar, se busca por la referencia en `notas` (DEC-013).
- El estado se trae cada 5 minutos (cron) o con «Actualizar estado NexoB2B». Cada cambio deja una nota que avisa al comprador, con bultos, peso, transporte, guía y mensaje del mayorista.
- El pedido se confirma en Odoo cuando el mayorista lo confirma (DEC-012), y se cancela si el mayorista lo cancela.
- Si vuelve devuelto, se crea una actividad. Al pasar a entregado, si queda recepción pendiente, se crea la actividad «validá la recepción».
- Los documentos nuevos (remitos, facturas) quedan como enlaces en el chatter.
- Cancelar desde Odoo llama a NexoB2B solo en `cargada` o `devuelto`.
- Tests: 14 en `nexo_b2b/tests/test_orders.py`, con NexoB2B simulado.

**Probado en el navegador contra la API real, sin enviar:**
- «+ Al pedido» creó P00014: Las Perdices CAJA a $55.000 + IVA = $66.550, igual que el portal.
- La ventana de envío cargó los medios de pago reales de Truglio y se cerró sin enviar.

**Probado con un envío real (German, 23/11/2026):**
- P00014 → **ORD-00055 a Truglio Hnos SRL** (no a «Mayorista de Prueba», como decía DEC-015). Transferencia, 1 CAJA Las Perdices, $55.000 neto y $66.550 final: el mismo precio que tenía Odoo.
- El mayorista la confirmó en menos de 2 minutos. Con «Actualizar estado» el pedido pasó a Pedido de compra y se creó la recepción.
- Se encontró y corrigió un error: NexoB2B manda los importes como texto (`"0.00"`) y Odoo los sumaba como texto (`ValueError: '0.000.00'`). Ahora se convierten, y el NexoB2B simulado de los tests los manda igual que el real.

**Sin probar:**
- Armando, listo, en transporte, entregado, documentos y cancelación, con datos reales.
- El cron en el servicio real: necesita `--max-cron-threads` mayor que 0. El servidor de prueba corre con 0, así que ahí se usa el botón.
- Recibir la mercadería con Inventario: se instaló, pero no se validó una recepción.

## Qué hace el código (desafío 1)
- **Ajustes → NexoB2B:** URL, clave publicable, estado de la sesión, «Iniciar sesión», «Probar conexión», «Cerrar sesión», mapeos y registro.
- **Login:** la contraseña viaja una sola vez al servidor de Odoo y de ahí a NexoB2B. No se guarda, no se registra y la pantalla la borra. Solo se guarda el token con su vencimiento, en `nexo.token` (solo administradores). Si el usuario es del equipo y no tiene `comprar`, la sesión se rechaza con un mensaje claro.
- **Compras → Pedidos → Catálogo NexoB2B**, igual que el portal del comercio en NexoB2B (`storefront/…/comercio/productos/page.tsx`):
  - Búsqueda por texto, marca o EAN, con pausa de 400 ms y descarte de respuestas viejas.
  - Filtros en «chips» por pasillo, rubro y subrubro, que salen de `/store/taxonomia`.
  - «Mostrar sin stock» (lo filtra el servidor) y «Solo mayoristas con alta aprobada». Este último se filtra sobre lo ya traído, igual que en el portal, porque NexoB2B no tiene ese filtro.
  - Vistas Lista, Chica y Grande, contador «N de total», páginas de 50 y «Cargar más».
  - Precios sin decimales, como el portal.
  - Se manda el `comercio_id` del token, **siempre junto con el token**, para que NexoB2B filtre por zona y diga `tiene_alta`.
- **«Agregar»** (por mayorista): vuelve a leer el producto de NexoB2B en el servidor y no toma nada del navegador. Si la alícuota no está confirmada o la categoría no está mapeada, pregunta en la misma pantalla y lo recuerda. Crea el producto (EAN en `barcode`, marca, foto, categoría, impuestos, Unidades), el proveedor (`nexo_mayorista_id`) y una línea de proveedor por presentación, con la unidad «x12 Unidades» y el precio tal cual. El `ean_propio` va a `product.uom`.
- **Idempotencia:** se reconoce el producto por `nexo_b2b_id` o por EAN, y la línea por `nexo_presentacion_id`. Repetir actualiza, no duplica. Si el producto ya era del comercio, no se pisa nada suyo: solo se vincula y se suman las líneas.
- **Diagnóstico:** cada llamada lleva `X-Nexo-Connector: odoo/<versión del módulo>` y `X-Request-Id`. `nexo.log` guarda método, ruta, estado, duración e id de solicitud, nunca cabeceras ni cuerpos. «Descargar diagnóstico» arma un JSON con las versiones, la configuración (la clave como `***`), la prueba de conexión y las últimas 200 llamadas.

- **Compras → Pedidos → Mayoristas NexoB2B**, igual que «Descubrí mayoristas» del portal del comercio (`storefront/…/comercio/mayoristas/page.tsx`). IMPLEMENTADO y probado con la API real el 23/11/2026:
  - Búsqueda por nombre, rubro o ciudad, con pausa de 400 ms.
  - Radio de 25 / 50 / 100 / 200 km o todo el país, desde la ubicación del comercio en NexoB2B. Sin ubicación se muestran todos, con aviso.
  - Chips de rubros de mayorista. Por defecto vienen marcados los que compra el tipo de comercio (`/store/tipos-comercio`). Están «Todos los rubros» y «Volver a mis rubros», y si con sus rubros no hay nadie se muestran todos los de la zona, con aviso.
  - Orden por cercanía, cantidad de productos o nombre.
  - Tarjeta con portada, logo, distancia, rubro y ciudad, productos publicados y tres fotos de muestra.
  - Estado del alta («Solicitud pendiente», «Habilitado», «Rechazado»). Con el alta aceptada, muestra el vendedor asignado y su contacto.
  - «Ya es proveedor en Odoo» si existe el contacto con ese `nexo_mayorista_id`.
  - **«Ver catálogo»** abre el catálogo filtrado por ese mayorista (`mayorista_id`). Probado: Truglio Hnos SRL, 3 de 3 productos.
  - **«Pedir alta»** abre una confirmación con un mensaje opcional y llama a `POST /store/solicitudes`. Solo lo ve habilitado un responsable de Compras (`purchase.group_purchase_manager`). **El envío no se probó contra NexoB2B real**, porque crea una solicitud real; está cubierto por los tests con NexoB2B simulado. El modal se abrió y se canceló.
## Lo que se verificó en el Odoo 20 real (servidor `odoo-linware`)
Leído del `addons/` instalado, no por analogía con versiones anteriores.

- **Paquete:** `odoo 20.0+e.20260927`, Enterprise, instalado como paquete `.deb`. `release.py`: `version_info = (20, 0, 0, FINAL, 0, '')`. Python 3.12.3, `requests` 2.31.0.
- **Servicio:** `odoo.service` con `--config /etc/odoo/odoo.conf`, que solo lee el usuario `odoo`. Addons en `/usr/lib/python3/dist-packages/odoo/addons`, donde ya está el módulo propio `linware_web_lead`.
- **Manifest:** versión `20.0.x.y.z`. Vistas de lista: `<list>`.
- **Permisos:** `ir.model.access.csv` **ya no existe**. Ahora es `security/ir.access.csv`, con las columnas `id,name,model_id,group_id/id,operation,domain`. `operation` es un subconjunto de `crud`, y una fila sin grupo y con dominio es una restricción (reemplaza a `ir.rule`).
- **`ir.config_parameter`:** `get_param` y `set_param` **ya no existen**. Ahora son `get_str`, `get_bool`, `get_int`, `get_float` y `set_*`.
- **ORM:** las restricciones van con `models.Constraint(...)` y los índices con `models.Index`. Se importa `from odoo import _, api, fields, models, Command`.
- **JS:** OWL **3.0.0-alpha.49**. Usa `props = useProps(...)` con tipos `t.*`, `proxy(...)` en lugar de `useState`, `t-model.proxy` y `this.` explícito en las plantillas. Las acciones de cliente se registran en `registry.category("actions")` y `useDebounced` viene de `@web/core/utils/timing`.
- **Impuestos del producto:** `taxes_id` (venta) y `supplier_taxes_id` (compra).
- **`product.template`:** `barcode`, `categ_id` (opcional), `type` (`consu`/`service`/`combo`), `is_storable`, `purchase_ok`, `uom_id`. **No existe `uom_po_id`.**
- **No existe `product.packaging`:** los empaques son unidades `uom.uom` sin categorías, con `relative_uom_id` y `relative_factor`. `product.uom` vincula producto, unidad y `barcode` (único). Grupo `uom.group_uom`.
- **`product.supplierinfo`:** `uom_id` (requerido), y `price` y `min_qty` expresados en esa unidad.
- **Marca:** no existe en `product`.
- **`l10n_ar`:** instalada en la base de prueba el 23/11/2026, con el plan `ar_ri` (Responsable Inscripto), país AR y moneda ARS.
  - En Odoo 20, `account.chart.template.try_loading('ar_ri', company)` instala el módulo y, si la compañía no tiene asientos, borra el plan anterior.
  - El mapeo de alícuotas quedó propuesto, sin confirmar: 10,5 % → VAT 10.5% (venta y compra), 21 % → VAT 21%, 27 % → VAT 27%.
  - 0 % queda sin propuesta, porque hay varios impuestos de 0 % (VAT 0%, 0% EXEMPT, 0% NT, 0% NA). **PENDIENTE DE VALIDAR** con NexoB2B qué significa su 0 %: exento, no gravado o gravado al 0 %.
  - Los tests de `nexo_b2b` siguen pasando con `l10n_ar` (16 de 16).
- Los campos binarios (por ejemplo `image_1920`) reciben `BinaryValue` (`odoo.tools.binary.BinaryBytes`), no bytes en base64. Con bytes da `TypeError: use BinaryValue instead of bytes` (encontrado al probar «Agregar» con la API real, 23/11/2026).
- Las plantillas de OWL 3 no ven los globales de JS (`String`, `Number`, `Math`…): `String(x)` da `ctx.String is not a function`. Se resuelve con un método del componente.
- Cambiar la localización fiscal borra impuestos. Por eso el mapeo de alícuotas usa `ondelete='set null'`: si se borra un impuesto, la fila queda sin él y se vuelve a preguntar.

## Sin verificar / riesgos conocidos
- **Presentación «CAJA» con factor 1** (Las Perdices, Truglio): Odoo la toma como una unidad. Pedido en `docs/PARA-NEXOB2B_de-NEXO-ODOO_ordenes-desde-conectores.md`.
- **`reenviar` de NexoB2B toma el precio del cliente:** pedido en `docs/PARA-NEXOB2B_de-NEXO-ODOO_reenviar-toma-precio-del-cliente.md`.
- **Un alta rechazada no se puede volver a pedir:** NexoB2B devuelve 409 si ya hay una solicitud, en cualquier estado. El portal se comporta igual. PENDIENTE DE VALIDAR con NexoB2B si es a propósito.
- **Secreto JWT por defecto en NexoB2B:** pedido en `docs/PARA-NEXOB2B_de-NEXO-ODOO_jwt-secret-por-defecto.md`.
- **«Ver catálogo» desde Mayoristas:** si se recarga la página, el filtro por mayorista se pierde, porque los parámetros de la acción no van en la URL.
- La interfaz con datos: OWL 3 es alfa, y la lista de productos y el panel de «Agregar» todavía no se dibujaron.
- En Ajustes, tocar «Probar conexión» con cambios sin guardar abre el diálogo de Odoo, y después de guardar la página se recarga sin correr la prueba. Hay que tocarlo de nuevo. Es el comportamiento estándar de Ajustes, pero conviene tenerlo en cuenta.
- La forma real de las respuestas de NexoB2B: se armó leyendo su código, no llamándolo.
- Las cuentas contables y el método de valuación que heredan las categorías nuevas (DEC-007).
- Los valores de `unidad_base` del maestro: se mapean `unidad`, `kg` y `litro`, y el resto va en Unidades con aviso.
- La moneda: se asume que los precios de NexoB2B están en la moneda de la compañía (ARS).
- La foto se descarga de la URL que manda NexoB2B (máximo 5 MB, solo `image/*`). Si NexoB2B mandara una URL interna, el servidor de Odoo la pediría igual. Es aceptable mientras la fuente sea NexoB2B.
- **Filtro por pasillo:** muchos productos del maestro no tienen `pasillo_id` (por ejemplo, Tecnología da 0 y su rubro Informática, 803). Es un tema de datos de NexoB2B, que el portal también tiene. Pedido en `docs/PARA-NEXOB2B_de-NEXO-ODOO_productos-sin-pasillo.md`. El conector no lo disimula.
- **«Solo con alta»** filtra solo la página ya cargada: con pocas altas puede mostrar menos de 50 aunque haya más. El portal se comporta igual.
- **Algunos EAN del maestro no son EAN** (por ejemplo, `NXB-1000816`). Ese valor iría tal cual al `barcode` de Odoo. PENDIENTE DE VALIDAR con NexoB2B si es intencional.
- **Nombres de presentación «1» o «unidad»:** son datos de NexoB2B y se muestran tal cual.
- `sku` y `cantidad_minima` no vienen en `/store/productos`: quedan vacíos (pedido a NexoB2B en `docs/`).
