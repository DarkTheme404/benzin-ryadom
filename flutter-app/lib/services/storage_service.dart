import 'package:shared_preferences/shared_preferences.dart';

class StorageService {
  static final StorageService _instance = StorageService._internal();
  factory StorageService() => _instance;
  StorageService._internal();

  SharedPreferences? _prefs;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  int? get userId => _prefs?.getInt('user_id');
  set userId(int? value) {
    if (value != null) {
      _prefs?.setInt('user_id', value);
    } else {
      _prefs?.remove('user_id');
    }
  }

  bool get isVkUser => _prefs?.getBool('is_vk_user') ?? false;
  set isVkUser(bool value) => _prefs?.setBool('is_vk_user', value);

  String? get selectedCity => _prefs?.getString('selected_city');
  set selectedCity(String? value) {
    if (value != null) {
      _prefs?.setString('selected_city', value);
    } else {
      _prefs?.remove('selected_city');
    }
  }

  String? get selectedFuel => _prefs?.getString('selected_fuel');
  set selectedFuel(String? value) {
    if (value != null) {
      _prefs?.setString('selected_fuel', value);
    } else {
      _prefs?.remove('selected_fuel');
    }
  }

  bool get isFirstLaunch => _prefs?.getBool('first_launch') ?? true;
  set firstLaunch(bool value) => _prefs?.setBool('first_launch', value);

  bool get darkMode => _prefs?.getBool('dark_mode') ?? true;
  set darkMode(bool value) => _prefs?.setBool('dark_mode', value);

  String? get authToken => _prefs?.getString('auth_token');
  set authToken(String? value) {
    if (value != null) {
      _prefs?.setString('auth_token', value);
    } else {
      _prefs?.remove('auth_token');
    }
  }
}
