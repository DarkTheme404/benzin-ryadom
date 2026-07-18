import 'dart:convert';
import 'package:http/http.dart' as http;
import '../config/api.dart';
import '../models/station.dart';
import '../models/user.dart';
import '../models/route.dart';

class ApiService {
  static final ApiService _instance = ApiService._internal();
  factory ApiService() => _instance;
  ApiService._internal();

  String? _userId;
  String _userIdType = 'telegram_id';

  void setUserId(int id, {bool isVk = false}) {
    _userId = id.toString();
    _userIdType = isVk ? 'vk_user_id' : 'telegram_id';
  }

  Map<String, String> get _idParam => _userId != null
      ? {_userIdType: _userId!}
      : {};

  Future<Map<String, dynamic>> _get(String path,
      [Map<String, String>? extraParams]) async {
    final params = {...?extraParams};
    params.addAll(_idParam);
    final uri =
        Uri.parse('${ApiConfig.apiBase}$path').replace(queryParameters: params);
    final resp =
        await http.get(uri).timeout(ApiConfig.timeout);
    if (resp.statusCode == 200) {
      return jsonDecode(resp.body);
    }
    throw ApiException(resp.statusCode, resp.body);
  }

  Future<Map<String, dynamic>> _post(String path,
      [Map<String, dynamic>? body]) async {
    final uri = Uri.parse('${ApiConfig.apiBase}$path');
    final resp = await http
        .post(uri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body ?? {}))
        .timeout(ApiConfig.timeout);
    if (resp.statusCode == 200 || resp.statusCode == 201) {
      return jsonDecode(resp.body);
    }
    throw ApiException(resp.statusCode, resp.body);
  }

  Future<List<Station>> getStations({
    required double lat,
    required double lon,
    double radius = 50000,
    String? fuel,
  }) async {
    final data = await _get('/stations', {
      'lat': lat.toString(),
      'lon': lon.toString(),
      'radius': radius.toString(),
      if (fuel != null) 'fuel': fuel,
    });
    final list = data['stations'] as List? ?? [];
    return list.map((s) => Station.fromJson(s)).toList();
  }

  Future<List<Station>> getStationsByCity({
    required String city,
    String? fuel,
    String? network,
    double? maxPrice,
  }) async {
    final data = await _get('/stations/by-city', {
      'city': city,
      if (fuel != null) 'fuel': fuel,
      if (network != null) 'network': network,
      if (maxPrice != null) 'max_price': maxPrice.toString(),
    });
    final list = data['stations'] as List? ?? [];
    return list.map((s) => Station.fromJson(s)).toList();
  }

  Future<List<Station>> searchStations(String query) async {
    final data = await _get('/search', {'q': query});
    final list = data['stations'] as List? ?? [];
    return list.map((s) => Station.fromJson(s)).toList();
  }

  Future<Station?> getStationDetail(int id) async {
    final data = await _get('/stations/$id');
    if (data.containsKey('station')) {
      return Station.fromJson(data['station']);
    }
    return null;
  }

  Future<List<Map<String, dynamic>>> getStationPrices(int id) async {
    final data = await _get('/stations/$id/prices');
    return (data['prices'] as List? ?? [])
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }

  Future<List<Map<String, dynamic>>> getPriceHistory(
    int id, {
    String fuel = '95',
    int days = 30,
  }) async {
    final data = await _get('/stations/$id/price-history', {
      'fuel': fuel,
      'days': days.toString(),
    });
    return (data['history'] as List? ?? [])
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }

  Future<List<Map<String, dynamic>>> searchCities(String query) async {
    final data = await _get('/cities', {'q': query});
    return (data['cities'] as List? ?? [])
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }

  Future<List<FuelRoute>> getRoutes({String? query}) async {
    final data = await _get('/routes', {
      if (query != null) 'q': query,
    });
    final list = data['routes'] as List? ?? [];
    return list.map((r) => FuelRoute.fromJson(r)).toList();
  }

  Future<List<Station>> getRouteStations(int routeId) async {
    final data = await _get('/routes/$routeId/stations');
    final list = data['stations'] as List? ?? [];
    return list.map((s) => Station.fromJson(s)).toList();
  }

  Future<Map<String, dynamic>> getRouteFuel({
    required double fromLat,
    required double fromLon,
    required double toLat,
    required double toLon,
    String fuel = '95',
  }) async {
    return await _get('/route/fuel', {
      'from_lat': fromLat.toString(),
      'from_lon': fromLon.toString(),
      'to_lat': toLat.toString(),
      'to_lon': toLon.toString(),
      'fuel': fuel,
    });
  }

  Future<Map<String, dynamic>> getReverseGeocode(
      double lat, double lon) async {
    return await _get('/reverse-geocode', {
      'lat': lat.toString(),
      'lon': lon.toString(),
    });
  }

  Future<UserProfile?> getUserProfile() async {
    if (_userId == null) return null;
    try {
      final data = await _get('/premium/status');
      return UserProfile.fromJson(data);
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>> getPremiumPlans() async {
    return await _get('/premium/plans');
  }

  Future<Map<String, dynamic>> getPremiumStatus() async {
    return await _get('/premium/status');
  }

  Future<Map<String, dynamic>> activateTrial() async {
    return await _post('/premium/trial', _idParam);
  }

  Future<Map<String, dynamic>> createPayment(String plan) async {
    return await _post('/premium/create-payment', {
      ..._idParam,
      'plan': plan,
    });
  }

  Future<Map<String, dynamic>> getFounderStatus() async {
    return await _get('/founder/status');
  }

  Future<Map<String, dynamic>> getFounderList() async {
    return await _get('/founder/list');
  }

  Future<Map<String, dynamic>> getReferralBalance() async {
    return await _get('/referral/balance');
  }

  Future<Map<String, dynamic>> getReferralEarnings() async {
    return await _get('/referral/earnings');
  }

  Future<Map<String, dynamic>> getReferralStats() async {
    return await _get('/referral/stats');
  }

  Future<Map<String, dynamic>> getReferralCode() async {
    return await _get('/referral/code');
  }

  Future<Map<String, dynamic>> requestWithdrawal(double amount) async {
    return await _post('/referral/withdraw', {
      ..._idParam,
      'amount': amount,
    });
  }

  Future<Map<String, dynamic>> submitReport({
    required int stationId,
    required String fuelType,
    String? availability,
    double? price,
    int? queueMinutes,
    String? limits,
    bool? canisterBan,
  }) async {
    return await _post('/reports', {
      ..._idParam,
      'station_id': stationId,
      'fuel_type': fuelType,
      if (availability != null) 'availability': availability,
      if (price != null) 'price': price,
      if (queueMinutes != null) 'queue_minutes': queueMinutes,
      if (limits != null) 'limits': limits,
      if (canisterBan != null) 'canister_ban': canisterBan,
    });
  }

  Future<Map<String, dynamic>> submitReview({
    required int stationId,
    required int rating,
    String? comment,
  }) async {
    return await _post('/reviews', {
      ..._idParam,
      'station_id': stationId,
      'rating': rating,
      if (comment != null) 'comment': comment,
    });
  }

  Future<Map<String, dynamic>> submitPriceUpdate({
    required int stationId,
    required String fuelType,
    required double price,
  }) async {
    return await _post('/price-update', {
      ..._idParam,
      'station_id': stationId,
      'fuel_type': fuelType,
      'price': price,
    });
  }

  Future<Map<String, dynamic>> createFuelAlarm({
    required int stationId,
    required String fuelType,
  }) async {
    return await _post('/fuel-alarm/create', {
      ..._idParam,
      'station_id': stationId,
      'fuel_type': fuelType,
    });
  }

  Future<Map<String, dynamic>> deleteFuelAlarm({
    required int stationId,
    required String fuelType,
  }) async {
    return await _post('/fuel-alarm/delete', {
      ..._idParam,
      'station_id': stationId,
      'fuel_type': fuelType,
    });
  }

  Future<List<Map<String, dynamic>>> getFuelAlarms() async {
    final data = await _get('/fuel-alarm/list');
    return (data['alarms'] as List? ?? [])
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }

  Future<Map<String, dynamic>> getSavings() async {
    return await _get('/user/savings');
  }

  Future<Map<String, dynamic>> getStats() async {
    return await _get('/stats');
  }

  Future<Map<String, dynamic>> applyReferral(String code) async {
    return await _post('/referral/apply', {
      ..._idParam,
      'code': code,
    });
  }
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  ApiException(this.statusCode, this.body);

  @override
  String toString() => 'ApiException($statusCode): $body';
}
