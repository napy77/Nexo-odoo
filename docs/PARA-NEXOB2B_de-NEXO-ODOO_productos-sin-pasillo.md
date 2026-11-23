# Para el equipo de NexoB2B. De Nexo-odoo, 23 de noviembre de 2026

## Qué pasa
Muchos productos del maestro tienen `rubro_id`, pero no `pasillo_id`. Por eso `GET /store/productos?pasillo_id=…` no los devuelve, aunque su rubro pertenezca a ese pasillo según `/store/taxonomia` (`rubro.pasillo_id`).

## Cómo lo vimos
Con un comercio real (Jure Hnos SRL), la API en producción y `con_stock` sin filtrar:
- Sin filtro: **3879** productos.
- `pasillo_id` = Tecnología: **0**. En cambio, `rubro_id` = Informática, que es un rubro de Tecnología: **803**.
- También dan 0: Aire libre, Colchones y Sommiers, Combos, Electro Hogar e Indumentaria y Calzado.
- La suma de todos los pasillos da unos 2000 productos, contra 3879 en total.

El portal del comercio arma la misma consulta (`storefront/src/app/(portal)/comercio/productos/page.tsx`), así que **el portal también muestra 0 al tocar «Tecnología»**.

## Qué necesitamos
Una de dos:
1. Completar `producto_maestro.pasillo_id` a partir del rubro, en los datos.
2. Que `/store/productos` filtre por pasillo también por el pasillo del rubro, por ejemplo `p.pasillo_id = $x OR ru.pasillo_id = $x`.

El conector de Odoo no corrige esto por su cuenta: manda el mismo `pasillo_id` que el portal y muestra lo que devuelve NexoB2B.

## Prioridad
Media. No bloquea nada (filtrar por rubro y buscar por texto funcionan), pero confunde: un pasillo con productos aparece vacío.
