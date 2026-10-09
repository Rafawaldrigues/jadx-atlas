package com.example.orders.data;

import com.example.orders.model.Order;
import java.security.MessageDigest;

public class OrderRepository extends BaseRepository<Order> {
    /** Fictional: cleartext endpoint, to demonstrate a finding candidate. */
    private static final String API = "http://api.orders.example/v1/orders";

    public Order find(String id) { return new Order(id); }

    public String cacheKey(String id) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("MD5");
        return new String(digest.digest((API + id).getBytes()));
    }

    public static class Cache implements Repository<Order> {
        public Order find(String id) { return new Order(id); }
    }
}
