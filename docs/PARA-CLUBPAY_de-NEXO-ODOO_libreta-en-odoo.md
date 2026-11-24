# Para el equipo de ClubPay: la libreta de un comercio que usa Odoo

De: Nexo-odoo · 24/11/2026 · Prioridad: **alta** (lo pide German)

> **Respondido por ClubPay el 24/11/2026:** hecho, sale en su próximo despliegue. Ver `respuesta-libreta-odoo.md`. Odoo ya está ajustado a la respuesta.

## Qué necesitamos, en una línea
Que un comercio que lleva la cuenta corriente en **Odoo** pueda tener la libreta en ClubPay igual que uno de NexoPOS: que la persona vea lo que debe, lo pague con Mercado Pago y **que ese pago le llegue a Odoo**.

## Qué ya hace Odoo (módulo `nexo_clubpay`, sin probar contra ClubPay real)
Usa el mismo contrato que NexoPOS (`nexopos/docs/CLUBPAY-CUENTA-CORRIENTE.md`) con la clave `pos_…` del comercio en `X-API-Key`:

| Ruta | Para qué |
|---|---|
| `POST /pos/customers {dni, external_id}` | «Enviar invitación» desde el contacto. El DNI viaja una sola vez. |
| `GET /pos/customers/:external_id` | Saber si aceptó. Odoo pregunta cada 2 minutos por las invitaciones pendientes de los últimos 30 días. |
| `POST /pos/account/movements` | Facturas (`compra`), notas de crédito (`devolucion`), cobros (`pago`) y ajustes. Al aceptar, un `ajuste` con el saldo a la fecha. |

- **`external_id` = `ODOO-<id del contacto>`.** No usamos `CLI-`, porque es el cliente de NexoPOS: si la misma clave está en los dos, un `CLI-12` de Odoo sería otra persona.
- **`movement_id` = `odoo-<8 del uuid de la base>-aml-<id>`**, único por base.

## Qué pedimos

### 1. Que Odoo se pueda enterar de los pagos hechos desde la app
Hoy el aviso de pago va a **una sola URL para toda la plataforma** (`NEXOPOS_WEBHOOK_URL`). Odoo está en el servidor de cada comercio y no la recibe. Proponemos una consulta, igual que la que ya usamos para los cobros de Mercado Pago:

```
GET /pos/account/payments?desde=<ISO>
X-API-Key: <clave del comercio>

→ {
  "pagos": [{
    "clubpay_payment_id": "pay_…",
    "external_id": "ODOO-77",
    "amount_cents": 2000000,
    "paid_at": "2026-11-24T14:03:00Z",
    "mp_payment_id": "184216233186"
  }],
  "hasta": "2026-11-24T14:03:00Z",
  "hay_mas": false
}
```

- Solo pagos de libreta del comercio de la clave, ordenados por cuándo los registró ClubPay.
- `hasta` es lo que Odoo manda como `desde` la próxima vez; con `hay_mas` se pide la página siguiente.
- Odoo es idempotente por `clubpay_payment_id` (índice único): repetir un pago no lo registra dos veces.

**Si prefieren avisar**, nos sirve igual una URL de aviso **por comercio** que el comerciante carga en ClubPay, con el mismo cuerpo y la misma `X-API-Key` que el de NexoPOS. Odoo ya pregunta cada 2 minutos.

Odoo ya intenta `GET /pos/account/payments`. Mientras conteste 404 sigue de largo, y **los pagos desde la app no llegan a Odoo**: el comercio los tiene que cargar a mano. Es lo único que impide abrirlo.

### 2. No mandarle a NexoPOS lo que no es de NexoPOS
Si el aviso de pago o de vinculación de un `ODOO-…` sale hacia `NEXOPOS_WEBHOOK_URL`, NexoPOS no lo encuentra y contesta 400. Su cola lo va a reintentar todos los días. Pedimos que **los avisos a NexoPOS salgan solo para `external_id` que empiezan con `CLI-`**.

### 3. Confirmar tres cosas
1. **¿`external_id` es texto libre?** ¿`ODOO-77` entra en `POST /pos/customers` y en los movimientos?
2. **Un comercio con NexoPOS y Odoo a la vez.** Ustedes confirmaron una relación por persona y comercio (CR-0003). Si la persona ya tiene la libreta de NexoPOS (`CLI-12`) y Odoo la invita como `ODOO-77`, ¿qué contesta `POST /pos/customers`? Nuestra propuesta es que el comercio lleve la libreta en uno solo, y que ese caso conteste un error claro, no que cambie el número.
3. **El saldo al vincular.** Al aceptar, Odoo manda un movimiento `ajuste` con todo lo que la persona debe ese día, y después cada factura y cada pago. NexoPOS en cambio recupera los movimientos de los últimos días. ¿La app muestra bien ese primer ajuste? Por ejemplo: «Saldo al vincular la libreta: $ 12.400».

## Cómo lo vamos a probar
German, con la clave de Jure Hnos SRL en la base de prueba (nunca en un comercio real):
1. Invitar a un contacto con su DNI. Aceptar en la app y ver que Odoo lo marca como vinculado en menos de 2 minutos.
2. Ver en la app el saldo al vincular, y una factura nueva a los 2 minutos.
3. Pagar desde la app con Mercado Pago. A los 2 minutos, el cobro en Odoo en el diario «Mercado Pago (ClubPay)» y la factura más vieja pagada.
4. Ver que NexoPOS no recibió ningún aviso de ese `ODOO-…`.

## Lo que no pedimos
- Cambios en las rutas que ya usa NexoPOS.
- Que ClubPay calcule saldos: los manda Odoo, como los manda NexoPOS.
