# Para el equipo de NexoB2B. De Nexo-odoo, 23 de noviembre de 2026

## Qué vimos
`POST /store/ordenes/:id/reenviar` (`apps/backend/src/api/store/ordenes/[id]/reenviar/route.ts`) arma los ítems con `precio_unitario`, `alicuota_iva`, `nombre`, `sku` y `ean` **tal como llegan en el cuerpo del pedido**, y con eso recalcula los totales.

`POST /store/ordenes` ya no lo permite. Su comentario lo explica: «el precio no puede venir del cliente», y hoy resuelve todo desde `presentacion_id`. Reenviar quedó con la lógica vieja.

## Por qué importa
Un comercio puede esperar a que el mayorista le devuelva un pedido y reenviarlo con el precio que quiera, por ejemplo $1. Si el mayorista confirma sin mirar el detalle, el pedido queda con ese precio. Además, los ítems reenviados pierden `presentacion_id` y `listing_id`.

## Qué proponemos
Que reenviar use la misma resolución que `POST /store/ordenes` (`resolverItemDesdePresentacion`): recibir `[{presentacion_id, cantidad}]` y tomar precio, IVA y nombre del servidor, con la lista del comercio aplicada.

## Qué hace el conector de Odoo
No usa esta ruta. Si un pedido vuelve devuelto, sugiere cancelarlo y armar uno nuevo (DEC-012 de Nexo-odoo).

## Prioridad
Alta: se puede usar para pagar menos de lo que corresponde.
