package br.exemplo.pedidos.model;

import java.io.Serializable;

public abstract class Entidade implements Serializable {
    public final String id;
    protected Entidade(String id) { this.id = id; }
}
