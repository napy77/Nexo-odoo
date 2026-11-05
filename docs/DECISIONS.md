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
- **PROPUESTO, pendiente de leer Odoo 20:** usar el mecanismo nativo de Odoo (unidad de compra o `product.packaging`) en lugar de simularlo con `min_qty`. Se decide con German después de inspeccionar el código real.
- La conversión de caja a unidades al recibir la mercadería corresponde al desafío 3.
- Candidata a regla de todo el ecosistema (la oficializa nexo-ecosystem).

## DEC-009 — Marca en un campo propio `nexo_marca`
- **Motivo:** Odoo base no tiene marca, y no queremos depender de módulos de terceros.

## DEC-010 — Tests con respuestas simuladas; la API real solo a mano y de lectura
- Los tests de Odoo simulan las respuestas HTTP de NexoB2B y cubren el mapeo, la idempotencia y los errores. Contra la API real solo se prueba a mano y en modo lectura.
