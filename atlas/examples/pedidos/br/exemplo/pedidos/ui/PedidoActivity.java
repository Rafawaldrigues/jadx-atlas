package br.exemplo.pedidos.ui;

import br.exemplo.pedidos.contract.PedidoView;
import br.exemplo.pedidos.contract.Carregavel;

public class PedidoActivity extends BaseActivity implements PedidoView, Carregavel {
    @Override
    public void mostrarPedido(String nome) {
        mostrarMensagem("Pedido: " + nome);
    }

    @Override
    public void carregar() {
        mostrarPedido("Pizza margherita");
    }

    @Override
    public void mostrarErro(String erro) {
        mostrarMensagem(erro);
    }
}
