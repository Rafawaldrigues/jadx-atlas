package br.exemplo.pedidos.ui;

import androidx.appcompat.app.AppCompatActivity;

/** Exemplo fictício para explorar a hierarquia. */
public abstract class BaseActivity extends AppCompatActivity {
    protected void mostrarMensagem(String mensagem) {
        System.out.println(mensagem);
    }
}
