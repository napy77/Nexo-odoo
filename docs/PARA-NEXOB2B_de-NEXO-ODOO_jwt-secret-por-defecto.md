# Para el equipo de NexoB2B. De Nexo-odoo, 23 de noviembre de 2026

## Qué vimos
En `apps/backend/src`, **87 archivos** validan el token así:

```ts
jwt.verify(token, process.env.JWT_SECRET || "<valor fijo>")
```

El valor fijo está en el repositorio. No lo copiamos acá. Si en algún servidor no está definida `JWT_SECRET`, el backend firma y valida con ese valor. Entonces cualquiera que lea el código puede fabricar un token con cualquier `comercio_id`, por ejemplo para pedir altas o leer datos de otro comercio. Lo encontramos leyendo `api/store/mayoristas/lista/route.ts` y `api/store/solicitudes/route.ts`.

## Qué no sabemos
Si en producción `JWT_SECRET` está definida. Desde Nexo-odoo no lo podemos ni debemos comprobar: **PENDIENTE DE VALIDAR** por NexoB2B.

## Qué proponemos
1. Confirmar que `JWT_SECRET` está definida en producción con un valor propio, distinto del fijo.
2. Sacar el valor por defecto: que el backend no arranque si falta `JWT_SECRET`, en un solo lugar (por ejemplo, `lib/`) que usen todas las rutas.
3. Si alguna vez corrió sin la variable, cambiar el secreto. Eso invalida los tokens actuales: los comercios, NexoPOS y este conector vuelven a iniciar sesión.

## Prioridad
Alta si el punto 1 no se cumple. Si se cumple, media: el riesgo queda en entornos nuevos o mal configurados.
