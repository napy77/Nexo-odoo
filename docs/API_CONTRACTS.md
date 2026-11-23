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
