import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { Component, onWillStart, proxy, useProps } from "@odoo/owl";

const SERVICE = "nexo.b2b.service";
// Pausa al escribir antes de buscar, como en NexoPOS.
const SEARCH_DELAY = 400;

const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });

/**
 * Catálogo NexoB2B: busca en línea en el catálogo maestro y solo escribe en
 * Odoo al tocar «Agregar». Todo pasa por el servidor de Odoo (el token no
 * llega al navegador).
 */
export class NexoB2bCatalog extends Component {
    static template = "nexo_b2b.Catalog";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.actionService = useService("action");
        // Cada búsqueda lleva un número; si al volver ya hay una más nueva, se
        // descarta (tipear rápido no pisa resultados).
        this.searchSeq = 0;
        this.state = proxy({
            status: null,
            showLogin: false,
            login: { email: "", password: "", busy: false, error: null },
            query: "",
            conStock: true,
            page: 1,
            loading: false,
            result: null,
            error: null,
            busyKey: null,
            pending: null,
        });
        this.debouncedSearch = useDebounced(() => this.search(), SEARCH_DELAY);

        onWillStart(async () => {
            this.state.status = await this.orm.call(SERVICE, "get_status", []);
            const params = this.props.action.params || {};
            this.state.showLogin = Boolean(params.login) || !this.state.status.connected;
            if (!this.state.showLogin) {
                await this.search();
            }
        });
    }

    // ------------------------------------------------------------------
    // Sesión
    // ------------------------------------------------------------------

    async onLogin(ev) {
        ev.preventDefault();
        const login = this.state.login;
        login.busy = true;
        login.error = null;
        try {
            const res = await this.orm.call(SERVICE, "login", [login.email, login.password]);
            if (res.ok) {
                this.state.status = res.status;
                this.state.showLogin = false;
                this.notification.add(res.message, { type: "success" });
                await this.search();
            } else {
                login.error = res.message;
            }
        } finally {
            // La contraseña no queda en memoria de la pantalla. El campo se
            // limpia a mano: el estado vuelve al mismo "" con el que se dibujó
            // y OWL no ve un cambio que aplicar al input.
            login.password = "";
            const input = ev.target.querySelector("input[type=password]");
            if (input) {
                input.value = "";
            }
            login.busy = false;
        }
    }

    // ------------------------------------------------------------------
    // Búsqueda
    // ------------------------------------------------------------------

    onQueryInput() {
        this.state.page = 1;
        this.debouncedSearch();
    }

    onToggleStock(ev) {
        // El tilde es «Mostrar sin stock»: tildado = no filtrar por stock.
        this.state.conStock = !ev.target.checked;
        this.state.page = 1;
        this.search();
    }

    goToPage(page) {
        this.state.page = page;
        this.search();
    }

    async search() {
        const seq = ++this.searchSeq;
        this.state.loading = true;
        try {
            const res = await this.orm.call(SERVICE, "search_catalog", [
                this.state.query,
                this.state.page,
                this.state.conStock,
            ]);
            if (seq !== this.searchSeq) {
                return;
            }
            if (res.ok) {
                this.state.result = res;
                this.state.error = null;
            } else {
                this.state.error = res.message;
                if (res.kind === "auth") {
                    this.state.status = await this.orm.call(SERVICE, "get_status", []);
                    this.state.showLogin = true;
                }
            }
        } finally {
            if (seq === this.searchSeq) {
                this.state.loading = false;
            }
        }
    }

    // ------------------------------------------------------------------
    // Agregar
    // ------------------------------------------------------------------

    async add(producto, mayorista, choices = {}) {
        this.state.busyKey = `${producto.id}:${mayorista.id}`;
        try {
            const res = await this.orm.call(SERVICE, "add_product", [
                producto.id,
                producto.ean,
                mayorista.id,
                choices,
            ]);
            if (res.status === "needs_input") {
                this.state.pending = {
                    producto,
                    mayorista,
                    tax: res.tax,
                    category: res.category,
                    saleTaxId: res.tax?.sale_tax_id ? String(res.tax.sale_tax_id) : "",
                    purchaseTaxId: res.tax?.purchase_tax_id ? String(res.tax.purchase_tax_id) : "",
                    withoutTax: false,
                    categoryMode: "create",
                    categId: "",
                };
                return;
            }
            if (res.status !== "ok") {
                this.notification.add(res.message, { type: "danger", sticky: true });
                return;
            }
            this.state.pending = null;
            mayorista.agregado = true;
            producto.odoo_product_id = res.product_id;
            const parts = [res.product_created ? "Producto creado" : "Producto existente actualizado"];
            if (res.vendor_created) {
                parts.push(`proveedor ${mayorista.nombre} creado`);
            }
            parts.push(`${res.lines_created} línea(s) de proveedor nuevas, ${res.lines_updated} actualizadas`);
            this.notification.add(parts.join(" · "), { type: "success" });
            for (const warning of res.warnings) {
                this.notification.add(warning, { type: "warning", sticky: true });
            }
        } finally {
            this.state.busyKey = null;
        }
    }

    confirmPending() {
        const p = this.state.pending;
        const choices = {};
        if (p.tax) {
            choices.tax = {
                sale_tax_id: p.saleTaxId ? Number(p.saleTaxId) : false,
                purchase_tax_id: p.purchaseTaxId ? Number(p.purchaseTaxId) : false,
                without_tax: p.withoutTax,
            };
        }
        if (p.category) {
            choices.category = {
                mode: p.categoryMode,
                categ_id: p.categId ? Number(p.categId) : false,
            };
        }
        return this.add(p.producto, p.mayorista, choices);
    }

    cancelPending() {
        this.state.pending = null;
    }

    setPending(field, ev) {
        const target = ev.target;
        this.state.pending[field] = target.type === "checkbox" ? target.checked : target.value;
    }

    openProduct(producto) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "product.template",
            res_id: producto.odoo_product_id,
            views: [[false, "form"]],
        });
    }

    // ------------------------------------------------------------------
    // Formato
    // ------------------------------------------------------------------

    formatPrice(value) {
        return value === null || value === undefined ? "—" : money.format(value);
    }

    priceLabel(mayorista) {
        return mayorista.muestra_precio === "final" ? "precio final" : "neto + IVA";
    }

    isBusy(producto, mayorista) {
        return this.state.busyKey === `${producto.id}:${mayorista.id}`;
    }
}

registry.category("actions").add("nexo_b2b.catalog", NexoB2bCatalog);
