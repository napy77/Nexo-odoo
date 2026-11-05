# CLAUDE.md — Nexo-odoo

Idioma: español, conciso. Si German se equivoca o hay una opción mejor, decíselo y discutanlo.

## Reglas que no se negocian
1. Un repo por ERP y una rama por versión de Odoo (`20.0`, `19.0`, `18.0`). `main` tiene solo README, CLAUDE.md y `docs/`.
2. No se modifican NexoB2B, NexoPOS, NexoTienda, ClubPay ni nexo-ecosystem: solo se leen. Si hace falta un cambio en uno de ellos, se escribe un pedido en `docs/PARA-<PROYECTO>_de-NEXO-ODOO_<tema>.md`.
3. No inventar nada. Cada cosa se marca como IMPLEMENTADO, PARCIAL, EN DESARROLLO, PROPUESTO o PENDIENTE DE VALIDAR. Si el código real contradice la documentación, gana el código, y se avisa.
4. Credenciales: nunca en git, ni en docs, ni en logs, ni siquiera partes. Se mencionan solo por nombre y propósito. La contraseña del comercio no se guarda (DEC-003).
5. Probar solo en bases de prueba. Nunca crear pedidos ni datos en un comercio real.
6. Commit y push solo cuando German lo pide. Los pedidos a otros equipos se pushean, porque el ecosistema los lee desde GitHub.
7. `docs/CURRENT_STATE.md` se lee al empezar cada tarea y se actualiza al terminarla.

## Específico del repo
- No se asume nada de Odoo 20 por analogía con 17 o 18. La fuente de verdad es el `addons/` instalado.
- Cada llamada a NexoB2B lleva la cabecera `X-Nexo-Connector: odoo/<versión>` y un id de solicitud.
- El catálogo no se copia: se busca en línea y solo se escribe en Odoo al tocar «Agregar».
- El `barcode` del producto en Odoo es siempre el EAN del maestro de NexoB2B.

## Fuentes de solo lectura
- NexoB2B: `/Users/german/Claude/Projects/nexob2b/nexob2b` (backend en `apps/backend/src`).
- Ecosistema: `/Users/german/Nexo-Ecosystem` (`contracts/`, `architecture/`, `sources/*/docs/`).
