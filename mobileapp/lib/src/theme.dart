import 'package:flutter/material.dart';

abstract final class GuardAidColors {
  static const navy = Color(0xFF0B314A);
  static const navySoft = Color(0xFF174A68);
  static const canvas = Color(0xFFF3F7FA);
  static const border = Color(0xFFD9E3EA);
  static const muted = Color(0xFF627787);
  static const weapon = Color(0xFFC73532);
  static const violence = Color(0xFFD87918);
  static const success = Color(0xFF27845A);
  static const info = Color(0xFF2878A6);
}

ThemeData buildGuardAidTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: GuardAidColors.navy,
    brightness: Brightness.light,
    primary: GuardAidColors.navy,
    secondary: GuardAidColors.info,
    error: GuardAidColors.weapon,
    surface: Colors.white,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: GuardAidColors.canvas,
    appBarTheme: const AppBarTheme(
      backgroundColor: GuardAidColors.canvas,
      foregroundColor: GuardAidColors.navy,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(
        color: GuardAidColors.navy,
        fontSize: 20,
        fontWeight: FontWeight.w800,
      ),
    ),
    cardTheme: CardThemeData(
      color: Colors.white,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: const BorderSide(color: GuardAidColors.border),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: GuardAidColors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: GuardAidColors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: GuardAidColors.navy, width: 1.5),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        textStyle: const TextStyle(fontWeight: FontWeight.w700),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        side: const BorderSide(color: GuardAidColors.border),
        textStyle: const TextStyle(fontWeight: FontWeight.w700),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        minimumSize: const Size(48, 48),
        tapTargetSize: MaterialTapTargetSize.padded,
      ),
    ),
    navigationBarTheme: const NavigationBarThemeData(
      backgroundColor: Colors.white,
      indicatorColor: Color(0xFFDCEAF2),
      labelTextStyle: WidgetStatePropertyAll(
        TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
      ),
    ),
    dividerColor: GuardAidColors.border,
  );
}
