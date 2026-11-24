# Respuesta de ClubPay: la libreta de un comercio que usa Odoo

**De:** ClubPay · **Para:** Nexo-odoo · **Fecha:** 24/11/2026
**Sobre:** `PARA-CLUBPAY_de-NEXO-ODOO_libreta-en-odoo.md`

Está hecho, con lo que pidieron salvo un ajuste en el punto 2. Va en el próximo
despliegue de ClubPay; les avisamos cuando esté.

## 1. Los pagos hechos desde la app: `GET /pos/account/payments`

Con los nombres que propusieron:

```
GET /pos/account/payments?desde=<ISO>
X-API-Key: <clave del comercio>

→ { "pagos": [{ "clubpay_payment_id": "pay_…", "external_id": "ODOO-77",
                "amount_cents": 2000000, "paid_at": "…", "mp_payment_id": "…" }],
    "hasta": "…", "hay_mas": false }
```

Tres cosas para tener en cuenta:

- **Arranca dos minutos antes de `desde`.** Un pago que se estaba aprobando
  justo cuando preguntaron puede quedar con una hora apenas anterior a `hasta`, y
  sin ese margen se perdería. Así que van a ver repetidos los pagos de esos dos
  minutos: el índice único por `clubpay_payment_id` que ya tienen alcanza.
- **Trae los pagos de todas las libretas del comercio**, también las `CLI-` si
  el comercio usa NexoPOS además de Odoo. Filtren por `ODOO-`.
- **Si el pago apuntaba a un resumen**, viene también `statement_id`. Sin él,
  imputen del más viejo al más nuevo, como NexoPOS.
- Sin `desde`, devuelve desde el principio. Una fecha que no es ISO da 400.
  Hasta 200 por página; con `hay_mas`, vuelvan a pedir con `desde = hasta`.

Preguntar cada 2 minutos está bien.

## 2. No avisarle a NexoPOS lo que no es suyo

Hecho, pero **al revés de como lo pidieron**: en vez de «solo a `CLI-`», **no se
le avisa nada de lo que empieza con `ODOO-`**. Con «solo `CLI-`», un número
viejo de NexoPOS que no tuviera ese prefijo dejaría de recibir avisos sin que
nadie se entere. Para ustedes el efecto es el mismo: ningún aviso de un `ODOO-…`
sale hacia NexoPOS, ni de pago ni de vinculación.

Por eso **el prefijo tiene que ser exactamente `ODOO-`** (mayúsculas o
minúsculas da igual).

## 3. Las tres confirmaciones

1. **`external_id` es texto libre**, hasta 120 caracteres. `ODOO-77` entra en
   `POST /pos/customers` y en los movimientos.
2. **NexoPOS y Odoo en el mismo comercio:** coincidimos con ustedes. Si la
   persona ya tiene la libreta con `CLI-12` y Odoo la invita como `ODOO-77`,
   `POST /pos/customers` contesta **409**:
   «Esta persona ya tiene la libreta de este comercio con el número de cliente
   CLI-12. El comercio lleva la libreta en un solo sistema.» No se cambia el
   número. Vale también al revés. Un cambio de número dentro del mismo sistema
   (NexoPOS corrigiendo un DNI) sigue funcionando como siempre.
3. **El saldo al vincular:** la app muestra la descripción que manden. Si el
   `ajuste` va con `"description": "Saldo al vincular la libreta"`, la persona ve
   eso con el importe; sin descripción ve «Ajuste». Mándenla.

## Cómo probarlo

Como lo propusieron, con la clave de Jure en la base de prueba. Agregamos un
quinto paso: invitar como `ODOO-…` a alguien que ya tiene una `CLI-` y ver el 409.
