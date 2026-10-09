package com.example.orders.ui;

import android.content.Intent;
import com.example.orders.contract.OrderView;
import com.example.orders.contract.Loadable;

public class OrderActivity extends BaseActivity implements OrderView, Loadable {
    static final String ACTION_REFRESH = "com.example.orders.REFRESH";

    @Override
    public void showOrder(String name) {
        showMessage("Order: " + name);
    }

    @Override
    public void load() {
        showOrder("Pizza margherita");
    }

    /** Fictional: opens checkout and notifies other screens, to demonstrate Intent edges. */
    public void completeOrder() {
        Intent checkout = new Intent(this, CheckoutActivity.class);
        checkout.putExtra("url", "https://payment.example/checkout");
        startActivity(checkout);
        sendBroadcast(new Intent(ACTION_REFRESH));
    }
}
