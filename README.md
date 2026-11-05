# Nexo-odoo

Conector de Odoo para el ecosistema Nexo. Sirve para que un comercio que ya usa Odoo trabaje con la red Nexo (NexoB2B, NexoPOS, ClubPay) sin dejar su ERP.

## Estado
**EN DESARROLLO.** Desafío 1: desde Odoo, buscar en el catálogo maestro de NexoB2B y agregar un producto a Odoo. Ver [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md).

## Módulos
| Módulo | Qué hace | Depende de |
|---|---|---|
| `nexo_core` | Credenciales, cliente HTTP, errores, diagnóstico | — |
| `nexo_b2b` | Catálogo y compras de NexoB2B | `nexo_core` |

## Versiones soportadas
Una rama por versión de Odoo. `main` tiene solo la documentación general.

| Rama | Odoo | Estado |
|---|---|---|
| `20.0` | 20 | EN DESARROLLO |
| `19.0` | 19 | PROPUESTO |
| `18.0` | 18 Community | PROPUESTO |

## Instalación
Solo en instalaciones propias u Odoo.sh. **Odoo Online no permite módulos de terceros.** Ver [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (se completa con la primera versión instalable).

## Documentación
- [docs/DECISIONS.md](docs/DECISIONS.md): decisiones numeradas, con su motivo.
- `docs/PARA-<PROYECTO>_de-NEXO-ODOO_<tema>.md`: pedidos a otros proyectos del ecosistema.
