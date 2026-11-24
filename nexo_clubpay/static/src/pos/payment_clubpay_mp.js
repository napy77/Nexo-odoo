import { proxy } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { formatCurrency } from "@point_of_sale/app/models/utils/currency";
import { ClubPayMpPopup } from "./clubpay_mp_popup";

const POLL_MS = 2000;
const TITULOS = {
    expired: _t("El QR venció"),
    cancelled: _t("Cobro cancelado"),
    refunded: _t("El pago se devolvió"),
    error: _t("No se pudo cobrar con Mercado Pago"),
};

/**
 * Cobro con Mercado Pago a través de ClubPay, igual que en NexoPOS (DEC-024):
 *
 * 1. La caja pide a ClubPay un QR de mostrador por el importe de la línea.
 * 2. El cliente lo escanea con Mercado Pago o cualquier billetera. Se pregunta
 *    cada 2 s; cuando Mercado Pago lo acredita, la línea queda hecha.
 * 3. Un rechazo no es final: con el mismo QR puede probar otra tarjeta.
 * 4. Antes de cobrar con otro medio se cancela el cobro. Si ClubPay contesta
 *    que ya se pagó, la venta se cierra con ese pago.
 *
 * El servidor vuelve a comprobar el pago al recibir la venta.
 */
export class PaymentClubPayMp extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        // Las devoluciones se hacen desde el cobro, en el backend.
        this.supports_refunds = false;
        this.flow = null;
    }

    money(amount) {
        return formatCurrency(amount || 0, this.pos.currency);
    }

    async sendPaymentRequest(line) {
        await super.sendPaymentRequest(...arguments);
        const order = this.pos.getOrder();
        if (line.amount <= 0 || order.isRefund) {
            this.alert(
                _t(
                    "Las devoluciones de Mercado Pago se hacen desde Punto de Venta → Cobros con Mercado Pago → «Devolver»."
                )
            );
            return false;
        }
        line.setPaymentStatus("waitingCard");
        return await new Promise((resolve) => {
            this.flow = {
                line,
                order,
                amount: line.amount,
                resolve,
                timer: null,
                info: proxy({ state: "loading", amount_label: this.money(line.amount) }),
            };
            this.flow.close = this.dialog.add(
                ClubPayMpPopup,
                {
                    info: this.flow.info,
                    print: () => this.print(),
                    cancel: () => this.finish(false),
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
        if (flow.paymentId && ["pending", "rejected", "expired"].includes(flow.info.state)) {
            // El QR viejo todavía se podría pagar: se cancela antes de dar otro.
            try {
                const old = await this.callPaymentMethod("clubpay_mp_cancel", [
                    [flow.line.payment_method_id.id],
                    flow.paymentId,
                ]);
                if (old.state === "paid") {
                    this.update(old);
                    return;
                }
            } catch {
                // Uno vencido que no se pudo cancelar lo vigila el cron.
            }
        }
        Object.assign(flow.info, { state: "loading", error: false });
        try {
            const data = await this.callPaymentMethod("clubpay_mp_create", [
                [flow.line.payment_method_id.id],
                { amount: flow.amount, order_uuid: flow.order.uuid, order_name: flow.order.name },
            ]);
            this.update(data);
            if (this.flow === flow) {
                this.startPolling();
            }
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
            amount_label: this.money(data.amount ?? flow.amount),
        });
        flow.paymentId = data.payment_id;
        if (data.state === "paid") {
            this.accept();
        }
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
        if (!flow?.paymentId || flow.polling) {
            return;
        }
        flow.polling = true;
        try {
            const data = await this.callPaymentMethod("clubpay_mp_status", [
                [flow.line.payment_method_id.id],
                flow.paymentId,
            ]);
            if (this.flow === flow) {
                this.update(data);
                // `rejected` no es final: se sigue preguntando.
                if (!["pending", "rejected"].includes(data.state)) {
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
        if (!flow || flow.info.state !== "paid") {
            return;
        }
        const { line, info } = flow;
        line.transaction_id = info.payment_id;
        line.payment_ref_no = info.mp_payment_id || "";
        this.done(true);
    }

    async finish(success, { fromDialog = false } = {}) {
        const flow = this.flow;
        if (!flow) {
            return;
        }
        if (flow.paymentId && ["pending", "rejected", "expired"].includes(flow.info.state)) {
            try {
                const data = await this.callPaymentMethod("clubpay_mp_cancel", [
                    [flow.line.payment_method_id.id],
                    flow.paymentId,
                ]);
                Object.assign(flow.info, data);
            } catch (error) {
                // Si no se pudo cancelar, el QR podría seguir vivo: no se cierra
                // como si nada, para no cobrar dos veces.
                if (flow.info.state === "pending" || flow.info.state === "rejected") {
                    Object.assign(flow.info, {
                        error: _t("No se pudo cancelar el QR: %s. Probá de nuevo antes de cobrar con otro medio.", error.data?.message || error.message || ""),
                    });
                    if (fromDialog) {
                        this.reopen(flow);
                    }
                    return;
                }
            }
        }
        if (flow.info.state === "paid") {
            // Se pagó mientras tanto: la venta se cierra con ese pago.
            this.accept();
            return;
        }
        this.done(success, { fromDialog });
        setTimeout(() => flow.order.removePaymentline(flow.line));
    }

    reopen(flow) {
        flow.close = this.dialog.add(
            ClubPayMpPopup,
            {
                info: flow.info,
                print: () => this.print(),
                cancel: () => this.finish(false),
                retry: () => this.create(),
            },
            { onClose: () => this.finish(false, { fromDialog: true }) }
        );
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
                mp: true,
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
        this.dialog.add(AlertDialog, { title: "Mercado Pago", body });
    }
}

registry.category("pos_payment_providers").add("clubpay_mp", PaymentClubPayMp);
