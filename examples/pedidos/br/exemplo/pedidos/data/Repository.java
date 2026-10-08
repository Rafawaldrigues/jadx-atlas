package br.exemplo.pedidos.data;

public interface Repository<T> {
    T buscar(String id);
}
