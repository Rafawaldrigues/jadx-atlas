package br.exemplo.pedidos.ui;

import android.webkit.WebView;
import br.exemplo.pedidos.contract.PedidoView;

public class CheckoutActivity extends BaseActivity implements PedidoView {
    public void mostrarPedido(String nome) { mostrarMensagem(nome); }
    public void mostrarErro(String erro) { mostrarMensagem(erro); }

    /** Fictício: abre o pagamento numa WebView com a URL recebida pelo deep link. */
    public void abrirPagamento(WebView webView) {
        String url = getIntent().getStringExtra("url");
        webView.getSettings().setJavaScriptEnabled(true);
        webView.loadUrl(url);
    }
}
