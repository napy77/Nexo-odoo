# Para el equipo de NexoB2B. De Nexo-odoo, 4 de noviembre de 2026

## Qué necesitamos
Que `GET /store/productos` incluya en cada `mayoristas[].presentaciones[]`:
- `sku`: el código del mayorista (`producto_mayorista_presentacion.sku`).
- `cantidad_minima`: la cantidad mínima de compra, si existe en el modelo. La carga `POST /v1/mayorista/sync`.

## Por qué
El conector de Odoo crea, al tocar «Agregar», la línea de proveedor del producto (precio, código del proveedor y cantidad mínima). Hoy el catálogo no devuelve `sku` ni `cantidad_minima`, aunque el contrato del producto (§6.2 del brief) los menciona y `sku` existe en la base (se usa en `api/store/ordenes/route.ts`).
Sin `sku`, la orden de compra de Odoo no puede mostrar el código del mayorista. En el desafío 3 eso hace falta para que el mayorista reconozca lo que se le pide.

## Prioridad
Baja para el desafío 1: mientras tanto, el conector deja esos datos vacíos y no inventa valores. Hace falta antes del desafío 3 (órdenes de compra).

## Otra cosa del contrato (corrección de la documentación, no un cambio de código)
La documentación de `/store/productos` no coincide con lo que devuelve el código hoy:
- Los nombres de la taxonomía son `pasillo_nombre`, `rubro_nombre` y `subrubro_nombre`.
- Las presentaciones vienen dentro de `mayoristas[]`.
- `id` es el de `producto_mayorista_presentacion` y `presentacion_id` es el del maestro.
- Los parámetros son `q`, `page`, `pageSize` (máximo 100) y `con_stock=1`. Si no se manda `con_stock`, el servidor no filtra por stock.
- Los tokens duran 30 días, tanto para el dueño como para los usuarios del equipo.
- Un usuario desactivado devuelve 403 con `{ error }`, no 401 con `codigo`.
