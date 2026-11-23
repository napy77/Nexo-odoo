import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { Component, onWillStart, proxy, useProps } from "@odoo/owl";

const SERVICE = "nexo.b2b.service";
// Pausa al escribir antes de buscar, como en NexoPOS.
const SEARCH_DELAY = 400;

// Sin decimales, como el portal del comercio en NexoB2B.
const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 });
const VIEWS = ["lista", "chica", "grande"];
const VIEW_KEY = "nexo_b2b_catalog_view";

function savedView() {
    try {
        const view = window.localStorage.getItem(VIEW_KEY);
        return VIEWS.includes(view) ? view : "lista";
    } catch {
        return "lista";
    }
}

/**
 * Catálogo NexoB2B: busca en línea en el catálogo maestro y solo escribe en
 * Odoo al tocar «Agregar». Todo pasa por el servidor de Odoo (el token no
 * llega al navegador).
 *
 * Filtros, vistas y «Cargar más» copian el portal del comercio en NexoB2B
 * (storefront/src/app/(portal)/comercio/productos/page.tsx).
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
            soloConAlta: false,
            pasilloId: null,
            rubroId: null,
            subrubroId: null,
            // Desde «Mayoristas NexoB2B» → «Ver catálogo».
            mayorista: null,
            taxonomy: { pasillos: [], rubros: [], subrubros: [] },
            view: savedView(),
            page: 1,
            totalPages: 1,
            total: 0,
            productos: [],
            searched: false,
            loading: false,
            loadingMore: false,
            error: null,
            busyKey: null,
            pending: null,
            detail: null,
        });
        this.debouncedSearch = useDebounced(() => this.search(), SEARCH_DELAY);

        onWillStart(async () => {
            this.state.status = await this.orm.call(SERVICE, "get_status", []);
            const params = this.props.action.params || {};
            if (params.mayorista_id) {
                this.state.mayorista = { id: params.mayorista_id, nombre: params.mayorista_nombre };
            }
            this.state.showLogin = Boolean(params.login) || !this.state.status.connected;
            if (!this.state.showLogin) {
                await Promise.all([this.loadTaxonomy(), this.search()]);
            }
        });
    }

    async loadTaxonomy() {
        const res = await this.orm.call(SERVICE, "get_taxonomy", []);
        if (res.ok) {
            this.state.taxonomy = res;
        }
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
                await Promise.all([this.loadTaxonomy(), this.search()]);
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
        this.debouncedSearch();
    }

    toggleStock() {
        this.state.conStock = !this.state.conStock;
        this.search();
    }

    toggleAlta() {
        // Como en el portal, se filtra sobre lo ya traído: NexoB2B no tiene
        // este filtro del lado del servidor.
        this.state.soloConAlta = !this.state.soloConAlta;
    }

    selectPasillo(id) {
        this.state.pasilloId = this.state.pasilloId === id ? null : id;
        this.state.rubroId = null;
        this.state.subrubroId = null;
        this.search();
    }

    selectRubro(id) {
        this.state.rubroId = this.state.rubroId === id ? null : id;
        this.state.subrubroId = null;
        this.search();
    }

    selectSubrubro(id) {
        this.state.subrubroId = this.state.subrubroId === id ? null : id;
        this.search();
    }

    clearMayorista() {
        this.state.mayorista = null;
        this.search();
    }

    clearFilters() {
        this.state.mayorista = null;
        this.state.pasilloId = null;
        this.state.rubroId = null;
        this.state.subrubroId = null;
        this.state.soloConAlta = false;
        this.search();
    }

    setView(view) {
        this.state.view = view;
        try {
            window.localStorage.setItem(VIEW_KEY, view);
        } catch {
            // Sin almacenamiento (ventana privada): la vista vale solo ahora.
        }
    }

    get hayFiltros() {
        const s = this.state;
        return Boolean(s.mayorista || s.pasilloId || s.rubroId || s.subrubroId || s.soloConAlta);
    }

    get rubros() {
        return this.state.taxonomy.rubros.filter((r) => r.pasillo_id === this.state.pasilloId);
    }

    get subrubros() {
        return this.state.taxonomy.subrubros.filter((s) => s.rubro_id === this.state.rubroId);
    }

    get productosMostrados() {
        const productos = this.state.productos;
        return this.state.soloConAlta
            ? productos.filter((p) => p.mayoristas.some((m) => m.tiene_alta === true))
            : productos;
    }

    loadMore() {
        return this.search({ append: true });
    }

    async search({ append = false } = {}) {
        const seq = ++this.searchSeq;
        const page = append ? this.state.page + 1 : 1;
        this.state[append ? "loadingMore" : "loading"] = true;
        try {
            const res = await this.orm.call(SERVICE, "search_catalog", [
                this.state.query,
                page,
                this.state.conStock,
                {
                    pasillo_id: this.state.pasilloId,
                    rubro_id: this.state.rubroId,
                    subrubro_id: this.state.subrubroId,
                    mayorista_id: this.state.mayorista?.id || null,
                },
            ]);
            if (seq !== this.searchSeq) {
                return;
            }
            if (res.ok) {
                this.state.productos = append ? [...this.state.productos, ...res.productos] : res.productos;
                this.state.page = res.page;
                this.state.totalPages = res.totalPages;
                this.state.total = res.total;
                this.state.searched = true;
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
                this.state.loadingMore = false;
            }
        }
    }

    openDetail(producto) {
        this.state.detail = producto;
    }

    closeDetail() {
        this.state.detail = null;
    }

    // ------------------------------------------------------------------
    // Agregar
    // ------------------------------------------------------------------

    /**
     * «Agregar» (sin `presentacion`) o «Al pedido» (con ella): las dos crean o
     * actualizan el producto; la segunda además lo suma al borrador de pedido
     * de compra de ese mayorista.
     */
    async add(producto, mayorista, choices = {}, presentacion = null) {
        this.state.busyKey = this.busyKey(producto, mayorista, presentacion);
        try {
            const res = presentacion
                ? await this.orm.call(SERVICE, "add_to_order", [
                      producto.id,
                      producto.ean,
                      mayorista.id,
                      presentacion.id,
                      1,
                      choices,
                  ])
                : await this.orm.call(SERVICE, "add_product", [producto.id, producto.ean, mayorista.id, choices]);
            if (res.status === "needs_input") {
                this.state.pending = {
                    producto,
                    mayorista,
                    presentacion,
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
            if (res.order_id) {
                this.notifyOrder(res, mayorista);
                return;
            }
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
        return this.add(p.producto, p.mayorista, choices, p.presentacion);
    }

    notifyOrder(res, mayorista) {
        const close = this.notification.add(
            `${res.line_qty} × ${res.unidad} en ${res.order_name}` +
                (res.order_created ? ` (pedido nuevo a ${mayorista.nombre})` : ""),
            {
                type: "success",
                buttons: [
                    {
                        name: "Ver pedido",
                        primary: true,
                        onClick: () => {
                            close();
                            this.openOrder(res.order_id);
                        },
                    },
                ],
            }
        );
        for (const warning of res.warnings || []) {
            this.notification.add(warning, { type: "warning", sticky: true });
        }
    }

    openOrder(orderId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: orderId,
            views: [[false, "form"]],
        });
    }

    cancelPending() {
        this.state.pending = null;
    }

    // Las plantillas de OWL 3 no ven los globales de JS (String, Number…):
    // la comparación se hace acá.
    isPendingTax(field, taxId) {
        return String(taxId) === this.state.pending?.[field];
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

    /** Precio más bajo por unidad entre mayoristas, para las tarjetas. */
    bestUnitPrice(producto) {
        let best = null;
        for (const m of producto.mayoristas) {
            for (const pr of m.presentaciones) {
                if (pr.precio_mostrar === null || pr.precio_mostrar === undefined) {
                    continue;
                }
                const unit = pr.precio_mostrar / (pr.factor || 1);
                if (best === null || unit < best) {
                    best = unit;
                }
            }
        }
        return best;
    }

    priceLabel(mayorista) {
        return mayorista.muestra_precio === "final" ? "precio final" : "neto + IVA";
    }

    busyKey(producto, mayorista, presentacion = null) {
        return `${producto.id}:${mayorista.id}:${presentacion ? presentacion.id : ""}`;
    }

    isBusy(producto, mayorista, presentacion = null) {
        return this.state.busyKey === this.busyKey(producto, mayorista, presentacion);
    }
}

registry.category("actions").add("nexo_b2b.catalog", NexoB2bCatalog);
