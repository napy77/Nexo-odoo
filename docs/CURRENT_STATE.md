# Estado actual — Nexo-odoo (rama 20.0)

Actualizado al 23/11/2026 (tarde). Se lee al empezar cada tarea y se actualiza al terminarla.

## Resumen
| Tema | Estado |
|---|---|
| `nexo_core`: cliente HTTP, errores, sesión (token), registro y diagnóstico | PARCIAL: instalado y con tests pasando en una base de prueba; sin API real |
| `nexo_b2b`: Ajustes, login, catálogo (OWL), «Agregar», mapeos | PARCIAL: instalado y con tests pasando en una base de prueba; pantallas vistas en el navegador; sin API real |
| Tests con respuestas simuladas (`nexo_core/tests`, `nexo_b2b/tests`) | IMPLEMENTADO: **58 tests, 0 fallas** (23/11/2026, base `nexo_test_ci`) |
| Login y búsqueda contra la API real de NexoB2B (`https://nexob2b.app`) | IMPLEMENTADO: probado por German con Jure Hnos SRL (dueño), 3879 productos |
| «Agregar» contra la API real | IMPLEMENTADO: probado por German el 23/11/2026 con «Accesorios Lona Pelopincho 1130» (Rivera Hogar) |
| Compras vía NexoB2B (pedido de compra → orden, estado y avisos) | PARCIAL: envío real y confirmación probados (ORD-00055); faltan armado, transporte, entrega y recepción |
| `nexo_pos`: precios y stock de Odoo en NexoPOS (base de NexoTienda) | PARCIAL: 14 tests; lectura e importación probadas con Jure; escritura sin probar |
| Pedidos de NexoTienda en la caja de Odoo | PROPUESTO: `docs/PROPUESTA-caja-odoo-y-nexotienda.md`; depende de NexoPOS (pedido enviado) |
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
