import { proxy } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { formatCurrency } from "@point_of_sale/app/models/utils/currency";
import { ClubPayPopup } from "./clubpay_popup";

const POLL_MS = 2000;
const TITULOS = {
    rejected: _t("El socio lo rechazó desde la app"),
    expired: _t("El código venció"),
    cancelled: _t("Cobro cancelado"),
    error: _t("No se pudo usar ClubPay"),
};

/**
 * Descuento de ClubPay en la caja, igual que en NexoPOS:
 *
 * 1. El cajero elige «Cupón ClubPay» y se pide a ClubPay un QR por el total de
 *    la orden.
 * 2. El QR se muestra (o se imprime en la ticketera) y se pregunta cada 2 s.
 * 3. Cuando el socio confirma, la línea de pago queda por el descuento que dijo
 *    ClubPay y el resto se cobra con otra forma de pago.
 *
 * La caja no calcula el descuento. El servidor vuelve a comprobarlo al recibir
 * la venta (`pos.payment.create`).
 */
export class PaymentClubPay extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        // ClubPay no tiene todavía cómo anular un descuento ya aplicado.
        this.supports_refunds = false;
        this.flow = null;
    }

    money(amount) {
        return formatCurrency(amount || 0, this.pos.currency);
    }

    async sendPaymentRequest(line) {
        await super.sendPaymentRequest(...arguments);
        const order = this.pos.getOrder();
        if (line.amount < 0 || order.isRefund) {
            this.alert(_t("ClubPay no hace devoluciones desde la caja. Coordinalas con ClubPay."));
            return false;
        }
        const otra = order.payment_ids.find(
            (pl) => pl !== line && pl.payment_method_id.payment_provider === "clubpay" && pl.isDone()
        );
        if (otra) {
            this.alert(_t("Esta venta ya tiene un cupón de ClubPay."));
            return false;
        }
        line.setPaymentStatus("waitingCard");
        return await new Promise((resolve) => {
            this.flow = {
                line,
                order,
                total: order.totalDue,
                resolve,
                timer: null,
                info: proxy({ state: "loading" }),
            };
            this.flow.close = this.dialog.add(
                ClubPayPopup,
                {
                    info: this.flow.info,
                    print: () => this.print(),
                    cancel: () => this.finish(false),
                    accept: () => this.accept(),
                    retry: () => this.create(),
                },
                { onClose: () => this.finish(false, { fromDialog: true }) }
            );
            this.create();
        });
    }

    async create() {
        const flow = this.flow;
        if (!flow) {
            return;
        }
        this.stopPolling();
        Object.assign(flow.info, { state: "loading", error: false });
        try {
            const data = await this.callPaymentMethod("clubpay_create", [
                [flow.line.payment_method_id.id],
                { amount: flow.total, order_uuid: flow.order.uuid },
            ]);
            this.update(data);
            this.startPolling();
        } catch (error) {
            Object.assign(flow.info, {
                state: "error",
                title: TITULOS.error,
                error: error.data?.message || error.message || String(error),
            });
        }
    }

    update(data) {
        const flow = this.flow;
        if (!flow) {
            return;
        }
        Object.assign(flow.info, data, {
            title: TITULOS[data.state] || "",
            amount_label: this.money(data.ticket_total ?? flow.total),
            discount_label: this.money(data.discount),
            neto_label: this.money(data.neto),
        });
        flow.chargeId = data.charge_id;
    }

    startPolling() {
        this.stopPolling();
        this.flow.timer = setInterval(() => this.poll(), POLL_MS);
    }

    stopPolling() {
        if (this.flow?.timer) {
            clearInterval(this.flow.timer);
            this.flow.timer = null;
        }
    }

    async poll() {
        const flow = this.flow;
        if (!flow?.chargeId || flow.polling) {
            return;
        }
        flow.polling = true;
        try {
            const data = await this.callPaymentMethod("clubpay_status", [
                [flow.line.payment_method_id.id],
                flow.chargeId,
            ]);
            if (this.flow === flow) {
                this.update(data);
                if (data.state !== "pending") {
                    this.stopPolling();
                }
            }
        } catch {
            // Sin red se vuelve a preguntar en dos segundos.
        } finally {
            flow.polling = false;
        }
    }

    accept() {
        const flow = this.flow;
        if (!flow || flow.info.state !== "applied") {
            return;
        }
        const { line, info } = flow;
        line.setAmount(info.discount);
        line.transaction_id = info.charge_id;
        line.cardholder_name = info.member_name || "";
        line.payment_ref_no = info.transaction_id || "";
        this.done(true);
    }

    async finish(success, { fromDialog = false } = {}) {
        const flow = this.flow;
        if (!flow) {
            return;
        }
        if (flow.chargeId && flow.info.state === "pending") {
            try {
                await this.callPaymentMethod("clubpay_cancel", [
                    [flow.line.payment_method_id.id],
                    flow.chargeId,
                ]);
            } catch {
                // Si no se pudo cancelar, vence solo a los 3 minutos.
            }
        }
        if (flow.info.state === "applied" && !success) {
            // El socio ya confirmó: ClubPay registró la operación. Se aplica
            // igual, porque no hay forma de anularla desde acá.
            this.accept();
            return;
        }
        this.done(success, { fromDialog });
        // Volver al ticket sin descuento: la línea vacía no sirve para nada.
        setTimeout(() => flow.order.removePaymentline(flow.line));
    }

    done(success, { fromDialog = false } = {}) {
        const flow = this.flow;
        this.stopPolling();
        this.flow = null;
        if (!fromDialog) {
            flow.close();
        }
        flow.resolve(success);
    }

    async sendPaymentCancel(line) {
        await super.sendPaymentCancel(...arguments);
        if (this.flow?.line === line) {
            await this.finish(false);
        }
        return true;
    }

    async print() {
        const info = this.flow?.info;
        if (!info?.qr_image) {
            return;
        }
        const printer = this.pos.ticketPrinter;
        const iframe = await printer.generateIframe("nexo_clubpay.pos_clubpay_qr_receipt", {
            image: { logo: this.pos.config.receiptLogoUrl, qr: info.qr_image },
            extra_data: {
                company: this.pos.company.name,
                total: info.amount_label,
                vence: info.expires_at
                    ? new Date(info.expires_at).toLocaleTimeString("es-AR", {
                          hour: "2-digit",
                          minute: "2-digit",
                          hourCycle: "h23",
                      })
                    : "",
                simulated: info.simulated,
            },
        });
        await printer.printWithFallback({ iframe });
    }

    alert(body) {
        this.dialog.add(AlertDialog, { title: "ClubPay", body });
    }
}

registry.category("pos_payment_providers").add("clubpay", PaymentClubPay);

// Al elegir «Cupón ClubPay» el QR sale solo, sin tocar «Enviar»: en la fila no
// hay tiempo para un paso de más.
patch(PaymentScreen.prototype, {
    async addNewPaymentLine(paymentMethod) {
        const added = await super.addNewPaymentLine(...arguments);
        if (added && paymentMethod.payment_provider === "clubpay") {
            const line = this.paymentLines.at(-1);
            if (line?.payment_method_id === paymentMethod) {
                await this.sendPaymentRequest(line);
            }
        }
        return added;
    },
});
