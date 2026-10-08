package br.exemplo.pedidos.ui;

import br.exemplo.pedidos.contract.Carregavel;

public class HistoricoActivity extends BaseActivity implements Carregavel {
    public void carregar() { mostrarMensagem("Histórico de pedidos"); }
}
