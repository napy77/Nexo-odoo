import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { Component, onWillStart, proxy, useProps } from "@odoo/owl";

const SERVICE = "nexo.b2b.service";
const SEARCH_DELAY = 400;

const ESTADOS = {
    pendiente: { label: "Solicitud pendiente", css: "text-bg-warning" },
    aceptado: { label: "Habilitado", css: "text-bg-success" },
    rechazado: { label: "Rechazado", css: "text-bg-danger" },
};
const ORDENES = [
    ["cercania", "Más cerca"],
    ["productos", "Más productos"],
    ["nombre", "Nombre"],
];

/**
 * Mayoristas NexoB2B: igual que «Descubrí mayoristas» en el portal del
 * comercio (storefront/src/app/(portal)/comercio/mayoristas/page.tsx).
 * Buscar, ver el estado del alta, pedirla y abrir su catálogo.
 */
export class NexoB2bMayoristas extends Component {
    static template = "nexo_b2b.Mayoristas";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.actionService = useService("action");
        this.searchSeq = 0;
        this.ordenes = ORDENES;
        this.state = proxy({
            status: null,
            context: null,
            busqueda: "",
            radioKm: 50,
            rubrosActivos: [],
            orden: "cercania",
            mayoristas: [],
            sinDeSusRubros: false,
            loading: false,
            searched: false,
            error: null,
            alta: null,
        });
        this.debouncedSearch = useDebounced(() => this.search(), SEARCH_DELAY);

        onWillStart(async () => {
            this.state.status = await this.orm.call(SERVICE, "get_status", []);
            if (!this.state.status.connected) {
                return;
            }
            const context = await this.orm.call(SERVICE, "get_mayoristas_context", []);
            if (!context.ok) {
                this.state.error = context.message;
                return;
            }
            this.state.context = context;
            // Por defecto, lo que compra este tipo de comercio (como el portal).
            this.state.rubrosActivos = [...context.rubros_sugeridos];
            await this.search();
        });
    }

    openLogin() {
        this.actionService.doAction({ type: "ir.actions.client", tag: "nexo_b2b.catalog", params: { login: true } });
    }

    // ------------------------------------------------------------------
    // Filtros
    // ------------------------------------------------------------------

    onBusquedaInput() {
        this.debouncedSearch();
    }

    setRadio(km) {
        this.state.radioKm = km;
        this.search();
    }

    toggleRubro(rubro) {
        const activos = this.state.rubrosActivos;
        this.state.rubrosActivos = activos.includes(rubro) ? activos.filter((r) => r !== rubro) : [...activos, rubro];
        this.search();
    }

    todosLosRubros() {
        this.state.rubrosActivos = [];
        this.search();
    }

    misRubros() {
        this.state.rubrosActivos = [...this.state.context.rubros_sugeridos];
        this.search();
    }

    setOrden(ev) {
        this.state.orden = ev.target.value;
    }

    isRubroActivo(rubro) {
        return this.state.rubrosActivos.includes(rubro);
    }

    get rubros() {
        // Los de NexoB2B más los que traen los resultados, sin repetir.
        const todos = new Set(this.state.context?.rubros || []);
        for (const m of this.state.mayoristas) {
            for (const r of m.rubros) {
                todos.add(r);
            }
        }
        return [...todos].sort((a, b) => a.localeCompare(b, "es"));
    }

    get sonMisRubros() {
        const sugeridos = this.state.context?.rubros_sugeridos || [];
        const activos = this.state.rubrosActivos;
        return sugeridos.length === activos.length && sugeridos.every((r) => activos.includes(r));
    }

    get radioLabel() {
        return this.state.radioKm === 9999 ? "todo el país" : `${this.state.radioKm} km`;
    }

    radioText(km) {
        return km === 9999 ? "Todo el país" : `${km} km`;
    }

    get siguienteRadio() {
        return (this.state.context?.radios || []).find((r) => r > this.state.radioKm);
    }

    ampliarRadio() {
        this.setRadio(this.siguienteRadio || 9999);
    }

    get ordenados() {
        const lista = [...this.state.mayoristas];
        if (this.state.orden === "productos") {
            lista.sort((a, b) => b.productos_count - a.productos_count);
        } else if (this.state.orden === "nombre") {
            lista.sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
        } else {
            // Cercanía: ya viene ordenado por NexoB2B; los que no tienen
            // ubicación van al final.
            lista.sort((a, b) => (a.distancia_km === null) - (b.distancia_km === null));
        }
        return lista;
    }

    async search() {
        const seq = ++this.searchSeq;
        this.state.loading = true;
        try {
            const res = await this.orm.call(SERVICE, "search_mayoristas", [
                this.state.busqueda,
                this.state.radioKm,
                this.state.rubrosActivos,
                this.state.context?.ubicacion || null,
            ]);
            if (seq !== this.searchSeq) {
                return;
            }
            if (res.ok) {
                this.state.mayoristas = res.mayoristas;
                this.state.sinDeSusRubros = res.sin_de_sus_rubros;
                this.state.searched = true;
                this.state.error = null;
            } else {
                this.state.error = res.message;
                if (res.kind === "auth") {
                    this.state.status = await this.orm.call(SERVICE, "get_status", []);
                }
            }
        } finally {
            if (seq === this.searchSeq) {
                this.state.loading = false;
            }
        }
    }

    // ------------------------------------------------------------------
    // Alta y catálogo
    // ------------------------------------------------------------------

    askAlta(mayorista) {
        this.state.alta = { mayorista, mensaje: "", busy: false };
    }

    cancelAlta() {
        this.state.alta = null;
    }

    async confirmAlta() {
        const alta = this.state.alta;
        alta.busy = true;
        try {
            const res = await this.orm.call(SERVICE, "request_alta", [alta.mayorista.id, alta.mensaje]);
            if (res.ok) {
                alta.mayorista.estado = res.estado;
                this.notification.add(`Alta pedida a ${alta.mayorista.nombre}. Te avisa NexoB2B cuando la acepte.`, {
                    type: "success",
                });
                this.state.alta = null;
            } else {
                this.notification.add(res.message, { type: "danger", sticky: true });
                alta.busy = false;
                // Puede que ya hubiera una solicitud: se refresca el estado.
                this.search();
            }
        } catch (error) {
            alta.busy = false;
            throw error;
        }
    }

    openCatalog(mayorista) {
        this.actionService.doAction({
            type: "ir.actions.client",
            tag: "nexo_b2b.catalog",
            params: { mayorista_id: mayorista.id, mayorista_nombre: mayorista.nombre },
        });
    }

    openPartner(mayorista) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: mayorista.partner_id,
            views: [[false, "form"]],
        });
    }

    // ------------------------------------------------------------------
    // Formato
    // ------------------------------------------------------------------

    estado(mayorista) {
        return ESTADOS[mayorista.estado] || null;
    }

    distancia(mayorista) {
        const km = mayorista.distancia_km;
        return km < 1 ? `${Math.round(km * 1000)} m` : `${km} km`;
    }

    lugar(mayorista) {
        return [mayorista.rubros[0], [mayorista.ciudad, mayorista.provincia].filter(Boolean).join(", ")]
            .filter(Boolean)
            .join(" · ");
    }

    iniciales(mayorista) {
        return (mayorista.nombre || "?")
            .split(/\s+/)
            .slice(0, 2)
            .map((w) => w[0])
            .join("")
            .toUpperCase();
    }
}

registry.category("actions").add("nexo_b2b.mayoristas", NexoB2bMayoristas);
