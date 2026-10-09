Real JADX 1.5.6 output produced from this project's own code (tests/apk/testapp and a minimal example),
kept to test how the `renamed from` / `compiled from` comments are recognised.

- `compiled_from.java`: `jadx` on a jar where class `b` is declared in `Helper.java`.
- `renamed_with_reason.java`: the same jar with `--use-source-name-as-class-name-alias if-better`.
- `renamed_deobf.java`: `jadx --deobf` on the test app built with R8 (`build_test_apk.py --obfuscate`).
- `r8_toplevel_dollar.java`: R8 removed `Outer` and kept `Outer$InnerReceiver` as a top-level class.
