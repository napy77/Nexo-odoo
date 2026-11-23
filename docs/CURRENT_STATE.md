# Estado actual — Nexo-odoo (rama 20.0)

Actualizado al 23/11/2026 (tarde). Se lee al empezar cada tarea y se actualiza al terminarla.

## Resumen
| Tema | Estado |
|---|---|
| `nexo_core`: cliente HTTP, errores, sesión (token), registro y diagnóstico | PARCIAL: instalado y con tests pasando en una base de prueba; sin API real |
| `nexo_b2b`: Ajustes, login, catálogo (OWL), «Agregar», mapeos | PARCIAL: instalado y con tests pasando en una base de prueba; pantallas vistas en el navegador; sin API real |
| Tests con respuestas simuladas (`nexo_core/tests`, `nexo_b2b/tests`) | IMPLEMENTADO: **23 tests, 0 fallas** (23/11/2026, base `nexo_test_ci`) |
| Login y búsqueda contra la API real de NexoB2B (`https://nexob2b.app`) | IMPLEMENTADO: probado por German con Jure Hnos SRL (dueño), 3879 productos |
| «Agregar» contra la API real | IMPLEMENTADO: probado por German el 23/11/2026 con «Accesorios Lona Pelopincho 1130» (Rivera Hogar) |
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
- **`l10n_ar`:** disponible. **PENDIENTE DE VALIDAR:** si está instalada en la base de prueba.
- Los campos binarios (por ejemplo `image_1920`) reciben `BinaryValue` (`odoo.tools.binary.BinaryBytes`), no bytes en base64. Con bytes da `TypeError: use BinaryValue instead of bytes` (encontrado al probar «Agregar» con la API real, 23/11/2026).
- Las plantillas de OWL 3 no ven los globales de JS (`String`, `Number`, `Math`…): `String(x)` da `ctx.String is not a function`. Se resuelve con un método del componente.
- Cambiar la localización fiscal borra impuestos. Por eso el mapeo de alícuotas usa `ondelete='set null'`: si se borra un impuesto, la fila queda sin él y se vuelve a preguntar.

## Sin verificar / riesgos conocidos
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
