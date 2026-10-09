package br.atlas.testapp;

import java.security.cert.X509Certificate;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

public class InsecureClient {
    public void connect(String endpoint) {
        try {
            TrustManager[] trustAll = new TrustManager[]{new X509TrustManager() {
                public void checkClientTrusted(X509Certificate[] chain, String authType) {}
                public void checkServerTrusted(X509Certificate[] chain, String authType) {}
                public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            }};
            SSLContext context = SSLContext.getInstance("TLS");
            context.init(null, trustAll, null);
            HttpsURLConnection connection = (HttpsURLConnection) new java.net.URL(endpoint).openConnection();
            connection.setSSLSocketFactory(context.getSocketFactory());
            connection.connect();
        } catch (Exception ignored) {
        }
    }
}
