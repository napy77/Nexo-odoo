import { Dialog } from "@web/core/dialog/dialog";
import { Component, useProps, t } from "@odoo/owl";

/**
 * El QR de ClubPay en la caja, como en NexoPOS: el cajero lo da vuelta hacia
 * el cliente o lo imprime, y espera. Cuando el socio confirma, muestra su
 * nombre y número: es la única defensa contra que alguien de la fila escanee
 * el QR de otro (contrato de ClubPay).
 *
 * `info` es el estado reactivo que mantiene `PaymentClubPay`.
 */
export class ClubPayPopup extends Component {
    static template = "nexo_clubpay.ClubPayPopup";
    static components = { Dialog };

    props = useProps({
        info: t.object(),
        print: t.function(),
        cancel: t.function(),
        accept: t.function(),
        acceptMp: t.function(),
        retry: t.function(),
        close: t.function(),
    });

    get vence() {
        const expires = this.props.info.expires_at;
        if (!expires) {
            return "";
        }
        return new Date(expires).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    }
}
