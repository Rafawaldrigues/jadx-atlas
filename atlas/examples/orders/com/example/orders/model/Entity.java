package com.example.orders.model;

import java.io.Serializable;

public abstract class Entity implements Serializable {
    public final String id;
    protected Entity(String id) { this.id = id; }
}
