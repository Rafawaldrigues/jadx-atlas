package com.example.orders.data;

public interface Repository<T> {
    T find(String id);
}
