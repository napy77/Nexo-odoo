# Instalación — Nexo-odoo

## Dónde funciona
| Tipo de Odoo | ¿Sirve? |
|---|---|
| Instalación propia (paquete, fuente o Docker) | Sí |
| Odoo.sh | Sí: el repo se agrega como submódulo de la rama de Odoo.sh |
| **Odoo Online (SaaS)** | **No**: no permite instalar módulos de terceros |

Requisitos: Odoo 20 (rama `20.0` de este repo), Python 3.12 y `requests` (viene con Odoo). El servidor de Odoo tiene que poder salir por HTTPS hacia NexoB2B.

## Instancia de Linware (`odoo-linware`)
Odoo está instalado como paquete en `/usr/lib/python3/dist-packages/odoo` y corre como el usuario `odoo`. `<host-odoo-linware>` es la dirección pública del servidor: la pasa German y no va en el repo.

### 1. Correr los tests en una base descartable, sin tocar el servicio — IMPLEMENTADO
`/opt/nexo-odoo` es de `german` (grupo `odoo`), así que se copia sin `sudo` desde la Mac. `german` tiene `sudo` sin contraseña solo como `odoo` para `odoo`, `createdb`, `dropdb` y `psql`, y para `systemctl restart|status odoo` (`/etc/sudoers.d/nexo-odoo`; borrarlo al terminar el desarrollo).
```bash
rsync -a --delete --exclude .git --exclude docs --exclude '*.md' /Users/german/ClaudeCode/Nexo-odoo/ german@<host-odoo-linware>:/opt/nexo-odoo/
```
Primera vez (`-i`; para actualizar, `-u`):
```bash
ssh german@<host-odoo-linware> 'sudo -n -u odoo /usr/bin/odoo -c /etc/odoo/odoo.conf --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/opt/nexo-odoo -d nexo_test_ci -i nexo_core,nexo_b2b --test-tags /nexo_core,/nexo_b2b --without-demo=all --stop-after-init --http-port=8099 --workers=0 --max-cron-threads=0 --logfile=/tmp/nexo_test_ci.log'
```
```bash
ssh german@<host-odoo-linware> 'grep -E "odoo.tests.(result|stats)|ERROR" /tmp/nexo_test_ci.log | grep -v html4css1 | tail -20'
```
Crea la base `nexo_test_ci` en el mismo PostgreSQL y no toca las demás bases. Para borrarla después:
```bash
ssh german@<host-odoo-linware> 'sudo -n -u odoo dropdb nexo_test_ci'
```
Para ver la base de prueba en el navegador hay que levantar un Odoo aparte con túnel y `timeout`, para que no quede vivo si se corta la conexión (los procesos son del usuario `odoo` y `german` no los puede matar):
```bash
ssh -tt -L 8098:localhost:8098 german@<host-odoo-linware> 'timeout 900 sudo -n -u odoo /usr/bin/odoo -c /etc/odoo/odoo.conf --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/opt/nexo-odoo -d nexo_test_ci --db-filter=^nexo_test_ci$ --http-port=8098 --workers=0 --max-cron-threads=0 --logfile=/tmp/nexo_ui.log'
```
Los cambios de JS se ven recién al reiniciar ese Odoo: el paquete de assets no se regenera en caliente.

### 2. Instalar en la base de prueba — PROPUESTO
1. Agregar `/opt/nexo-odoo` al `addons_path` de `/etc/odoo/odoo.conf`. Es mejor que copiar el módulo dentro de `dist-packages`, porque así lo separa de las actualizaciones del paquete.
2. `sudo systemctl restart odoo`
3. En la base de prueba: Aplicaciones → Actualizar lista → instalar **Nexo · NexoB2B** (instala `nexo_core` también).
4. Ajustes → Inventario → activar **Unidades de medida y empaques**.
5. Ajustes → NexoB2B → URL y clave publicable → **Iniciar sesión** → **Probar conexión**.

**Nunca en una base real de un comercio** mientras esté en desarrollo.
