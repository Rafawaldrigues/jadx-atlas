package br.exemplo.pedidos.ui;

import br.exemplo.pedidos.contract.PedidoView;

public class CheckoutActivity extends BaseActivity implements PedidoView {
    public void mostrarPedido(String nome) { mostrarMensagem(nome); }
    public void mostrarErro(String erro) { mostrarMensagem(erro); }
}
