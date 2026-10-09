package com.example.orders.data;

public abstract class BaseRepository<T> implements Repository<T> {
    protected String source = "local";
}
