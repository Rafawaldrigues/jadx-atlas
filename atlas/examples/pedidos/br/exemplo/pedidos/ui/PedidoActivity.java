package br.exemplo.pedidos.ui;

import android.content.Intent;
import br.exemplo.pedidos.contract.PedidoView;
import br.exemplo.pedidos.contract.Carregavel;

public class PedidoActivity extends BaseActivity implements PedidoView, Carregavel {
    static final String ACAO_ATUALIZAR = "br.exemplo.pedidos.ATUALIZAR";

    @Override
    public void mostrarPedido(String nome) {
        mostrarMensagem("Pedido: " + nome);
    }

    @Override
    public void carregar() {
        mostrarPedido("Pizza margherita");
    }

    /** Fictício: abre o checkout e avisa outras telas, para demonstrar arestas de Intent. */
    public void finalizar() {
        Intent checkout = new Intent(this, CheckoutActivity.class);
        checkout.putExtra("url", "https://pagamento.example/checkout");
        startActivity(checkout);
        sendBroadcast(new Intent(ACAO_ATUALIZAR));
    }
}
