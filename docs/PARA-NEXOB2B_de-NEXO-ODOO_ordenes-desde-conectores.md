# Para el equipo de NexoB2B. De Nexo-odoo, 23 de noviembre de 2026

El conector de Odoo ya arma y envía pedidos con `POST /store/ordenes` y sigue su estado. Para hacerlo bien nos faltan cuatro cosas. Ninguna bloquea; hoy cada una se resuelve con un parche del lado de Odoo.

## 1. Un aviso al comercio cuando cambia el estado del pedido
**Hoy:** al cambiar el estado, NexoB2B le manda al comercio un email y un push al celular (`notificarCambioEstado` en `lib/notifications.ts`). El mayorista, en cambio, tiene un webhook (`dispararWebhookMayorista`), pero el comercio no tiene nada equivalente. Odoo consulta `GET /store/ordenes/:id` de cada pedido abierto cada 5 minutos.

**Pedimos** una de estas dos:
- Un webhook del comercio, como el del mayorista, con `orden.estado_cambiado` y el id de la orden. Con eso Odoo deja de consultar.
- O `GET /store/ordenes?actualizadas_desde=<fecha>`, que devuelva solo lo que cambió. Hoy `GET /store/ordenes` devuelve **todas** las órdenes con sus ítems, sin paginar.

## 2. Una clave de idempotencia o referencia externa en `POST /store/ordenes`
**Hoy:** si la respuesta se pierde (timeout, red), no hay forma segura de saber si la orden se creó. Odoo pone «Pedido de compra Odoo <número>» en la primera línea de `notas`, y antes de reenviar la busca en `GET /store/ordenes`.

**Pedimos** un campo `referencia_externa` (único por comercio) o una cabecera `Idempotency-Key`. Si ya existe, que devuelva la orden existente en lugar de crear otra.

## 3. Datos de presentaciones con factor equivocado
«Vino Reserva Malbec 750 Ml Las Perdices» (EAN 12263774003), mayorista Truglio Hnos SRL: «CAJA» tiene `factor` 1, igual que «BOTELLA». La caja cuesta $55.000 y la botella $9.500, así que el factor debería ser 6. Con factor 1, Odoo recibe la caja como si fuera una unidad: el stock y el costo por unidad quedan mal.

**Pedimos** revisar el factor en los datos y, si se puede, validar al cargar que una presentación más cara que la unidad no tenga factor 1. **No sabemos** cuántos productos hay así.

## 4. Un entorno de pruebas con datos falsos
Ya estaba previsto en el brief. Mientras no exista, la primera prueba real la hace German a mano: un pedido de su comercio a «Mayorista de Prueba», que después se cancela (DEC-015 de Nexo-odoo).

## Prioridad
Media: 1 y 2. Baja: 3, aunque afecta a cualquier comercio que compre esa caja. El 4 ya estaba acordado.
