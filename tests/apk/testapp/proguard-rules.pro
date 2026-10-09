# Keep what the manifest references (as AGP does); everything else may be renamed.
-keep public class * extends android.app.Activity
-keep public class * extends android.app.Service
-keep public class * extends android.content.BroadcastReceiver
-keep public class * extends android.content.ContentProvider
-keep public class * extends android.app.Application
# Keep the original source file name so JADX can print "compiled from".
-keepattributes SourceFile,LineNumberTable
