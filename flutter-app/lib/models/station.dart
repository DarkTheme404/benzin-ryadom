class Station {
  final int id;
  final String name;
  final String? address;
  final String? city;
  final double? lat;
  final double? lon;
  final String? network;
  final String? operator;
  final List<StationStatus> statuses;
  final String? lastUpdate;
  final double? distance;
  final double? rating;
  final bool isVerified;

  Station({
    required this.id,
    required this.name,
    this.address,
    this.city,
    this.lat,
    this.lon,
    this.network,
    this.operator,
    this.statuses = const [],
    this.lastUpdate,
    this.distance,
    this.rating,
    this.isVerified = false,
  });

  factory Station.fromJson(Map<String, dynamic> json) {
    final statusesRaw = json['statuses'] as List? ?? [];
    final statuses = statusesRaw
        .map((e) => StationStatus.fromJson(e as Map<String, dynamic>))
        .toList();

    return Station(
      id: json['id'] ?? 0,
      name: json['name'] ?? '',
      address: json['address'],
      city: json['city'],
      lat: json['lat']?.toDouble(),
      lon: json['lon']?.toDouble(),
      network: json['network'],
      operator: json['operator'],
      statuses: statuses,
      lastUpdate: statuses.isNotEmpty ? statuses.first.createdAt : null,
      distance: (json['distance_km'] ?? json['distance'])?.toDouble(),
      rating: (json['avg_rating'] ?? json['rating'])?.toDouble(),
      isVerified: json['is_verified'] ?? false,
    );
  }

  String? get mainPrice {
    for (final s in statuses) {
      if (s.price != null) return '${s.price!.toStringAsFixed(2)} ₽';
    }
    return null;
  }

  String? mainPriceForFuel(String fuel) {
    for (final s in statuses) {
      if (s.fuelType == fuel && s.price != null) {
        return '${s.price!.toStringAsFixed(2)} ₽';
      }
    }
    return null;
  }

  List<StationStatus> statusesForFuel(String fuel) {
    return statuses.where((s) => s.fuelType == fuel).toList();
  }

  String get fuelStatus {
    if (statuses.isEmpty) return 'unknown';
    final has = statuses.where((s) => s.available == true).length;
    final no = statuses.where((s) => s.available == false).length;
    if (has == statuses.length) return 'available';
    if (no == statuses.length) return 'unavailable';
    if (has > 0) return 'partial';
    return 'unknown';
  }

  String fuelStatusForType(String fuel) {
    final filtered = statuses.where((s) => s.fuelType == fuel).toList();
    if (filtered.isEmpty) return 'unknown';
    final has = filtered.where((s) => s.available == true).length;
    final no = filtered.where((s) => s.available == false).length;
    if (has == filtered.length) return 'available';
    if (no == filtered.length) return 'unavailable';
    if (has > 0) return 'partial';
    return 'unknown';
  }
}

class StationStatus {
  final String fuelType;
  final bool? available;
  final double? price;
  final String? source;
  final String? createdAt;

  StationStatus({
    required this.fuelType,
    this.available,
    this.price,
    this.source,
    this.createdAt,
  });

  factory StationStatus.fromJson(Map<String, dynamic> json) {
    return StationStatus(
      fuelType: json['fuel_type'] ?? '',
      available: json['available'] as bool?,
      price: json['price']?.toDouble(),
      source: json['source'],
      createdAt: json['created_at'],
    );
  }

  String get priceText {
    if (price == null) return '—';
    return '${price!.toStringAsFixed(2)} ₽';
  }
}
