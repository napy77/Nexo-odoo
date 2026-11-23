# Para el equipo de NexoPOS: pedidos de NexoTienda en la API para ERP

De: Nexo-odoo · 23/11/2026 · Prioridad: **alta** (lo pide German)

## Qué necesitamos, en una línea
Que el ERP del comercio pueda **ver y mover los pedidos de NexoTienda** con la misma clave `npos_…` que ya usa para `/api/erp/v1/productos`, `/precios` y `/stock`.

## Para qué
- German decidió que un comercio con Odoo cobre en el **Punto de Venta de Odoo** y atienda ahí los pedidos de NexoTienda.
- NexoTienda no cambia: sus pedidos siguen entrando a NexoPOS por `POST /v1/orders`, y NexoPOS sigue siendo el dueño del pedido.
- Lo que falta es que Odoo los vea y los mueva. Hoy `/api/erp/v1` no tiene pedidos, y `GET /ventas` los trae recién cuando están entregados.
- Probado el 23/11 con Jure Hnos SRL: el pedido P-0962559F aparece en nexopos.app/pedidos y no hay forma de leerlo con la clave del ERP.

## Autenticación: la clave del ERP, sin sesión
- **Misma clave y mismo middleware** que el resto de la API para ERP: `Authorization: Bearer npos_…`, `requiereClaveErp`, con el comercio sacado de la clave (`req.erp.commerceId`).
- **No vence.** Vale hasta que el comerciante la revoca en Configuración → Conectar tu sistema, como hoy con `/productos`. No hay login, ni token de 12 horas, ni renovación.
- **Odoo no va a usar `/api/auth/login`.** Ese camino necesita email y contraseña cada 12 horas, y para que la caja ande sola Odoo tendría que guardar la contraseña de la cuenta de NexoB2B. No queremos guardarla.
- Si quieren limitar qué abre cada clave, por ejemplo una clave sin pedidos, nos sirve igual. Que el 403 diga qué permiso falta.

## Qué pedimos

### 1. `GET /api/erp/v1/pedidos?actualizados_desde=<ISO>`
- Los pedidos de tienda del comercio de la clave, **en cualquier estado**, cuyo `updated_at` sea posterior a esa fecha, ordenados por `updated_at`.
- Paginado como `/ventas`: `hasta` (el `updated_at` del último) y `hay_mas`. Odoo manda el `hasta` como `actualizados_desde` en la siguiente llamada.
- Sin `actualizados_desde`: los que están en curso (`recibido`, `aceptado`, `listo`, `en_camino`).
- Centavos enteros, como el resto de la API.

```json
{
  "pedidos": [{
    "code": "P-0962559F",
    "status": "recibido",
    "creado": "2026-11-23T20:41:00.000Z",
    "actualizado": "2026-11-23T20:41:00.000Z",
    "retiro": { "tipo": "retiro", "franja": "Hoy 18 a 20 h", "direccion": null },
    "pago": { "medio": "efectivo_entrega", "estado": "pendiente", "id": null },
    "contacto": { "nombre": "German Yovan", "telefono": "3515630140" },
    "cuenta_id": null,
    "listo_para": null,
    "motivo_cancelacion": null,
    "revision": null,
    "lineas": [{
      "producto_id": "5404",
      "ean": "7790580109882",
      "nombre": "Tableta Chocolate Extra Fino Aguila x 150 Grs — unidad",
      "unidad": "unidad",
      "cantidad": "1.000",
      "precio_centavos": 500000
    }],
    "subtotal_centavos": 500000,
    "cargo_centavos": 0,
    "total_centavos": 500000
  }],
  "hasta": "2026-11-23T20:41:00.000Z",
  "hay_mas": false
}
```

Los nombres son una propuesta: si prefieren los de `armarOrder` (`slotKind`, `paymentStatus`…), nos adaptamos. **Lo que no puede faltar:**
- **`producto_id` y `ean` en cada línea.** Odoo vincula por `producto_id`, que es el `id` de `GET /productos`. Hoy `GET /api/pedidos` trae solo el nombre, y con eso Odoo no sabe qué producto es.
- **`actualizado`**, para no traer todo cada minuto.
- **`contacto`**, porque el cajero tiene que saber a quién entregarle.
- **`revision`** (CR-0003) mientras el pedido esté `recibido`.

### 2. Mover el pedido desde el ERP
| Ruta | Cuerpo | Igual que |
|---|---|---|
| `POST /api/erp/v1/pedidos/:code/aceptar` | `{ "listo_para": "30 minutos" }` | `POST /api/pedidos/:id/aceptar` |
| `POST /api/erp/v1/pedidos/:code/listo` | — | `/:id/listo` |
| `POST /api/erp/v1/pedidos/:code/en-camino` | — | `/:id/en-camino` (solo reparto) |
| `POST /api/erp/v1/pedidos/:code/entregado` | — | `/:id/entregado` |
| `POST /api/erp/v1/pedidos/:code/cancelar` | `{ "motivo": "…" }` | `/:id/cancelar` |

- **Por `code`**, no por id interno: es lo que ve el comprador y lo que Odoo guarda.
- **Que corra exactamente la misma lógica que la pantalla:**
  - el descuento de stock al aceptar, con cupo o stock y el movimiento `PEDIDO-<code>`;
  - la devolución al cancelar;
  - el reembolso de Mercado Pago;
  - el aviso a NexoTienda (`encolarEvento`);
  - la nota de venta al entregar.

  Hoy esa lógica está dentro de los handlers de `pedidos.ts` y toma el comercio de `req.auth`. Proponemos sacarla a funciones que reciban `commerceId` y llamarlas desde los dos routers. Si se copia, en un tiempo hacen cosas distintas.
- **Respuesta:** el pedido completo, con la forma del punto 1. Así Odoo no tiene que volver a leerlo.
- **Repetir no rompe nada.** Si el pedido ya está en el estado pedido, que conteste 200 con el pedido. Si el paso no corresponde, 409 con el estado actual en el cuerpo (`{ "error": "…", "status": "entregado" }`). Odoo reintenta cuando se corta la red, y necesita saber si el primer intento llegó.

### 3. Que `GET /ventas` diga de dónde vino cada venta
- Un campo `pedido_code` en cada venta: `null` para el mostrador y el código para un pedido de tienda entregado.
- Sin eso, un ERP que ya registró el pedido lo cuenta dos veces cuando aparece como nota de venta.
- Hace falta solo si el comercio también cobra en el mostrador de NexoPOS.

### 4. Opcional: aviso al ERP cuando entra o cambia un pedido
- Un webhook por comercio, con la URL que carga el comerciante y el mismo `encolarEvento` que ya usan para NexoTienda.
- Sin él, Odoo consulta el punto 1 cada minuto. Es aceptable: un pedido nuevo tarda como mucho un minuto en verse en la caja.

## Cómo lo vamos a probar
Lo prueba German con Jure Hnos SRL, la misma clave de hoy:
1. Hacer un pedido en la NexoTienda de Jure y verlo con `GET /api/erp/v1/pedidos`, con `producto_id` y `ean`.
2. Aceptarlo desde Odoo: NexoTienda tiene que mostrar «el comercio está armando tu pedido», y el stock de NexoPOS tiene que bajar.
3. Llevarlo por listo y entregado: tiene que aparecer en `GET /ventas` con su `pedido_code`.
4. Hacer otro pedido y cancelarlo desde Odoo: el stock vuelve y NexoTienda lo muestra cancelado.
5. Repetir el `aceptar` de un pedido ya aceptado: 200, sin descontar el stock dos veces.

## Preguntas
1. **`revision` (CR-0003):** ¿un pedido aceptado desde el ERP tiene que pasar igual por la confirmación extra? Proponemos que sí: Odoo muestra el motivo y pide confirmar antes de llamar a `aceptar`.
2. **EAN repetido en Jure.** El EAN 12263774003 (Vino Reserva Malbec 750 Ml Las Perdices) está en tres productos del mismo comercio, según la lectura con `GET /api/erp/v1/productos` del 23/11:
   - `5403`, «— BOTELLA», a $12.000 con costo $9.500.
   - `44506`, «— BOTELLA», a $10.500 con costo $9.500.
   - `59390`, «— CAJA», sin precio de venta y con costo $55.000.

   ¿Las dos «BOTELLA» son un duplicado? ¿Cada presentación es un producto aparte con stock propio? En Odoo la caja y la botella son el mismo producto en dos unidades, y necesitamos saber si una caja recibida va a NexoPOS como +1 «CAJA» o como +6 «BOTELLA».

## Lo que no pedimos
- Que el pedido viva en Odoo, ni que NexoTienda hable con Odoo.
- Cambios en `/v1/orders` ni en la pantalla de pedidos.
- Precio y stock ya los escribe Odoo con `/precios` y `/stock` en modo `ajuste`, y anda.
