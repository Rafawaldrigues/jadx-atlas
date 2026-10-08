package br.exemplo.pedidos.data;

import br.exemplo.pedidos.model.Pedido;
import java.security.MessageDigest;

public class PedidoRepository extends BaseRepository<Pedido> {
    /** Fictício: endpoint em texto claro, para demonstrar um candidato a achado. */
    private static final String API = "http://api.pedidos.example/v1/pedidos";

    public Pedido buscar(String id) { return new Pedido(id); }

    public String chaveDeCache(String id) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("MD5");
        return new String(digest.digest((API + id).getBytes()));
    }

    public static class Cache implements Repository<Pedido> {
        public Pedido buscar(String id) { return new Pedido(id); }
    }
}
