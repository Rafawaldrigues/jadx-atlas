package br.atlas.testapp;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;

public class DeepLinkActivity extends Activity {
    @Override
    protected void onResume() {
        super.onResume();
        Uri data = getIntent().getData();
        Intent next = new Intent(this, PaymentActivity.class);
        next.putExtra("endpoint", data == null ? null : data.getQueryParameter("endpoint"));
        startActivity(next);
    }
}
