package com.example.orders.ui;

import android.webkit.WebView;
import com.example.orders.contract.OrderView;

public class CheckoutActivity extends BaseActivity implements OrderView {
    public void showOrder(String name) { showMessage(name); }
    public void showError(String error) { showMessage(error); }

    /** Fictional: opens the payment page in a WebView with the URL received through the deep link. */
    public void openPayment(WebView webView) {
        String url = getIntent().getStringExtra("url");
        webView.getSettings().setJavaScriptEnabled(true);
        webView.loadUrl(url);
    }
}
