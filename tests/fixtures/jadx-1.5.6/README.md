Saída real do JADX 1.5.6 sobre código deste projeto (tests/apk/testapp e um exemplo mínimo),
guardada para testar o reconhecimento dos comentários `renamed from` / `compiled from`.

- `compiled_from.java`: `jadx` em um jar com a classe `b` declarada em `Helper.java`.
- `renamed_with_reason.java`: o mesmo jar com `--use-source-name-as-class-name-alias if-better`.
- `renamed_deobf.java`: `jadx --deobf` no APK do testapp passado pelo R8 (`build_test_apk.py --obfuscate`).
- `r8_toplevel_dollar.java`: R8 removeu `Outer` e manteve `Outer$InnerReceiver` como classe de topo.
