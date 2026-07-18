# ProGuard rules for Бензин рядом

# Keep Google Maps
-keep class com.google.android.gms.maps.** { *; }

# Keep Flutter
-keep class io.flutter.** { *; }

# Keep HTTP models
-keep class com.benzinryadom.app.** { *; }

# Suppress warnings
-dontwarn javax.annotation.**
-dontwarn sun.misc.Unsafe
