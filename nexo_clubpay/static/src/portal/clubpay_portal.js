import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { Interaction } from "@web/public/interaction";

/**
 * El cliente pide el QR de ClubPay desde el presupuesto. Se consulta cada 2
 * segundos mientras está en pantalla, como en la caja; cuando el socio
 * confirma en su teléfono, la página se recarga con el descuento.
 */
export class NexoClubpayPortal extends Interaction {
    static selector = ".o_nexo_clubpay";
    dynamicContent = {
        ".o_nexo_clubpay_start": { "t-on-click": this.onStartClick },
        ".o_nexo_clubpay_cancel": { "t-on-click": this.onCancelClick },
    };

    setup() {
        this.orderId = this.el.dataset.orderId;
        this.accessToken = this.el.dataset.accessToken;
        this.chargeId = null;
        this.timer = null;
        this.registerCleanup(() => this.stopPolling());
    }

    url(action) {
        return `/my/orders/${this.orderId}/clubpay/${action}`;
    }

    message(text, kind = "danger") {
        const el = this.el.querySelector(".o_nexo_clubpay_msg");
        el.className = `o_nexo_clubpay_msg mt-2 ${text ? `alert alert-${kind}` : ""}`;
        el.textContent = text || "";
    }

    // No se llama `start`: en Interaction es el arranque y correría al abrir la página.
    async onStartClick() {
        this.message("");
        const data = await this.waitFor(rpc(this.url("start"), { access_token: this.accessToken }));
        if (data.error) {
            this.message(data.error);
            return;
        }
        this.show(data);
        this.startPolling();
    }

    show(data) {
        this.chargeId = data.charge_id;
        const box = this.el.querySelector(".o_nexo_clubpay_qr");
        box.classList.remove("d-none");
        this.el.querySelector(".o_nexo_clubpay_start").classList.add("d-none");
        this.el.querySelector(".o_nexo_clubpay_sim").classList.toggle("d-none", !data.simulated);
        if (data.qr_image) {
            this.el.querySelector(".o_nexo_clubpay_img").src = data.qr_image;
        }
        this.el.querySelector(".o_nexo_clubpay_link").href = data.qr_payload;
        if (data.expires_at) {
            const hora = new Date(data.expires_at).toLocaleTimeString("es-AR", {
                hour: "2-digit",
                minute: "2-digit",
                hourCycle: "h23",
            });
            this.el.querySelector(".o_nexo_clubpay_vence").textContent =
                `Vence a las ${hora}. Si no llegás, tocá de nuevo y sale otro.`;
        }
    }

    hide() {
        this.el.querySelector(".o_nexo_clubpay_qr").classList.add("d-none");
        this.el.querySelector(".o_nexo_clubpay_start").classList.remove("d-none");
    }

    startPolling() {
        this.stopPolling();
        this.timer = setInterval(() => this.poll(), 2000);
    }

    stopPolling() {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }

    async poll() {
        if (!this.chargeId || this.polling) {
            return;
        }
        this.polling = true;
        let data;
        try {
            data = await rpc(this.url("status"), {
                charge_id: this.chargeId,
                access_token: this.accessToken,
            });
        } catch {
            // Sin red se vuelve a preguntar en dos segundos.
            return;
        } finally {
            this.polling = false;
        }
        if (data.error && !data.state) {
            this.stopPolling();
            this.message(data.error);
            return;
        }
        if (data.state === "pending") {
            return;
        }
        this.stopPolling();
        if (data.state === "applied") {
            window.location.reload();
            return;
        }
        this.hide();
        const motivos = {
            expired: "El código venció. Tocá el botón para generar otro.",
            rejected: data.error || "Lo rechazaste desde la app de ClubPay.",
            cancelled: "Se canceló.",
        };
        this.message(motivos[data.state] || data.error, "info");
    }

    async onCancelClick() {
        this.stopPolling();
        if (this.chargeId) {
            await this.waitFor(
                rpc(this.url("cancel"), { charge_id: this.chargeId, access_token: this.accessToken })
            );
        }
        this.chargeId = null;
        this.hide();
    }
}

registry.category("public.interactions").add("nexo_clubpay.portal", NexoClubpayPortal);
