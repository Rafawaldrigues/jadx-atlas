package br.exemplo.pedidos.data;

import br.exemplo.pedidos.model.Pedido;

public class PedidoRepository extends BaseRepository<Pedido> {
    public Pedido buscar(String id) { return new Pedido(id); }

    public static class Cache implements Repository<Pedido> {
        public Pedido buscar(String id) { return new Pedido(id); }
    }
}
