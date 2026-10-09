package com.example.orders.ui;

import androidx.appcompat.app.AppCompatActivity;

/** Fictional example to explore the hierarchy. */
public abstract class BaseActivity extends AppCompatActivity {
    protected void showMessage(String message) {
        System.out.println(message);
    }
}
