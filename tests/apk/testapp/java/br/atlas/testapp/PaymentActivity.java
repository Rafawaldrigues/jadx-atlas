package br.atlas.testapp;

import android.app.Activity;

public class PaymentActivity extends Activity {
    @Override
    protected void onResume() {
        super.onResume();
        InsecureClient client = new InsecureClient();
        client.connect(getIntent().getStringExtra("endpoint"));
    }
}
