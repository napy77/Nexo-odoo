import { Dialog } from "@web/core/dialog/dialog";
import { Component, useProps, t } from "@odoo/owl";

/**
 * El QR de Mercado Pago (vía ClubPay) en la caja. Lo lee Mercado Pago o
 * cualquier billetera. Cuando se acredita, la ventana se cierra sola.
 *
 * `info` es el estado reactivo que mantiene `PaymentClubPayMp`.
 */
export class ClubPayMpPopup extends Component {
    static template = "nexo_clubpay.ClubPayMpPopup";
    static components = { Dialog };

    props = useProps({
        info: t.object(),
        print: t.function(),
        cancel: t.function(),
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
