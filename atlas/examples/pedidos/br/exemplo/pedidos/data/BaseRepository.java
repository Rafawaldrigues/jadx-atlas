package br.exemplo.pedidos.data;

public abstract class BaseRepository<T> implements Repository<T> {
    protected String origem = "local";
}
